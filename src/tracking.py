"""Lifecycle tracking: ghost/repost detection, daily closed-job checks, follow-up reminders."""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from . import net
from .models import Job, _norm_text, normalize_company

log = logging.getLogger(__name__)

REPOST_THRESHOLD = 3          # same company+title appearing as a new posting this many times
REPOST_WINDOW_DAYS = 60
FOLLOW_UP_AFTER_DAYS = 7
CLOSED_CHECK_EVERY_HOURS = 20
CLOSED_CHECK_MAX = 80
CLOSED_CHECK_SECONDS = 60

_CLOSED_TEXT = re.compile(
    r"no longer accepting applications|job (is )?no longer available|position has been filled|"
    r"this job (has )?(expired|closed)|posting (has )?(expired|closed)|job not found|"
    r"the job you are looking for (is no longer|does not exist)|this position is closed|"
    r"no longer open|vacancy (has been )?closed",
    re.I,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------------------------------- ghost jobs
def fingerprint(job: Job) -> str:
    return f"{normalize_company(job.company)}|{_norm_text(job.title)}"


def track_reposts(jobs: list[Job], meta: dict[str, Any]) -> int:
    """Record each new posting under its company+title fingerprint; flag ones that keep coming back.

    A role re-posted 3+ times in 60 days (new ids/urls each time) is often an evergreen or ghost listing.
    """
    seen: dict[str, list[list[str]]] = meta.setdefault("fingerprints", {})
    cutoff = (_now() - timedelta(days=REPOST_WINDOW_DAYS)).date().isoformat()
    flagged = 0
    for job in jobs:
        fp = fingerprint(job)
        entries = [e for e in seen.get(fp, []) if e[1] >= cutoff]
        if job.id not in {e[0] for e in entries}:
            entries.append([job.id, _now().date().isoformat()])
        seen[fp] = entries[-10:]
        if len(entries) >= REPOST_THRESHOLD and "reposted" not in job.flags:
            job.flags.append("reposted")
            job.reasons.append(f"re-posted {len(entries)}x in 60d")
            flagged += 1
    # Drop fingerprints that have aged out entirely so state stays small.
    for fp in [k for k, v in seen.items() if not v or v[-1][1] < cutoff]:
        del seen[fp]
    return flagged


# ------------------------------------------------------------------------------------------- closed jobs
def _ats_check(url: str) -> bool | None:
    """True = open, False = closed, None = can't tell. Uses each ATS's public API where possible."""
    parts = urlsplit(url)
    host, path = parts.netloc.lower(), parts.path
    m = re.match(r"/([^/]+)/jobs/(\d+)", path)
    if "greenhouse.io" in host and m:
        r = net.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
        return None if r.status_code >= 500 else r.status_code == 200
    m = re.match(r"/([^/]+)/([0-9a-f-]{36})", path)
    if host == "jobs.lever.co" and m:
        r = net.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
        return None if r.status_code >= 500 else r.status_code == 200
    if host == "jobs.ashbyhq.com" and m:
        r = net.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
        if not r.ok:
            return None
        return any(j.get("id") == m.group(2) for j in r.json().get("jobs", []))
    m = re.match(r"/([^/]+)/(\d+)", path)
    if host == "jobs.smartrecruiters.com" and m:
        r = net.get(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
        return None if r.status_code >= 500 else r.status_code == 200
    m = re.match(r"(?:/[a-z]{2}-[A-Z]{2})?/([^/]+)(/job/.+)", path)
    if host.endswith("myworkdayjobs.com") and m:
        tenant = host.split(".")[0]
        r = net.get(f"https://{host}/wday/cxs/{tenant}/{m.group(1)}{m.group(2)}", headers={"Accept": "application/json"})
        if r.status_code in (404, 410):
            return False
        if not r.ok:
            return None
        info = r.json().get("jobPostingInfo") or {}
        return bool(info.get("posted", True)) and bool(info.get("canApply", True))
    return None


def is_open(url: str) -> bool | None:
    try:
        verdict = _ats_check(url)
        if verdict is not None:
            return verdict
        r = net.get(url, allow_redirects=True)
        if r.status_code in (404, 410):
            return False
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            return None  # blocked or flaky: don't guess
        return not _CLOSED_TEXT.search(r.text[:200_000])
    except (net.requests.RequestException, ValueError):
        return None


def closed_check_due(meta: dict[str, Any]) -> bool:
    last = _parse(meta.get("last_closed_check"))
    return last is None or _now() - last > timedelta(hours=CLOSED_CHECK_EVERY_HOURS)


def check_closed(jobs: list[Job], meta: dict[str, Any], force: bool = False) -> dict[str, int]:
    """Re-open links of jobs you might still apply to; flag the ones that are gone. Runs about once a day."""
    stats = {"checked": 0, "closed": 0, "unknown": 0}
    if not force and not closed_check_due(meta):
        return stats
    candidates = [j for j in jobs if j.status in ("new", "shortlisted") and "closed" not in j.flags
                  and (j.status == "shortlisted" or (j.apply_priority or 0) >= 55)]
    candidates.sort(key=lambda j: (j.status != "shortlisted", -(j.apply_priority or 0)))
    candidates = candidates[:CLOSED_CHECK_MAX]
    deadline = time.monotonic() + CLOSED_CHECK_SECONDS

    def one(job: Job) -> tuple[Job, bool | None]:
        if time.monotonic() > deadline:
            return job, None
        return job, is_open(job.url)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for job, verdict in pool.map(one, candidates):
            stats["checked"] += 1
            if verdict is False:
                job.flags.append("closed")
                job.history.append({"event": "closed_detected", "at": _now().isoformat(timespec="seconds")})
                stats["closed"] += 1
            elif verdict is None:
                stats["unknown"] += 1
    meta["last_closed_check"] = _now().isoformat(timespec="seconds")
    log.info("closed check: %d checked, %d closed, %d unknown", stats["checked"], stats["closed"], stats["unknown"])
    return stats


# ------------------------------------------------------------------------------------------- follow-ups
def last_status_change(job: Job) -> datetime | None:
    stamps = [_parse(h.get("at")) for h in job.history if h.get("status")]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def follow_ups(jobs: list[Job], days: int = FOLLOW_UP_AFTER_DAYS) -> list[Job]:
    """Applied N+ days ago with no newer status: time to nudge a recruiter or referrer."""
    cutoff = _now() - timedelta(days=days)
    due = []
    for job in jobs:
        changed = last_status_change(job)
        if job.status == "applied" and changed and changed <= cutoff:
            due.append(job)
    return sorted(due, key=lambda j: last_status_change(j) or _now())
