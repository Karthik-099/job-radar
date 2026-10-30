"""World map of employers: per country, the biggest companies in DevOps-relevant industries, from Wikidata (CC0).

One SPARQL query per industry group (11 queries, 3 at a time, ~1-2 min total) against query.wikidata.org: companies with that
industry (P452), a country (P17) and an official website (P856), not dissolved (P576). Ranked per country by
employees (P1128), then raw revenue (P2139), then Wikipedia sitelink count (notability); top TOP_N kept.
Written to state/world.json, refreshed every REFRESH_DAYS; any failure keeps the previous file.

Countries come from profile/countries.json, keyed by ISO 3166-1 numeric code ("442") = the feature id in the
Natural Earth world-atlas countries-50m.json. Atlas features without a numeric id (Kosovo, Somaliland, N. Cyprus,
Indian Ocean Ter., Siachen Glacier) are keyed by their atlas properties.name instead, so the globe looks up
`feature.id ?? feature.properties.name`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from . import net
from .config import PROFILE_DIR, STATE_DIR

log = logging.getLogger(__name__)

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "job-radar/1.0 (personal job search)"
COUNTRIES_FILE = PROFILE_DIR / "countries.json"
WORLD_FILE = STATE_DIR / "world.json"
REFRESH_DAYS = 30
TOP_N = 25

# industry label -> Wikidata P452 values (labels verified on Wikidata). Order = priority when a company has several.
INDUSTRIES: dict[str, list[str]] = {
    "cloud": ["Q483639", "Q671224"],  # cloud computing, data center
    "software": ["Q880371", "Q638608", "Q1254596", "Q7397", "Q1058914"],  # software industry/development, SaaS, ...
    "IT services": ["Q11661", "Q1540863", "Q3510521", "Q11660", "Q132898730"],  # IT, IT consulting, security, AI, hw
    "internet": ["Q75", "Q56611700"],  # Internet, Internet industry
    "telecom": ["Q2401742", "Q418", "Q56598901"],  # telecom industry, telecommunications, mobile phone industry
    "semiconductors": ["Q2986369"],
    "video games": ["Q941594", "Q1368944"],
    "e-commerce": ["Q484847"],
    "fintech": ["Q16319025", "Q100269062"],  # fintech, cryptocurrency industry
    "banking & finance": ["Q837171", "Q22687", "Q806718", "Q57774188", "Q43015", "Q29584334"],
    "consulting": ["Q130286"],  # business and management consultancy
}
# Wikidata entities companies use for P17 that differ from the ISO entity in countries.json
COUNTRY_ALIASES = {"Q55": "528", "Q756617": "208"}  # Netherlands (country), Danish Realm

QUERY = """SELECT ?co ?coLabel ?country ?site ?emp ?rev ?sl ?hqLabel WHERE {
  { SELECT ?co ?country (MAX(STR(?s)) AS ?site) (MAX(?e) AS ?emp) (MAX(?r) AS ?rev) (SAMPLE(?h) AS ?hq) (MAX(?l) AS ?sl) WHERE {
      VALUES ?ind { %s }
      ?co wdt:P452 ?ind ; wdt:P17 ?country ; wdt:P856 ?s .
      FILTER NOT EXISTS { ?co wdt:P576 [] }
      OPTIONAL { ?co wdt:P1128 ?e } OPTIONAL { ?co wdt:P2139 ?r } OPTIONAL { ?co wdt:P159 ?h }
      OPTIONAL { ?co wikibase:sitelinks ?l }
    } GROUP BY ?co ?country }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul,fr,de,es,it,pt,nl". }
}"""

_SUFFIX = re.compile(r",?\s+(Inc\.?|Ltd\.?|Limited|LLC|L\.L\.C\.|plc|PLC|S\.A\.?|SA|AG|GmbH|N\.V\.|NV|B\.V\.|BV|SE|AB|"
                     r"ASA|Oyj|S\.p\.A\.|SpA|Corp\.?|Corporation|Co\.,? Ltd\.?|Pvt\.? Ltd\.?|Private Limited)$")


def load_countries() -> dict[str, dict[str, str]]:
    return json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v else None
    except ValueError:
        return None


def short_name(name: str) -> str:
    name = _SUFFIX.sub("", name.strip())
    return name if len(name) <= 60 else name[:59] + "…"


def parse_rows(data: dict[str, Any], industry: str) -> list[dict[str, Any]]:
    """SPARQL JSON -> company rows. Drops rows without an http(s) website or an English-ish label."""
    rows = []
    for b in data["results"]["bindings"]:
        v = {k: x["value"] for k, x in b.items()}
        site, name = v.get("site", ""), v.get("coLabel", "")
        qid = v["co"].rsplit("/", 1)[-1]
        if not re.match(r"https?://\S+$", site) or not name or name == qid:
            continue
        emp = _num(v.get("emp"))
        hq = v.get("hqLabel") or ""
        rows.append({"qid": qid, "country": v["country"].rsplit("/", 1)[-1], "name": short_name(name),
                     "website": site, "industry": industry, "employees": int(emp) if emp is not None else None,
                     "revenue": _num(v.get("rev")), "sitelinks": int(_num(v.get("sl")) or 0),
                     "hq_city": "" if re.fullmatch(r"Q\d+", hq) else hq})
    return rows


def assemble(rows: list[dict[str, Any]], countries: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Every country key present; companies deduped by QID per country (first row wins), ranked, top TOP_N."""
    by_qid = {c["qid"]: k for k, c in countries.items() if c.get("qid")}
    by_qid.update(COUNTRY_ALIASES)
    out = {k: {"name": c["name"], "iso2": c["iso2"], "companies": []} for k, c in countries.items()}
    seen: dict[str, set[str]] = {k: set() for k in countries}
    for r in rows:
        key = by_qid.get(r["country"])
        if key is None or r["qid"] in seen[key]:
            continue
        seen[key].add(r["qid"])
        out[key]["companies"].append(r)
    for c in out.values():
        # shortcut: revenue is compared raw across currencies (only breaks ties among companies without headcount)
        c["companies"].sort(key=lambda r: (r["employees"] or -1, r["revenue"] or -1, r["sitelinks"], r["name"]),
                            reverse=True)
        c["companies"] = [{k: r[k] for k in ("name", "website", "industry", "employees", "hq_city")}
                          for r in c["companies"][:TOP_N]]
    return out


