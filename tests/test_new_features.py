"""Gmail sync rules, truthful tailoring, same-role hiding, job ids, status re-sends, video ranking."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src import gmail_sync, update_status
from src.models import Job, posting_ref, role_key, role_title
from src.recommend import apply_next
from src.state import JobStore
from src.tailor import VOCAB, jd_terms, load_base, plan, resume_text
from src.videos import parse_feed, rank

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def _job(title="DevOps Engineer", company="Razorpay", status="applied", applied_days_ago=5, **kw):
    j = Job(title=title, company=company, location="Bengaluru", url=f"https://x/{title}/{company}", source="t", **kw)
    j.status = status
    if status != "new":
        j.history.append({"status": "applied", "at": (NOW - timedelta(days=applied_days_ago)).isoformat(), "via": "dashboard"})
        if status != "applied":
            j.history.append({"status": status, "at": (NOW - timedelta(days=1)).isoformat(), "via": "dashboard"})
    return j


def _msg(subject, body, sender="Razorpay Recruiting <no-reply@greenhouse.io>", days_ago=1, mid="m1"):
    name, addr = sender.split(" <")
    return {"id": mid, "from_name": name, "from_addr": addr.rstrip(">"), "subject": subject, "body": body,
            "date": NOW - timedelta(days=days_ago)}


def _store(*jobs):
    st = JobStore.__new__(JobStore)
    st.jobs, st.meta = {j.id: j for j in jobs}, {}
    return st


REJECT = "Thank you for applying to the DevOps Engineer role at Razorpay. Unfortunately, we have decided to move forward with other candidates."


# ---------------------------------------------------------------- Gmail: classification
def test_classify():
    assert gmail_sync.classify("Your application", REJECT) == "rejected"
    assert gmail_sync.classify("Interview invitation", "We would like to schedule an interview with you") == "interview"
    assert gmail_sync.classify("Next steps", "Please complete the online assessment on HackerRank") == "shortlisted"
    assert gmail_sync.classify("Offer", "We are pleased to offer you the position") == "offer"
    # A confirmation that says "unfortunately we can't reply to everyone" is NOT a rejection.
    assert gmail_sync.classify("Application received", "Thanks for applying. Due to volume we can't reply to every applicant.") is None


# ---------------------------------------------------------------- Gmail: only jobs you applied to
def test_rejection_updates_only_the_applied_job():
    job = _job()
    st = _store(job)
    stats = gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update on your application", REJECT)])
    assert stats["updated"] == 1 and job.status == "rejected"
    assert job.history[-1]["via"] == "gmail"


def test_not_applied_job_is_never_touched():
    job = _job(status="new")
    st = _store(job)
    stats = gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update", REJECT)])
    assert stats["updated"] == 0 and job.status == "new"


def test_other_company_is_ignored():
    job = _job(company="Zerodha")
    st = _store(job)
    gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update", REJECT)])
    assert job.status == "applied"


def test_ambiguous_jobs_at_one_company_are_left_for_review():
    a, b = _job(title="SRE"), _job(title="Cloud Engineer")
    st = _store(a, b)
    stats = gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update", "Unfortunately, we have decided to move forward with other candidates.")])
    assert stats["updated"] == 0 and a.status == b.status == "applied"
    assert st.meta["gmail"]["review"][0]["company"] == "Razorpay"


def test_title_picks_the_right_job():
    a, b = _job(title="Site Reliability Engineer"), _job(title="DevOps Engineer")
    st = _store(a, b)
    gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update", REJECT)])
    assert b.status == "rejected" and a.status == "applied"


def test_old_email_from_before_the_application_is_ignored():
    job = _job(applied_days_ago=2)
    st = _store(job)
    gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update", REJECT, days_ago=60)])
    assert job.status == "applied"


def test_never_moves_backwards_and_dry_run_changes_nothing():
    job = _job(status="interview")
    st = _store(job)
    stats = gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Assessment", "Please complete the online assessment on HackerRank", days_ago=0)])
    assert stats["updated"] == 0 and job.status == "interview"
    job2 = _job()
    st2 = _store(job2)
    stats = gmail_sync.sync(st2, dry_run=True, use_ai=False, now=NOW, messages=[_msg("Update", REJECT)])
    assert stats["updated"] == 1 and job2.status == "applied" and "gmail" not in st2.meta


def test_generic_company_name_needs_strong_proof():
    job = _job(company="Remote")
    st = _store(job)
    gmail_sync.sync(st, use_ai=False, now=NOW, messages=[_msg("Update", "This remote role: unfortunately, we have decided to move forward with other candidates.", sender="Acme <jobs@acme.com>")])
    assert job.status == "applied"


def test_ai_veto_wins(monkeypatch):
    job = _job()
    st = _store(job)
    monkeypatch.setattr(gmail_sync, "ai_check", lambda m, c: {"related": False, "job": None, "outcome": "other", "confidence": 0.9})
    gmail_sync.sync(st, use_ai=True, now=NOW, messages=[_msg("Update", REJECT)])
    assert job.status == "applied"


def test_gmail_token_must_be_read_only(monkeypatch):
    class R:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"access_token": "ya29.x", "scope": "https://mail.google.com/"}
    for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"):
        monkeypatch.setenv(k, "v")
    monkeypatch.setattr(gmail_sync.requests, "post", lambda *a, **k: R())
    try:
        gmail_sync.access_token()
        raise AssertionError("broader scope accepted")
    except gmail_sync.GmailError as exc:
        assert "read-only" in str(exc)


# ---------------------------------------------------------------- tailoring is truthful
def test_tailoring_adds_only_proven_keywords_and_keeps_every_bullet():
    base = load_base()
    jd = "We need Infrastructure as Code, Go, Kafka, Kubernetes, root cause analysis and shell scripting."
    p = plan("Site Reliability Engineer", jd_terms(jd), base)
    assert "Infrastructure as Code" in p["added"] and "Root Cause Analysis" in p["have"]  # "RCA" already there
    assert "Go" in p["missing"] and "Kafka" in p["missing"]
    assert not {"Go", "Kafka"} & set(p["added"])
    assert p["after"] >= p["before"] and p["fam"] == "sre"
    for order, job in zip(p["exp"], base["experience"]):
        assert order is None or sorted(order) == list(range(len(job["bullets"])))


def test_every_addable_term_has_evidence_in_the_resume_vocab():
    text = resume_text(load_base())
    for t in VOCAB:
        if t.evidence and t.evidence.search(text):
            assert t.jd.pattern  # addable terms are proven by the resume text itself


# ---------------------------------------------------------------- same role, job ids
def test_role_key_ignores_city_and_board_noise():
    assert role_title("DevOps Engineer (SRE) - Bengaluru | Remote") == "devops engineer"
    assert role_key("Razorpay Pvt Ltd", "DevOps Engineer - Bangalore") == role_key("Razorpay", "DevOps Engineer")
    assert role_key("Acme", "DevOps Engineer I") != role_key("Acme", "DevOps Engineer II")


def test_apply_next_skips_roles_you_applied_to():
    applied = _job()
    dup = _job(status="new")
    dup.url, dup.location, dup.apply_priority = "https://other", "Pune", 90
    dup.id = "f" * 16
    assert dup not in apply_next([applied, dup])


def test_posting_ref():
    assert posting_ref("https://boards.greenhouse.io/gitlab/jobs/8867756002") == "8867756002"
    assert posting_ref("https://x.wd1.myworkdayjobs.com/Ext/job/Pune/DevOps-Engineer_R-289609-1") == "R-289609"
    assert posting_ref("https://in.indeed.com/viewjob?jk=abc", "Job ID: JR123456. We are hiring") == "JR123456"
    assert posting_ref("https://in.indeed.com/viewjob?jk=abcdef") == ""


# ---------------------------------------------------------------- dashboard re-sends are safe
def test_status_resend_is_idempotent_and_never_undoes_newer_changes():
    job = _job()
    st = _store(job)
    st.save = lambda: None
    change = {"id": job.id, "status": "applied", "at": (NOW - timedelta(days=5)).isoformat()}
    assert update_status.apply_changes(st, [change])["applied"] == 0  # already applied: no duplicate entry
    job.status = "rejected"
    job.history.append({"status": "rejected", "at": (NOW - timedelta(days=1)).isoformat(), "via": "gmail"})
    stats = update_status.apply_changes(st, [change])
    assert job.status == "rejected" and stats.get("stale") == 1


# ---------------------------------------------------------------- videos
FEED = """<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
<entry><yt:videoId>a1</yt:videoId><title>Kubernetes Gateway API explained</title><link rel="alternate" href="https://www.youtube.com/watch?v=a1"/>
<published>2026-10-15T10:00:00+00:00</published><media:group><media:description>istio and k8s</media:description><media:community><media:statistics views="5000"/></media:community></media:group></entry>
<entry><yt:videoId>s1</yt:videoId><title>Quick tip #shorts</title><link rel="alternate" href="https://www.youtube.com/shorts/s1"/><published>2026-10-15T10:00:00+00:00</published></entry>
<entry><yt:videoId>b1</yt:videoId><title>My vlog</title><link rel="alternate" href="https://www.youtube.com/watch?v=b1"/><published>2026-10-15T10:00:00+00:00</published></entry>
</feed>"""


def test_videos_skip_shorts_and_off_topic_and_flag_gaps():
    vids = parse_feed(FEED, "CNCF")
    assert [v["id"] for v in vids] == ["a1", "b1"]
    ranked = rank(vids, ["istio"], now=NOW)
    assert [v["id"] for v in ranked] == ["a1"] and ranked[0]["gap"] == "istio"
