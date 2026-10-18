"""Secrets: read from the environment (GitHub Actions secrets in CI, ./.env locally). Never logged.

- `.env` is loaded only if present, never overrides real env vars, and must not be readable by others.
- Every secret value that is read is registered with a logging filter that masks it, so even an
  exception message or a URL containing a key prints as ***.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import sys

from .config import ROOT

_KNOWN: set[str] = set()
# Shapes of common API keys/tokens, masked even if they never went through secret().
_TOKEN_SHAPES = re.compile(
    r"gsk_[A-Za-z0-9]{20,}|sk-or-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{30,}|AQ\.[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}|ya29\.[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{30,}"
)


def load_dotenv(path=ROOT / ".env") -> None:
    if not path.exists():
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"warning: {path} is readable by other users; run: chmod 600 {path}", file=sys.stderr)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0] if not value.lstrip().startswith("#") else ""  # inline comments
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and value and key not in os.environ:
            os.environ[key] = value


def secret(name: str) -> str | None:
    value = os.environ.get(name) or None
    if value:
        _KNOWN.add(value)
    return value


def register_secret(value: str) -> None:
    """Mask a value obtained at runtime (e.g. a short-lived OAuth access token) in every log line."""
    if value:
        _KNOWN.add(value)


def redact(text: str) -> str:
    for value in _KNOWN:
        if len(value) >= 8:
            text = text.replace(value, "***")
    return _TOKEN_SHAPES.sub("***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def install_log_redaction() -> None:
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactingFilter())
