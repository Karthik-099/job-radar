"""Load and validate profile/preferences.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "profile"
STATE_DIR = ROOT / "state"


@dataclass
class Preferences:
    title_include: list[str]
    title_exclude: list[str]
    description_exclude: list[str]
    company_exclude: list[str]
    max_required_years: int
    max_age_days: int
    keep_unknown_date: bool
    abroad_countries: list[str]
    abroad_keep_if_visa: bool
    abroad_drop_if_no_sponsorship: bool
    keep_unknown_location: bool
    drop_local_language: bool
    target_companies: list[str]
    region_weights: dict[str, float]
    city_boost: dict[str, float] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def source_cfg(self, name: str) -> dict[str, Any]:
        return self.sources.get(name) or {}

    def source_enabled(self, name: str) -> bool:
        return bool(self.source_cfg(name).get("enabled", False))


def _list(data: dict[str, Any], *keys: str) -> list[str]:
    node: Any = data
    for key in keys:
        node = (node or {}).get(key)
    return [str(x) for x in (node or [])]


def load_preferences(path: Path | None = None) -> Preferences:
    path = path or PROFILE_DIR / "preferences.yaml"
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    titles = data.get("titles") or {}
    loc = data.get("locations") or {}
    recency = data.get("recency") or {}
    prefs = Preferences(
        title_include=_list(titles, "include"),
        title_exclude=_list(titles, "exclude"),
        description_exclude=_list(data, "description_exclude"),
        company_exclude=_list(data, "company_exclude"),
        max_required_years=int((data.get("experience") or {}).get("max_required_years", 3)),
        max_age_days=int(recency.get("max_age_days", 30)),
        keep_unknown_date=bool(recency.get("keep_unknown_date", True)),
        abroad_countries=[c.lower() for c in _list(loc, "abroad_countries")],
        abroad_keep_if_visa=bool(loc.get("abroad_keep_if_visa", True)),
        abroad_drop_if_no_sponsorship=bool(loc.get("abroad_drop_if_no_sponsorship", True)),
        keep_unknown_location=bool(loc.get("keep_unknown_location", True)),
        drop_local_language=bool(loc.get("drop_local_language", True)),
        target_companies=_list(data, "target_companies"),
        region_weights={k: float(v) for k, v in (loc.get("region_weights") or {}).items()},
        city_boost={k.lower(): float(v) for k, v in (loc.get("city_boost") or {}).items()},
        sources=data.get("sources") or {},
        raw=data,
    )
    if not prefs.title_include:
        raise ValueError(f"{path}: titles.include must not be empty")
    return prefs
