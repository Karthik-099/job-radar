from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.filters import enrich
from src.learn import ats_from_url
from src.match import keyword_fit
from src.models import Job
from src.recommend import apply_next, rank


@pytest.mark.parametrize("url,expected", [
    ("https://job-boards.greenhouse.io/gitlab/jobs/8867756002", {"ats": "greenhouse", "slug": "gitlab"}),
    ("https://boards.greenhouse.io/embed/job_app?for=razorpay&token=1", {"ats": "greenhouse", "slug": "razorpay"}),
    ("https://jobs.lever.co/valarian/b84391ed", {"ats": "lever", "slug": "valarian"}),
    ("https://jobs.ashbyhq.com/posthog/0be1b52c", {"ats": "ashby", "slug": "posthog"}),
    ("https://jobs.smartrecruiters.com/ServiceNow/744000155836759", {"ats": "smartrecruiters", "slug": "ServiceNow"}),
    ("https://dentsuaegis.wd3.myworkdayjobs.com/en-US/DAN_GLOBAL/job/x/DevOps_R1",
     {"ats": "workday", "url": "https://dentsuaegis.wd3.myworkdayjobs.com/DAN_GLOBAL"}),
    ("https://in.indeed.com/viewjob?jk=1", None),
])
def test_ats_from_url(url, expected):
    assert ats_from_url(url) == expected


def test_keyword_fit_rewards_overlap():
    good = keyword_fit("DevOps Engineer", "Kubernetes, Terraform, Argo CD, Prometheus, Grafana, AWS, Azure, Python, Linux", 1)
    weak = keyword_fit("DevOps Engineer", "Java, Kafka, Windows Server, PowerShell, Cisco routers", 1)
    assert good["score"] > 7 > weak["score"]
    assert "kubernetes" in good["matched_skills"] and "java" in weak["missing_skills"]
    assert keyword_fit("DevOps", "Kubernetes Terraform", 5)["level_fit"] == "too_senior"


def _job(prefs, **kw):
    base = dict(title="DevOps Engineer", company="Acme", location="Bengaluru, Karnataka", url="https://x/1", source="indeed",
                description="Kubernetes Terraform AWS Prometheus Grafana Linux Python 1+ years of experience",
                posted_at=datetime.now(timezone.utc).isoformat())
    base.update(kw)
    return rank(enrich(Job(**base), prefs), prefs)


def test_rank_prefers_bangalore_fresh_fit(prefs):
    blr = _job(prefs)
    old = _job(prefs, url="https://x/2", posted_at=(datetime.now(timezone.utc) - timedelta(days=25)).isoformat())
    abroad = _job(prefs, url="https://x/3", location="Austin, TX")
    assert blr.apply_priority > old.apply_priority
    assert blr.apply_priority > abroad.apply_priority
    assert "Bengaluru" in blr.reasons or "Bangalore" in blr.reasons


def test_apply_next_caps_per_company(prefs):
    jobs = [_job(prefs, url=f"https://x/{i}", title=f"DevOps Engineer {chr(65 + i)}") for i in range(5)]
    jobs.append(_job(prefs, url="https://x/9", company="Other"))
    picks = apply_next(jobs, n=10)
    assert sum(j.company == "Acme" for j in picks) == 3 and len(picks) == 4


def test_age_label_hours_and_days():
    from src.recommend import age_label
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    j = Job(title="t", company="c", location="l", url="u", source="s", posted_at="2026-10-18T07:30:00+00:00")
    assert age_label(j, now) == "posted 4h ago"
    j.posted_at = "2026-10-15T00:00:00+00:00"  # date-only source
    assert age_label(j, now) == "posted 3d ago"


def test_closing_soon_boosts(prefs):
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    a = _job(prefs, url="https://x/a")
    b = _job(prefs, url="https://x/b", expires_at=soon)
    assert b.apply_priority > a.apply_priority and any("closes in" in r for r in b.reasons)
