"""Build the dashboard payload (jobs + analytics), encrypt it, and write dist/ for publishing.

Only dist/ is published, and only these files: index.html, app.js, app.css, manifest.webmanifest, sw.js,
icon.svg, data/jobs.enc.json. `assert_publishable` refuses anything else and any plaintext job data.
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import PROFILE_DIR, ROOT
from .crypto import encrypt_json
from .models import Job
from .recommend import apply_next
from .tracking import follow_ups, last_status_change

DASHBOARD_SRC = ROOT / "dashboard"
DIST = ROOT / "dist"
PUBLIC_FILES = {"index.html", "app.js", "crypto.js", "app.css", "manifest.webmanifest", "sw.js", "icon.svg", "data/jobs.enc.json"}
MAX_JOBS = 900
SNIPPET = 900


def _days_ago(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400


def select_jobs(jobs: list[Job]) -> list[Job]:
    """Everything you acted on, plus open jobs worth looking at."""
    acted = [j for j in jobs if j.status != "new"]
    open_good = [j for j in jobs if j.status == "new" and "closed" not in j.flags
                 and ((j.score or 0) >= 5 or (j.apply_priority or 0) >= 50)]
    open_good.sort(key=lambda j: j.apply_priority or 0, reverse=True)
    return acted + open_good[: max(0, MAX_JOBS - len(acted))]


def compact(job: Job) -> dict[str, Any]:
    s = job.scoring or {}
    return {
        "id": job.id, "title": job.title, "company": job.company, "location": job.location,
        "region": job.region, "country": job.country, "remote": job.remote,
        "url": job.url, "alt": job.alt_urls[:2], "source": job.source,
        "posted": job.posted_at, "expires": job.expires_at, "seen": job.first_seen,
        "status": job.status, "history": job.history[-12:],
        "score": job.score, "prio": job.apply_priority, "reasons": job.reasons, "flags": job.flags,
        "target": "target_company" in job.tags, "years": job.min_years, "salary": job.salary,
        "visa": job.visa_sponsorship, "desc": (job.description or "")[:SNIPPET],
        "ai": {k: s.get(k) for k in ("method", "provider", "level_fit", "matched_skills", "missing_skills",
                                      "tailoring_tips", "referral_message_draft", "interview_topics", "red_flags")},
    }


def analytics(jobs: list[Job], daily: dict[str, dict[str, int]], now: datetime) -> dict[str, Any]:
    week = [j for j in jobs if (_days_ago(j.first_seen, now) or 99) <= 7]
    prev = [j for j in jobs if 7 < (_days_ago(j.first_seen, now) or 99) <= 14]
    good_week = [j for j in week if (j.score or 0) >= 6]

    # Skill gap: what the jobs you match want that your resume doesn't show.
    gap = Counter(s.lower() for j in good_week for s in (j.scoring or {}).get("missing_skills") or [])
    have = Counter(s.lower() for j in good_week for s in (j.scoring or {}).get("matched_skills") or [])
    n_good = max(1, len(good_week))

    # Hiring momentum: companies opening the most matching roles this week vs last week.
    this_w = Counter(j.company for j in week)
    last_w = Counter(j.company for j in prev)
    momentum = [{"company": c, "this_week": n, "last_week": last_w.get(c, 0)} for c, n in this_w.most_common(12)]

    statuses = Counter(j.status for j in jobs)
    applied_ever = [j for j in jobs if any(h.get("status") == "applied" for h in j.history) or j.status in
                    ("applied", "interview", "offer", "rejected")]
    responded = [j for j in applied_ever if j.status in ("interview", "offer", "rejected")]
    interviews = [j for j in applied_ever if j.status in ("interview", "offer")
                  or any(h.get("status") == "interview" for h in j.history)]

    days = [(now - timedelta(days=i)).date().isoformat() for i in range(29, -1, -1)]
    return {
        "daily": [{"date": d, **daily.get(d, {})} for d in days],
        "skill_gap": [{"skill": s, "pct": round(100 * n / n_good), "count": n} for s, n in gap.most_common(10)],
        "strengths": [{"skill": s, "pct": round(100 * n / n_good)} for s, n in have.most_common(8)],
        "momentum": momentum,
        "regions": dict(Counter(j.region for j in week)),
        "sources": dict(Counter(j.source for j in week).most_common(12)),
        "statuses": dict(statuses),
        "funnel": {"tracked": len(jobs), "applied": len(applied_ever), "responded": len(responded),
                   "interviews": len(interviews), "offers": statuses.get("offer", 0)},
        "week": {"new": len(week), "good": len(good_week), "prev_new": len(prev),
                 "india": sum(j.region == "india" for j in week), "remote": sum(j.region == "remote" for j in week),
                 "abroad": sum(j.region == "abroad" for j in week)},
    }


def build_payload(jobs: list[Job], meta: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    chosen = select_jobs(jobs)
    picks = apply_next(chosen, n=20)
    due = follow_ups(jobs)
    return {
        "v": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "last_run": {k: v for k, v in (meta.get("last_run") or {}).items() if not isinstance(v, (list, dict))},
        "jobs": [compact(j) for j in chosen],
        "apply_next": [j.id for j in picks],
        "follow_ups": [{"id": j.id, "since": (last_status_change(j) or now).isoformat(timespec="seconds")} for j in due],
        "analytics": analytics(jobs, meta.get("daily") or {}, now),
    }


def _forbidden_strings() -> list[str]:
    """Plaintext that must never reach the public repo: contact details and resume lines."""
    resume = (PROFILE_DIR / "resume.md").read_text(encoding="utf-8") if (PROFILE_DIR / "resume.md").exists() else ""
    lines = [l.strip("-# ").strip() for l in resume.splitlines()]
    return [l for l in lines if len(l) > 60][:20]


def assert_publishable(dist: Path = DIST) -> None:
    files = {str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file()}
    extra = files - PUBLIC_FILES
    if extra:
        raise RuntimeError(f"refusing to publish unexpected files: {sorted(extra)}")
    blob = json.loads((dist / "data/jobs.enc.json").read_text(encoding="utf-8"))
    if set(blob) != {"v", "salt", "iv", "iters", "ct"}:
        raise RuntimeError("jobs.enc.json is not in the encrypted format")
    for name in files:
        text = (dist / name).read_text(encoding="utf-8", errors="ignore")
        if re.search(r"[A-Za-z0-9._%+-]+@gmail\.com|\+91[- ]?\d{10}", text):
            raise RuntimeError(f"{name} contains contact details")
        for line in _forbidden_strings():
            if line in text:
                raise RuntimeError(f"{name} contains resume text")


def export(jobs: list[Job], meta: dict[str, Any], passphrase: str, dist: Path = DIST) -> Path:
    payload = build_payload(jobs, meta)
    if dist.exists():
        shutil.rmtree(dist)
    (dist / "data").mkdir(parents=True)
    for name in PUBLIC_FILES - {"data/jobs.enc.json"}:
        shutil.copy2(DASHBOARD_SRC / name, dist / name)
    out = dist / "data" / "jobs.enc.json"
    out.write_text(json.dumps(encrypt_json(payload, passphrase)), encoding="utf-8")
    assert_publishable(dist)
    return out
