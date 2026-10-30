from __future__ import annotations

import json
from pathlib import Path

from src import world

FIXTURE = json.loads((Path(__file__).parent / "vectors" / "wikidata_companies.json").read_text(encoding="utf-8"))


def _world() -> dict:
    return world.assemble(world.parse_rows(FIXTURE, "software"), world.load_countries())


def test_parse_drops_non_http_sites_and_unlabelled():
    names = {r["name"] for r in world.parse_rows(FIXTURE, "software")}
    assert "FTP Only" not in names and "FTP Only Ltd" not in names
    assert not any(n.startswith("Q1000") for n in names)
    assert "Spotify Technology" in names  # legal suffix stripped


def test_rank_dedupe_and_shape():
    lu = _world()["442"]
    assert lu["name"] == "Luxembourg" and lu["iso2"] == "LU"
    names = [c["name"] for c in lu["companies"]]
    # employees first, then revenue, then sitelinks; Spotify appears once despite two rows
    assert names == ["IKANO", "Spotify Technology", "Small Lux Fintech", "Tiny Lux Startup"]
    assert lu["companies"][0] == {"name": "IKANO", "website": "https://group.ikano", "industry": "software",
                                  "employees": 8000, "hq_city": "Luxembourg"}
    assert lu["companies"][3]["hq_city"] == ""  # unresolved QID label dropped


def test_alias_and_unknown_country():
    w = _world()
    assert [c["name"] for c in w["528"]["companies"]] == ["Dutch Cloud"]  # Q55 -> Kingdom of the Netherlands
    assert sum(len(c["companies"]) for c in w.values()) == 5  # USSR row ignored


def test_every_country_and_atlas_feature_present():
    w = _world()
    countries = world.load_countries()
    assert set(w) == set(countries) and len(w) >= 254
    for key in ("442", "356", "276", "528", "372", "702", "784", "840", "Kosovo", "Somaliland", "N. Cyprus",
                "Indian Ocean Ter.", "Siachen Glacier"):
        assert key in w
    assert all(k.isdigit() and len(k) == 3 for k in w if k not in
               ("Kosovo", "Somaliland", "N. Cyprus", "Indian Ocean Ter.", "Siachen Glacier"))


def test_top_n_cap():
    rows = [{"qid": f"Q{i}", "country": "Q32", "name": f"C{i}", "website": "https://x", "industry": "software",
             "employees": i, "revenue": None, "sitelinks": 0, "hq_city": ""} for i in range(40)]
    lu = world.assemble(rows, world.load_countries())["442"]["companies"]
    assert len(lu) == world.TOP_N and lu[0]["employees"] == 39


def test_refresh_failure_keeps_old_file(tmp_path, monkeypatch):
    old = tmp_path / "world.json"
    old.write_text("OLD")
    monkeypatch.setattr(world, "WORLD_FILE", old)

    def boom(**_):
        raise world.net.requests.ConnectionError("offline")

    monkeypatch.setattr(world, "build_world", boom)
    assert world.refresh({}, force=True) is False and old.read_text() == "OLD"
    assert world.refresh({"world_at": "2099-01-01T00:00:00+00:00"}) is False  # not due
