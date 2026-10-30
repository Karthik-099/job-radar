"""Build the dashboard payload (jobs + analytics), encrypt it, and write dist/ for publishing.

Only dist/ is published, and only these files: index.html, app.js, app.css, manifest.webmanifest, sw.js,
icon.svg, data/jobs.enc.json. `assert_publishable` refuses anything else and any plaintext job data.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import PROFILE_DIR, ROOT
from .crypto import encrypt_json
from .budget import DEFAULT_LIMIT, load as load_usage, used_minutes
from .inbox import review_key
from .models import Job, posting_ref, role_key
from .recommend import apply_next
from .tailor import jd_terms, load_base, plan, public_base
from .tracking import follow_ups, last_status_change

DASHBOARD_SRC = ROOT / "dashboard"
DIST = ROOT / "dist"
FONTS = {f"fonts/lato-{w}.woff2" for w in ("400", "700", "400-italic", "700-italic")}
# Third-party libraries, pinned and served from the dashboard itself (see dashboard/vendor/README.md).
VENDOR = {f"vendor/{n}" for n in ("d3-array.min.js", "d3-geo.min.js", "topojson-client.min.js", "countries-50m.json",
                                  "pdf.min.mjs", "pdf.worker.min.mjs")}
ENCRYPTED = {"data/jobs.enc.json", "data/world.enc.json"}
PUBLIC_FILES = {"index.html", "app.js", "map.js", "crypto.js", "app.css", "manifest.webmanifest", "sw.js", "icon.svg",
                "autofill.user.js", *ENCRYPTED, *FONTS, *VENDOR}
WORLD_FILE = ROOT / "state" / "world.json"
MAX_JOBS = 900
SNIPPET = 900
SHORT_SNIPPET = 260
FULL_SNIPPETS = 250  # select_jobs() lists acted jobs first, then open ones by priority


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


def compact(job: Job, base: dict[str, Any] | None = None, snippet: int = SNIPPET) -> dict[str, Any]:
    s = job.scoring or {}
    terms = job.jd_terms or jd_terms(f"{job.title}\n{job.description}")
    tailored = plan(job.title, terms, base) if base and job.status not in ("ignored", "rejected") else {}
    return {
        "id": job.id, "title": job.title, "company": job.company, "location": job.location,
        "region": job.region, "country": job.country, "remote": job.remote,
        "url": job.url, "alt": job.alt_urls[:2], "source": job.source,
        "posted": job.posted_at, "expires": job.expires_at, "seen": job.first_seen,
        "status": job.status, "history": job.history[-12:],
        "score": job.score, "prio": job.apply_priority, "reasons": job.reasons, "flags": job.flags,
        "target": "target_company" in job.tags, "yc": "yc" in job.tags, "years": job.min_years, "salary": job.salary,
        "visa": job.visa_sponsorship, "desc": (job.description or "")[:snippet],
        "rk": role_key(job.company, job.title), "ref": job.ref or posting_ref(job.url, job.description),
        "tl": tailored, "letter": (job.letter or {}).get("text", ""),
        "ai": {k: s.get(k) for k in ("method", "provider", "level_fit", "matched_skills", "missing_skills",
                                      "tailoring_tips", "interview_topics", "red_flags", "must_haves", "brief", "legit")},
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
                    ("applied", "shortlisted", "interview", "offer", "rejected")]
    responded = [j for j in applied_ever if j.status in ("shortlisted", "interview", "offer", "rejected")]
    shortlisted = [j for j in applied_ever if j.status in ("shortlisted", "interview", "offer")
                   or any(h.get("status") == "shortlisted" for h in j.history)]
    interviews = [j for j in applied_ever if j.status in ("interview", "offer")
                  or any(h.get("status") == "interview" for h in j.history)]

    days = [(now - timedelta(days=i)).date().isoformat() for i in range(89, -1, -1)]
    return {
        "daily": [{"date": d, **daily.get(d, {})} for d in days],
        "skill_gap": [{"skill": s, "pct": round(100 * n / n_good), "count": n} for s, n in gap.most_common(10)],
        "strengths": [{"skill": s, "pct": round(100 * n / n_good)} for s, n in have.most_common(8)],
        "momentum": momentum,
        "regions": dict(Counter(j.region for j in week)),
        "sources": dict(Counter(j.source for j in week).most_common(12)),
        "statuses": dict(statuses),
        "funnel": {"tracked": len(jobs), "applied": len(applied_ever), "responded": len(responded),
                   "shortlisted": len(shortlisted), "interviews": len(interviews), "offers": statuses.get("offer", 0)},
        "match_quality": {
            "strong": sum(1 for j in jobs if j.status == "new" and (j.score or 0) >= 8),
            "good": sum(1 for j in jobs if j.status == "new" and 6 <= (j.score or 0) < 8),
            "weak": sum(1 for j in jobs if j.status == "new" and (j.score or 0) < 6),
        },
        "week": {"new": len(week), "good": len(good_week), "prev_new": len(prev),
                 "india": sum(j.region == "india" for j in week), "remote": sum(j.region == "remote" for j in week),
                 "abroad": sum(j.region == "abroad" for j in week)},
    }


def build_payload(jobs: list[Job], meta: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    chosen = select_jobs(jobs)
    picks = apply_next(chosen, n=20)
    due = follow_ups(jobs)
    base = load_base()
    gmail = meta.get("gmail") or {}
    return {
        "v": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "last_run": {k: v for k, v in (meta.get("last_run") or {}).items() if not isinstance(v, (list, dict))},
        "schedule": {"every_hours": 4, "minute_utc": 17},  # mirrors the cron in .github/workflows/scan.yml
        "budget": {"used": used_minutes(load_usage()), "cap": DEFAULT_LIMIT},
        "runs": (meta.get("runs") or [])[-56:],
        # Full posting snippet for jobs you acted on and the top open ones; a short preview for the long tail.
        "jobs": [compact(j, base, SNIPPET if j.status != "new" or i < FULL_SNIPPETS else SHORT_SNIPPET)
                 for i, j in enumerate(chosen)],
        "resume": public_base(base),
        "videos": (meta.get("videos") or [])[:100], "videos_at": meta.get("videos_at"),
        "gmail": {"last_sync": gmail.get("last_sync"), "stats": gmail.get("last_stats") or {},
                  "review": [{**r, "key": review_key(r)} for r in (gmail.get("review") or [])[-12:]]},
        "resume_import": meta.get("resume_import"),
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
    for name in ENCRYPTED & files | {"data/jobs.enc.json"}:
        blob = json.loads((dist / name).read_text(encoding="utf-8"))
        if set(blob) != {"v", "salt", "iv", "iters", "ct"}:
            raise RuntimeError(f"{name} is not in the encrypted format")
    for name in files - FONTS - VENDOR - ENCRYPTED:
        text = (dist / name).read_text(encoding="utf-8", errors="ignore")
        if re.search(r"[A-Za-z0-9._%+-]+@gmail\.com|\+91[- ]?\d{10}", text):
            raise RuntimeError(f"{name} contains contact details")
        for line in _forbidden_strings():
            if line in text:
                raise RuntimeError(f"{name} contains resume text")


def stamp_build(dist: Path) -> str:
    """Version the page's own assets (app.js?v=<hash>). GitHub Pages lets browsers reuse files for 10 minutes, so
    without this a new index.html could run with a cached old app.js and break. Returns the build id."""
    code = b"".join((dist / n).read_bytes() for n in ("app.js", "app.css", "crypto.js", "map.js", "sw.js"))
    build = hashlib.sha1(code).hexdigest()[:10]
    page = (dist / "index.html").read_text(encoding="utf-8")
    for name in ("app.css", "crypto.js", "app.js"):
        page = page.replace(f'"{name}"', f'"{name}?v={build}"')
    page = page.replace("<title>", f'<meta name="jr-build" content="{build}">\n<title>', 1)
    (dist / "index.html").write_text(page, encoding="utf-8")
    return build


def export(jobs: list[Job], meta: dict[str, Any], passphrase: str, dist: Path = DIST) -> Path:
    payload = build_payload(jobs, meta)
    if dist.exists():
        shutil.rmtree(dist)
    for sub in ("data", "fonts", "vendor"):
        (dist / sub).mkdir(parents=True)
    for name in PUBLIC_FILES - ENCRYPTED:
        shutil.copy2(DASHBOARD_SRC / name, dist / name)
    stamp_build(dist)
    out = dist / "data" / "jobs.enc.json"
    out.write_text(json.dumps(encrypt_json(payload, passphrase)), encoding="utf-8")
    if WORLD_FILE.exists():  # employers by country for the Map tab, encrypted like everything else
        world = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
        (dist / "data" / "world.enc.json").write_text(json.dumps(encrypt_json(world, passphrase)), encoding="utf-8")
    assert_publishable(dist)
    return out
