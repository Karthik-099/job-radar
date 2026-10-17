"""Free public job board APIs and feeds. Each returns title-matched jobs only.

All of these ask for attribution: the dashboard links every job to its original URL.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .. import net
from ..envsecrets import secret
from ..models import Job
from .ats import _iso, html_to_text
from .base import FetchContext, register

log = logging.getLogger(__name__)


def _epoch(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _terms(ctx: FetchContext, name: str, default: list[str]) -> list[str]:
    return ctx.prefs.source_cfg(name).get("search_terms") or default


@register("remotive")
def remotive(ctx: FetchContext) -> list[Job]:
    jobs: dict[str, Job] = {}
    for params in ({"category": "devops"}, *({"search": t} for t in _terms(ctx, "remotive", ["kubernetes", "sre"]))):
        for item in net.get_json("https://remotive.com/api/remote-jobs", params=params).get("jobs", []):
            if not ctx.wants(item.get("title", "")):
                continue
            jobs[item["url"]] = Job(
                title=item["title"], company=item.get("company_name", ""),
                location="Remote" + (f" ({item['candidate_required_location']})" if item.get("candidate_required_location") else ""),
                url=item["url"], source="remotive", description=html_to_text(item.get("description", "")),
                remote=True, posted_at=_iso(item.get("publication_date")), salary=item.get("salary", ""),
            )
    return list(jobs.values())


@register("remoteok")
def remoteok(ctx: FetchContext) -> list[Job]:
    jobs = []
    for item in net.get_json("https://remoteok.com/api"):
        if not isinstance(item, dict) or "position" not in item or not ctx.wants(item["position"]):
            continue
        lo, hi = item.get("salary_min"), item.get("salary_max")
        jobs.append(Job(
            title=item["position"], company=item.get("company", ""),
            location="Remote" + (f" ({item['location']})" if item.get("location") else ""),
            url=item.get("url", ""), source="remoteok", description=html_to_text(item.get("description", "")),
            remote=True, posted_at=_iso(item.get("date")), salary=f"USD {lo}-{hi}" if lo and hi else "",
        ))
    return jobs


WWR_FEEDS = ("remote-devops-sysadmin-jobs", "remote-back-end-programming-jobs")


@register("weworkremotely")
def weworkremotely(ctx: FetchContext) -> list[Job]:
    jobs = []
    for feed in WWR_FEEDS:
        resp = net.get(f"https://weworkremotely.com/categories/{feed}.rss")
        resp.raise_for_status()
        for item in ET.fromstring(resp.content).iter("item"):
            raw_title = item.findtext("title", "")
            company, _, title = raw_title.partition(": ")
            if not title:
                company, title = "", raw_title
            if not ctx.wants(title):
                continue
            pub = item.findtext("pubDate")
            region = item.findtext("region", "") or "Anywhere"
            jobs.append(Job(
                title=title, company=company, location=f"Remote ({region})", url=item.findtext("link", ""),
                source="weworkremotely", description=html_to_text(item.findtext("description", "")),
                remote=True, posted_at=parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat(timespec="seconds") if pub else None,
            ))
    return jobs


@register("himalayas")
def himalayas(ctx: FetchContext) -> list[Job]:
    jobs: dict[str, Job] = {}
    for term in _terms(ctx, "himalayas", ["devops", "site reliability", "platform engineer", "kubernetes", "cloud engineer"]):
        data = net.get_json("https://himalayas.app/jobs/api/search", params={"q": term, "limit": 50})
        for item in data.get("jobs") or []:
            if not ctx.wants(item.get("title", "")):
                continue
            where = ", ".join(item.get("locationRestrictions") or []) or "Worldwide"
            url = item.get("applicationLink") or item.get("guid", "")
            lo, hi = item.get("minSalary"), item.get("maxSalary")
            jobs[url] = Job(
                title=item["title"], company=item.get("companyName", ""), location=f"Remote ({where})",
                url=url, source="himalayas", description=html_to_text(item.get("description", "")),
                remote=True, posted_at=_epoch(item.get("pubDate")), expires_at=_epoch(item.get("expiryDate")),
                salary=f"{item.get('currency') or ''} {lo}-{hi}".strip() if lo and hi else "",
            )
    return list(jobs.values())


@register("jobicy")
def jobicy(ctx: FetchContext) -> list[Job]:
    jobs: dict[str, Job] = {}
    for tag in _terms(ctx, "jobicy", ["devops", "sre", "kubernetes", "cloud"]):
        data = net.get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": tag})
        for item in data.get("jobs") or []:
            if not ctx.wants(item.get("jobTitle", "")):
                continue
            jobs[item["url"]] = Job(
                title=html_to_text(item["jobTitle"]), company=item.get("companyName", ""),
                location=f"Remote ({item.get('jobGeo') or 'Anywhere'})", url=item["url"], source="jobicy",
                description=html_to_text(item.get("jobDescription", "")), remote=True, posted_at=_iso(item.get("pubDate")),
            )
    return list(jobs.values())


@register("arbeitnow")
def arbeitnow(ctx: FetchContext) -> list[Job]:
    """Europe-heavy board; we ask only for postings that offer visa sponsorship."""
    jobs = []
    for page in (1, 2):
        data = net.get_json("https://www.arbeitnow.com/api/job-board-api",
                            params={"visa_sponsorship": "true", "page": page})
        for item in data.get("data") or []:
            if not ctx.wants(item.get("title", "")):
                continue
            jobs.append(Job(
                title=item["title"], company=item.get("company_name", ""),
                location=item.get("location", "") + (", Germany" if "germany" not in item.get("location", "").lower() else ""),
                url=item.get("url", ""), source="arbeitnow",
                description=html_to_text(item.get("description", "")) + "\n\nVisa sponsorship is available.",
                remote=bool(item.get("remote")), posted_at=_epoch(item.get("created_at")),
            ))
        if not (data.get("links") or {}).get("next"):
            break
    return jobs


_HN_HEAD = re.compile(r"^(?P<company>[^|]{2,60})\|(?P<rest>.+)$")


@register("hn")
def hn_who_is_hiring(ctx: FetchContext) -> list[Job]:
    """Latest 'Ask HN: Who is hiring?' thread via Algolia. Comments are free-form; we parse the
    common 'Company | Role | Location | ...' header line and keep the comment as description."""
    stories = net.get_json("https://hn.algolia.com/api/v1/search_by_date",
                           params={"tags": "story,author_whoishiring", "hitsPerPage": 5}).get("hits", [])
    story = next((s for s in stories if "who is hiring" in (s.get("title") or "").lower()), None)
    if not story:
        return []
    sid = story["objectID"]
    jobs: dict[str, Job] = {}
    for term in _terms(ctx, "hn", ["devops", "sre", "site reliability", "platform engineer", "kubernetes", "infrastructure"]):
        hits = net.get_json("https://hn.algolia.com/api/v1/search",
                            params={"tags": f"comment,story_{sid}", "query": term, "hitsPerPage": 100}).get("hits", [])
        for hit in hits:
            text = html_to_text(hit.get("comment_text") or "")
            first = text.split("\n", 1)[0]
            m = _HN_HEAD.match(first)
            if not m:
                continue
            fields = [f.strip() for f in m.group("rest").split("|")]
            title = next((f for f in fields if ctx.wants(f)), "")
            if not title:
                continue
            loc = next((f for f in fields if re.search(r"remote|onsite|hybrid|india|bangalore|[A-Z][a-z]+, [A-Z]", f, re.I)), "")
            url = f"https://news.ycombinator.com/item?id={hit['objectID']}"
            jobs[url] = Job(
                title=title, company=m.group("company").strip(), location=loc, url=url, source="hn",
                description=text, remote="remote" in loc.lower(), posted_at=_iso(hit.get("created_at")),
            )
    return list(jobs.values())


# ----------------------------------------------------------------- keyed (optional, free tiers)
@register("adzuna")
def adzuna(ctx: FetchContext) -> list[Job]:
    app_id, app_key = secret("ADZUNA_APP_ID"), secret("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        log.info("adzuna: ADZUNA_APP_ID/ADZUNA_APP_KEY not set, skipping")
        return []
    cfg = ctx.prefs.source_cfg("adzuna")
    days = max(1, ctx.hours_old(cfg) // 24)
    jobs: dict[str, Job] = {}
    for country, where in [(c.get("country", "in"), c.get("where", "")) for c in cfg.get("locations") or [{"country": "in"}]]:
        for term in cfg.get("search_terms") or ["devops engineer", "site reliability engineer", "platform engineer"]:
            params = {"app_id": app_id, "app_key": app_key, "what": term, "max_days_old": days,
                      "results_per_page": 50, "content-type": "application/json"}
            if where:
                params["where"] = where
            data = net.get_json(f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params)
            for item in data.get("results") or []:
                if not ctx.wants(item.get("title", "")):
                    continue
                lo, hi = item.get("salary_min"), item.get("salary_max")
                jobs[item["redirect_url"]] = Job(
                    title=html_to_text(item["title"]), company=(item.get("company") or {}).get("display_name", ""),
                    location=(item.get("location") or {}).get("display_name", ""), url=item["redirect_url"],
                    source="adzuna", description=html_to_text(item.get("description", "")),
                    posted_at=_iso(item.get("created")), salary=f"{lo:,.0f}-{hi:,.0f}" if lo and hi else "",
                )
    return list(jobs.values())


@register("jooble")
def jooble(ctx: FetchContext) -> list[Job]:
    key = secret("JOOBLE_API_KEY")
    if not key:
        log.info("jooble: JOOBLE_API_KEY not set, skipping")
        return []
    cfg = ctx.prefs.source_cfg("jooble")
    jobs: dict[str, Job] = {}
    for where in cfg.get("locations") or ["Bangalore", "India"]:
        for term in cfg.get("search_terms") or ["devops engineer", "site reliability engineer", "platform engineer"]:
            resp = net.post(f"https://jooble.org/api/{key}", json={"keywords": term, "location": where, "page": 1})
            resp.raise_for_status()
            for item in resp.json().get("jobs") or []:
                if not ctx.wants(item.get("title", "")):
                    continue
                jobs[item["link"]] = Job(
                    title=html_to_text(item["title"]), company=item.get("company", ""), location=item.get("location", ""),
                    url=item["link"], source="jooble", description=html_to_text(item.get("snippet", "")),
                    posted_at=_iso(item.get("updated")), salary=item.get("salary", ""),
                )
    return list(jobs.values())
