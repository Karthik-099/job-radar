"""GitHub Actions minutes guard. Private repos get 2,000 free minutes a month; this keeps us far below.

GitHub bills each job rounded up to the whole minute. Every run records its billed minutes in
state/usage.json. Two rules decide whether a scheduled run may start:

  hard cap  never start once the month's total reaches --limit (default 1,050; CI adds ~50 on top).
  pacing    spread the cap evenly over the month: a scheduled run is skipped while usage is ahead of
            limit x (fraction of month elapsed) + slack. A burst of runs early in the month therefore can't
            eat the end of the month, and the total can never pass the cap.

A skipped run still costs GitHub's minimum of 1 billed minute, which is recorded too.

    python -m src.budget check --limit 1050 --pace   # writes skip=true|false to $GITHUB_OUTPUT
    python -m src.budget record --seconds 143 --kind scan
"""
from __future__ import annotations

import argparse
import calendar
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

# No third-party imports here: the guard runs before dependencies are installed, so a skipped run is ~10s.
USAGE_FILE = Path(__file__).resolve().parent.parent / "state" / "usage.json"


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load(path: Path = USAGE_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def used_minutes(data: dict, month: str | None = None) -> int:
    return int((data.get(month or _month()) or {}).get("minutes", 0))


DEFAULT_LIMIT = 1050
PACE_SLACK = 60  # minutes a run may be ahead of the even line (lets the first runs of a month through)


def month_fraction(now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    days = calendar.monthrange(now.year, now.month)[1]
    return ((now.day - 1) + (now.hour + now.minute / 60) / 24) / days


def decide(used: int, limit: int, pace: bool, now: datetime | None = None) -> tuple[bool, str]:
    """(run?, reason)."""
    if used >= limit:
        return False, f"hard cap reached ({used} >= {limit})"
    allowed = round(limit * month_fraction(now)) + PACE_SLACK
    if pace and used >= allowed:
        return False, f"ahead of pace ({used} >= {allowed} allowed so far this month)"
    return True, f"ok ({used} used, {allowed if pace else limit} allowed so far)"


def record(seconds: float, kind: str, path: Path = USAGE_FILE) -> dict:
    data = load(path)
    month = data.setdefault(_month(), {"minutes": 0, "runs": {}})
    month["minutes"] += max(1, math.ceil(seconds / 60))
    month["runs"][kind] = month["runs"].get(kind, 0) + 1
    # Keep a year of history.
    for key in sorted(data)[:-12]:
        del data[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    c.add_argument("--pace", action="store_true", help="also spread the limit evenly over the month")
    r = sub.add_parser("record")
    r.add_argument("--seconds", type=float, required=True)
    r.add_argument("--kind", default="scan")
    args = p.parse_args()
    if args.cmd == "record":
        data = record(args.seconds, args.kind)
        print(f"minutes this month: {used_minutes(data)}")
        return 0
    used = used_minutes(load())
    go, why = decide(used, args.limit, args.pace)
    print(f"minutes this month: {used} / cap {args.limit} (free quota 2000) -> {'run' if go else 'SKIP'}: {why}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"skip={'false' if go else 'true'}\nused={used}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
