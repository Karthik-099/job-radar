"""Find which ATS a company uses and write it to companies.yaml.

    python -m src.discover                 # probe every name in profile/company_seeds.txt
    python -m src.discover "Grafana Labs"  # probe specific names

Greenhouse, Lever, Ashby and SmartRecruiters are probed by slug guesses. Workday tenants can't be
guessed reliably, so add them by hand as `{name, ats: workday, url: https://<tenant>.wdN.myworkdayjobs.com/<Site>}`.
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yaml

from . import net
from .config import PROFILE_DIR
from .sources.ats import COMPANIES_FILE

SEEDS_FILE = PROFILE_DIR / "company_seeds.txt"


def slug_candidates(name: str) -> list[str]:
    base = name.lower().strip()
    words = re.findall(r"[a-z0-9]+", base)
    core = [w for w in words if w not in {"inc", "labs", "technologies", "technology", "software", "india", "the", "hq", "io", "ai"}]
    cands = ["".join(words), "-".join(words), "".join(core), "-".join(core)]
    if words:
        cands.append(words[0])
    if len(words) > 1:
        cands.append(words[0] + "hq")
        cands.append("".join(words) + "inc")
    return list(dict.fromkeys(c for c in cands if c))


def _count(ats: str, slug: str) -> int:
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
    except (ValueError, net.requests.RequestException):
        return -1
    return -1


def probe(name: str) -> dict[str, Any] | None:
    best: tuple[int, str, str] | None = None
    for slug in slug_candidates(name):
        for ats in ("greenhouse", "lever", "ashby", "smartrecruiters"):
            n = _count(ats, slug)
            if n > 0 and (best is None or n > best[0]):
                best = (n, ats, slug)
        if best:
            break  # first slug that hits anywhere is almost always right
    if not best:
        return None
    return {"name": name, "ats": best[1], "slug": best[2], "jobs_at_discovery": best[0]}


def load_existing() -> dict[str, Any]:
    if COMPANIES_FILE.exists():
        return yaml.safe_load(COMPANIES_FILE.read_text(encoding="utf-8")) or {}
    return {}


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("names", nargs="*")
    args = p.parse_args(argv)
    names = args.names or [
        line.strip() for line in SEEDS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    data = load_existing()
    companies: list[dict[str, Any]] = data.get("companies") or []
    known = {c["name"].lower() for c in companies}
    todo = list(dict.fromkeys(n for n in names if n.lower() not in known))
    print(f"probing {len(todo)} companies ({len(names) - len(todo)} already in {COMPANIES_FILE.name})")

    found, missing = [], []
    with ThreadPoolExecutor(max_workers=12) as pool:
        for name, hit in zip(todo, pool.map(probe, todo)):
            if hit:
                found.append(hit)
                print(f"  + {name:30} {hit['ats']:15} {hit['slug']:30} {hit['jobs_at_discovery']} jobs")
            else:
                missing.append(name)
    companies.extend(found)
    companies.sort(key=lambda c: c["name"].lower())
    data["companies"] = companies
    header = (
        "# Company career feeds. Generated/merged by `python -m src.discover`; safe to edit by hand.\n"
        "# ats: greenhouse | lever | ashby | smartrecruiters (slug)  or  workday (url). enabled: false to pause.\n"
    )
    COMPANIES_FILE.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    print(f"\nadded {len(found)}; not found on a supported ATS ({len(missing)}): {', '.join(missing)}")


if __name__ == "__main__":
    main(sys.argv[1:])
