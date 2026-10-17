"""job-radar pipeline:
fetch -> enrich -> dedupe -> filter -> rank -> learn companies -> ghost check -> LLM score -> re-rank
      -> closed-job check (daily) -> save state -> encrypted dashboard export -> email digest

Usage:
    python -m src.main --dry-run                 # everything except writing/publishing/notifying
    python -m src.main --source indeed --dry-run # test one source
    python -m src.main --backfill                # first run: look back recency.max_age_days
    python -m src.main --no-fetch                # rescore/re-export/notify from saved state only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from .config import STATE_DIR, Preferences, load_preferences
from .dedupe import dedupe, drop_seen
from .filters import JobFilter, enrich
from .envsecrets import install_log_redaction, load_dotenv
from .learn import learn_companies
from .models import Job
from .sources import ats, boards, jobspy_source  # noqa: F401  (importing registers the sources)
from .sources.base import REGISTRY, FetchContext, run_sources
from .export import export
from .notify import notify
from .recommend import apply_next, rank
from .scoring import score_jobs
from .envsecrets import secret
from .state import JobStore
from .tracking import check_closed, track_reposts

log = logging.getLogger("job_radar")

# Run budget: a 3-hourly cron must stay well inside 2,000 free minutes/month.
FETCH_BUDGET_SECONDS = 180
MAX_DESCRIPTION_CHARS = 8000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="job-radar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="fetch and filter but write nothing")
    p.add_argument("--source", action="append", default=[], help="only run this source (repeatable or comma list)")
    p.add_argument("--backfill", action="store_true", help="look back recency.max_age_days instead of hours_old")
    p.add_argument("--countries", default="", help="world sources: scan these countries instead of the rotation (comma list)")
    p.add_argument("--limit", type=int, default=40, help="rows to print")
    p.add_argument("--budget", type=int, default=FETCH_BUDGET_SECONDS, help="seconds allowed for fetching")
    p.add_argument("--max-llm", type=int, default=40, help="max jobs sent to the LLM per run (cache hits are free)")
    p.add_argument("--no-score", action="store_true", help="skip LLM scoring (keyword scores only)")
    p.add_argument("--no-fetch", action="store_true", help="skip sources; work on saved state")
    p.add_argument("--export", action="store_true", help="write the encrypted dashboard to dist/ (needs DASHBOARD_PASSWORD)")
    p.add_argument("--notify", action="store_true", help="post the email digest (GitHub Actions only)")
    p.add_argument("--check-closed", action="store_true", help="force the closed-job check now")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def selected_sources(args: argparse.Namespace, prefs: Preferences) -> list[str]:
    wanted = [s.strip() for item in args.source for s in item.split(",") if s.strip()]
    if wanted:
        unknown = [s for s in wanted if s not in REGISTRY]
        if unknown:
            raise SystemExit(f"unknown source(s): {', '.join(unknown)}. Known: {', '.join(sorted(REGISTRY))}")
        return wanted
    return [name for name in REGISTRY if prefs.source_enabled(name)]


def print_table(jobs: list[Job], limit: int) -> None:
    if not jobs:
        print("No new matching jobs.")
        return
    print(f"\n{'#':>3}  {'prio':>5} {'fit':>4} {'region':7} {'yrs':>3}  {'title':42} {'company':24} {'location':24} {'posted':10} source")
    for i, j in enumerate(jobs[:limit], 1):
        posted = (j.posted_at or "")[:10]
        yrs = "" if j.min_years is None else str(j.min_years)
        star = "*" if "target_company" in j.tags else " "
        print(f"{i:>3}{star} {j.apply_priority or 0:>5} {j.score or 0:>4} {j.region[:7]:7} {yrs:>3}  {j.title[:42]:42} {j.company[:24]:24} {j.location[:24]:24} {posted:10} {j.source}")
        print(f"      {j.url}")
    if len(jobs) > limit:
        print(f"... and {len(jobs) - limit} more")
    print("(* = target company)")


def print_apply_next(picks: list[Job]) -> None:
    if not picks:
        return
    print("\n=== APPLY NEXT ===")
    for i, j in enumerate(picks, 1):
        flags = f"  [!] {', '.join(j.flags)}" if j.flags else ""
        print(f"{i:>2}. [{j.apply_priority:>5}] {j.title[:50]} | {j.company[:28]} | {j.location[:30]}")
        print(f"      why: {'; '.join(j.reasons)}{flags}")
        print(f"      {j.url}")


def write_summary(stats: dict) -> None:
    """Append a short Markdown summary to the GitHub Actions job page when running in CI."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## job-radar run", "", "| metric | value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in stats.items() if not isinstance(v, (dict, list))]
    lines += ["", "**Per source**", "", "| source | jobs | seconds | error |", "|---|---|---|---|"]
    lines += [f"| {s['name']} | {s['jobs']} | {s['seconds']} | {s['error']} |" for s in stats["sources"]]
    lines += ["", "**Rejections**: " + ", ".join(f"{k}={v}" for k, v in stats["rejections"].items())]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    prefs = load_preferences()
    names = [] if args.no_fetch else selected_sources(args, prefs)
    if not names and not args.no_fetch:
        print("No sources enabled. Enable one in profile/preferences.yaml or pass --source.")
        return 1
    store = JobStore()
    log.info("sources: %s | tracked jobs: %d | backfill=%s", ", ".join(names) or "-", len(store.jobs), args.backfill)

    # 1. Fetch (each source isolated and time-boxed).
    ctx = FetchContext(prefs=prefs, backfill=args.backfill, deadline=time.monotonic() + args.budget,
                       known_urls=store.seen_urls,
                       countries=[c.strip().lower() for c in args.countries.split(",") if c.strip()])
    results = run_sources(names, ctx, timeout=args.budget + 15) if names else []
    raw = [j for r in results for j in r.jobs]

    # 2. Normalize, dedupe, drop known, cheap filters, keyword rank.
    for job in raw:
        enrich(job, prefs)
        job.description = (job.description or "")[:MAX_DESCRIPTION_CHARS]
    unique = dedupe(raw)
    fresh = drop_seen(unique, store.seen_ids, store.seen_urls)
    jf = JobFilter(prefs)
    kept = jf.apply(fresh)
    for job in kept:
        rank(job, prefs)
    kept.sort(key=lambda j: j.apply_priority or 0, reverse=True)
    learned = learn_companies([j for j in unique if jf.title_ok(j.title)], dry_run=args.dry_run)
    ghosts = track_reposts(kept, store.meta)

    # 3. Pool = everything still tracked plus this run's jobs; LLM-score the most promising unscored ones.
    pool = list({j.id: j for j in [*store.jobs.values(), *kept]}.values())
    for job in pool:
        rank(job, prefs)
    score_stats: dict[str, int] = {}
    if not args.no_score:
        candidates = sorted((j for j in pool if j.status in ("new", "shortlisted") and "closed" not in j.flags
                             and (j.scoring or {}).get("method") != "llm" and (j.apply_priority or 0) >= 45),
                            key=lambda j: j.apply_priority or 0, reverse=True)
        score_stats = score_jobs(candidates, max_jobs=args.max_llm, in_ci=bool(os.environ.get("GITHUB_ACTIONS")))
        for job in pool:
            rank(job, prefs)

    # 4. Daily: re-open saved links and flag closed postings.
    closed_stats = (check_closed(pool, store.meta, force=args.check_closed)
                    if not (args.dry_run or args.no_fetch) or args.check_closed else {})

    print_table(kept, args.limit)
    print_apply_next(apply_next(pool, n=15))

    elapsed = round(time.monotonic() - started, 1)
    stats = {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_seconds": elapsed,
        "raw": len(raw), "unique": len(unique), "fresh": len(fresh), "kept": len(kept),
        "kept_india": sum(j.region == "india" for j in kept),
        "kept_remote": sum(j.region == "remote" for j in kept),
        "kept_abroad": sum(j.region == "abroad" for j in kept),
        "dry_run": args.dry_run,
        "sources": [{"name": r.name, "jobs": len(r.jobs), "seconds": round(r.seconds, 1), "error": r.error} for r in results],
        "rejections": dict(jf.rejections.most_common()),
        "companies_learned": len(learned),
        "reposts_flagged": ghosts,
        "llm_scored": score_stats.get("llm", 0), "llm_cached": score_stats.get("cached", 0),
        "closed_found": closed_stats.get("closed", 0),
    }
    by_source: dict[str, int] = {}
    for j in kept:
        for tag in j.tags:
            if tag.startswith("src:"):
                by_source[tag[4:]] = by_source.get(tag[4:], 0) + 1
    stats["kept_by_source"] = by_source
    print("\nkept by source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])))
    print(f"raw={len(raw)} unique={len(unique)} fresh={len(fresh)} kept={len(kept)} rejected={dict(jf.rejections)}")
    print(f"companies learned: {len(learned)} | reposts flagged: {ghosts} | "
          f"llm scored: {stats['llm_scored']} (cache hits {stats['llm_cached']}) | closed found: {stats['closed_found']}")

    if args.dry_run:
        print(f"run duration: {elapsed}s (dry run: nothing saved)")
        write_summary(stats)
        return 0

    # 5. Save state.
    store.add(kept)
    for job in kept:  # same objects as in pool; add() keeps existing ones
        store.jobs[job.id] = job
    _record_daily(store.meta, kept)
    pruned = store.prune()
    store.meta["last_run"] = stats
    store.save()
    (STATE_DIR / "last_run.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    log.info("saved %d jobs (pruned %d)", len(store.jobs), pruned)

    # 6. Encrypted dashboard export + 7. email digest.
    jobs_now = list(store.jobs.values())
    if args.export:
        password = secret("DASHBOARD_PASSWORD")
        if password:
            out = export(jobs_now, store.meta, password)
            print(f"dashboard exported: {out} ({out.stat().st_size // 1024} KB encrypted)")
        else:
            print("export skipped: DASHBOARD_PASSWORD not set")
    if args.notify:
        goal = int((prefs.raw.get("goals") or {}).get("daily_applications", 5))
        print("notify:", notify(kept, jobs_now, store.meta, stats, goal=goal))
        store.save()  # persists last_summary_date
    print(f"run duration: {round(time.monotonic() - started, 1)}s")
    write_summary(stats)
    return 0 if (not results or any(not r.error for r in results)) else 2


def _record_daily(meta: dict, kept: list[Job]) -> None:
    """Compact per-day counters for the analytics charts (kept forever, ~50 bytes/day)."""
    day = datetime.now(timezone.utc).date().isoformat()
    d = meta.setdefault("daily", {}).setdefault(day, {"new": 0, "india": 0, "remote": 0, "abroad": 0, "good": 0})
    d["new"] += len(kept)
    for j in kept:
        if j.region in ("india", "remote", "abroad"):
            d[j.region] += 1
        if (j.apply_priority or 0) >= 60:
            d["good"] += 1


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("JobSpy", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    install_log_redaction()
    load_dotenv()
    code = run(args)
    sys.stdout.flush()
    sys.stderr.flush()
    # A timed-out source can leave a worker thread blocked on the network; don't wait for it.
    os._exit(code)


if __name__ == "__main__":
    main()
