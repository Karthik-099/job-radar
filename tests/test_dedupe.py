from __future__ import annotations

from src.dedupe import dedupe, drop_seen
from src.models import Job, dedupe_key, normalize_url


def job(title="DevOps Engineer", company="Razorpay", location="Bengaluru, Karnataka, India", url="https://a.test/1",
        source="indeed", desc="") -> Job:
    return Job(title=title, company=company, location=location, url=url, source=source, description=desc)


def test_key_normalizes_company_and_city():
    assert dedupe_key("Razorpay Software Pvt Ltd", "DevOps Engineer", "Bangalore") == \
        dedupe_key("razorpay software", "devops  engineer", "Bengaluru, Karnataka")


def test_url_normalization_strips_tracking():
    assert normalize_url("https://Jobs.Lever.co/acme/123/?utm_source=x&lever-source=li") == "https://jobs.lever.co/acme/123"
    assert normalize_url("https://x.test/j?id=5&utm_medium=y") == "https://x.test/j?id=5"


def test_dedupe_same_hash_keeps_richer_and_merges_urls():
    a = job(source="indeed", url="https://in.indeed.com/viewjob?jk=1", desc="short")
    b = job(source="naukri", url="https://naukri.com/job/1", location="Bangalore", desc="a much longer description")
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].description == "a much longer description"
    assert out[0].id == a.id
    assert "https://in.indeed.com/viewjob?jk=1" in out[0].alt_urls
    assert {"src:indeed", "src:naukri"} <= set(out[0].tags)


def test_dedupe_same_url_different_title():
    a = job(title="DevOps Engineer", url="https://x.test/j/1?utm_source=a")
    b = job(title="DevOps Engineer (AWS)", url="https://x.test/j/1")
    assert len(dedupe([a, b])) == 1


def test_dedupe_distinct_jobs_survive():
    assert len(dedupe([job(), job(company="CRED", url="https://a.test/2"), job(location="Pune", url="https://a.test/3")])) == 3


def test_drop_seen_by_id_and_url():
    a, b, c = job(), job(company="CRED", url="https://x.test/seen?utm_source=z"), job(company="PhonePe", url="https://x.test/new")
    fresh = drop_seen([a, b, c], seen_ids={a.id}, seen_urls={"https://x.test/seen"})
    assert [j.company for j in fresh] == ["PhonePe"]


def test_generic_state_location_merges_into_city():
    from src.filters import enrich
    from src.config import load_preferences
    prefs = load_preferences()
    a = enrich(job(company="Mastercard", title="Site Reliability Engineer II", location="Pune, India", url="https://wd/1"), prefs)
    b = enrich(job(company="Mastercard", title="Site Reliability Engineer II", location="MH, India", url="https://appcast/2",
                   source="indeed"), prefs)
    c = enrich(job(company="Mastercard", title="Site Reliability Engineer II", location="Dublin, Ireland", url="https://wd/3"), prefs)
    out = dedupe([a, b, c])
    assert len(out) == 2
    pune = next(j for j in out if "Pune" in j.location)
    assert "https://appcast/2" in pune.alt_urls
