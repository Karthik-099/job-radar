from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Preferences, load_preferences

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def prefs() -> Preferences:
    return load_preferences(ROOT / "profile" / "preferences.yaml")
