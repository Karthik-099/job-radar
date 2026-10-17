from __future__ import annotations

import json

import pytest

from src import scoring
from src.models import Job


@pytest.fixture(autouse=True)
def _no_real_fallback(monkeypatch):
    monkeypatch.setattr(scoring, "fallback_providers", lambda: [])


def _job(i=1, desc="Kubernetes Terraform"):
    return Job(title=f"DevOps {i}", company="Acme", location="Bengaluru", url=f"https://x/{i}", source="t", description=desc)


def test_normalize_cleans_style_and_clamps():
    out = scoring.normalize_result({
        "score": 14, "level_fit": "weird", "matched_skills": ["k8s"] * 20,
        "referral_message_draft": "Hi — I love this role! Really‑excited",
        "tailoring_tips": ["a", "b", "c", "d"], "interview_topics": list("abcdefg"),
    })
    assert out["score"] == 10.0 and out["level_fit"] == "ok"
    assert len(out["matched_skills"]) == 8 and len(out["tailoring_tips"]) == 3 and len(out["interview_topics"]) == 5
    assert "—" not in out["referral_message_draft"] and "!" not in out["referral_message_draft"]
    assert "‑" not in out["referral_message_draft"]


def test_normalize_rejects_missing_score():
    assert scoring.normalize_result({"level_fit": "ok"}) is None


def test_parse_handles_fences_and_bare_lists():
    assert scoring.parse_response('```json\n{"results":[{"id":"a","score":5}]}\n```')[0]["id"] == "a"
    assert scoring.parse_response('noise {"results":[{"id":"b","score":5}]} trailing')[0]["id"] == "b"
    assert scoring.parse_response("not json") == []


def test_cache_hit_skips_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(scoring.ScoreCache.__init__, "__defaults__", (tmp_path / "cache.json",))
    job = _job()
    key = scoring.content_hash(job, scoring._resume())
    (tmp_path / "cache.json").write_text(json.dumps({key: {"score": 7.5, "level_fit": "ok", "method": "llm",
                                                             "cached_at": 9999999999}}))
    calls = []
    monkeypatch.setattr(scoring, "call_provider", lambda *a, **k: calls.append(1) or [])
    stats = scoring.score_jobs([job])
    assert stats["cached"] == 1 and not calls and job.score == 7.5


def test_quota_falls_through_to_next_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring.ScoreCache.__init__, "__defaults__", (tmp_path / "cache.json",))
    p1 = scoring.Provider("a", "u", "K1", ("m1",), {})
    p2 = scoring.Provider("b", "u", "K2", ("m2",), {})
    monkeypatch.setattr(scoring, "available_providers", lambda in_ci: [(p1, "m1"), (p2, "m2")])
    jobs = [_job(1), _job(2)]

    def fake(p, model, resume, batch, timeout=60):
        if p.name == "a":
            raise scoring.QuotaExhausted("a: 429")
        return [{"id": j.id, "score": 6, "level_fit": "ok"} for j in batch]

    monkeypatch.setattr(scoring, "call_provider", fake)
    stats = scoring.score_jobs(jobs)
    assert stats["llm"] == 2 and jobs[0].scoring["provider"] == "b"


def test_rate_limited_lane_hands_work_to_others(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring.ScoreCache.__init__, "__defaults__", (tmp_path / "cache.json",))
    slow = scoring.Provider("slow", "u", "K1", ("m1",), {})
    fast = scoring.Provider("fast", "u", "K2", ("m2",), {})
    monkeypatch.setattr(scoring, "available_providers", lambda in_ci: [(slow, "m1"), (fast, "m2")])
    jobs = [_job(i) for i in range(12)]  # 3 batches

    def fake(p, model, resume, batch, timeout=60):
        if p.name == "slow":
            raise scoring.RateLimited(0.2)
        return [{"id": j.id, "score": 7, "level_fit": "ok"} for j in batch]

    monkeypatch.setattr(scoring, "call_provider", fake)
    stats = scoring.score_jobs(jobs, time_budget=10)
    assert stats["llm"] == 12 and all(j.scoring["provider"] == "fast" for j in jobs)


