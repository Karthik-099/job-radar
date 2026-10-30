"""Dashboard -> repo inbox: status changes, notes, Gmail review decisions and resume uploads arrive as small
JSON files the dashboard commits straight to state/inbox/ through GitHub's contents API. That costs 0 Actions
minutes (no workflow runs per click). Every scan applies them and deletes the files it applied.

File format (one file per dashboard sync):
  {"v": 1, "kind": "status", "changes": [{"id", "status", "at", "note"?}, ...]}
  {"v": 1, "kind": "resume", "name": "cv.pdf", "text": "...plain text extracted in the browser..."}
  {"v": 1, "kind": "dismiss", "review": ["<review key>", ...]}       (Gmail review items you dismissed)

The input is untrusted (anyone holding the token could write it): sizes are capped, statuses validated by
update_status.apply_changes, unknown kinds are dropped, and a bad file is deleted, never retried forever.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import STATE_DIR
from .state import JobStore
from .update_status import apply_changes

log = logging.getLogger(__name__)

INBOX = STATE_DIR / "inbox"
MAX_FILE_BYTES = 400_000
MAX_FILES = 300


def ingest(store: JobStore, inbox: Path = INBOX) -> dict[str, Any]:
    """Apply every inbox file in name (= time) order; returns counters and the newest uploaded resume."""
    stats: dict[str, Any] = {"files": 0, "applied": 0, "stale": 0, "invalid": 0, "dismissed": 0, "bad_files": 0,
                             "resume": None}
    if not inbox.is_dir():
        return stats
    for path in sorted(inbox.glob("*.json"))[:MAX_FILES]:
        stats["files"] += 1
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("too large")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            kind = data.get("kind")
            if kind == "status":
                changes = [c for c in data.get("changes") or [] if isinstance(c, dict)]
                for k, v in apply_changes(store, changes).items():
                    stats[k] = stats.get(k, 0) + v
            elif kind == "resume":
                text = str(data.get("text") or "")
                if len(text) < 300:
                    raise ValueError("resume text too short")
                stats["resume"] = {"name": str(data.get("name") or "resume")[:80], "text": text[:60_000]}
            elif kind == "dismiss":
                keys = {str(k) for k in data.get("review") or []}
                gmail = store.meta.get("gmail") or {}
                before = len(gmail.get("review") or [])
                gmail["review"] = [r for r in gmail.get("review") or [] if review_key(r) not in keys]
                stats["dismissed"] += before - len(gmail["review"])
            else:
                raise ValueError(f"unknown kind {kind!r}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            stats["bad_files"] += 1
            log.warning("inbox: dropped %s (%s)", path.name, exc)
        path.unlink(missing_ok=True)
    if stats["files"]:
        log.info("inbox: %d files, %d status changes applied, %d stale, %d bad files", stats["files"],
                 stats["applied"], stats["stale"], stats["bad_files"])
    return stats


def review_key(entry: dict[str, Any]) -> str:
    """Stable id of a Gmail review item, shared with the dashboard."""
    return f"{entry.get('at', '')}|{entry.get('company', '')}"[:120]
