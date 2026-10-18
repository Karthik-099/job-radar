"""Big cloud employers with their own (public, no login) job search APIs.

  amazon     GET  www.amazon.jobs/en/search.json            (Amazon + AWS, India and remote)
  microsoft  GET  apply.careers.microsoft.com/api/pcsx/search  (+ /position_details for the JD)

Google, Oracle and Tesla have no stable public API; their roles still arrive through Indeed and LinkedIn.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .. import net
from ..models import Job
from .ats import _too_old, html_to_text
from .base import FetchContext, register

log = logging.getLogger(__name__)

AMAZON_TERMS = ["devops", "site reliability", "systems development engineer", "cloud support engineer",
                "infrastructure engineer", "kubernetes", "cloud engineer", "mlops"]
MICROSOFT_TERMS = ["site reliability engineer devops", "azure infrastructure kubernetes", "cloud support engineer"]
MICROSOFT_PAGES = 5  # 10 results per page; its search is semantic, so matching titles sit a few pages in
MS_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
              "Accept": "application/json"}


def _amazon_date(value: str) -> str | None:
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except (AttributeError, ValueError):
        return None


@register("amazon")
def amazon(ctx: FetchContext) -> list[Job]:
    cfg = ctx.prefs.source_cfg("amazon")
    seen: set[str] = set()
    jobs: list[Job] = []
    for term in cfg.get("search_terms") or AMAZON_TERMS:
        if ctx.time_left() <= 0:
            break
        data = net.get_json("https://www.amazon.jobs/en/search.json", params={
            "base_query": term, "normalized_country_code[]": "IND", "result_limit": 50, "sort": "recent"})
        for item in data.get("jobs") or []:
            jid, title = str(item.get("id_icims") or item.get("id")), (item.get("title") or "").strip()
            url = f"https://www.amazon.jobs{item.get('job_path', '')}"
            posted = _amazon_date(item.get("posted_date", ""))
            if jid in seen or not ctx.wants(title) or ctx.is_known(url) or _too_old(posted, ctx):
                continue
            seen.add(jid)
            desc = "\n\n".join(html_to_text(item.get(k, "")) for k in ("description", "basic_qualifications", "preferred_qualifications"))
            location = ", ".join(x for x in (item.get("city"), item.get("state"), "India") if x)
            jobs.append(Job(title=title, company=item.get("company_name") or "Amazon", location=location, url=url,
                            source="amazon", description=desc, posted_at=posted, ref=jid))
    return jobs


@register("microsoft")
def microsoft(ctx: FetchContext) -> list[Job]:
    cfg = ctx.prefs.source_cfg("microsoft")
    seen: set[str] = set()
    jobs: list[Job] = []
    for term in cfg.get("search_terms") or MICROSOFT_TERMS:
        for page in range(MICROSOFT_PAGES):
            if ctx.time_left() <= 0:
                return jobs
            data = net.get_json("https://apply.careers.microsoft.com/api/pcsx/search", headers=MS_HEADERS, params={
                "domain": "microsoft.com", "query": term, "location": "India", "start": page * 10, "sort_by": "relevance"})
            positions = (data.get("data") or {}).get("positions") or []
            for item in positions:
                job = _microsoft_job(item, seen, ctx)
                if job:
                    jobs.append(job)
            if len(positions) < 10:
                break
    return jobs


def _microsoft_job(item: dict[str, Any], seen: set[str], ctx: FetchContext) -> Job | None:
    pid, title = str(item.get("id")), (item.get("name") or "").strip()
    url = f"https://apply.careers.microsoft.com/careers/job/{pid}"
    posted = (datetime.fromtimestamp(int(item["postedTs"]), tz=timezone.utc).isoformat(timespec="seconds")
              if item.get("postedTs") else None)
    if pid in seen or not ctx.wants(title) or ctx.is_known(url) or _too_old(posted, ctx) or ctx.time_left() <= 0:
        return None
    seen.add(pid)
    detail: dict[str, Any] = net.get_json("https://apply.careers.microsoft.com/api/pcsx/position_details", headers=MS_HEADERS,
                                          params={"position_id": pid, "domain": "microsoft.com", "hl": "en"}).get("data") or {}
    return Job(title=title, company="Microsoft", location=" / ".join(item.get("locations") or []) or "India",
               url=url, source="microsoft", description=html_to_text(detail.get("jobDescription", "")),
               posted_at=posted, remote=item.get("workLocationOption") == "remote", ref=str(item.get("displayJobId") or ""))
