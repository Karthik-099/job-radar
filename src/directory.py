"""Company directory: thousands of potential employers by industry, plus universities/research/government bodies.

Free sources only, no page scraping, and never any request to linkedin.com:
  1. Wikidata SPARQL (CC0): one query per category (companies whose industry P452 maps to it, with an official website
     P856, not dissolved P576), ranked by employees (P1128) then Wikipedia sitelinks, capped per category; one extra
     India query over all industries; four institution queries (universities, research institutes, government science/
     tech bodies, India). LinkedIn comes only from P4264 -> https://www.linkedin.com/company/<id>/.
  2. Y Combinator directory via the yc-oss JSON mirror (Active/Public companies).
  3. companies.yaml: every polled career board, marked tracked with its public board URL.
Merged by registrable website domain, then by normalised name. Written to state/directory.json at most every
REFRESH_DAYS; network part has a BUDGET_S deadline and keeps whatever finished (skips are logged).
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import requests
import yaml

from . import net
from .config import ROOT, STATE_DIR
from .models import normalize_company
from .world import ENDPOINT, UA, _num, load_countries, short_name

log = logging.getLogger(__name__)

DIRECTORY_FILE = STATE_DIR / "directory.json"
COMPANIES_FILE = ROOT / "companies.yaml"
YC_URL = "https://yc-oss.github.io/api/companies/all.json"
REFRESH_DAYS = 30
BUDGET_S = 150
CAT_LIMIT = 2000  # companies per category query (India query is uncapped up to IN_LIMIT)
IN_LIMIT = 5000
LI_PREFIX = "https://www.linkedin.com/company/"
LI_PERSON = "https://www.linkedin.com/in/"
MAX_FOUNDERS = 4

# category label -> Wikidata P452 industry values (English labels checked on Wikidata, Oct 2026)
CATEGORY_INDUSTRIES: dict[str, list[str]] = {
    "Cloud & Infrastructure": ["Q483639", "Q671224", "Q5892272", "Q1481411"],  # cloud, data center, web hosting, ITSM
    "AI & Machine Learning": ["Q11660", "Q2539", "Q170978"],  # AI, machine learning, robotics
    "Developer Tools": ["Q638608", "Q112165968", "Q386275"],  # software development, computer programming, web dev
    "Cybersecurity": ["Q3510521", "Q17233037", "Q189900", "Q5276156"],  # computer/cyber/information/digital security
    "Data & Analytics": ["Q3353185", "Q485396", "Q97466014", "Q1988917", "Q2374463", "Q858810", "Q8513", "Q5001888"],
    "Enterprise SaaS": ["Q1254596", "Q880371", "Q112165934", "Q23931362", "Q1323528"],  # SaaS, software ind., legal tech
    "Fintech & Banking": ["Q16319025", "Q100269062", "Q837171", "Q806718", "Q57774188", "Q22687", "Q43015",
                          "Q29584334", "Q3435731", "Q43183", "Q2518196", "Q348303", "Q14864997", "Q2111088"],
    "Healthcare & Healthtech": ["Q31207", "Q15067276", "Q63383285", "Q1519843", "Q10910281", "Q5276090"],
    "Biotech & Pharma": ["Q7108", "Q12303494", "Q507443"],
    "Space & Aerospace": ["Q2729047", "Q3477363", "Q1875606"],  # space industry, aerospace industry, aircraft industry
    "Automotive & Mobility": ["Q190117", "Q786820", "Q3477381", "Q122198663", "Q27973"],
    "E-commerce & Retail": ["Q484847", "Q126793", "Q331814"],
    "Gaming & Media": ["Q941594", "Q1368944", "Q75", "Q56611700", "Q56611639", "Q11033", "Q173799", "Q19595701",
                       "Q15078788", "Q1415395"],  # games, Internet (consumer web), media, entertainment, broadcasting, film
    "Telecom & Networking": ["Q2401742", "Q418", "Q56598901", "Q1301371"],
    "Semiconductors & Hardware": ["Q2986369", "Q132898730", "Q11650", "Q5358497", "Q73768396", "Q1067601", "Q184199"],
    "Edtech": ["Q1068473", "Q182250"],
    "Logistics & Supply Chain": ["Q177777", "Q651658", "Q1807108", "Q867147", "Q492886", "Q2234623"],
    "Energy & Climate": ["Q2151621", "Q12705", "Q2316331", "Q1341477", "Q383973", "Q192127", "Q99529212", "Q862571"],
    "IT Services & Consulting": ["Q11661", "Q1540863", "Q110702998", "Q112165970", "Q5268834", "Q11492876",
                                 "Q21157865", "Q11016", "Q63769412", "Q23699878", "Q130286", "Q112166041"],
}
CATEGORIES = list(CATEGORY_INDUSTRIES) + ["Other"]
INDUSTRY_CATEGORY = {q: cat for cat, qs in CATEGORY_INDUSTRIES.items() for q in qs}

# YC industries / subindustries / tags -> category
YC_CATEGORY = {
    "Infrastructure": "Cloud & Infrastructure", "Cloud Computing": "Cloud & Infrastructure",
    "AI": "AI & Machine Learning", "Artificial Intelligence": "AI & Machine Learning", "Generative AI":
    "AI & Machine Learning", "Machine Learning": "AI & Machine Learning", "Deep Learning": "AI & Machine Learning",
    "Computer Vision": "AI & Machine Learning", "Conversational AI": "AI & Machine Learning", "AIOps":
    "AI & Machine Learning", "Robotics": "AI & Machine Learning", "AI Assistant": "AI & Machine Learning",
    "Engineering, Product and Design": "Developer Tools", "Developer Tools": "Developer Tools",
    "Open Source": "Developer Tools", "API": "Developer Tools", "DevOps": "Developer Tools",
    "Security": "Cybersecurity", "Cybersecurity": "Cybersecurity",
    "Analytics": "Data & Analytics", "Data Engineering": "Data & Analytics", "Big Data": "Data & Analytics",
    "B2B": "Enterprise SaaS", "SaaS": "Enterprise SaaS", "Productivity": "Enterprise SaaS", "Marketing":
    "Enterprise SaaS", "Sales": "Enterprise SaaS", "Operations": "Enterprise SaaS", "Human Resources":
    "Enterprise SaaS", "Recruiting and Talent": "Enterprise SaaS", "Legal": "Enterprise SaaS", "Office Management":
    "Enterprise SaaS", "Finance and Accounting": "Enterprise SaaS", "Enterprise Software": "Enterprise SaaS",
    "Fintech": "Fintech & Banking", "Payments": "Fintech & Banking", "Banking and Exchange": "Fintech & Banking",
    "Insurance": "Fintech & Banking", "Credit and Lending": "Fintech & Banking", "Consumer Finance":
    "Fintech & Banking", "Asset Management": "Fintech & Banking", "Crypto / Web3": "Fintech & Banking",
    "Healthcare": "Healthcare & Healthtech", "Healthcare IT": "Healthcare & Healthtech", "Healthcare Services":
    "Healthcare & Healthtech", "Consumer Health and Wellness": "Healthcare & Healthtech", "Medical Devices":
    "Healthcare & Healthtech", "Diagnostics": "Healthcare & Healthtech", "Digital Health": "Healthcare & Healthtech",
    "Therapeutics": "Biotech & Pharma", "Drug Discovery and Delivery": "Biotech & Pharma", "Industrial Bio":
    "Biotech & Pharma", "Biotech": "Biotech & Pharma",
    "Aviation and Space": "Space & Aerospace", "Drones": "Space & Aerospace", "Aerospace": "Space & Aerospace",
    "Automotive": "Automotive & Mobility", "Transportation Services": "Automotive & Mobility",
    "Retail": "E-commerce & Retail", "Apparel and Cosmetics": "E-commerce & Retail", "E-commerce":
    "E-commerce & Retail", "Marketplace": "E-commerce & Retail", "Home and Personal": "E-commerce & Retail",
    "Gaming": "Gaming & Media", "Content": "Gaming & Media", "Social": "Gaming & Media",
    "Virtual and Augmented Reality": "Gaming & Media", "Video": "Gaming & Media",
    "Telecommunications": "Telecom & Networking", "Networking": "Telecom & Networking",
    "Consumer Electronics": "Semiconductors & Hardware", "Manufacturing and Robotics": "Semiconductors & Hardware",
    "Hardware": "Semiconductors & Hardware", "Semiconductors": "Semiconductors & Hardware",
    "Education": "Edtech", "AI-Enhanced Learning": "Edtech",
    "Supply Chain and Logistics": "Logistics & Supply Chain", "Logistics": "Logistics & Supply Chain",
    "Supply Chain": "Logistics & Supply Chain",
    "Energy": "Energy & Climate", "Climate": "Energy & Climate",
}

COMPANY_QUERY = """SELECT ?co ?coLabel ?site ?emp ?sl ?ind ?li ?logo ?iso ?hqLabel WHERE {
  { SELECT ?co (SAMPLE(?s) AS ?site) (COALESCE(MAX(?e), -1) AS ?emp) (MAX(?l) AS ?sl) WHERE {
      VALUES ?i { %s }
      ?co wdt:P452 ?i ; wdt:P856 ?s ; wikibase:sitelinks ?l . %s
      FILTER NOT EXISTS { ?co wdt:P576 [] }
      OPTIONAL { ?co wdt:P1128 ?e FILTER(isNumeric(?e)) }
    } GROUP BY ?co ORDER BY DESC(?emp) DESC(?sl) LIMIT %d }
  OPTIONAL { ?co wdt:P452 ?ind } OPTIONAL { ?co wdt:P4264 ?li } OPTIONAL { ?co wdt:P154 ?logo }
  OPTIONAL { ?co wdt:P17/wdt:P297 ?iso } OPTIONAL { ?co wdt:P159 ?hq }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". }
}"""

# founders (P112) that Wikidata lists with a LinkedIn profile ID (P6634); joined to companies by QID afterwards.
# Starting from P6634 keeps it ~20 s; filtering by industry first times out.
FOUNDERS_QUERY = """SELECT ?co ?fLabel ?li WHERE {
  ?f wdt:P6634 ?li . ?co wdt:P112 ?f ; wdt:P856 [] .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". }
}"""

# institution type -> WHERE fragment selecting ?co; the closure (P279*) on small roots stays well under the timeout
INST_SELECT = {
    "university": "?t wdt:P279* wd:Q3918 . ?co wdt:P31 ?t .",
    "research": "?t wdt:P279* wd:Q31855 . ?co wdt:P31 ?t .",
    # space agencies, HPC centres, government agencies whose field of work (P101) is science/tech/IT/research,
    # plus Indian bodies named explicitly (NIC, DRDO, C-DAC, ISRO)
    "government": "{ VALUES ?t { wd:Q17505024 wd:Q25345660 } ?co wdt:P31 ?t . } UNION "
                  "{ VALUES ?f { wd:Q336 wd:Q11016 wd:Q11661 wd:Q21198 wd:Q42240 } ?co wdt:P31 wd:Q327333 ; "
                  "wdt:P101 ?f . } UNION { VALUES ?co { wd:Q313019 wd:Q1154393 wd:Q3519908 wd:Q229058 } }",
    # India: universities (incl. deemed / tech univ.), Institutes of National Importance (IITs/NITs/IIITs),
    # engineering schools, research institutes and centres
    "india": "{ ?t wdt:P279* wd:Q3918 . ?co wdt:P31 ?t . } UNION { VALUES ?t { wd:Q47531586 wd:Q1663017 wd:Q31855 "
             "wd:Q7315155 } ?co wdt:P31 ?t . } ?co wdt:P17 wd:Q668 .",
}
INST_LIMIT = {"university": 1500, "research": 800, "government": 400, "india": 700}
INST_QUERY = """SELECT ?co ?coLabel ?site ?sl ?st ?li ?logo ?iso ?cityLabel ?ty WHERE {
  { SELECT ?co (SAMPLE(?s) AS ?site) (MAX(?l) AS ?sl) (MAX(?n) AS ?st) (SAMPLE(?t) AS ?ty) WHERE {
      %s
      ?co wdt:P856 ?s ; wikibase:sitelinks ?l .
      FILTER NOT EXISTS { ?co wdt:P576 [] }
      OPTIONAL { ?co wdt:P2196 ?n FILTER(isNumeric(?n)) }
    } GROUP BY ?co ORDER BY DESC(?sl) LIMIT %d }
  OPTIONAL { ?co wdt:P4264 ?li } OPTIONAL { ?co wdt:P154 ?logo }
  OPTIONAL { ?co wdt:P17/wdt:P297 ?iso } OPTIONAL { ?co wdt:P131 ?city }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,mul". }
}"""

CAREERS = {
    "greenhouse": "https://job-boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
    "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://jobs.smartrecruiters.com/{}",
    "workable": "https://apply.workable.com/{}/", "recruitee": "https://{}.recruitee.com/",
    "bamboohr": "https://{}.bamboohr.com/careers", "teamtailor": "https://{}.teamtailor.com/jobs",
}
# shortcut: 2-label public suffixes are a fixed list, not the PSL; add one here if two firms wrongly merge
_SLD = {"co", "com", "net", "org", "ac", "gov", "edu", "or", "ne", "go", "res", "nic", "gen", "firm", "ind", "ltd", "plc"}
# shared hosts where the path, not the domain, identifies the company
_SHARED = {"google.com", "facebook.com", "github.io", "github.com", "wordpress.com", "blogspot.com", "medium.com",
           "wixsite.com", "notion.site", "twitter.com", "x.com", "instagram.com", "youtube.com", "linktr.ee"}
_COUNTRY_ALIASES = {"usa": "US", "united states": "US", "united states of america": "US", "uk": "GB",
                    "england": "GB", "south korea": "KR", "russia": "RU", "vietnam": "VN", "taiwan": "TW",
                    "iran": "IR", "turkey": "TR", "czech republic": "CZ", "the netherlands": "NL", "uae": "AE"}


def _http(url: str | None) -> str:
    url = (url or "").strip()
    if not re.match(r"https?://[^\s/]+\.[^\s]+$", url):
        return ""
    host = urlsplit(url).netloc.lower().split(":")[0]
    return "" if host == "linkedin.com" or host.endswith(".linkedin.com") else url


def domain_key(url: str) -> str:
    """Registrable domain ('https://www.cloud.google.co.uk/x' -> 'google.co.uk'); shared hosts keep their path."""
    parts = urlsplit(url.strip().lower())
    host = parts.netloc.split("@")[-1].split(":")[0].removeprefix("www.")
    if not host:
        return ""
    labels = host.split(".")
    n = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SLD else 2
    reg = ".".join(labels[-n:])
    if reg in _SHARED:
        path = parts.path.strip("/").split("/")[0]
        return f"{host}/{path}" if path else host
    return reg


def logo_url(commons: str | None) -> str:
    """P154 value (Commons Special:FilePath URL) -> 96 px thumbnail URL."""
    if not commons:
        return ""
    name = unquote(commons.rsplit("/", 1)[-1])
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name)}?width=96"


def linkedin_url(li_id: str | None) -> str:
    li_id = (li_id or "").strip().strip("/")
    return f"{LI_PREFIX}{quote(li_id)}/" if re.fullmatch(r"[\w.%&'-]+", li_id) else ""


def person_url(li_id: str | None) -> str:
    li_id = (li_id or "").strip().strip("/")
    return f"{LI_PERSON}{quote(li_id)}/" if re.fullmatch(r"[\w.%-]+", li_id) else ""


def parse_founders(data: dict[str, Any]) -> dict[str, list[list[str]]]:
    """SPARQL rows -> {company qid: [[founder name, linkedin profile url], ...]} (max MAX_FOUNDERS each)."""
    out: dict[str, list[list[str]]] = {}
    for v in _rows(data):
        name, url = _label(v.get("fLabel")), person_url(v.get("li"))
        lst = out.setdefault(v["co"], [])
        if name and url and len(lst) < MAX_FOUNDERS and all(f[1] != url for f in lst):
            lst.append([name, url])
    return {k: v for k, v in out.items() if v}


def careers_url(entry: dict[str, Any]) -> str:
    ats, slug = entry.get("ats", ""), str(entry.get("slug") or "").strip()
    if ats == "workday":
        return _http(entry.get("url"))
    return CAREERS[ats].format(slug) if ats in CAREERS and slug else ""


def _blurb(text: str | None) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= 120 else text[:119].rstrip() + "…"


def _sparql(query: str, deadline: float) -> dict[str, Any]:
    """One WDQS query, bounded by the overall deadline. One retry on 429 if Retry-After fits the budget."""
    # plain requests (not net.get): its 3 automatic retries on 504 would blow the time budget
    for attempt in range(2):
        left = deadline - time.monotonic()
        if left < 5:
            raise TimeoutError("budget exhausted")
        net._space(ENDPOINT)
        r = requests.get(ENDPOINT, params={"query": query, "format": "json"}, timeout=(10, left),
                         headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(float(r.headers.get("Retry-After") or 5), max(0, deadline - time.monotonic() - 10)))
            continue
        r.raise_for_status()
        return r.json()
    raise TimeoutError("rate limited")


def _rows(data: dict[str, Any]) -> list[dict[str, str]]:
    return [{k: x["value"].rsplit("/entity/", 1)[-1] if x.get("type") == "uri" and "/entity/" in x["value"]
             else x["value"] for k, x in b.items()} for b in data["results"]["bindings"]]


def _label(v: str | None) -> str:
    return "" if not v or re.fullmatch(r"Q\d+", v) else v


def parse_companies(data: dict[str, Any], prefer_cc: str = "") -> dict[str, dict[str, Any]]:
    """SPARQL rows (one per industry x country x ... combination) -> {qid: company}. Drops unlabelled/no-website."""
    out: dict[str, dict[str, Any]] = {}
    for v in _rows(data):
        qid, name, site = v["co"], _label(v.get("coLabel")), _http(v.get("site"))
        if not name or not site:
            continue
        c = out.get(qid)
        if c is None:
            emp = int(_num(v.get("emp")) or -1)
            c = out[qid] = {"qid": qid, "n": short_name(name), "w": site, "li": "", "lg": "", "c": [], "cc": "",
                            "ct": "", "e": emp if emp >= 0 else None, "sl": int(_num(v.get("sl")) or 0)}
        cat = INDUSTRY_CATEGORY.get(v.get("ind", ""))
        if cat and cat not in c["c"]:
            c["c"].append(cat)
        c["li"] = c["li"] or linkedin_url(v.get("li"))
        c["lg"] = c["lg"] or logo_url(v.get("logo"))
        iso = v.get("iso", "")
        if iso and (not c["cc"] or iso == prefer_cc):
            c["cc"] = iso
        c["ct"] = c["ct"] or _label(v.get("hqLabel"))
    return out


def parse_institutions(data: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for v in _rows(data):
        qid, name, site = v["co"], _label(v.get("coLabel")), _http(v.get("site"))
        if not name or not site:
            continue
        ty = kind
        if kind == "india":
            ty = "research" if v.get("ty") in ("Q31855", "Q7315155") else "university"
        c = out.get(qid)
        if c is None:
            st = _num(v.get("st"))
            c = out[qid] = {"qid": qid, "n": name, "w": site, "li": "", "lg": "", "ty": ty, "cc": "", "ct": "",
                            "s": int(st) if st else None, "sl": int(_num(v.get("sl")) or 0)}
        c["li"] = c["li"] or linkedin_url(v.get("li"))
        c["lg"] = c["lg"] or logo_url(v.get("logo"))
        iso = v.get("iso", "")
        if iso and (not c["cc"] or (kind == "india" and iso == "IN")):
            c["cc"] = iso
        c["ct"] = c["ct"] or _label(v.get("cityLabel"))
    return out


def _country_iso() -> dict[str, str]:
    m = {c["name"].lower(): c["iso2"] for c in load_countries().values() if c.get("iso2")}
    m.update({k: v for k, v in _COUNTRY_ALIASES.items()})
    return m


def parse_yc(items: list[dict[str, Any]], iso: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for x in items:
        if x.get("status") not in ("Active", "Public") or not x.get("name"):
            continue
        cats: list[str] = []
        for key in [x.get("subindustry", "").split(" -> ")[-1], *x.get("industries", []), *x.get("tags", [])]:
            cat = YC_CATEGORY.get(key)
            if cat and cat not in cats:
                cats.append(cat)
        loc = (x.get("all_locations") or "").split(";")[0].strip()
        segs = [s.strip() for s in loc.split(",") if s.strip()]
        out.append({"n": x["name"].strip(), "w": _http(x.get("website")), "li": "", "lg": _http(x.get("small_logo_thumb_url")),
                    "c": cats, "cc": iso.get(segs[-1].lower(), "") if segs else "",
                    "ct": segs[0] if len(segs) > 1 else "", "e": x.get("team_size") or None,
                    "yc": x.get("batch") or "", "h": bool(x.get("isHiring")), "b": _blurb(x.get("one_liner"))})
    return out


def parse_tracked(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"n": e["name"], "cr": careers_url(e), "t": True, "yc": e.get("yc") or "", "c": []}
            for e in cfg.get("companies", []) if e.get("name")]


def merge(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge by registrable domain, then by normalised name (only when a website doesn't contradict it).
    Earlier sources win on conflicts; empty fields are filled from later ones; categories are unioned."""
    out: list[dict[str, Any]] = []
    by_dom: dict[str, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for src in sources:
        for rec in src:
            dom = domain_key(rec.get("w") or "")
            key = normalize_company(rec["n"])
            hit = by_dom.get(dom) if dom else None
            if hit is None and key:
                cand = by_name.get(key)
                if cand is not None and (not dom or not cand.get("w") or domain_key(cand["w"]) == dom):
                    hit = cand
            if hit is None:
                hit = {"n": rec["n"], "w": "", "li": "", "lg": "", "c": [], "cc": "", "ct": "", "e": None, "yc": "",
                       "h": False, "b": "", "cr": "", "t": False, "sl": 0, "f": []}
                out.append(hit)
            for k, v in rec.items():
                if k == "c":
                    hit["c"] += [c for c in v if c not in hit["c"]]
                elif k == "e":
                    hit["e"] = hit["e"] if hit["e"] is not None else v
                elif v and not hit.get(k):
                    hit[k] = v
            if dom:
                by_dom.setdefault(domain_key(hit["w"]) or dom, hit)
                by_dom.setdefault(dom, hit)
            if key:
                by_name.setdefault(key, hit)
    return out


def finalize(companies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = []
    for c in companies:
        cats = sorted(set(c["c"]) & set(CATEGORY_INDUSTRIES), key=CATEGORIES.index)[:3]
        if not cats:
            if not c["t"]:
                continue
            cats = ["Other"]
        keep.append({"n": c["n"], "w": c["w"], "li": c["li"], "lg": c["lg"], "c": cats, "cc": c["cc"], "ct": c["ct"],
                     "e": int(c["e"]) if c["e"] is not None else None, "yc": c["yc"], "h": c["h"], "b": c["b"],
                     "cr": c["cr"], "t": c["t"], "f": c["f"], "_sl": c.get("sl", 0)})
    keep.sort(key=lambda c: (not c["t"], -(c["e"] if c["e"] is not None else -1), -c["_sl"], c["n"].lower()))
    for c in keep:
        del c["_sl"]
    return keep


def build_directory(budget: float = BUDGET_S) -> dict[str, Any]:
    """Fetch everything within `budget` seconds; failed/late queries are logged and skipped.
    Raises only if no Wikidata company query succeeded (so refresh keeps the old file)."""
    t0 = time.monotonic()
    deadline = t0 + budget
    wd = lambda qids, extra, limit: COMPANY_QUERY % (" ".join("wd:" + q for q in qids), extra, limit)  # noqa: E731
    all_q = [q for qs in CATEGORY_INDUSTRIES.values() for q in qs]
    # India first so it is never the one cut by the deadline
    jobs: list[tuple[str, str, str]] = [("co", "India", wd(all_q, "?co wdt:P17 wd:Q668 .", IN_LIMIT)),
                                        ("inst", "india", INST_QUERY % (INST_SELECT["india"], INST_LIMIT["india"])),
                                        ("f", "founders", FOUNDERS_QUERY)]
    jobs += [("co", cat, wd(qs, "", CAT_LIMIT)) for cat, qs in CATEGORY_INDUSTRIES.items()]
    jobs += [("inst", k, INST_QUERY % (INST_SELECT[k], INST_LIMIT[k])) for k in ("university", "research", "government")]

    def one(job: tuple[str, str, str]) -> dict[str, dict[str, Any]]:
        kind, name, query = job
        t = time.monotonic()
        data = _sparql(query, deadline)
        got = parse_companies(data, "IN" if name == "India" else "") if kind == "co" else \
            parse_founders(data) if kind == "f" else parse_institutions(data, name)
        log.info("directory: %-4s %-26s %5d in %4.1fs", kind, name, len(got), time.monotonic() - t)
        return got

    yc: list[dict[str, Any]] = []
    try:
        r = net.get(YC_URL, timeout=(10, 40))
        r.raise_for_status()
        yc = parse_yc(r.json(), _country_iso())
        log.info("directory: yc %d active/public companies", len(yc))
    except (requests.RequestException, ValueError) as e:
        log.warning("directory: YC directory skipped: %s", e)

    pool = ThreadPoolExecutor(max_workers=3)  # under WDQS's 5-concurrent-per-IP limit, like world.py
    futs = {pool.submit(one, j): j for j in jobs}
    wait(futs, timeout=max(0, deadline - time.monotonic()))
    pool.shutdown(wait=False, cancel_futures=True)
    cos: dict[str, dict[str, Any]] = {}
    insts: dict[str, dict[str, Any]] = {}
    founders: dict[str, list[list[str]]] = {}
    skipped = []
    for f, (kind, name, _) in futs.items():
        if not f.done() or f.cancelled() or f.exception() is not None:
            skipped.append(name)
            log.warning("directory: skipped %s %s: %s", kind, name,
                        "deadline" if not f.done() or f.cancelled() else repr(f.exception())[:200])
            continue
        if kind == "f":
            founders = f.result()
            continue
        target = cos if kind == "co" else insts
        for qid, rec in f.result().items():
            if qid in target:
                prev = target[qid]
                prev["c"] = prev.get("c", []) + [c for c in rec.get("c", []) if c not in prev.get("c", [])]
                for k, v in rec.items():
                    if v and not prev.get(k):
                        prev[k] = v
            else:
                target[qid] = rec
    if not cos:
        raise RuntimeError("directory: no Wikidata company query succeeded")
    for qid, c in cos.items():
        c["f"] = founders.get(qid, [])

    tracked = parse_tracked(yaml.safe_load(COMPANIES_FILE.read_text(encoding="utf-8")) or {})
    wd_list = sorted(cos.values(), key=lambda c: (-(c["e"] or -1), -c["sl"]))
    companies = finalize(merge([{k: v for k, v in c.items() if k != "qid"} for c in wd_list], yc, tracked))

    order = {"university": 0, "research": 1, "government": 2}
    institutions = [{k: c[k] for k in ("n", "w", "li", "lg", "ty", "cc", "ct", "s")}
                    for c in sorted(insts.values(), key=lambda c: (order[c["ty"]], -c["sl"], c["n"]))]
    log.info("directory: built in %.0fs (%d skipped)", time.monotonic() - t0, len(skipped))
    return {"version": 1, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": "Wikidata (CC0), YC directory via yc-oss, companies.yaml", "categories": CATEGORIES,
            "companies": companies, "institutions": institutions, "skipped": skipped}


def _built_at() -> tuple[str | None, int]:
    try:
        old = json.loads(DIRECTORY_FILE.read_text(encoding="utf-8"))
        return old.get("built_at"), len(old.get("companies", []))
    except (OSError, ValueError):
        return None, 0


def refresh(meta: dict[str, Any], force: bool = False) -> bool:
    """Rebuild state/directory.json at most every REFRESH_DAYS. True if rebuilt; any failure keeps the old file."""
    old_at, old_n = _built_at()
    last = meta.get("directory_at") or old_at
    if not force and last and DIRECTORY_FILE.exists() and \
            datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(days=REFRESH_DAYS):
        return False
    try:
        d = build_directory()
    except (requests.RequestException, ValueError, KeyError, RuntimeError, OSError) as e:
        log.warning("directory: refresh failed, keeping old file: %s", e)
        return False
    if len(d["companies"]) < old_n // 2:  # a mostly-failed run must not replace a good file
        log.warning("directory: only %d companies (old file %d), keeping old file", len(d["companies"]), old_n)
        return False
    tmp = DIRECTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(DIRECTORY_FILE)
    meta["directory_at"] = d["built_at"]
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = time.monotonic()
    if not refresh({}, force=True):
        raise SystemExit("directory: build failed")
    raw = DIRECTORY_FILE.read_bytes()
    d = json.loads(raw)
    cos, inst = d["companies"], d["institutions"]
    print(f"\ncompanies: {len(cos)}  (tracked {sum(c['t'] for c in cos)}, YC {sum(bool(c['yc']) for c in cos)}, "
          f"LinkedIn {sum(bool(c['li']) for c in cos)}, logo {sum(bool(c['lg']) for c in cos)}, "
          f"website {sum(bool(c['w']) for c in cos)}, founders {sum(bool(c['f']) for c in cos)})")
    cat = Counter(x for c in cos for x in c["c"])
    for k in d["categories"]:
        print(f"  {k:28} {cat[k]:6}")
    print("companies per country (top 15):", ", ".join(f"{k or '?'} {n}" for k, n in
                                                    Counter(c["cc"] for c in cos).most_common(15)))
    print(f"institutions: {len(inst)}  (LinkedIn {sum(bool(c['li']) for c in inst)}, logo {sum(bool(c['lg']) for c in inst)})")
    print("  per type:", dict(Counter(c["ty"] for c in inst)))
    print("  per country (top 15):", ", ".join(f"{k or '?'} {n}" for k, n in Counter(c["cc"] for c in inst).most_common(15)))
    print(f"skipped: {d['skipped'] or 'none'}")
    print(f"total {time.monotonic() - t:.0f}s, {len(raw) / 1e6:.2f} MB raw, {len(gzip.compress(raw)) / 1e6:.2f} MB gzipped")


if __name__ == "__main__":
    main()
