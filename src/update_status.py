"""Apply status changes sent from the dashboard (via the update-status workflow).

    python -m src.update_status '[{"id":"ab12...","status":"applied","at":"2026-10-18T10:00:00Z"}]'

Input is untrusted (it arrives through workflow_dispatch), so every field is validated: ids must exist,
statuses must be known, timestamps must parse, notes are length-capped, and at most 200 changes per call.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

from .models import STATUSES
from .state import JobStore

MAX_CHANGES = 200
_ID = re.compile(r"^[0-9a-f]{16}$")


def _ts(value: Any) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if dt > datetime.now(timezone.utc):
            raise ValueError
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


def apply_changes(store: JobStore, changes: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"applied": 0, "unknown_id": 0, "invalid": 0}
    for ch in changes[:MAX_CHANGES]:
        jid, status = str(ch.get("id", "")), str(ch.get("status", ""))
        if not _ID.match(jid) or status not in STATUSES:
            stats["invalid"] += 1
            continue
        job = store.jobs.get(jid)
        if job is None:
            stats["unknown_id"] += 1
            continue
        entry: dict[str, Any] = {"status": status, "at": _ts(ch.get("at")), "via": "dashboard"}
        note = str(ch.get("note") or "")[:500]
        if note:
            entry["note"] = note
        # The dashboard re-sends every change until it sees it here (a queued run can be cancelled by GitHub),
        # so repeats are normal: skip ones already applied, and never let an older change undo a newer one.
        latest = max((h.get("at", "") for h in job.history if h.get("status")), default="")
        last_note = next((h.get("note", "") for h in reversed(job.history) if h.get("note")), "")
        if latest and entry["at"] < latest:
            stats["stale"] = stats.get("stale", 0) + 1
            continue
        if job.status == status and note == last_note:
            continue
        if job.status != status or note:
            job.status = status
            job.history.append(entry)
            stats["applied"] += 1
    return stats


def main(argv: list[str]) -> int:
    # In CI the JSON arrives via the CHANGES env var (never interpolated into a shell command).
    raw = argv[0] if argv else os.environ.get("CHANGES") or sys.stdin.read()
    try:
        changes = json.loads(raw)
    except json.JSONDecodeError:
        print("invalid JSON input")
        return 2
    if isinstance(changes, dict):
        changes = [changes]
    if not isinstance(changes, list):
        print("expected a JSON list of changes")
        return 2
    store = JobStore()
    stats = apply_changes(store, [c for c in changes if isinstance(c, dict)])
    if stats["applied"]:
        store.save()
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