def build_world(timeout: float = 90) -> dict[str, Any]:
    """Query Wikidata for every industry group. Raises on any failure (so refresh keeps the old file)."""
    def one(item: tuple[str, list[str]]) -> list[dict[str, Any]]:
        industry, qids = item
        t = time.monotonic()
        r = net.get(ENDPOINT, params={"query": QUERY % " ".join("wd:" + q for q in qids), "format": "json"},
                    headers={"User-Agent": UA, "Accept": "application/sparql-results+json"}, timeout=(10, timeout))
        r.raise_for_status()
        got = parse_rows(r.json(), industry)
        log.info("world: %-18s %5d companies in %.1fs", industry, len(got), time.monotonic() - t)
        return got

    # 3 parallel queries: under WDQS's 5-per-IP limit; map() keeps INDUSTRIES order for dedupe priority
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [r for got in pool.map(one, INDUSTRIES.items()) for r in got]
    return {"version": 1, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "Wikidata (CC0)", "countries": assemble(rows, load_countries())}


def refresh(meta: dict[str, Any], force: bool = False) -> bool:
    """Rebuild state/world.json at most every REFRESH_DAYS. Returns True if rebuilt; failures keep the old file."""
    last = meta.get("world_at")
    if not last and WORLD_FILE.exists():  # a file built earlier (e.g. committed) counts too
        try:
            last = json.loads(WORLD_FILE.read_text(encoding="utf-8")).get("built_at")
        except (OSError, ValueError):
            last = None
    if not force and last and WORLD_FILE.exists() and \
            datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(days=REFRESH_DAYS):
        return False
    try:
        world = build_world()
    except (net.requests.RequestException, ValueError, KeyError) as e:
        log.warning("world: refresh failed, keeping old file: %s", e)
        return False
    tmp = WORLD_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(world, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(WORLD_FILE)
    meta["world_at"] = world["built_at"]
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = time.monotonic()
    if not refresh({}, force=True):
        raise SystemExit("world: build failed")
    world = json.loads(WORLD_FILE.read_text(encoding="utf-8"))
    conts = {k: c.get("continent") or "?" for k, c in load_countries().items()}
    stats: dict[str, list[int]] = {}
    for k, c in world["countries"].items():
        s = stats.setdefault(conts[k], [0, 0, 0])
        s[0] += 1
        s[1] += bool(c["companies"])
        s[2] += len(c["companies"])
    print(f"built in {time.monotonic() - t:.0f}s, {WORLD_FILE.stat().st_size / 1024:.0f} KiB")
    print(f"{'continent':15} countries  with>=1  companies")
    for cont, (n, hit, tot) in sorted(stats.items()):
        print(f"{cont:15} {n:9} {hit:8} {tot:10}")
    n, hit, tot = (sum(s[i] for s in stats.values()) for i in range(3))
    print(f"{'TOTAL':15} {n:9} {hit:8} {tot:10}")


if __name__ == "__main__":
    main()
