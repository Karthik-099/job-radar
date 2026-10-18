"""Email alerts with zero email setup: post a digest to a GitHub issue in this private repo.

GitHub emails repo watchers about new issues and comments, and you watch your own repo by default, so
each digest lands in the inbox of your GitHub account's email. Issues are authored by github-actions[bot]
(GITHUB_TOKEN), which is what makes GitHub notify you (your own actions don't notify you).

- One issue per day (IST). The first run after 8 AM IST writes the morning summary (apply next, follow-ups,
  yesterday vs goal); other runs add a comment only when there are new jobs worth your time, so there is
  never an empty email.
- Yesterday's issue is closed automatically.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .envsecrets import secret
from .models import Job
from .models import role_key
from .recommend import age_label, applied_roles, apply_next
from .tracking import follow_ups

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
LABEL = "job-radar-digest"
WORTH_ALERT = 60  # apply_priority threshold for "new jobs worth your time"
SUMMARY_HOUR_IST = 8


def _api(path: str, method: str = "GET", **kw: Any) -> requests.Response:
    repo = os.environ["GITHUB_REPOSITORY"]
    resp = requests.request(method, f"https://api.github.com/repos/{repo}{path}", timeout=20,
                            headers={"Authorization": f"Bearer {secret('GITHUB_TOKEN')}",
                                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                            **kw)
    resp.raise_for_status()
    return resp


def _line(job: Job) -> str:
    bits = [f"**{job.title}** · {job.company} · {job.location or job.region}"]
    meta = [f"priority {job.apply_priority:.0f}", f"fit {job.score}/10" if job.score is not None else "",
            age_label(job)]
    closes = next((r for r in job.reasons if r.startswith("closes")), "")
    if closes:
        meta.append(f"⏰ {closes}")
    return f"- {bits[0]}  \n  {' · '.join(m for m in meta if m)} · [open job]({job.url})"


def digest_body(new_jobs: list[Job], all_jobs: list[Job], dashboard_url: str, summary: bool,
                goal: int, applied_yesterday: int, stats: dict[str, Any]) -> str:
    done = applied_roles(all_jobs)  # same role you already applied to (other city/board): not news
    worth = sorted((j for j in new_jobs if (j.apply_priority or 0) >= WORTH_ALERT
                    and role_key(j.company, j.title) not in done),
                   key=lambda j: j.apply_priority or 0, reverse=True)
    parts: list[str] = []
    if summary:
        picks = apply_next(all_jobs, n=10)
        due = follow_ups(all_jobs)
        parts.append(f"### Good morning. Your job radar for {datetime.now(IST):%A %d %b}")
        parts.append(f"Applied yesterday: **{applied_yesterday}/{goal}**. "
                     f"Open jobs tracked: **{sum(j.status == 'new' for j in all_jobs)}**.")
        if picks:
            parts.append("#### Apply next")
            parts += [_line(j) for j in picks]
        if due:
            parts.append("#### Follow up (applied 7+ days ago, no reply)")
            parts += [f"- {j.title} · {j.company} · [open]({j.url})" for j in due[:8]]
    if worth:
        parts.append(f"#### {len(worth)} new job{'s' if len(worth) != 1 else ''} worth applying to"
                     f" (India {sum(j.region == 'india' for j in worth)}, remote {sum(j.region == 'remote' for j in worth)},"
                     f" abroad {sum(j.region == 'abroad' for j in worth)})")
        parts += [_line(j) for j in worth[:15]]
        if len(worth) > 15:
            parts.append(f"...and {len(worth) - 15} more on the dashboard.")
    if not parts:
        return ""
    parts.append(f"\n[Open dashboard]({dashboard_url}) · run took {stats.get('duration_seconds', '?')}s · "
                 f"{stats.get('kept', 0)} new matches this run")
    return "\n".join(parts)


def notify(new_jobs: list[Job], all_jobs: list[Job], meta: dict[str, Any], stats: dict[str, Any],
           goal: int = 5) -> str:
    """Post the digest. Returns what happened, for the run log. Never raises."""
    if not (os.environ.get("GITHUB_ACTIONS") and secret("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY")):
        return "skipped (not in GitHub Actions)"
    owner = os.environ["GITHUB_REPOSITORY"].split("/")[0]
    dashboard_url = os.environ.get("DASHBOARD_URL") or f"https://{owner.lower()}.github.io/job-radar-dashboard/"
    today = datetime.now(IST).date().isoformat()
    # Morning summary: first run after 08:00 IST (the 03:17 UTC run = 08:47 IST). Other runs only alert on new jobs.
    summary = meta.get("last_summary_date") != today and datetime.now(IST).hour >= SUMMARY_HOUR_IST
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    applied_yesterday = sum(1 for j in all_jobs for h in j.history
                            if h.get("status") == "applied" and (h.get("at") or "")[:10] == yesterday)
    body = digest_body(new_jobs, all_jobs, dashboard_url, summary, goal, applied_yesterday, stats)
    if not body:
        return "nothing worth an email"
    try:
        title = f"Job Radar · {today}"
        issues = _api(f"/issues?state=open&labels={LABEL}&per_page=20").json()
        current = next((i for i in issues if i["title"] == title), None)
        for old in issues:
            if old is not current:
                _api(f"/issues/{old['number']}", "PATCH", json={"state": "closed"})
        if current is None:
            _api("/issues", "POST", json={"title": title, "body": body, "labels": [LABEL]})
            what = "created today's digest issue"
        else:
            _api(f"/issues/{current['number']}/comments", "POST", json={"body": body})
            what = "commented on today's digest issue"
        if summary:
            meta["last_summary_date"] = today
        return what
    except requests.RequestException as exc:
        log.warning("notify failed: %s", type(exc).__name__)
        return f"failed ({type(exc).__name__})"
