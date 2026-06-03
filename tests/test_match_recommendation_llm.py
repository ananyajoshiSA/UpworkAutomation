"""Tests for LLM-backed opportunity matching and recommendation reasoning.

These tests never make real network calls. The provider adapter is
either monkey-patched on :mod:`app.services.llm_client` or
``llm_client.call_text_llm`` itself is patched on the calling module
so we can assert what was sent and what shape came back.
"""

from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.models.schemas import ProofPoint
from app.services import llm_client
from app.services import match_engine
from app.services import recommendation as recommendation_module
from app.services.match_engine import evaluate
from app.services.recommendation import cap_two_lines, recommend, verdict_for_score
from app.services.scoring import score


VALID_OPENAI_KEY = "sk-proj-fake-key-for-testing-1234567890abcd"
VALID_ANTHROPIC_KEY = "sk-ant-test-1234567890abcdef"


# ---------------------------------------------------------------------------
# Streamlit session-state shim so the usage log lives in-memory
# ---------------------------------------------------------------------------


class _FakeSessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _FakeStreamlit:
    def __init__(self):
        self.session_state = _FakeSessionState()


@pytest.fixture()
def fake_streamlit(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake)
    yield fake


def _settings(**overrides) -> Settings:
    base = dict(
        llm_provider="anthropic",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
    )
    base.update(overrides)
    return Settings(**base)


def _field(value: str = "Not visible", confidence: str = "low", source: str = "not visible") -> dict:
    return {"value": value, "confidence": confidence, "source": source}


def _job(**overrides) -> dict:
    base = {name: _field() for name in (
        "job_title", "job_description", "client_need", "required_deliverables",
        "required_skills", "budget_or_rate", "project_type", "experience_level",
        "project_duration", "proposal_count", "payment_verification",
        "client_rating", "client_total_spend", "hire_rate", "client_location",
        "connects_required",
    )}
    for key, value in overrides.items():
        if isinstance(value, dict):
            base[key] = value
        else:
            base[key] = _field(value=str(value), confidence="medium", source="ocr extracted")
    return base


def _proof(claim_type: str, *, claim_text: str = "", skills=(), tools=(), industries=()) -> ProofPoint:
    return ProofPoint(
        evidence_id=f"ev_{claim_type}_{abs(hash((claim_type, claim_text)))%10**8}",
        source_file="/tmp/dossier/profile.md",
        source_type="upwork_profile",
        source_priority=5,
        source_location="body#para1",
        claim_type=claim_type,
        claim_text=claim_text or claim_type,
        skills=list(skills),
        tools=list(tools),
        industries=list(industries),
        confidence="high",
    )


def _strong_evidence():
    return [
        _proof("skill", skills=["python", "react", "aws"]),
        _proof("tool", tools=["docker", "postgres"]),
        _proof("industry", industries=["fintech"]),
        _proof("experience", claim_text="6 years building SaaS platforms"),
        _proof("project", claim_text="Built payment ledger for fintech client"),
        _proof("portfolio", claim_text="Case study: $2M revenue lift"),
        _proof("positioning", claim_text="Senior Python engineer for fintech SaaS"),
    ]


def _strong_job() -> dict:
    return _job(
        job_title="Senior Python Engineer",
        job_description="Build Python + React APIs",
        client_need="Need a senior engineer",
        required_skills="Python, React, AWS",
        budget_or_rate="$85/hr",
        proposal_count="3",
        payment_verification="Verified",
        client_rating="4.9",
        client_total_spend="$50k",
        hire_rate="80%",
    )


# ---------------------------------------------------------------------------
# 1. match_engine calls llm_client with task_name=opportunity_matching
# ---------------------------------------------------------------------------


