from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src import budget, export, tracking, update_status
from src.models import Job
from src.state import JobStore


def _job(i=1, **kw):
    base = dict(title="DevOps Engineer", company="Acme", location="Bengaluru", url=f"https://x/{i}", source="t")
    base.update(kw)
    return Job(**base)


def test_reposts_flagged_after_threshold():
    meta: dict = {}
    for i in range(3):
        j = _job(i, url=f"https://x/{i}", location=f"City{i}")  # different ids, same company+title
        tracking.track_reposts([j], meta)
    assert "reposted" in j.flags


def test_follow_ups_after_seven_days():
    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    a = _job(1, status="applied", history=[{"status": "applied", "at": old}])
    b = _job(2, status="applied", history=[{"status": "applied", "at": recent}])
    c = _job(3, status="interview", history=[{"status": "applied", "at": old}, {"status": "interview", "at": recent}])
    assert tracking.follow_ups([a, b, c]) == [a]


def test_closed_check_marks_closed(monkeypatch):
    jobs = [_job(1, apply_priority=80), _job(2, apply_priority=80), _job(3, apply_priority=10)]
    verdicts = {"https://x/1": False, "https://x/2": True}
    monkeypatch.setattr(tracking, "is_open", lambda url: verdicts.get(url))
    meta: dict = {}
    stats = tracking.check_closed(jobs, meta)
    assert stats["closed"] == 1 and "closed" in jobs[0].flags and stats["checked"] == 2
    assert tracking.check_closed(jobs, meta)["checked"] == 0  # not due again for ~20h


def test_closed_text_detection():
    assert tracking._CLOSED_TEXT.search("Sorry, this job is no longer available.")
    assert not tracking._CLOSED_TEXT.search("Apply now for this exciting role")


def test_update_status_validates(tmp_path):
    store = JobStore(tmp_path / "jobs.json")
    j = _job(1)
    store.add([j])
    stats = update_status.apply_changes(store, [
        {"id": j.id, "status": "applied", "at": "2026-10-18T10:00:00Z", "note": "via referral"},
        {"id": j.id, "status": "hacked"},
        {"id": "../../etc/passwd", "status": "applied"},
        {"id": "0123456789abcdef", "status": "applied"},
    ])
    assert stats == {"applied": 1, "unknown_id": 1, "invalid": 2}
    assert j.status == "applied" and j.history[-1]["note"] == "via referral"


def test_state_round_trip_one_job_per_line(tmp_path):
    store = JobStore(tmp_path / "jobs.json")
    store.add([_job(1), _job(2, company="Other")])
    store.save()
    text = (tmp_path / "jobs.json").read_text()
    assert len(text.splitlines()) == 5  # meta line, "jobs" opener, 2 jobs, closer
    again = JobStore(tmp_path / "jobs.json")
    assert set(again.jobs) == set(store.jobs)


def test_budget_record_and_check(tmp_path):
    path = tmp_path / "usage.json"
    budget.record(61, "scan", path)
    budget.record(30, "status", path)
    assert budget.used_minutes(budget.load(path)) == 3  # 2 (rounded up) + 1


def test_export_is_encrypted_and_guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "DASHBOARD_SRC", export.DASHBOARD_SRC)
    jobs = [_job(1, score=8.0, apply_priority=80, description="Kubernetes " * 50)]
    out = export.export(jobs, {"daily": {}}, "correct horse battery staple", dist=tmp_path / "dist")
    blob = json.loads(out.read_text())
    assert set(blob) == {"v", "salt", "iv", "iters", "ct"}
    assert "Kubernetes" not in out.read_text() and "Acme" not in out.read_text()
    from src.crypto import decrypt_json
    payload = decrypt_json(blob, "correct horse battery staple")
    assert payload["jobs"][0]["title"] == "DevOps Engineer" and payload["apply_next"] == [jobs[0].id]


def test_guard_rejects_extra_files(tmp_path):
    jobs = [_job(1, score=8.0, apply_priority=80)]
    dist = tmp_path / "dist"
    export.export(jobs, {}, "correct horse battery staple", dist=dist)
    (dist / "leak.json").write_text("{}")
    with pytest.raises(RuntimeError, match="unexpected files"):
        export.assert_publishable(dist)


def test_digest_body_lists_worthy_jobs_and_skips_empty():
    from src import notify
    good = _job(1, apply_priority=80, score=8.0, region="india")
    meh = _job(2, apply_priority=30, score=3.0)
    body = notify.digest_body([good, meh], [good, meh], "https://x.github.io/job-radar-dashboard/", False, 5, 0, {"kept": 2})
    assert "1 new job worth applying to" in body and "open job" in body and "DevOps Engineer" in body
    assert notify.digest_body([meh], [meh], "u", False, 5, 0, {}) == ""


def test_notify_skips_outside_actions(monkeypatch):
    from src import notify
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert notify.notify([], [], {}, {}).startswith("skipped")
