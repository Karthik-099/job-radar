"""job-radar pipeline:
fetch -> enrich -> dedupe -> filter -> rank -> learn companies -> ghost check -> LLM score -> re-rank
      -> closed-job check (daily) -> Gmail replies (read-only) -> save state -> encrypted dashboard export
      -> email digest

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
from pathlib import Path

from .config import STATE_DIR, Preferences, load_preferences
from .dedupe import dedupe, drop_seen, merge_multi_city
from .filters import JobFilter, enrich
from .envsecrets import install_log_redaction, load_dotenv
from .learn import learn_companies
from .models import Job, posting_ref
from .sources import ats, bigtech, boards, jobspy_source  # noqa: F401  (importing registers the sources)
from .sources.base import REGISTRY, FetchContext, run_sources
from .export import export
from .notify import notify
from .recommend import apply_next, rank
from .scoring import score_jobs
from .envsecrets import secret
from .state import JobStore
from .guard import suspicious
from .inbox import ingest
from .tailor import jd_terms
from .tracking import check_closed, drop_same_role

log = logging.getLogger("job_radar")
JOBSPY_SOURCES = {"indeed", "naukri", "google", "linkedin", "indeed_world", "linkedin_world", "glassdoor", "bayt"}

# Run budget: a 4-hourly cron must stay well inside the 1,050-minute cap (src/budget.py).
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
    p.add_argument("--gmail", action="store_true", help="update applied jobs from Gmail replies (read-only)")
    p.add_argument("--quiet", action="store_true", help="no job titles/companies in the output (CI logs)")
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

    # 0. Dashboard inbox: your marks, notes, review decisions and an uploaded resume (0 Actions minutes).
    inbox_stats: dict = {}
    if not args.dry_run:
        inbox_stats = ingest(store)
        if os.environ.get("INBOX_DIR"):  # separate inbox repo checked out by the workflow
            extra = ingest(store, Path(os.environ["INBOX_DIR"]))
            for k, v in extra.items():
                if k == "resume":
                    inbox_stats[k] = v or inbox_stats.get(k)
                else:
                    inbox_stats[k] = inbox_stats.get(k, 0) + v
        if inbox_stats.get("resume"):
            from .resume_import import import_resume
            result = import_resume(inbox_stats["resume"]["text"], inbox_stats["resume"]["name"])
            log.info("resume upload: %s", result.get("status"))
            store.meta["resume_import"] = result

    # 1. Fetch (each source isolated and time-boxed).
    ctx = FetchContext(prefs=prefs, backfill=args.backfill, deadline=time.monotonic() + args.budget,
                       known_urls=store.seen_urls,
                       countries=[c.strip().lower() for c in args.countries.split(",") if c.strip()])
    if any(n in JOBSPY_SOURCES for n in names):
        jobspy_source.load_jobspy()  # import on the main thread first (parallel first-imports deadlock)
    results = run_sources(names, ctx, timeout=args.budget + 15) if names else []
    raw = [j for r in results for j in r.jobs]

    # 2. Normalize, dedupe, drop known, cheap filters, keyword rank.
    for job in raw:
        enrich(job, prefs)
        job.description = (job.description or "")[:MAX_DESCRIPTION_CHARS]
    unique = merge_multi_city(dedupe(raw))
    fresh = drop_seen(unique, store.seen_ids, store.seen_urls)
    jf = JobFilter(prefs)
    kept = jf.apply(fresh)
    kept, repeats = drop_same_role(kept, store.jobs, store.meta)  # same role already tracked: not new
    for job in kept:
        # The full JD is only available now (state keeps a snippet): extract ATS keywords and the job id once.
        job.jd_terms = jd_terms(f"{job.title}\n{job.description}")
        job.ref = job.ref or posting_ref(job.url, job.description)
        if suspicious(job.description) and "hidden_text" not in job.flags:
            job.flags.append("hidden_text")  # tried to hide text or instruct an AI screener
        rank(job, prefs)
    kept.sort(key=lambda j: j.apply_priority or 0, reverse=True)
    learned = learn_companies([j for j in unique if jf.title_ok(j.title)], dry_run=args.dry_run)
    ghosts = sum("reposted" in j.flags for j in store.jobs.values())

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

    # 3b. Cover letters for the very top picks (fact-checked against the resume, redrafted only on resume change).
    letters = 0
    if not args.no_score and not args.dry_run:
        from .letters import write_letters
        letters = write_letters([j for j in apply_next(pool, n=12)
                                 if (j.scoring or {}).get("method") == "llm" and (j.apply_priority or 0) >= 60])

    # 4. Daily: re-open saved links and flag closed postings.
    closed_stats = (check_closed(pool, store.meta, force=args.check_closed)
                    if not (args.dry_run or args.no_fetch) or args.check_closed else {})

    # 5. Replies in Gmail move jobs you applied to (read-only; never touches jobs you didn't apply to).
    gmail_stats: dict = {}
    if args.gmail and not args.dry_run:
        gmail_stats = _gmail(store, prefs)

    if not args.quiet:
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
        "repeats_skipped": repeats,
        "sources_ok": sum(1 for r in results if not r.error),
        "sources_total": len(results),
        "sources_failed": ", ".join(r.name for r in results if r.error),
        "llm_scored": score_stats.get("llm", 0), "llm_cached": score_stats.get("cached", 0),
        "closed_found": closed_stats.get("closed", 0),
        "gmail_updated": gmail_stats.get("updated", 0),
        "letters": letters,
    }
    by_source: dict[str, int] = {}
    for j in kept:
        for tag in j.tags:
            if tag.startswith("src:"):
                by_source[tag[4:]] = by_source.get(tag[4:], 0) + 1
    stats["kept_by_source"] = by_source
    print("\nkept by source: " + ", ".join(f"{k}={v}" for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])))
    print(f"raw={len(raw)} unique={len(unique)} fresh={len(fresh)} kept={len(kept)} rejected={dict(jf.rejections)}")
    if inbox_stats.get("files"):
        print(f"dashboard inbox: {inbox_stats['files']} files, {inbox_stats['applied']} changes applied")
    print(f"companies learned: {len(learned)} | same role skipped: {repeats} | reposts flagged: {ghosts} | "
          f"llm scored: {stats['llm_scored']} (cache hits {stats['llm_cached']}) | closed found: {stats['closed_found']}")

    if args.dry_run:
        print(f"run duration: {elapsed}s (dry run: nothing saved)")
        write_summary(stats)
        return 0

    if names and not args.no_fetch:  # "Learn" tab: recent videos, refreshed about twice a day
        from .videos import refresh
        try:
            refresh(store.meta, gaps=_skill_gaps(pool))
        except Exception as exc:  # noqa: BLE001 - optional feature, never fails the scan
            log.warning("videos skipped: %s", type(exc).__name__)
        from . import world  # Map tab: employers by country from Wikidata, rebuilt at most monthly
        try:
            world.refresh(store.meta)
        except Exception as exc:  # noqa: BLE001 - optional feature, never fails the scan
            log.warning("world map refresh skipped: %s", type(exc).__name__)

    # 6. Save state.
    store.add(kept)
    for job in kept:  # same objects as in pool; add() keeps existing ones
        store.jobs[job.id] = job
    _record_daily(store.meta, kept)
    if names:  # a real scan, not a status-only re-export
        _record_run(store.meta, stats, fresh_count=len(unique), already=len(unique) - len(fresh) + repeats)
    pruned = store.prune()
    if names:  # a status-only re-export must not overwrite the last real scan's summary
        store.meta["last_run"] = stats
    store.save()
    (STATE_DIR / "last_run.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    log.info("saved %d jobs (pruned %d)", len(store.jobs), pruned)

    # 7. Encrypted dashboard export + 8. email digest.
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


def _skill_gaps(jobs: list[Job]) -> list[str]:
    from collections import Counter
    gaps = Counter(s.lower() for j in jobs if (j.score or 0) >= 6 for s in (j.scoring or {}).get("missing_skills") or [])
    return [s for s, _ in gaps.most_common(8)]


def _gmail(store: JobStore, prefs: Preferences) -> dict:
    from .gmail_sync import GmailError, sync
    cfg = prefs.raw.get("gmail") or {}
    if not cfg.get("enabled", True) or not secret("GMAIL_REFRESH_TOKEN"):
        return {}
    try:
        stats = sync(store, use_ai=bool(cfg.get("use_ai", True)))
    except (GmailError, OSError, ValueError) as exc:  # never let Gmail trouble sink the scan
        log.warning("gmail sync skipped: %s", exc)
        return {}
    log.info("gmail: read %d, hiring %d, updated %d, skipped %d",
             stats["read"], stats["hiring"], stats["updated"], stats["skipped"])
    return stats


def _record_run(meta: dict, stats: dict, fresh_count: int, already: int) -> None:
    """One line per scan for the dashboard's run history (last 7 days of 3-hourly runs)."""
    runs = meta.setdefault("runs", [])
    runs.append({
        "at": stats["finished_at"], "added": stats["kept"], "already_tracked": already, "seen": fresh_count,
        "scored": stats["llm_scored"], "seconds": stats["duration_seconds"],
        "sources_ok": stats["sources_ok"], "sources_total": stats["sources_total"],
        "trigger": "schedule" if os.environ.get("GITHUB_EVENT_NAME") == "schedule" else "manual",
    })
    del runs[:-56]


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
