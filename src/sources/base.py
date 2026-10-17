"""Source registry and isolated, time-boxed execution."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Preferences
from ..geo import classify
from ..models import Job, normalize_url

log = logging.getLogger(__name__)


@dataclass
class FetchContext:
    prefs: Preferences
    backfill: bool = False
    deadline: float = field(default_factory=lambda: time.monotonic() + 240)
    title_ok: Callable[[str], bool] | None = None  # cheap pre-check before fetching descriptions
    known_urls: set[str] = field(default_factory=set)  # normalized URLs already in state
    countries: list[str] = field(default_factory=list)  # --countries override for world sources

    def __post_init__(self) -> None:
        if self.title_ok is None:
            from ..filters import JobFilter

            self.title_ok = JobFilter(self.prefs).title_ok

    def wants(self, title: str) -> bool:
        return self.title_ok(title) if self.title_ok else True

    def is_known(self, url: str) -> bool:
        """Already tracked: skip the (slow) description fetch."""
        return bool(url) and normalize_url(url) in self.known_urls

    def location_ok(self, location: str) -> bool:
        """Cheap location pre-check on list pages, before the slow description fetch.

        Big employers' onsite roles in unlisted countries (mostly the US) almost never sponsor
        junior hires, so we skip them here. Remote boards don't call this: their JDs are already
        in hand and the full filter decides using the sponsorship text."""
        if not location:
            return True
        geo = classify(location)
        return geo.region != "abroad" or geo.country in self.prefs.abroad_countries

    def time_left(self) -> float:
        return self.deadline - time.monotonic()

    def hours_old(self, cfg: dict) -> int:
        if self.backfill:
            return self.prefs.max_age_days * 24
        return int(cfg.get("hours_old", 72))


FetchFn = Callable[[FetchContext], list[Job]]
REGISTRY: dict[str, FetchFn] = {}


def register(name: str) -> Callable[[FetchFn], FetchFn]:
    def deco(fn: FetchFn) -> FetchFn:
        REGISTRY[name] = fn
        return fn
    return deco


@dataclass
class SourceResult:
    name: str
    jobs: list[Job]
    seconds: float
    error: str = ""


def run_sources(names: list[str], ctx: FetchContext, timeout: float) -> list[SourceResult]:
    """Run each source in its own daemon thread so one hang or crash never blocks the run."""
    results: dict[str, SourceResult] = {}
    threads: list[tuple[str, threading.Thread, float]] = []

    def worker(name: str, started: float) -> None:
        try:
            jobs = REGISTRY[name](ctx)
            results[name] = SourceResult(name, jobs, time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            log.warning("source %s failed: %s: %s", name, type(exc).__name__, exc)
            results[name] = SourceResult(name, [], time.monotonic() - started, f"{type(exc).__name__}: {exc}")

    for name in names:
        started = time.monotonic()
        t = threading.Thread(target=worker, args=(name, started), name=f"src-{name}", daemon=True)
        t.start()
        threads.append((name, t, started))

    hard_stop = time.monotonic() + timeout
    for name, t, started in threads:
        t.join(max(0.0, hard_stop - time.monotonic()))
        if t.is_alive():
            log.warning("source %s timed out after %.0fs, skipping", name, timeout)
            results[name] = SourceResult(name, [], time.monotonic() - started, "timeout")
    return [results[n] for n in names]
