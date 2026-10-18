"""Company career feeds straight from their applicant tracking systems (public, no login).

Endpoints (verified 2026-10):
  Greenhouse      GET  boards-api.greenhouse.io/v1/boards/<slug>/jobs            (+ /jobs/<id> for content)
  Lever           GET  api.lever.co/v0/postings/<slug>?mode=json
  Ashby           GET  api.ashbyhq.com/posting-api/job-board/<slug>
  SmartRecruiters GET  api.smartrecruiters.com/v1/companies/<slug>/postings?q=   (+ /postings/<id>)
  Workday         POST <host>/wday/cxs/<tenant>/<site>/jobs                       (+ GET <host>/wday/cxs/.../job/...)
"""
from __future__ import annotations

import html
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from bs4 import BeautifulSoup

from .. import net
from ..config import ROOT
from ..models import Job
from .base import FetchContext, register

log = logging.getLogger(__name__)

COMPANIES_FILE = ROOT / "companies.yaml"
MAX_COMPANY_WORKERS = 8
DEFAULT_TERMS = ["devops", "site reliability", "sre", "platform engineer", "cloud engineer",
                 "kubernetes", "infrastructure", "mlops"]


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(html.unescape(raw), "html.parser").get_text("\n")
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _iso_ms(ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _iso(value: Any) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _too_old(posted: str | None, ctx: FetchContext) -> bool:
    if not posted:
        return False
    dt = datetime.fromisoformat(posted)
    return dt < datetime.now(timezone.utc) - timedelta(days=ctx.prefs.max_age_days)


def load_companies(path: Path = COMPANIES_FILE) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [c for c in (data.get("companies") or []) if c.get("enabled", True)]


# --------------------------------------------------------------------------- Greenhouse
def greenhouse(c: dict[str, Any], ctx: FetchContext) -> list[Job]:
    slug = c["slug"]
    base = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    jobs: list[Job] = []
    for item in net.get_json(base).get("jobs", []):
        title = (item.get("title") or "").strip()
        posted = _iso(item.get("first_published") or item.get("updated_at"))
        if not ctx.wants(title) or _too_old(posted, ctx) or ctx.is_known(item.get("absolute_url", "")):
            continue
        if not ctx.location_ok(item.get("location", {}).get("name", "")) or ctx.time_left() <= 0:
            continue
        detail = net.get_json(f"{base}/{item['id']}")
        locs = [item.get("location", {}).get("name", "")]
        locs += [o.get("location") or o.get("name") or "" for o in detail.get("offices") or []]
        jobs.append(Job(
            title=title, company=c.get("name") or item.get("company_name", slug),
            location=" / ".join(dict.fromkeys(x for x in locs if x)), url=item.get("absolute_url", ""),
            source="greenhouse", description=html_to_text(detail.get("content", "")), posted_at=posted,
            expires_at=_iso(item.get("application_deadline")),
        ))
    return jobs


# --------------------------------------------------------------------------- Lever
def lever(c: dict[str, Any], ctx: FetchContext) -> list[Job]:
    slug = c["slug"]
    data = net.get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs: list[Job] = []
    for item in data if isinstance(data, list) else []:
        title = (item.get("text") or "").strip()
        posted = _iso_ms(item.get("createdAt"))
        if not ctx.wants(title) or _too_old(posted, ctx):
            continue
        cats = item.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location", "")]
        parts = [item.get("descriptionPlain", "")]
        for block in item.get("lists") or []:
            parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content', ''))}")
        parts.append(item.get("additionalPlain", ""))
        jobs.append(Job(
            title=title, company=c.get("name") or slug, location=" / ".join(x for x in locs if x),
            url=item.get("hostedUrl", ""), source="lever", description="\n\n".join(p for p in parts if p),
            remote=(item.get("workplaceType") == "remote"), posted_at=posted,
        ))
    return jobs


# --------------------------------------------------------------------------- Ashby
def ashby(c: dict[str, Any], ctx: FetchContext) -> list[Job]:
    slug = c["slug"]
    data = net.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    jobs: list[Job] = []
    for item in data.get("jobs") or []:
        title = (item.get("title") or "").strip()
        posted = _iso(item.get("publishedAt"))
        if not item.get("isListed", True) or not ctx.wants(title) or _too_old(posted, ctx):
            continue
        locs = [item.get("location", "")] + [s.get("location", "") for s in item.get("secondaryLocations") or []]
        comp = (item.get("compensation") or {}).get("compensationTierSummary") or ""
        jobs.append(Job(
            title=title, company=c.get("name") or slug, location=" / ".join(dict.fromkeys(x for x in locs if x)),
            url=item.get("jobUrl", ""), source="ashby", description=item.get("descriptionPlain", ""),
            remote=bool(item.get("isRemote")), posted_at=posted, salary=comp,
        ))
    return jobs


# --------------------------------------------------------------------------- SmartRecruiters
def smartrecruiters(c: dict[str, Any], ctx: FetchContext) -> list[Job]:
    slug = c["slug"]
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    seen: set[str] = set()
    jobs: list[Job] = []
    for term in c.get("search_terms") or ctx.prefs.source_cfg("ats").get("search_terms") or DEFAULT_TERMS:
        for item in net.get_json(base, params={"q": term, "limit": 100}).get("content") or []:
            pid = str(item.get("id"))
            title = (item.get("name") or "").strip()
            posted = _iso(item.get("releasedDate"))
            public_url = f"https://jobs.smartrecruiters.com/{slug}/{pid}"
            if pid in seen or not ctx.wants(title) or _too_old(posted, ctx) or ctx.is_known(public_url):
                continue
            loc_raw = item.get("location") or {}
            if not ctx.location_ok(loc_raw.get("fullLocation") or loc_raw.get("country", "")) or ctx.time_left() <= 0:
                continue
            seen.add(pid)
            detail = net.get_json(f"{base}/{pid}")
            sections = (detail.get("jobAd") or {}).get("sections") or {}
            desc = "\n\n".join(html_to_text((sections.get(k) or {}).get("text", ""))
                               for k in ("jobDescription", "qualifications", "additionalInformation"))
            loc = item.get("location") or {}
            location = ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)
            jobs.append(Job(
                title=title, company=c.get("name") or slug, location=location,
                url=detail.get("postingUrl") or public_url,
                source="smartrecruiters", description=desc, remote=bool(loc.get("remote")), posted_at=posted,
            ))
    return jobs


