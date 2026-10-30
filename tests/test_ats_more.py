"""Workable, Recruitee, BambooHR and Teamtailor parsing, from small recorded responses (no network)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src import discover, tracking
from src.learn import ats_from_url
from src.sources import ats
from src.sources.base import FetchContext

TODAY = datetime.now(timezone.utc).date().isoformat()
OLD = (datetime.now(timezone.utc) - timedelta(days=400)).date().isoformat()

WORKABLE = {
    "name": "Hugging Face",
    "jobs": [
        {"title": "Site Reliability Engineer", "shortcode": "81B46579FE", "telecommuting": True,
         "published_on": TODAY, "created_at": TODAY, "country": "France", "city": "Paris", "state": "Île-de-France",
         "locations": [{"country": "France", "countryCode": "FR", "city": "Paris", "region": "Île-de-France", "hidden": False}],
         "description": "<p>Run <b>Kubernetes</b> clusters.</p>"},
        {"title": "Old SRE", "shortcode": "OLD0000000", "published_on": OLD, "locations": [], "description": ""},
    ],
}

RECRUITEE = {"offers": [{
    "title": "DevOps Engineer", "slug": "devops-engineer-3", "status": "published", "company_name": "bunq",
    "published_at": f"{TODAY} 13:21:58 UTC", "created_at": f"{TODAY} 12:08:13 UTC", "close_at": None,
    "careers_url": "https://careers.bunq.com/o/devops-engineer-3", "remote": False,
    "locations": [{"id": 1, "name": "Amsterdam", "city": "Amsterdam", "country": "Netherlands"}],
    "location": "Amsterdam, Noord-Holland, Netherlands",
    "salary": {"min": 60000, "max": 80000, "currency": "EUR", "period": "year"},
    "description": "<p>Terraform and AWS.</p>", "requirements": "<ul><li>2 years Linux</li></ul>",
}]}

BAMBOO_LIST = {"meta": {"totalCount": 2}, "result": [
    {"id": "25", "jobOpeningName": "Platform Engineer", "location": {"city": None, "state": None},
     "atsLocation": {"country": "Germany", "state": None, "city": None}, "isRemote": None, "locationType": "1"},
    {"id": "26", "jobOpeningName": "Account Executive", "location": {"city": "Hamburg", "state": None},
     "atsLocation": {}, "locationType": "0"},
]}
BAMBOO_DETAIL = {"meta": {}, "result": {"jobOpening": {
    "jobOpeningName": "Platform Engineer", "jobOpeningStatus": "Open", "datePosted": TODAY, "compensation": None,
    "description": "<p>Kubermatic builds <b>Kubernetes</b> platforms.</p>",
}}}

TEAMTAILOR_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
  <channel>
    <title>Spacelift</title>
    <item>
      <title>DevOps Customer Support Engineer</title>
      <description>&lt;p&gt;Help users run &lt;strong&gt;Terraform&lt;/strong&gt;.&lt;/p&gt;</description>
      <pubDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y 09:33:49 +0200")}</pubDate>
      <link>https://careers.spacelift.io/jobs/8546114-devops-customer-support-engineer</link>
      <remoteStatus>fully</remoteStatus>
      <company_name>Spacelift</company_name>
      <tt:locations><tt:location><tt:name>Remote Europe</tt:name><tt:country>Poland</tt:country></tt:location></tt:locations>
    </item>
    <item>
      <title>Marketing Director</title><description></description>
      <pubDate>{datetime.now(timezone.utc).strftime("%a, %d %b %Y 09:33:49 +0200")}</pubDate>
      <link>https://careers.spacelift.io/jobs/1-marketing-director</link>
    </item>
  </channel>
</rss>""".encode()


class _Resp:
    def __init__(self, content: bytes, status: int = 200):
        self.content, self.status_code, self.ok = content, status, status < 400

    def raise_for_status(self) -> None:
        assert self.ok


@pytest.fixture()
def ctx(prefs) -> FetchContext:
    c = FetchContext(prefs, title_ok=lambda t: "marketing" not in t.lower() and "account" not in t.lower())
    c.location_ok = lambda loc: True  # parsing tests, not geography
    return c


