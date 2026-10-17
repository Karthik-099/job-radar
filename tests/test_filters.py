from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.filters import JobFilter, detect_visa, enrich, extract_min_years, is_target_company
from src.models import Job

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


def make(title="DevOps Engineer", company="Acme", location="Bengaluru, Karnataka", desc="", days_old=1, **kw) -> Job:
    posted = (NOW - timedelta(days=days_old)).isoformat() if days_old is not None else None
    return Job(title=title, company=company, location=location, url="https://x.test/1", source="t",
               description=desc, posted_at=posted, **kw)


def check(prefs, job):
    return JobFilter(prefs, now=NOW).reason(enrich(job, prefs))


@pytest.mark.parametrize("title", ["DevOps Engineer", "Site Reliability Engineer", "SRE II", "Platform Engineer",
                                   "Cloud Engineer - AWS", "Kubernetes Engineer", "MLOps Engineer", "Associate DevOps Engineer"])
def test_target_titles_pass(prefs, title):
    assert check(prefs, make(title=title)) is None


@pytest.mark.parametrize("title", ["Senior DevOps Engineer", "Sr. SRE", "Staff Platform Engineer", "DevOps Lead",
                                   "Principal Cloud Engineer", "DevOps Manager", "DevOps Architect", "SRE III", "Infrastructure Engineer AVP", "Site Reliability Engineer 3", "DEVOPS ENGINEER L4", "Site Reliability Engineer - 4",
                                   "DevOps Intern"])
def test_senior_titles_excluded(prefs, title):
    assert check(prefs, make(title=title)) == "title_excluded"


def test_non_target_title(prefs):
    assert check(prefs, make(title="Java Backend Developer")) == "title_not_target"


@pytest.mark.parametrize("text,expected", [
    ("You have 5+ years of experience with Kubernetes.", 5),
    ("Experience: 2-4 years in DevOps", 2),
    ("Minimum of 3 yrs hands-on experience", 3),
    ("We are a company with 25 years in business.", None),
    ("Founded 10 years ago. 1+ years of experience required.", 1),
    ("3+ years experience required. 7+ years experience preferred.", 3),
    ("", None),
])
def test_extract_min_years(text, expected):
    assert extract_min_years(text) == expected


def test_structured_experience_wins():
    assert extract_min_years("8+ years of experience", structured="0-3 Yrs") == 0


def test_experience_filter(prefs):
    assert check(prefs, make(desc="Requires 5+ years of experience in cloud.")) == "too_much_experience"
    assert check(prefs, make(desc="1-3 years of experience in cloud.")) is None


def test_recency(prefs):
    assert check(prefs, make(days_old=45)) == "too_old"
    assert check(prefs, make(days_old=None)) is None


def test_abroad_rules(prefs):
    # Listed country: kept.
    assert check(prefs, make(location="Berlin, Germany")) is None
    # Unlisted country without sponsorship mention: dropped.
    assert check(prefs, make(location="Austin, TX")) == "abroad_country_not_listed"
    # Unlisted country, but sponsorship offered: kept.
    assert check(prefs, make(location="Austin, TX", desc="Visa sponsorship is available.")) is None
    # Listed country, sponsorship explicitly refused: dropped.
    assert check(prefs, make(location="London, UK", desc="We do not offer visa sponsorship.")) == "abroad_no_sponsorship"


def test_remote_scopes(prefs):
    job = enrich(make(location="Remote"), prefs)
    assert job.region == "remote"
    job = enrich(make(location="Remote - US"), prefs)
    assert job.region == "abroad" and job.country == "usa"
    job = enrich(make(location="Remote, APAC"), prefs)
    assert job.region == "remote"


def test_description_exclude(prefs):
    assert check(prefs, make(desc="Active security clearance required.")) == "description_excluded"


def test_visa_detection():
    assert detect_visa("Relocation assistance provided for the right candidate.") is True
    assert detect_visa("Unfortunately we cannot sponsor visas.") is False
    assert detect_visa("Great team.") is None


