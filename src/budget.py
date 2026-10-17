"""GitHub Actions minutes guard. Private repos get 2,000 free minutes a month; this keeps us well under.

GitHub bills each job rounded up to the whole minute. We record every run's billed minutes in
state/usage.json and refuse to run once the month's total passes the limit.

    python -m src.budget check --limit 1700   # exit 0 = go; writes skip=true to $GITHUB_OUTPUT when over
    python -m src.budget record --seconds 143 --kind scan
"""
from __future__ import annotations

import argparse
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
    c.add_argument("--limit", type=int, default=1700)
    r = sub.add_parser("record")
    r.add_argument("--seconds", type=float, required=True)
    r.add_argument("--kind", default="scan")
    args = p.parse_args()
    if args.cmd == "record":
        data = record(args.seconds, args.kind)
        print(f"minutes this month: {used_minutes(data)}")
        return 0
    used = used_minutes(load())
    over = used >= args.limit
    print(f"minutes used this month: {used} / limit {args.limit} (free quota 2000) -> {'SKIP' if over else 'run'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"skip={'true' if over else 'false'}\nused={used}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