def test_workable(monkeypatch, ctx):
    monkeypatch.setattr(ats.net, "get_json", lambda url, **kw: WORKABLE)
    [job] = ats.workable({"slug": "huggingface"}, ctx)
    assert job.title == "Site Reliability Engineer" and job.company == "Hugging Face"
    assert job.url == "https://apply.workable.com/huggingface/j/81B46579FE/"
    assert job.location == "Paris, Île-de-France, France" and job.remote and job.source == "workable"
    assert "Kubernetes" in job.description and job.posted_at.startswith(TODAY)


def test_recruitee(monkeypatch, ctx):
    monkeypatch.setattr(ats.net, "get_json", lambda url, **kw: RECRUITEE)
    [job] = ats.recruitee({"slug": "bunq", "name": "bunq"}, ctx)
    assert job.url == "https://bunq.recruitee.com/o/devops-engineer-3"
    assert job.alt_urls == ["https://careers.bunq.com/o/devops-engineer-3"]
    assert job.location == "Amsterdam, Netherlands" and job.posted_at == f"{TODAY}T13:21:58+00:00"
    assert "Terraform" in job.description and "2 years Linux" in job.description
    assert job.salary == "EUR 60000-80000 / year"


def test_bamboohr(monkeypatch, ctx):
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return BAMBOO_DETAIL if url.endswith("/detail") else BAMBOO_LIST
    monkeypatch.setattr(ats.net, "get_json", fake)
    [job] = ats.bamboohr({"slug": "kubermatic", "name": "Kubermatic"}, ctx)
    assert job.url == "https://kubermatic.bamboohr.com/careers/25" and job.location == "Germany"
    assert job.remote and "Kubernetes" in job.description and job.posted_at.startswith(TODAY)
    assert calls == ["https://kubermatic.bamboohr.com/careers/list", "https://kubermatic.bamboohr.com/careers/25/detail"]


def test_teamtailor(monkeypatch, ctx):
    monkeypatch.setattr(ats.net, "get", lambda url, **kw: _Resp(TEAMTAILOR_RSS))
    [job] = ats.teamtailor({"slug": "spacelift"}, ctx)
    assert job.company == "Spacelift" and job.location == "Remote Europe" and job.remote
    assert job.url.endswith("/8546114-devops-customer-support-engineer") and "Terraform" in job.description
    assert job.posted_at is not None


def test_new_fetchers_are_registered():
    from src.sources.base import REGISTRY
    assert {"workable", "recruitee", "bamboohr", "teamtailor"} <= set(REGISTRY)


@pytest.mark.parametrize("url,expected", [
    ("https://apply.workable.com/huggingface/j/81B46579FE/", {"ats": "workable", "slug": "huggingface"}),
    ("https://apply.workable.com/j/81B46579FE", None),
    ("https://bunq.recruitee.com/o/devops-engineer-3", {"ats": "recruitee", "slug": "bunq"}),
    ("https://kubermatic.bamboohr.com/careers/25", {"ats": "bamboohr", "slug": "kubermatic"}),
    ("https://polestar.teamtailor.com/jobs/8476851-x", {"ats": "teamtailor", "slug": "polestar"}),
    ("https://www.teamtailor.com/en/", None),
])
def test_learn_new_ats_urls(url, expected):
    assert ats_from_url(url) == expected


def test_closed_check_new_ats(monkeypatch):
    seen = []

    class R:
        status_code, ok = 404, False

        def json(self):
            return {}
    monkeypatch.setattr(tracking.net, "get", lambda url, **kw: seen.append(url) or R())
    assert tracking._ats_check("https://apply.workable.com/skroutz/j/00C85BAC46/") is False
    assert tracking._ats_check("https://bunq.recruitee.com/o/devops-engineer-3") is False
    assert tracking._ats_check("https://kubermatic.bamboohr.com/careers/25") is False
    assert seen == ["https://apply.workable.com/api/v2/accounts/skroutz/jobs/00C85BAC46",
                    "https://bunq.recruitee.com/api/offers/devops-engineer-3",
                    "https://kubermatic.bamboohr.com/careers/25/detail"]


@pytest.mark.parametrize("name,text,ok", [
    ("Hugging Face", "Hugging Face", True),
    ("Grafana Labs", "Grafana", True),
    ("Fly.io", "Fly.io is hiring", True),
    ("dbt Labs", "DBT Capital", False),   # short core name must match in full
    ("Gan.ai", "GAN", False),
    ("Walmart", "IT Security Engineer at our London office", False),  # BambooHR demo tenant
])
def test_discover_name_guard(name, text, ok):
    assert discover.names_company(name, text) is ok
