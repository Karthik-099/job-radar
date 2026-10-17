"""Collapse duplicates within a batch and drop jobs already tracked in state."""
from __future__ import annotations

from collections.abc import Iterable

from .models import Job, _norm_text, normalize_company, normalize_location, normalize_url

# Location strings that name no city: Indeed's "MH, India", "KA, India", plain "India", "Remote".
_GENERIC_CITY = {"", "india", "remote", "anywhere", "worldwide"}


def _better(a: Job, b: Job) -> Job:
    """Keep the richer record: longer description wins, then the earlier posting date."""
    if len(b.description or "") > len(a.description or ""):
        return b
    if len(b.description or "") == len(a.description or "") and b.posted_at and (
        not a.posted_at or b.posted_at < a.posted_at
    ):
        return b
    return a


def _merge(keep: Job, drop: Job) -> Job:
    for url in [drop.url, *drop.alt_urls]:
        if url and url != keep.url and url not in keep.alt_urls:
            keep.alt_urls.append(url)
    for tag in [*drop.tags, f"src:{drop.source}"]:
        if tag not in keep.tags:
            keep.tags.append(tag)
    keep.remote = keep.remote or drop.remote
    keep.salary = keep.salary or drop.salary
    keep.posted_at = keep.posted_at or drop.posted_at
    return keep


def dedupe(jobs: Iterable[Job]) -> list[Job]:
    """Merge jobs that share a company+title+location hash or a normalized URL."""
    by_key: dict[str, Job] = {}
    alias: dict[str, str] = {}  # any hash or normalized URL -> canonical key in by_key
    for job in jobs:
        if f"src:{job.source}" not in job.tags:
            job.tags.append(f"src:{job.source}")
        canonical = alias.get(job.key) or (alias.get(job.norm_url) if job.norm_url else None)
        if canonical is None:
            canonical = job.key
            by_key[canonical] = job
        else:
            current = by_key[canonical]
            winner = _better(current, job)
            merged = _merge(winner, job if winner is current else current)
            merged.id = current.id  # keep the first id so the slot stays stable
            by_key[canonical] = merged
        alias.setdefault(job.key, canonical)
        if job.norm_url:
            alias.setdefault(job.norm_url, canonical)
    return _merge_generic_locations(list(by_key.values()))


def _is_generic(job: Job) -> bool:
    city = normalize_location(job.location)
    return city in _GENERIC_CITY or len(city) == 2  # two-letter state code


def _merge_generic_locations(jobs: list[Job]) -> list[Job]:
    """Second pass: "Mastercard | SRE II | MH, India" is the same posting as "... | Pune, India".
    Merge a city-less listing into the city-specific one with the same company + title + country."""
    specific: dict[tuple[str, str, str], Job] = {}
    for job in jobs:
        if not _is_generic(job):
            specific.setdefault((normalize_company(job.company), _norm_text(job.title), job.country), job)
    out: list[Job] = []
    for job in jobs:
        target = specific.get((normalize_company(job.company), _norm_text(job.title), job.country))
        if _is_generic(job) and target is not None and target is not job:
            _merge(target, job)
            if len(job.description or "") > len(target.description or ""):
                target.description = job.description
        else:
            out.append(job)
    return out


def drop_seen(jobs: Iterable[Job], seen_ids: set[str], seen_urls: set[str]) -> list[Job]:
    fresh = []
    for job in jobs:
        if job.id in seen_ids:
            continue
        urls = {normalize_url(u) for u in [job.url, *job.alt_urls]} - {""}
        if urls & seen_urls:
            continue
        fresh.append(job)
    return fresh

