"""Persistent job store: state/jobs.json in the private repo."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import STATE_DIR
from .models import Job, normalize_url

SCHEMA_VERSION = 1


class JobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or STATE_DIR / "jobs.json"
        self.jobs: dict[str, Job] = {}
        self.meta: dict[str, Any] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.meta = data.get("meta") or {}
            self.jobs = {jid: Job.from_dict(j) for jid, j in (data.get("jobs") or {}).items()}

    @property
    def seen_ids(self) -> set[str]:
        return set(self.jobs)

    @property
    def seen_urls(self) -> set[str]:
        urls: set[str] = set()
        for job in self.jobs.values():
            urls.update(normalize_url(u) for u in [job.url, *job.alt_urls] if u)
        return urls

    def add(self, jobs: list[Job]) -> None:
        for job in jobs:
            self.jobs.setdefault(job.id, job)

    def prune(self, max_age_days: int = 45) -> int:
        """Forget untouched jobs older than max_age_days; anything you acted on is kept forever."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        stale = [
            jid for jid, j in self.jobs.items()
            if j.status == "new" and _parse(j.first_seen) < cutoff
        ]
        for jid in stale:
            del self.jobs[jid]
        return len(stale)

    def save(self) -> None:
        self.meta.update(version=SCHEMA_VERSION, updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for job in self.jobs.values():
            job.description = (job.description or "")[:_desc_limit(job)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One job per line: small git diffs every run, so the private repo stays compact.
        lines = [f"{json.dumps(jid)}:{json.dumps(j.to_dict(), ensure_ascii=False, separators=(',', ':'))}"
                 for jid, j in sorted(self.jobs.items())]
        text = ('{"meta":' + json.dumps(self.meta, ensure_ascii=False, separators=(",", ":")) + ',\n"jobs":{\n'
                + ",\n".join(lines) + "\n}}\n")
        # Atomic write so a crash mid-run never corrupts the store.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.path)


def _desc_limit(job: Job) -> int:
    """Keep full-ish text only where it's still needed (LLM scoring); a snippet is enough otherwise."""
    if job.status == "new" and (job.scoring or {}).get("method") != "llm" and (job.apply_priority or 0) >= 45:
        return 2500
    return 900


def _parse(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