# --------------------------------------------------------------------------- Workday
_WD_POSTED = re.compile(r"posted\s+(today|yesterday|(\d+)(\+?)\s+days?\s+ago)", re.I)
WORKDAY_QUERY = 'devops OR sre OR "site reliability" OR kubernetes OR platform OR cloud OR infrastructure OR mlops'
WORKDAY_MAX_PAGES = 6


def workday_posted_days(text: str) -> int | None:
    m = _WD_POSTED.search(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    if word in ("today", "yesterday"):
        return 0 if word == "today" else 1
    return int(m.group(2)) + (1 if m.group(3) else 0)  # "30+ days ago" means older than 30


def parse_workday_url(url: str) -> tuple[str, str, str]:
    """https://mastercard.wd1.myworkdayjobs.com/en-US/CorporateCareers -> (host, tenant, site)."""
    parts = urlsplit(url)
    path = [p for p in parts.path.split("/") if p and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
    return parts.netloc, parts.netloc.split(".")[0], path[0]


def workday(c: dict[str, Any], ctx: FetchContext) -> list[Job]:
    """One OR-query per tenant; results are newest first, so stop paging past the look-back window."""
    host, tenant, site = parse_workday_url(c["url"])
    api = f"https://{host}/wday/cxs/{tenant}/{site}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    lookback_days = max(1, ctx.hours_old(ctx.prefs.source_cfg("workday")) // 24)
    jobs: list[Job] = []
    for page in range(WORKDAY_MAX_PAGES):
        body = {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": c.get("query") or WORKDAY_QUERY}
        resp = net.post(f"{api}/jobs", json=body, headers=headers)
        resp.raise_for_status()
        postings = resp.json().get("jobPostings") or []
        oldest = 0
        for item in postings:
            path, title = item.get("externalPath", ""), (item.get("title") or "").strip()
            days = workday_posted_days(item.get("postedOn", ""))
            oldest = max(oldest, days or 0)
            url = f"https://{host}/{site}{path}"
            if (days is not None and days > lookback_days) or not ctx.wants(title) or ctx.is_known(url):
                continue
            if not ctx.location_ok(item.get("locationsText", "")) or ctx.time_left() <= 0:
                continue
            info = net.get_json(f"{api}{path}", headers=headers).get("jobPostingInfo") or {}
            posted = _iso(info.get("startDate"))
            if _too_old(posted, ctx):
                continue
            country = (info.get("country") or {}).get("descriptor", "")
            place = info.get("location") or item.get("locationsText") or ""
            # "Pune, India" + "India" must not become "Pune, India, India".
            location = place if not country or country.split()[0].lower() in place.lower() else f"{place}, {country}".strip(", ")
            jobs.append(Job(
                title=title, company=c.get("name") or tenant, location=location,
                url=info.get("externalUrl") or url, alt_urls=[url] if info.get("externalUrl") not in (None, url) else [],
                source="workday", description=html_to_text(info.get("jobDescription", "")), posted_at=posted,
                expires_at=_iso(info.get("endDate")),
                remote="remote" in (info.get("remoteType") or location).lower(),
            ))
        if len(postings) < 20 or oldest > lookback_days:
            break
    return jobs


FETCHERS: dict[str, Callable[[dict[str, Any], FetchContext], list[Job]]] = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "smartrecruiters": smartrecruiters, "workday": workday,
}


def _fetch_ats(ats: str, ctx: FetchContext) -> list[Job]:
    companies = [c for c in load_companies() if c.get("ats") == ats]
    if not companies:
        return []
    errors: list[str] = []

    def one(c: dict[str, Any]) -> list[Job]:
        if ctx.time_left() <= 0:
            return []
        try:
            found = FETCHERS[ats](c, ctx)
            if c.get("yc"):  # Y Combinator company (batch name), shown as a badge on the dashboard
                for job in found:
                    job.tags.append("yc")
            return found
        except Exception as exc:  # noqa: BLE001 - one broken board must not sink the rest
            errors.append(f"{c.get('name')}: {type(exc).__name__}")
            return []

    jobs: list[Job] = []
    with ThreadPoolExecutor(max_workers=MAX_COMPANY_WORKERS) as pool:
        for batch in pool.map(one, companies):
            jobs.extend(batch)
    log.info("%s: %d title-matched jobs from %d companies%s", ats, len(jobs), len(companies),
             f" ({len(errors)} failed: {', '.join(errors[:5])})" if errors else "")
    return jobs


for _ats in FETCHERS:
    register(_ats)(lambda ctx, _ats=_ats: _fetch_ats(_ats, ctx))

