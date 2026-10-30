"""Find which ATS a company uses and write it to companies.yaml.

    python -m src.discover                 # probe every name in profile/company_seeds.txt
    python -m src.discover "Grafana Labs"  # probe specific names
    python -m src.discover --ats workable recruitee   # probe only some ATS types

Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, BambooHR and Teamtailor are probed by slug
guesses (the last four only count when the board names the company: their subdomains are first-come, and
BambooHR has many demo tenants). Workday tenants can't be guessed reliably, so add them by hand as
`{name, ats: workday, url: https://<tenant>.wdN.myworkdayjobs.com/<Site>}`.
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yaml

from . import net
from .config import PROFILE_DIR
from .sources.ats import COMPANIES_FILE

SEEDS_FILE = PROFILE_DIR / "company_seeds.txt"
_NOISE = {"inc", "labs", "technologies", "technology", "software", "india", "the", "hq", "io", "ai"}
PROBE_ATS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "bamboohr", "teamtailor")


def slug_candidates(name: str) -> list[str]:
    base = name.lower().strip()
    words = re.findall(r"[a-z0-9]+", base)
    core = [w for w in words if w not in _NOISE]
    cands = ["".join(words), "-".join(words), "".join(core), "-".join(core)]
    if words:
        cands.append(words[0])
    if len(words) > 1:
        cands.append(words[0] + "hq")
        cands.append("".join(words) + "inc")
    return list(dict.fromkeys(c for c in cands if c))


def names_company(name: str, text: str) -> bool:
    """Does the board's company name (or job text) mention this company? Guards against same-slug strangers."""
    flat = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    words = re.findall(r"[a-z0-9]+", name.lower())
    core = [w for w in words if w not in _NOISE]
    # A short core ("dbt" of dbt Labs, "gan" of Gan.ai) matches too many strangers; then the full name must appear.
    return any(len(c) >= minlen and c in flat for c, minlen in (("".join(words), 3), ("".join(core), 5)))


def _count(ats: str, slug: str, name: str = "") -> int:
    """Return open job count if the board exists, else -1."""
    try:
        if ats == "greenhouse":
            r = net.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
            return len(r.json().get("jobs", [])) if r.ok else -1
        if ats == "lever":
            r = net.get(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=200")
            return len(r.json()) if r.ok and isinstance(r.json(), list) and r.json() else -1
        if ats == "ashby":
            r = net.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            return len(r.json().get("jobs", [])) if r.ok else -1
        if ats == "smartrecruiters":
            r = net.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1")
            return int(r.json().get("totalFound", 0)) or -1 if r.ok else -1
        if ats == "workable":
            r = net.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
            if not r.ok or not names_company(name, r.json().get("name", "")):
                return -1
            return len(r.json().get("jobs") or []) or -1
        if ats == "recruitee":
            r = net.get(f"https://{slug}.recruitee.com/api/offers/")
            offers = (r.json().get("offers") or []) if r.ok else []
            return len(offers) if offers and names_company(name, offers[0].get("company_name", "")) else -1
        if ats == "bamboohr":
            r = net.get(f"https://{slug}.bamboohr.com/careers/list", allow_redirects=False)
            items = (r.json().get("result") or []) if r.status_code == 200 else []
            if not items:
                return -1
            detail = net.get_json(f"https://{slug}.bamboohr.com/careers/{items[0]['id']}/detail")
            desc = ((detail.get("result") or {}).get("jobOpening") or {}).get("description", "")
            return len(items) if names_company(name, desc) else -1
        if ats == "teamtailor":
            r = net.get(f"https://{slug}.teamtailor.com/jobs.rss")
            channel = ET.fromstring(r.content).find("channel") if r.ok else None
            if channel is None or not names_company(name, channel.findtext("title", "")):
                return -1
            return len(channel.findall("item")) or -1
    except (ValueError, ET.ParseError, net.requests.RequestException):
        return -1
    return -1


def probe(name: str, ats_types: tuple[str, ...] = PROBE_ATS) -> dict[str, Any] | None:
    best: tuple[int, str, str] | None = None
    for slug in slug_candidates(name):
        for ats in ats_types:
            n = _count(ats, slug, name)
            if n > 0 and (best is None or n > best[0]):
                best = (n, ats, slug)
        if best:
            break  # first slug that hits anywhere is almost always right
    if not best:
        return None
    return {"name": name, "ats": best[1], "slug": best[2], "jobs_at_discovery": best[0]}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*")
    p.add_argument("--ats", nargs="+", choices=PROBE_ATS, default=list(PROBE_ATS), help="ATS types to probe")
    args = p.parse_args(argv)
    ats_types = tuple(args.ats)
    names = args.names or [
        line.strip() for line in SEEDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    raw = COMPANIES_FILE.read_text(encoding="utf-8") if COMPANIES_FILE.exists() else ""
    data = yaml.safe_load(raw) or {}
    companies: list[dict[str, Any]] = data.get("companies") or []
    known = {c["name"].lower() for c in companies}
    todo = list(dict.fromkeys(n for n in names if n.lower() not in known))
    print(f"probing {len(todo)} companies ({len(names) - len(todo)} already in {COMPANIES_FILE.name}) on {', '.join(ats_types)}")

    found, missing = [], []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, hit in zip(todo, pool.map(lambda n: probe(n, ats_types), todo)):
            if hit:
                found.append(hit)
                print(f"  + {name:30} {hit['ats']:15} {hit['slug']:30} {hit['jobs_at_discovery']} jobs")
            else:
                missing.append(name)
    companies.extend(found)
    companies.sort(key=lambda c: c["name"].lower())
    data["companies"] = companies
    # Keep whatever header comments the file already has.
    header = "".join(line + "\n" for line in raw.splitlines() if line.startswith("#")) or (
        "# Company career feeds. Generated/merged by `python -m src.discover`; safe to edit by hand.\n"
        "# ats: greenhouse | lever | ashby | smartrecruiters | workable | recruitee | bamboohr | teamtailor (slug)\n"
        "#      or  workday (url). enabled: false to pause.\n"
    )
    COMPANIES_FILE.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    print(f"\nadded {len(found)}; not found on a supported ATS ({len(missing)}): {', '.join(missing)}")


if __name__ == "__main__":
    main(sys.argv[1:])
