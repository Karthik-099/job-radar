""""Apply next" ranking: which jobs are worth your time today, and why.

apply_priority (0-100) blends:
  fit        35  resume match (LLM score once Phase 3 runs, keyword score until then)
  location   20  Bangalore > Pune/Hyderabad > other India > remote > abroad with visa > abroad
  level      15  stated minimum years vs your ~1 year
  freshness  20  applying in the first 24-72 hours matters most
  company    10  target company, or a direct company career-site listing
Feasibility penalties: abroad without any sponsorship signal, title-only listings.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import Preferences
from .match import keyword_fit
from .models import Job

DIRECT_SOURCES = {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}


def _location_part(job: Job, prefs: Preferences) -> tuple[float, str]:
    low = job.location.lower()
    if job.region == "india":
        hits = [(boost, city) for city, boost in prefs.city_boost.items() if city in low]
        if job.remote and "remote" in prefs.city_boost:
            hits.append((prefs.city_boost["remote"], "remote"))
        if hits:
            boost, city = max(hits)
            return min(1.0, 0.75 + boost), ("India remote" if city == "remote" else city.title())
        return 0.75, "India"
    if job.region == "remote":
        return 0.7, "Remote, open to India"
    if job.region == "abroad":
        if job.visa_sponsorship:
            return 0.6, f"{job.country.title()} with visa/relocation support"
        if job.country in prefs.abroad_countries:
            return 0.35, f"{job.country.title()} (sponsorship not mentioned)"
        return 0.2, job.country.title() or "Abroad"
    return 0.4, "Location unclear"


def _level_part(min_years: int | None) -> tuple[float, str]:
    if min_years is None:
        return 0.7, ""
    if min_years <= 1:
        return 1.0, f"asks {min_years}+ yrs"
    if min_years == 2:
        return 0.85, "asks 2+ yrs"
    if min_years == 3:
        return 0.5, "asks 3+ yrs (stretch)"
    return 0.1, f"asks {min_years}+ yrs"


def age_label(job: Job, now: datetime | None = None) -> str:
    """'posted 5h ago' when the source gives a time, else 'posted today' / 'posted 3d ago'."""
    posted = job.posted_datetime()
    if not posted:
        return ""
    now = now or datetime.now(timezone.utc)
    hours = max(0.0, (now - posted).total_seconds() / 3600)
    has_time = (posted.hour, posted.minute, posted.second) != (0, 0, 0)
    if has_time and hours < 48:
        return f"posted {max(1, int(hours))}h ago"
    days = int(hours // 24)
    return "posted today" if days == 0 else f"posted {days}d ago"


def _fresh_part(job: Job) -> tuple[float, str]:
    posted = job.posted_datetime()
    if not posted:
        return 0.5, ""
    days = (datetime.now(timezone.utc) - posted).total_seconds() / 86400
    if days <= 1:
        value = 1.0
    elif days <= 3:
        value = 0.85
    else:
        value = max(0.0, 1 - days / 30)
    return value, age_label(job)


def _closes_in_days(job: Job) -> int | None:
    if not job.expires_at:
        return None
    try:
        end = datetime.fromisoformat(job.expires_at)
    except ValueError:
        return None
    return max(0, int((end - datetime.now(timezone.utc)).total_seconds() // 86400))


def rank(job: Job, prefs: Preferences) -> Job:
    if not job.scoring:
        job.scoring = keyword_fit(job.title, job.description, job.min_years)
    if job.score is None:
        job.score = job.scoring.get("score")
    fit = (job.score or 0) / 10
    loc, loc_why = _location_part(job, prefs)
    lvl, lvl_why = _level_part(job.min_years)
    fresh, fresh_why = _fresh_part(job)
    company = 1.0 if "target_company" in job.tags else 0.6 if job.source in DIRECT_SOURCES else 0.3

    value = 35 * fit + 20 * loc + 15 * lvl + 20 * fresh + 10 * company
    closes_in = _closes_in_days(job)
    if closes_in is not None and closes_in <= 3:
        value += 5  # apply before it closes
    if job.region == "abroad" and not job.visa_sponsorship and job.country not in prefs.abroad_countries:
        value -= 15
    if not job.description.strip():
        value -= 5
    if "reposted" in job.flags:
        value -= 8  # evergreen / ghost listing
    if "closed" in job.flags:
        value = 0
    job.apply_priority = round(max(0.0, min(100.0, value)), 1)

    matched = job.scoring.get("matched_skills") or []
    reasons = [f"fit {job.score}/10" + (f" ({', '.join(matched[:4])})" if matched else ""), loc_why]
    reasons += [r for r in (lvl_why, fresh_why) if r]
    if closes_in is not None:
        reasons.append("closes today" if closes_in == 0 else f"closes in {closes_in}d")
    if "reposted" in job.flags:
        reasons.append("re-posted often (possible ghost job)")
    if "closed" in job.flags:
        reasons.append("posting closed")
    if "target_company" in job.tags:
        reasons.append("target company")
    elif job.source in DIRECT_SOURCES:
        reasons.append("direct company listing")
    job.reasons = reasons
    if job.region == "abroad" and job.visa_sponsorship is None and "no_visa_info" not in job.flags:
        job.flags.append("no_visa_info")
    return job


def apply_next(jobs: list[Job], n: int = 15) -> list[Job]:
    """Best open jobs you haven't acted on, at most 3 per company so one employer can't flood the list."""
    per_company: dict[str, int] = {}
    picks: list[Job] = []
    for job in sorted((j for j in jobs if j.status == "new"), key=lambda j: j.apply_priority or 0, reverse=True):
        key = job.company.lower()
        if per_company.get(key, 0) >= 3:
            continue
        per_company[key] = per_company.get(key, 0) + 1
        picks.append(job)
        if len(picks) == n:
            break
    return picks
