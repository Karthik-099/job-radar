"""Grow companies.yaml automatically from the links job boards hand us.

When Indeed/LinkedIn/etc. point at a company's own ATS page (Greenhouse, Lever, Ashby, SmartRecruiters,
Workday, Workable, Recruitee, BambooHR, Teamtailor), that company is hiring for our titles, so we start polling
its career site directly. Direct polling catches new roles earlier and with full descriptions.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from .models import Job
from .sources.ats import COMPANIES_FILE

log = logging.getLogger(__name__)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"//([A-Za-z0-9-]+)\.recruitee\.com", re.I)),
    ("bamboohr", re.compile(r"//([A-Za-z0-9-]+)\.bamboohr\.com/(?:careers|jobs)", re.I)),
    ("teamtailor", re.compile(r"//([A-Za-z0-9-]+)\.teamtailor\.com", re.I)),
    ("workday", re.compile(r"(https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I)),
]
_NOT_SLUGS = {"embed", "jobs", "job", "v1", "boards", "careers", "search", "j", "api", "www", "app", "career"}


def ats_from_url(url: str) -> dict[str, str] | None:
    for ats, rx in _PATTERNS:
        m = rx.search(url or "")
        if not m:
            continue
        if ats == "workday":
            site = m.group(2)
            if site.lower() in ("job", "wday"):
                return None
            return {"ats": "workday", "url": f"{m.group(1).lower()}/{site}"}
        slug = m.group(1)
        if slug.lower() in _NOT_SLUGS:
            return None
        return {"ats": ats, "slug": slug.lower() if ats != "smartrecruiters" else slug}
    return None


def _key(entry: dict[str, Any]) -> tuple[str, str]:
    return entry["ats"], (entry.get("slug") or entry.get("url") or "").lower()


def learn_companies(jobs: list[Job], dry_run: bool = False) -> list[dict[str, Any]]:
    """Add companies found in job URLs to companies.yaml. Returns the new entries."""
    raw = COMPANIES_FILE.read_text(encoding="utf-8") if COMPANIES_FILE.exists() else ""
    header = "".join(line + "\n" for line in raw.splitlines() if line.startswith("#"))
    data = yaml.safe_load(raw) or {}
    companies: list[dict[str, Any]] = data.get("companies") or []
    known = {_key(c) for c in companies}
    added: list[dict[str, Any]] = []
    for job in jobs:
        for url in [job.url, *job.alt_urls]:
            hit = ats_from_url(url)
            if not hit or _key(hit) in known or not job.company:
                continue
            entry = {"name": job.company, **hit, "learned_from": job.source}
            known.add(_key(hit))
            added.append(entry)
    if added and not dry_run:
        companies.extend(added)
        companies.sort(key=lambda c: c["name"].lower())
        data["companies"] = companies
        COMPANIES_FILE.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120),
                                  encoding="utf-8")
    if added:
        log.info("learned %d new company career sites: %s", len(added), ", ".join(e["name"] for e in added[:10]))
    return added