def test_match_engine_calls_llm_for_opportunity_matching(fake_streamlit, monkeypatch):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)
    captured = {}

    def _fake_call_text(**kwargs):
        captured.update(kwargs)
        # Build a payload using only evidence_ids that came in through the prompt
        # so the normalizer keeps them. The prompt contains the JSON evidence
        # block; we approximate by taking the first id from kwargs.
        sample_id = ""
        import re as _re
        match = _re.search(r'"evidence_id":\s*"([^"]+)"', kwargs["user_prompt"])
        if match:
            sample_id = match.group(1)
        return llm_client.LLMCallResult(
            success=True,
            task_name=kwargs["task_name"],
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_text="...",
            response_json={
                "opportunity_summary": "Senior Python engineer to build payment APIs.",
                "required_skill_analysis": [
                    {
                        "requirement": "Python",
                        "match_level": "direct",
                        "matching_evidence_ids": [sample_id] if sample_id else [],
                        "reason": "Demonstrated Python work",
                    },
                    {
                        "requirement": "React",
                        "match_level": "adjacent",
                        "matching_evidence_ids": [],
                        "reason": "Some frontend exposure",
                    },
                ],
                "portfolio_proof_analysis": {
                    "rating": "medium",
                    "score_signal": 60,
                    "direct_proof": ["Payment ledger project"],
                    "adjacent_proof": [],
                    "missing_proof": [],
                    "matched_projects": ["Payment ledger for fintech client"],
                    "evidence_ids_used": [sample_id] if sample_id else [],
                    "short_reason": "One strong fintech project",
                    "confidence": "medium",
                },
                "skill_match": {
                    "rating": "strong",
                    "short_reason": "Strong Python + React overlap",
                    "evidence_ids_used": [sample_id] if sample_id else [],
                    "confidence": "high",
                },
                "industry_match": {"rating": "strong", "short_reason": "Fintech", "confidence": "high"},
                "experience_match": {"rating": "strong", "short_reason": "6yrs SaaS", "confidence": "high"},
                "budget_match": {"rating": "medium", "short_reason": "Within range", "confidence": "medium"},
                "competition_level": {"rating": "strong", "short_reason": "Low proposals", "confidence": "high"},
                "client_quality": {"rating": "strong", "short_reason": "Verified", "confidence": "high"},
                "proposal_winning_angle": "Lead with the payment ledger case study",
                "risk_level": {"rating": "weak", "short_reason": "Few risks", "confidence": "medium"},
                "overall_fit_summary": "Strong overall fit with clear skill overlap.",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(llm_client, "call_text_llm", _fake_call_text)
    monkeypatch.setattr(match_engine.llm_client, "call_text_llm", _fake_call_text)

    match_data = evaluate(_strong_job(), _strong_evidence(), settings=settings)

    assert captured["task_name"] == "opportunity_matching"
    assert captured["expected_json"] is True
    # Compact context only — opportunity profile + evidence, never the raw
    # dossier or full evidence index.
    assert "<opportunity>" in captured["user_prompt"]
    assert "<evidence>" in captured["user_prompt"]
    assert "<job>" in captured["user_prompt"]
    # API key never appears in the prompt
    assert VALID_ANTHROPIC_KEY not in captured["user_prompt"]
    assert VALID_ANTHROPIC_KEY not in (captured.get("system_prompt") or "")

    meta = match_data["__meta__"]
    assert meta["used_api"] is True
    assert meta["task_name"] == "opportunity_matching"
    assert meta["provider"] == "anthropic"
    assert meta["status"] == llm_client.STATUS_OK

    llm_match = match_data["llm_match"]
    # Simple per-dimension ratings remain available for downstream use.
    for dim in (
        "skill_match", "industry_match", "experience_match", "budget_match",
        "competition_level", "client_quality", "risk_level", "proposal_winning_angle",
    ):
        assert dim in llm_match
        assert llm_match[dim]["rating"] in {"strong", "medium", "weak", "unknown"}
        assert llm_match[dim]["confidence"] in {"high", "medium", "low", "unknown"}
        assert isinstance(llm_match[dim]["evidence_ids_used"], list)

    # The richer evidence-comparison blocks the new schema requires.
    ppa = llm_match["portfolio_proof_analysis"]
    assert ppa["rating"] in {"strong", "medium", "weak", "unknown"}
    assert 0 <= ppa["score_signal"] <= 100
    assert isinstance(ppa["evidence_ids_used"], list)
    rsa = llm_match["required_skill_analysis"]
    assert isinstance(rsa, list) and rsa
    assert rsa[0]["match_level"] in {"direct", "adjacent", "weak", "missing"}

    # The match result carries this opportunity's fingerprint.
    assert match_data["job_fingerprint"]

    # Proposal angle now carries the LLM's winning-angle line.
    assert "payment ledger" in match_data["proposal_angle"].lower()


# ---------------------------------------------------------------------------
# 2. recommendation calls LLM
# ---------------------------------------------------------------------------


def test_recommendation_calls_llm_for_recommendation_generation(
    fake_streamlit, monkeypatch
):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)
    captured = {}

    def _fake_call_text(**kwargs):
        captured.update(kwargs)
        return llm_client.LLMCallResult(
            success=True,
            task_name=kwargs["task_name"],
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_text="...",
            response_json={
                "verdict": "Strongly Proceed",
                "short_verdict": "Worth pursuing — strong fit.",
                "why": (
                    "Python + React overlap is strong and client signals are clean.\n"
                    "Competition is low so connects spend has a clear return."
                ),
                "match_strengths": [
                    "Python + React skill overlap",
                    "Verified client with high spend",
                ],
                "concerns": ["Only one fintech case study"],
                "connects_recommendation": "Spend boosted connects while competition is low.",
                "best_proposal_angle": "Lead with the payment ledger case study.",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(llm_client, "call_text_llm", _fake_call_text)
    monkeypatch.setattr(recommendation_module.llm_client, "call_text_llm", _fake_call_text)

    match_data = evaluate(_strong_job(), _strong_evidence())  # rule-based path
    score_result = score(match_data, dossier_strength=85, missing_critical_fields=0)
    rec = recommend(
        score_result, match_data, settings=settings, confirmed_job=_strong_job()
    )

    assert captured["task_name"] == "recommendation_generation"
    assert captured["expected_json"] is True
    assert VALID_ANTHROPIC_KEY not in captured["user_prompt"]

    meta = rec["__meta__"]
    assert meta["used_api"] is True
    assert meta["task_name"] == "recommendation_generation"

    # Verdict tier comes from the deterministic score, never the LLM. The
    # fake LLM returned "Strongly Proceed", but the host must use the tier
    # the numeric score lands in (no LOW-confidence softening here).
    assert score_result.confidence != "LOW"
    assert rec["verdict"] == verdict_for_score(score_result.total)
    # The recommendation is stamped with the current opportunity fingerprint.
    assert rec["job_fingerprint"] == match_data["job_fingerprint"]
    # Why must be at most two lines
    assert rec["why"].count("\n") <= 1
    assert rec["reasoning"] == rec["why"]  # backwards-compat alias
    assert rec["connects_recommendation"]
    assert rec["best_proposal_angle"]


# ---------------------------------------------------------------------------
# 3. Scoring is deterministic for a GIVEN match result, but the Portfolio
#    Proof score tracks the proof level (it is never a fixed value).
# ---------------------------------------------------------------------------


def _md_with_portfolio_analysis(rating, **kw):
    md = evaluate(_strong_job(), _strong_evidence())
    md["llm_match"] = {
        "portfolio_proof_analysis": {
            "rating": rating,
            "score_signal": kw.get("signal", 50),
            "direct_proof": kw.get("direct", []),
            "adjacent_proof": kw.get("adjacent", []),
            "missing_proof": kw.get("missing", []),
            "evidence_ids_used": kw.get("ids", []),
            "matched_projects": kw.get("projects", []),
            "short_reason": "x",
            "confidence": kw.get("conf", "medium"),
        }
    }
    return md


def test_portfolio_proof_score_tracks_proof_level_and_is_deterministic():
    strong = score(
        _md_with_portfolio_analysis(
            "strong", direct=["p1", "p2"], ids=["e1", "e2"], signal=90, conf="high"
        ),
        dossier_strength=85,
        missing_critical_fields=0,
    )
    medium = score(
        _md_with_portfolio_analysis("medium", adjacent=["a1"], ids=["e1"], signal=55),
        dossier_strength=85,
        missing_critical_fields=0,
    )
    weak = score(
        _md_with_portfolio_analysis("weak", adjacent=["a1"], signal=30),
        dossier_strength=85,
        missing_critical_fields=0,
    )
    none = score(
        _md_with_portfolio_analysis("unknown"),
        dossier_strength=85,
        missing_critical_fields=0,
    )

    # Portfolio Proof is NOT a fixed value — it tracks how much real proof
    # supports the opportunity.
    sp, mp = strong.sub_scores["portfolio_proof"], medium.sub_scores["portfolio_proof"]
    wp, npp = weak.sub_scores["portfolio_proof"], none.sub_scores["portfolio_proof"]
    assert sp > mp > wp > npp
    assert npp == 0
    assert sp <= 20

    # Components carry the documented provenance.
    comp = strong.components["portfolio_proof"]
    assert comp.max_value == 20
    assert comp.source == "llm_match_result + deterministic_scoring"
    assert comp.short_reason

    # Deterministic: identical match data scores identically.
    again = score(
        _md_with_portfolio_analysis(
            "strong", direct=["p1", "p2"], ids=["e1", "e2"], signal=90, conf="high"
        ),
        dossier_strength=85,
        missing_critical_fields=0,
    )
    assert again.total == strong.total
    assert again.sub_scores == strong.sub_scores


# ---------------------------------------------------------------------------
# 4. The "why" field is enforced to at most two lines
# ---------------------------------------------------------------------------


def test_recommendation_why_is_capped_at_two_lines(fake_streamlit, monkeypatch):
    settings = _settings(openai_api_key=VALID_OPENAI_KEY, llm_provider="openai")
    long_why = "\n".join(
        f"Line {i}: this is a long reasoning sentence that should be merged."
        for i in range(1, 8)
    )

    def _fake_call_text(**kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name=kwargs["task_name"],
            provider="openai",
            model="gpt-4o",
            used_api=True,
            response_json={
                "verdict": "Proceed",
                "short_verdict": "Worth a shot.",
                "why": long_why,
                "match_strengths": ["Skill overlap"],
                "concerns": ["Limited proof"],
                "connects_recommendation": "Spend connects, no boost.",
                "best_proposal_angle": "Lead with relevant past project.",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(recommendation_module.llm_client, "call_text_llm", _fake_call_text)

    match_data = evaluate(_strong_job(), _strong_evidence())
    score_result = score(match_data, dossier_strength=85, missing_critical_fields=0)
    rec = recommend(score_result, match_data, settings=settings)

    assert rec["why"].count("\n") <= 1


def test_cap_two_lines_single_long_line_splits():
    one_line = (
        "Skills overlap on python and react with three portfolio proof points. "
        "Client quality strong, competition low, budget acceptable — risk low at 88/100."
    )
    capped = cap_two_lines(one_line)
    assert capped.count("\n") <= 1


def test_cap_two_lines_empty_input():
    assert cap_two_lines("") == ""
    assert cap_two_lines(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 5. Fallback only runs when ALLOW_LOCAL_PLACEHOLDERS=true
# ---------------------------------------------------------------------------


def test_match_engine_fallback_only_when_allow_local_placeholders_true(fake_streamlit):
    no_key_no_fallback = _settings(
        anthropic_api_key=None, allow_local_placeholders=False
    )
    md = evaluate(_strong_job(), _strong_evidence(), settings=no_key_no_fallback)
    assert md["__meta__"]["used_api"] is False
    assert md["__meta__"]["status"] != "local_placeholder"
    assert "no llm api key" in (md["__meta__"]["error_message"] or "").lower()

    no_key_fallback = _settings(
        anthropic_api_key=None, allow_local_placeholders=True
    )
    md2 = evaluate(_strong_job(), _strong_evidence(), settings=no_key_fallback)
    assert md2["__meta__"]["used_api"] is False
    assert md2["__meta__"]["status"] == "local_placeholder"


def test_recommendation_fallback_only_when_allow_local_placeholders_true(fake_streamlit):
    match_data = evaluate(_strong_job(), _strong_evidence())
    score_result = score(match_data, dossier_strength=85, missing_critical_fields=0)

    no_key_no_fallback = _settings(
        anthropic_api_key=None, allow_local_placeholders=False
    )
    rec = recommend(score_result, match_data, settings=no_key_no_fallback)
    assert rec["__meta__"]["used_api"] is False
    assert rec["__meta__"]["status"] != "local_placeholder"

    no_key_fallback = _settings(
        anthropic_api_key=None, allow_local_placeholders=True
    )
    rec2 = recommend(score_result, match_data, settings=no_key_fallback)
    assert rec2["__meta__"]["used_api"] is False
    assert rec2["__meta__"]["status"] == "local_placeholder"


def test_match_engine_llm_failure_without_fallback_is_loud(fake_streamlit, monkeypatch):
    settings = _settings(
        anthropic_api_key=VALID_ANTHROPIC_KEY, allow_local_placeholders=False
    )

    def _fake_call_text(**kwargs):
        return llm_client.LLMCallResult(
            success=False,
            task_name=kwargs["task_name"],
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            error_message="Rate limit reached",
            status=llm_client.STATUS_QUOTA,
        )

    monkeypatch.setattr(match_engine.llm_client, "call_text_llm", _fake_call_text)

    md = evaluate(_strong_job(), _strong_evidence(), settings=settings)
    meta = md["__meta__"]
    assert meta["used_api"] is False
    assert meta["status"] == llm_client.STATUS_QUOTA
    assert "rate limit" in meta["error_message"].lower()


# ---------------------------------------------------------------------------
# 6. API usage log records both tasks
# ---------------------------------------------------------------------------


def test_api_usage_log_records_both_matching_and_recommendation(
    fake_streamlit, monkeypatch
):
    """End-to-end: patch the provider adapter so call_text_llm runs all the
    way through _finalize and writes a real usage-log entry per task."""
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    call_counter = {"n": 0}

    def _fake_anthropic_text(**kwargs):
        call_counter["n"] += 1
        # First call from match_engine; subsequent calls from recommendation.
        if call_counter["n"] == 1:
            payload = (
                '{"dimensions": {'
                + ", ".join(
                    f'"{d}": {{"rating": "medium", "short_reason": "ok",'
                    f' "evidence_ids_used": [], "risks": [], "confidence": "medium"}}'
                    for d in (
                        "skill_match", "industry_match", "experience_match",
                        "portfolio_proof_match", "budget_match",
                        "competition_level", "client_quality",
                        "proposal_winning_angle", "risk_level",
                    )
                )
                + "}}"
            )
        else:
            payload = (
                '{"verdict": "Proceed", "short_verdict": "OK.",'
                ' "why": "Solid overlap on the basics.\\nCompetition manageable.",'
                ' "match_strengths": ["Skill overlap"],'
                ' "concerns": ["Few proofs"],'
                ' "connects_recommendation": "Spend connects.",'
                ' "best_proposal_angle": "Lead with skills."}'
            )
        return (payload, 10, 5)

    monkeypatch.setattr(llm_client, "_anthropic_text", _fake_anthropic_text)

    md = evaluate(_strong_job(), _strong_evidence(), settings=settings)
    sr = score(md, dossier_strength=85, missing_critical_fields=0)
    recommend(sr, md, settings=settings, confirmed_job=_strong_job())

    log = fake_streamlit.session_state["api_usage_log"]
    by_task: dict[str, dict] = {}
    for entry in log:
        by_task[entry["task_name"]] = entry  # last entry wins
    assert by_task["opportunity_matching"]["used_api"] is True
    assert by_task["opportunity_matching"]["provider"] == "anthropic"
    assert by_task["recommendation_generation"]["used_api"] is True
    assert by_task["recommendation_generation"]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 7. The raw API key is never logged
# ---------------------------------------------------------------------------


def test_raw_api_key_never_logged_through_matching(fake_streamlit, monkeypatch, caplog):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    def _fake_anthropic_text(**kwargs):
        assert kwargs["api_key"] == VALID_ANTHROPIC_KEY
        return ("{\"dimensions\": {}}", 5, 2)

    monkeypatch.setattr(llm_client, "_anthropic_text", _fake_anthropic_text)

    with caplog.at_level(logging.INFO, logger="upwork_strategist.llm_client"):
        evaluate(_strong_job(), _strong_evidence(), settings=settings)

    for record in caplog.records:
        assert VALID_ANTHROPIC_KEY not in record.getMessage()
    for entry in fake_streamlit.session_state["api_usage_log"]:
        for v in entry.values():
            assert VALID_ANTHROPIC_KEY not in str(v or "")
        assert "response_text" not in entry
        assert "response_json" not in entry


# ---------------------------------------------------------------------------
# 6. Strengths and concerns must never share an identical point
# ---------------------------------------------------------------------------


def test_dedupe_strengths_concerns_drops_overlap_and_internal_dupes():
    """A concern matching a strength (case/space-insensitively) is dropped,
    and in-list duplicates collapse. Strengths win since they render first."""
    from app.services.recommendation import _dedupe_strengths_concerns

    payload = {
        "match_strengths": [
            "Low competition window",
            "Low competition window",  # internal dupe
            "Verified client",
        ],
        "concerns": [
            "  low   COMPETITION window ",  # same as a strength → dropped
            "Budget below target",
            "Budget below target",  # internal dupe → dropped
        ],
    }

    out = _dedupe_strengths_concerns(payload)

    assert out["strengths"] == ["Low competition window", "Verified client"]
    assert out["match_strengths"] == out["strengths"]
    assert out["concerns"] == ["Budget below target"]
    # No point appears on both sides.
    norm = lambda xs: {x.strip().casefold() for x in xs}
    assert norm(out["strengths"]).isdisjoint(norm(out["concerns"]))


def test_recommend_never_repeats_a_point_in_both_lists(fake_streamlit, monkeypatch):
    """End-to-end: even when the LLM echoes the same point on both sides,
    the returned recommendation keeps it only as a strength."""
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    def _fake_call_text(**_kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name="recommendation_generation",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_text="...",
            response_json={
                "verdict": "Proceed",
                "short_verdict": "Solid fit.",
                "why": "Skills overlap.\nClient looks clean.",
                "match_strengths": ["Strong skill overlap", "Verified client"],
                "concerns": ["Strong skill overlap", "Thin portfolio"],
                "connects_recommendation": "Spend connects.",
                "best_proposal_angle": "Lead with the case study.",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(llm_client, "call_text_llm", _fake_call_text)
    monkeypatch.setattr(
        recommendation_module.llm_client, "call_text_llm", _fake_call_text
    )

    match_data = evaluate(_strong_job(), _strong_evidence())
    score_result = score(match_data, dossier_strength=85, missing_critical_fields=0)
    rec = recommend(
        score_result, match_data, settings=settings, confirmed_job=_strong_job()
    )

    norm = lambda xs: {x.strip().casefold() for x in xs}
    assert norm(rec["strengths"]).isdisjoint(norm(rec["concerns"]))
    assert "Strong skill overlap" in rec["strengths"]
    assert "Strong skill overlap" not in rec["concerns"]