def test_no_keys_keeps_keyword_scores(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring.ScoreCache.__init__, "__defaults__", (tmp_path / "cache.json",))
    monkeypatch.setattr(scoring, "available_providers", lambda in_ci: [])
    job = _job()
    job.score = 4.0
    stats = scoring.score_jobs([job])
    assert stats["skipped_no_provider"] == 1 and job.score == 4.0


def test_resume_change_invalidates_cache():
    job = _job()
    assert scoring.content_hash(job, "resume v1") != scoring.content_hash(job, "resume v2")


def test_rank_free_models_only_free_text_models():
    models = [
        {"id": "big/model-550b:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 1_000_000,
         "architecture": {"output_modalities": ["text"]}},
        {"id": "nvidia/nemotron-120b:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 262_144,
         "architecture": {"output_modalities": ["text"]}},
        {"id": "paid/model-999b", "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "context_length": 200_000},
        {"id": "sneaky/model-700b:free", "pricing": {"prompt": "0", "completion": "0.00001"}, "context_length": 200_000},
        {"id": "google/lyria-3:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 1_000_000,
         "architecture": {"output_modalities": ["audio"]}},
        {"id": "x/guard-8b:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 128_000},
        {"id": "tiny/model-2b:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 8_000},
    ]
    picks = scoring.rank_free_models(models, n=5)
    assert picks == ["nvidia/nemotron-120b:free", "big/model-550b:free"]  # strong family first, then size


def test_error_body_with_200_is_rate_limited(monkeypatch):
    class R:
        status_code = 200
        headers = {}
        text = ""
        def raise_for_status(self): pass
        def json(self): return {"error": {"message": "Upstream error: Service temporarily overloaded"}}
    monkeypatch.setattr(scoring.requests, "post", lambda *a, **k: R())
    monkeypatch.setenv("K", "x")
    p = scoring.Provider("or", "u", "K", ("m",), {})
    with pytest.raises(scoring.RateLimited):
        scoring.call_provider(p, "m", "resume", [_job()])


def test_openrouter_fallback_only_when_primary_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring.ScoreCache.__init__, "__defaults__", (tmp_path / "cache.json",))
    primary = scoring.Provider("groq", "u", "K1", ("m1",), {})
    backup = scoring.Provider("openrouter", "u", "K2", ("free:free",), {})
    monkeypatch.setattr(scoring, "available_providers", lambda in_ci: [(primary, "m1")])
    called = []
    monkeypatch.setattr(scoring, "fallback_providers", lambda: called.append(1) or [(backup, "free:free")])

    def fake(p, model, resume, batch, timeout=60):
        if p.name == "groq":
            raise scoring.QuotaExhausted("groq: daily limit")
        return [{"id": j.id, "score": 6, "level_fit": "ok"} for j in batch]

    monkeypatch.setattr(scoring, "call_provider", fake)
    jobs = [_job(i) for i in range(8)]
    stats = scoring.score_jobs(jobs, time_budget=10)
    assert called and stats["llm"] == 8 and all(j.scoring["provider"] == "openrouter" for j in jobs)


def test_openrouter_not_used_when_primary_keeps_up(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring.ScoreCache.__init__, "__defaults__", (tmp_path / "cache.json",))
    primary = scoring.Provider("groq", "u", "K1", ("m1",), {})
    monkeypatch.setattr(scoring, "available_providers", lambda in_ci: [(primary, "m1")])
    called = []
    monkeypatch.setattr(scoring, "fallback_providers", lambda: called.append(1) or [])
    monkeypatch.setattr(scoring, "call_provider",
                        lambda p, m, r, batch, timeout=60: [{"id": j.id, "score": 7} for j in batch])
    stats = scoring.score_jobs([_job(i) for i in range(8)], time_budget=10)
    assert stats["llm"] == 8 and not called
