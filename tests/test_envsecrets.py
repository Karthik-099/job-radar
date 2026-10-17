from __future__ import annotations

import logging

from src import envsecrets


def test_dotenv_loads_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nFOO_KEY=abc123\nBAR_KEY=   # empty with inline comment\nEXISTING=fromfile\n")
    env.chmod(0o600)
    monkeypatch.setenv("EXISTING", "fromenv")
    monkeypatch.delenv("FOO_KEY", raising=False)
    monkeypatch.delenv("BAR_KEY", raising=False)
    envsecrets.load_dotenv(env)
    import os
    assert os.environ["FOO_KEY"] == "abc123"
    assert "BAR_KEY" not in os.environ
    assert os.environ["EXISTING"] == "fromenv"


def test_redaction_masks_known_and_key_shaped(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "supersecretvalue123")
    assert envsecrets.secret("MY_SECRET") == "supersecretvalue123"
    text = "url?key=supersecretvalue123 and gsk_" + "A" * 40 + " and AIza" + "b" * 35
    out = envsecrets.redact(text)
    assert "supersecretvalue123" not in out and "gsk_" not in out and "AIza" not in out


def test_log_filter_redacts(caplog, monkeypatch):
    monkeypatch.setenv("TOKEN_X", "tok_live_0123456789")
    envsecrets.secret("TOKEN_X")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "calling with %s", ("tok_live_0123456789",), None)
    envsecrets.RedactingFilter().filter(record)
    assert "tok_live" not in record.getMessage()