def test_target_company(prefs):
    assert is_target_company("Razorpay Software Pvt Ltd", prefs.target_companies)
    assert is_target_company("Grafana Labs", prefs.target_companies)
    assert not is_target_company("Credera", prefs.target_companies)


@pytest.mark.parametrize("title", ["BizOps Engineer II", "Cloud Operations Engineer", "Software Engineer, Infrastructure",
                                   "AWS DevOps Engineer", "Azure Engineer", "Linux System Administrator",
                                   "Systems Engineer - Linux", "Infrastructure Automation Engineer", "OpenShift Engineer",
                                   "AI Infrastructure Engineer", "Developer Platform Engineer", "CI/CD Engineer"])
def test_expanded_titles_pass(prefs, title):
    assert check(prefs, make(title=title)) is None


@pytest.mark.parametrize("title", ["Cloud Sales Specialist", "Platform Marketing Lead", "Java AWS Developer", ".NET cloud Developer",
                                   "Systems Engineer", "Frontend Developer"])
def test_expanded_titles_still_reject(prefs, title):
    assert check(prefs, make(title=title)) is not None


def test_city_priority(prefs):
    from src.recommend import rank
    def prio(loc):
        return rank(enrich(make(location=loc, desc="Kubernetes Terraform AWS"), prefs), prefs).apply_priority
    assert prio("Bengaluru, Karnataka") > prio("Pune, Maharashtra") > prio("Chennai, Tamil Nadu") > prio("Remote (Worldwide)")


def test_unlisted_country_now_classified(prefs):
    job = enrich(make(location="Monterrey, Mexico"), prefs)
    assert job.region == "abroad" and job.country == "mexico"


def test_company_exclude(prefs):
    assert check(prefs, make(company="TRIDENT ACADEMY")) == "company_excluded"
    assert check(prefs, make(company="Cisco")) is None


@pytest.mark.parametrize("title", ["DEVOPS ENGINEER L3", "Engineer II, DevOps Engineering", "Site Reliability Engineer II"])
def test_junior_levels_pass(prefs, title):
    assert check(prefs, make(title=title)) is None


@pytest.mark.parametrize("text,flag", [
    ("You speak fluent German and English.", "local_language"),
    ("Sehr gute Deutschkenntnisse in Wort und Schrift.", "local_language"),
    ("Dutch is mandatory for this role.", "local_language"),
    ("German is a plus but not required.", None),
    ("Strong communication skills in English.", None),
])
def test_language_requirement(text, flag):
    from src.filters import language_flags
    assert (flag in language_flags(text)) if flag else language_flags(text) == []


def test_non_english_jd():
    from src.filters import language_flags
    de = ("Wir suchen für unser Team in Berlin einen DevOps Engineer. Sie arbeiten mit Kubernetes und Terraform "
          "und unterstützen die Entwickler bei der Automatisierung. Wir bieten Ihnen ein tolles Team und flexible "
          "Arbeitszeiten, für die Sie mit uns gemeinsam neue Ideen umsetzen und auf Augenhöhe arbeiten.")
    assert "non_english" in language_flags(de)


def test_local_language_dropped_abroad(prefs):
    assert check(prefs, make(location="Berlin, Germany", desc="Fluent German is required.")) == "needs_local_language"
    # India jobs are never language-checked.
    assert check(prefs, make(desc="Fluent German is required.")) is None


@pytest.mark.parametrize("text,expected", [
    ("Apply by 30th October 2026.", "2026-10-30"),
    ("Last date to apply: 25/10/2026", "2026-10-25"),
    ("Application deadline - Nov 5, 2026 at 5pm", "2026-11-05"),
    ("Closing date: 2019-01-01", None),          # far in the past: ignored as noise
    ("We close deals fast.", None),
])
def test_extract_deadline(text, expected):
    from src.filters import extract_deadline
    got = extract_deadline(text, now=NOW)
    assert (got or "")[:10] == (expected or "")


def test_expired_jobs_dropped(prefs):
    assert check(prefs, make(desc="Apply by 15 October 2026.")) == "expired"
    assert check(prefs, make(desc="Apply by 30 October 2026.")) is None
