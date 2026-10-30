"""Job boards via python-jobspy: Indeed, Naukri, Glassdoor (India) and Bayt (Gulf); Google and LinkedIn best effort."""
from __future__ import annotations

import logging
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

from ..geo import COUNTRY_ALIASES, detect_country
from ..models import Job
from .base import FetchContext, register

log = logging.getLogger(__name__)

# Queries per site run a few at a time with jitter; boards rate-limit bursts.
MAX_PARALLEL_QUERIES = 3


def _s(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def _posted(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat(timespec="seconds")
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None


def _salary(row: dict[str, Any]) -> str:
    lo, hi = row.get("min_amount"), row.get("max_amount")
    lo_s, hi_s = _s(lo), _s(hi)
    if not lo_s and not hi_s:
        return ""
    fmt = lambda v: f"{float(v):,.0f}" if v else "?"  # noqa: E731
    return f"{_s(row.get('currency'))} {fmt(lo_s)}-{fmt(hi_s)} / {_s(row.get('interval')) or 'yearly'}".strip()


def row_to_job(row: dict[str, Any], site: str) -> Job:
    direct = _s(row.get("job_url_direct"))
    board = _s(row.get("job_url"))
    remote_raw = row.get("is_remote")
    return Job(
        title=_s(row.get("title")),
        company=_s(row.get("company")),
        location=_s(row.get("location")),
        url=direct or board,
        alt_urls=[board] if direct and board and board != direct else [],
        source=site,
        description=_s(row.get("description")),
        remote=bool(remote_raw) if isinstance(remote_raw, bool) else False,
        posted_at=_posted(row.get("date_posted")),
        salary=_salary(row),
        experience_text=_s(row.get("experience_range")),
    )


_IMPORT_LOCK = threading.Lock()


def load_jobspy() -> Any:
    """Import jobspy exactly once. Concurrent first imports from worker threads deadlock on module locks."""
    with _IMPORT_LOCK:
        from jobspy import scrape_jobs
    return scrape_jobs


def _query(site: str, term: str, loc: dict[str, Any], cfg: dict[str, Any], ctx: FetchContext) -> list[Job]:
    scrape_jobs = load_jobspy()

    if ctx.time_left() <= 0:
        return []
    hours_old = ctx.hours_old(cfg)

    time.sleep(random.uniform(0.5, 2.0))  # polite jitter
    kwargs: dict[str, Any] = dict(
        site_name=[site],
        search_term=term,
        location=loc.get("location"),
        results_wanted=int(cfg.get("results_wanted", 30)),
        hours_old=hours_old,
        description_format="markdown",
        verbose=0,
    )
    if site in ("indeed", "glassdoor"):
        kwargs["country_indeed"] = loc.get("country", "india")
        if not kwargs["location"]:
            kwargs.pop("location")  # whole country
    if site == "naukri" and loc.get("remote"):
        kwargs["is_remote"] = True
    if site == "google":
        kwargs["google_search_term"] = f"{term} jobs near {loc.get('location')} since {max(1, hours_old // 24)} days ago"
    # JobSpy >= 1.4 only opens each job page (for the description) when asked; LinkedIn's pages throttle fast.
    kwargs["fetch_description"] = bool(cfg.get("fetch_description", site != "linkedin"))

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            df = scrape_jobs(**kwargs)
            return [row_to_job(r, site) for r in df.to_dict("records")]
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(2 + attempt * 3)
    log.info("%s query %r @ %s failed: %s", site, term, loc.get("location"), last_exc)
    return []


def _fetch_site(site: str, ctx: FetchContext) -> list[Job]:
    cfg = ctx.prefs.source_cfg(site)
    terms: list[str] = cfg.get("search_terms") or []
    locations: list[dict[str, Any]] = cfg.get("locations") or [{"location": "India"}]
    queries = [(t, loc) for t in terms for loc in locations]
    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_QUERIES) as pool:
        for batch in pool.map(lambda q: _query(site, q[0], q[1], cfg, ctx), queries):
            jobs.extend(batch)
    log.info("%s: %d raw jobs from %d queries", site, len(jobs), len(queries))
    return jobs


for _site in ("indeed", "naukri", "google", "linkedin", "glassdoor", "bayt"):
    register(_site)(lambda ctx, _site=_site: _fetch_site(_site, ctx))


# --------------------------------------------------------------------------- worldwide rotation
# Indeed has a site per country (72 of them; see jobspy.model.Country). Scanning all of them every run
# would blow the time budget, so each run takes the next slice. "every_run" countries are always included.
def indeed_countries() -> list[str]:
    from jobspy.model import Country

    skip = {"INDIA", "US_CANADA", "WORLDWIDE"}  # India is covered by the main indeed source
    return [c.value[0].split(",")[0] for c in Country if c.name not in skip]


def rotation_slice(items: list[str], per_run: int, slot: int) -> list[str]:
    if not items or per_run <= 0:
        return []
    per_run = min(per_run, len(items))
    start = (slot * per_run) % len(items)
    return [items[(start + i) % len(items)] for i in range(per_run)]


def world_countries(ctx: FetchContext, cfg: dict[str, Any], all_countries: list[str]) -> list[str]:
    if ctx.countries:
        return ctx.countries
    every = [c.lower() for c in cfg.get("every_run") or []]
    rest = [c for c in all_countries if c not in every]
    slot = int(time.time() // (float(cfg.get("rotation_hours", 6)) * 3600))
    return list(dict.fromkeys(every + rotation_slice(rest, int(cfg.get("rotate_per_run", 8)), slot)))


@register("indeed_world")
def indeed_world(ctx: FetchContext) -> list[Job]:
    cfg = ctx.prefs.source_cfg("indeed_world")
    countries = world_countries(ctx, cfg, indeed_countries())
    terms: list[str] = cfg.get("search_terms") or ["devops engineer", "site reliability engineer"]
    queries = [(t, {"country": c}) for c in countries for t in terms]
    jobs: list[Job] = []

    def run(q: tuple[str, dict[str, Any]]) -> list[Job]:
        found = _query("indeed", q[0], q[1], cfg, ctx)
        country = COUNTRY_ALIASES.get(q[1]["country"], q[1]["country"])
        for job in found:  # Indeed abroad often gives "Berlin" or "Remote"; pin the country we searched
            if detect_country(job.location) != country:
                job.location = f"{job.location}, {country}" if job.location else country
        return found

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_QUERIES) as pool:
        for batch in pool.map(run, queries):
            jobs.extend(batch)
    for job in jobs:
        job.source = "indeed_world"
    log.info("indeed_world: %d raw jobs from %s", len(jobs), ", ".join(countries))
    return jobs


@register("linkedin_world")
def linkedin_world(ctx: FetchContext) -> list[Job]:
    """LinkedIn guest search accepts any country name, so this reaches every country, a few per run."""
    cfg = ctx.prefs.source_cfg("linkedin_world")
    countries = world_countries(ctx, cfg, [c.lower() for c in cfg.get("countries") or []])
    terms: list[str] = cfg.get("search_terms") or ["devops engineer"]
    queries = [(t, {"location": c.title()}) for c in countries for t in terms]
    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=2) as pool:  # LinkedIn throttles quickly
        for batch in pool.map(lambda q: _query("linkedin", q[0], q[1], cfg, ctx), queries):
            jobs.extend(batch)
    for job in jobs:
        job.source = "linkedin_world"
    log.info("linkedin_world: %d raw jobs from %s", len(jobs), ", ".join(countries))
    return jobs
