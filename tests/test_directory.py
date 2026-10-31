from __future__ import annotations

import json
import re
from pathlib import Path

from src import directory as d

FIX = json.loads((Path(__file__).parent / "vectors" / "directory_fixtures.json").read_text(encoding="utf-8"))
ISO = {"india": "IN", "usa": "US"}


def _built() -> list[dict]:
    wd = [{k: v for k, v in c.items() if k != "qid"} for c in d.parse_companies(FIX["companies"]).values()]
    tracked = d.parse_tracked({"companies": [{"name": "Supabase", "ats": "ashby", "slug": "supabase"},
                                             {"name": "Obscure Startup", "ats": "lever", "slug": "obscure"}]})
    return d.finalize(d.merge(wd, d.parse_yc(FIX["yc"], ISO), tracked))


def test_parse_companies_rows_grouped_and_cleaned():
    cos = d.parse_companies(FIX["companies"], prefer_cc="IN")
    assert set(cos) == {"Q1066085", "Q95", "Q5"}  # ftp site, unlabelled QID and linkedin.com website dropped
    inf = cos["Q1066085"]
    assert inf["n"] == "Infosys" and inf["e"] == 317240 and inf["cc"] == "IN" and inf["ct"] == "Bengaluru"
    assert inf["c"] == ["IT Services & Consulting", "Cloud & Infrastructure"]  # unmapped industry ignored
    assert inf["li"] == "https://www.linkedin.com/company/infosys/"
    assert cos["Q95"]["e"] is None and cos["Q95"]["ct"] == ""  # -1 = no headcount; unresolved HQ label dropped
    assert cos["Q5"]["e"] is None  # "unknown value" blank node, not a number


def test_parse_institutions_india_types():
    inst = d.parse_institutions(FIX["institutions"], "india")
    assert inst["Q1661242"]["ty"] == "university" and inst["Q1661242"]["s"] == 10500
    assert inst["Q948720"]["ty"] == "research" and inst["Q948720"]["li"] == ""


def test_yc_mapping_and_filters():
    yc = d.parse_yc(FIX["yc"], ISO)
    assert [c["n"] for c in yc] == ["Supabase", "Infosys"]  # Inactive dropped
    s = yc[0]
    assert s["c"] == ["Developer Tools", "Enterprise SaaS"] and s["cc"] == "US" and s["ct"] == "San Francisco"
    assert s["h"] is True and s["yc"] == "Summer 2020" and len(s["b"]) <= 120 and s["b"].endswith("…")


def test_merge_by_domain_and_name_and_sort():
    out = _built()
    names = [c["n"] for c in out]
    assert names[:2] == ["Supabase", "Obscure Startup"]  # tracked first (Supabase has employees)
    inf = next(c for c in out if c["n"] == "Infosys")  # Wikidata + YC merged by infosys.com
    assert names.count("Infosys") == 1 and inf["yc"] == "Winter 2099" and inf["e"] == 317240
    assert "Enterprise SaaS" in inf["c"] and len(inf["c"]) <= 3
    sb = out[0]
    assert sb["t"] and sb["cr"] == "https://jobs.ashbyhq.com/supabase" and sb["w"] == "https://supabase.com"
    assert out[1]["c"] == ["Other"]  # tracked, unmatched
    assert set(out[0]) == {"n", "w", "li", "lg", "c", "cc", "ct", "e", "yc", "h", "b", "cr", "t", "f"}


def test_domain_key():
    assert d.domain_key("https://www.careers.infosys.com/x") == "infosys.com"
    assert d.domain_key("http://www.bsnl.co.in/opencms/x") == "bsnl.co.in"
    assert d.domain_key("https://sites.google.com/view/acme") == "sites.google.com/view"
    assert d.domain_key("https://acme.github.io/") == "acme.github.io"


def test_careers_urls():
    cases = {"greenhouse": "https://job-boards.greenhouse.io/acme", "lever": "https://jobs.lever.co/acme",
             "ashby": "https://jobs.ashbyhq.com/acme", "smartrecruiters": "https://jobs.smartrecruiters.com/acme",
             "workable": "https://apply.workable.com/acme/", "recruitee": "https://acme.recruitee.com/",
             "bamboohr": "https://acme.bamboohr.com/careers", "teamtailor": "https://acme.teamtailor.com/jobs"}
    for ats, url in cases.items():
        assert d.careers_url({"ats": ats, "slug": "acme"}) == url
    wd = "https://acme.wd5.myworkdayjobs.com/External"
    assert d.careers_url({"ats": "workday", "url": wd}) == wd
    assert d.careers_url({"ats": "workday", "url": "javascript:alert(1)"}) == ""


def test_logo_url():
    assert d.logo_url("http://commons.wikimedia.org/wiki/Special:FilePath/Logo-SG-Soci%C3%A9t%C3%A9%20G.svg") == \
        "https://commons.wikimedia.org/wiki/Special:FilePath/Logo-SG-Soci%C3%A9t%C3%A9%20G.svg?width=96"
    assert d.logo_url("") == ""


def test_no_linkedin_urls_except_p4264():
    assert d.linkedin_url("bad id/../x") == ""
    for c in _built() + list(d.parse_institutions(FIX["institutions"], "india").values()):
        for k, v in c.items():
            if isinstance(v, str) and "linkedin" in v.lower():
                assert k == "li" and re.fullmatch(r"https://www\.linkedin\.com/company/[^/]+/", v), (k, v)
            if k in ("w", "lg", "cr") and v:
                assert v.startswith(("http://", "https://"))


def test_founders_profile_urls_only_from_p6634():
    rows = {"results": {"bindings": [
        {"co": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1"}, "fLabel": {"type": "literal", "value": "Ada"},
         "li": {"type": "literal", "value": "ada-l"}},
        {"co": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1"}, "fLabel": {"type": "literal", "value": "Ada"},
         "li": {"type": "literal", "value": "ada-l"}},
        {"co": {"type": "uri", "value": "http://www.wikidata.org/entity/Q2"}, "fLabel": {"type": "literal", "value": "Q99"},
         "li": {"type": "literal", "value": "x"}},
        {"co": {"type": "uri", "value": "http://www.wikidata.org/entity/Q3"}, "fLabel": {"type": "literal", "value": "Bob"},
         "li": {"type": "literal", "value": "../evil"}},
    ]}}
    assert d.parse_founders(rows) == {"Q1": [["Ada", "https://www.linkedin.com/in/ada-l/"]]}
    assert all(c["f"] == [] for c in _built())
