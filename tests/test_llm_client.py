"""Tests for the central LLM client and API-usage tracking.

These tests never make real network calls — provider adapters are
monkey-patched. The point is to assert the visible behaviours the user
asked for:

* every stage's task_name is recorded in the usage log,
* the API key is never returned, logged, or echoed,
* placeholder stages clearly set used_api=False,
* api_check routes through llm_client,
* proposal generation fails loudly when the LLM is unreachable and
  ALLOW_LOCAL_PLACEHOLDERS=false.
"""

from __future__ import annotations

import logging

import pytest

from app.config import LLM_TASK_NAMES, Settings
from app.services import llm_client
from app.services.api_gate import ApiGateError, run_capability_test
from app.services.evidence_index import build_evidence_index
from app.services.match_engine import evaluate
from app.services.recommendation import recommend
from app.services.scoring import score
from app.services.screenshot_parser import (
    SCREENSHOT_FIELDS,
    extract_fields,
    get_meta as get_screenshot_meta,
)
from app.services.verification import verify
from app.services.proposal_generator import (
    ProposalGenerationError,
    generate as generate_proposal,
)


VALID_ANTHROPIC_KEY = "sk-ant-test-1234567890abcdef"
VALID_OPENAI_KEY = "sk-proj-fake-key-for-testing-1234567890abcd"


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


# ---------------------------------------------------------------------------
# Session-state shim (the production code keys off `streamlit.session_state`).
# These tests provide an in-memory dict so the usage log can be inspected
# without a running streamlit context.
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


# ---------------------------------------------------------------------------
# 1. The known task names are exposed
# ---------------------------------------------------------------------------


def test_known_task_names_are_exposed():
    assert "api_check" in LLM_TASK_NAMES
    assert "screenshot_extraction" in LLM_TASK_NAMES
    assert "evidence_index_generation" in LLM_TASK_NAMES
    assert "opportunity_matching" in LLM_TASK_NAMES
    assert "recommendation_generation" in LLM_TASK_NAMES
    assert "proposal_generation" in LLM_TASK_NAMES
    assert "verification_pass" in LLM_TASK_NAMES


# ---------------------------------------------------------------------------
# 2. call_text_llm with no API key returns used_api=False and records
# ---------------------------------------------------------------------------


def test_call_text_llm_no_api_key_marks_used_api_false(fake_streamlit):
    settings = _settings(anthropic_api_key=None)
    result = llm_client.call_text_llm(
        task_name="api_check",
        system_prompt="",
        user_prompt="Reply with exactly: API OK",
        settings=settings,
    )
    assert result.success is False
    assert result.used_api is False
    assert result.status == llm_client.STATUS_NO_API
    assert result.error_message
    log = fake_streamlit.session_state["api_usage_log"]
    assert log[-1]["task_name"] == "api_check"
    assert log[-1]["used_api"] is False
    assert log[-1]["status"] == llm_client.STATUS_NO_API


# ---------------------------------------------------------------------------
# 3. call_text_llm with a working provider returns success and logs metadata
# ---------------------------------------------------------------------------


def test_call_text_llm_records_success_and_does_not_leak_key(fake_streamlit, monkeypatch):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    def _fake_anthropic_text(**kwargs):
        # The fake provider must never see the key formatted into output.
        assert kwargs["api_key"] == VALID_ANTHROPIC_KEY
        return ("API OK", 5, 2)

    monkeypatch.setattr(llm_client, "_anthropic_text", _fake_anthropic_text)
    result = llm_client.call_text_llm(
        task_name="api_check",
        system_prompt="",
        user_prompt="Reply with exactly: API OK",
        settings=settings,
    )
    assert result.success is True
    assert result.used_api is True
    assert result.response_text == "API OK"
    assert result.status == llm_client.STATUS_OK
    assert result.provider == "anthropic"
    assert result.model == "claude-sonnet-4-6"

    # API key never appears in repr or the logged entry.
    assert VALID_ANTHROPIC_KEY not in repr(result)
    entry = fake_streamlit.session_state["api_usage_log"][-1]
    for v in entry.values():
        assert VALID_ANTHROPIC_KEY != str(v)
        assert VALID_ANTHROPIC_KEY not in str(v or "")


# ---------------------------------------------------------------------------
# 4. api_gate live=True routes through llm_client
# ---------------------------------------------------------------------------


def test_api_check_live_routes_through_llm_client(fake_streamlit, monkeypatch):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)
    called = {}

    def _fake_call(**kwargs):
        called.update(kwargs)
        return llm_client.LLMCallResult(
            success=True,
            task_name=kwargs["task_name"],
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_text="API OK",
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(llm_client, "call_text_llm", _fake_call)

    result = run_capability_test(settings=settings, live=True)
    assert called["task_name"] == "api_check"
    assert "API OK" in called["user_prompt"]
    assert result.status == ApiGateError.API_OK
    assert result.ok is True


def test_api_check_live_maps_failure_to_build_plan_status(fake_streamlit, monkeypatch):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    def _fake_call(**kwargs):
        return llm_client.LLMCallResult(
            success=False,
            task_name=kwargs["task_name"],
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            status=llm_client.STATUS_QUOTA,
            error_message="Rate limit reached",
        )

    monkeypatch.setattr(llm_client, "call_text_llm", _fake_call)
    result = run_capability_test(settings=settings, live=True)
    assert result.status == ApiGateError.API_QUOTA_EXCEEDED
    assert result.ok is False


# ---------------------------------------------------------------------------
# 5. Screenshot extraction placeholder marks used_api False
# ---------------------------------------------------------------------------


def test_screenshot_extraction_with_no_images_marks_local(fake_streamlit):
    fields = extract_fields([])
    meta = get_screenshot_meta(fields)
    assert meta["used_api"] is False
    assert meta["task_name"] == "screenshot_extraction"
    # Every public field is "Not visible".
    for key in SCREENSHOT_FIELDS:
        assert fields[key]["value"] == "Not visible"
    # Recorded as local_placeholder in the usage log.
    log = fake_streamlit.session_state["api_usage_log"]
    assert any(
        e["task_name"] == "screenshot_extraction" and e["used_api"] is False
        for e in log
    )


# ---------------------------------------------------------------------------
# 6. Local stages all record used_api=False
# ---------------------------------------------------------------------------


def _empty_job() -> dict:
    base = {}
    for name in SCREENSHOT_FIELDS:
        base[name] = {"value": "Not visible", "confidence": "low", "source": "not visible"}
    return base


def test_match_engine_and_recommendation_mark_local(fake_streamlit):
    match_data = evaluate(_empty_job(), [])
    assert match_data["__meta__"]["used_api"] is False
    assert match_data["__meta__"]["task_name"] == "opportunity_matching"

    score_result = score(match_data, dossier_strength=0, missing_critical_fields=5)
    rec = recommend(score_result, match_data)
    assert rec["__meta__"]["used_api"] is False
    assert rec["__meta__"]["task_name"] == "recommendation_generation"

    tasks = [e["task_name"] for e in fake_streamlit.session_state["api_usage_log"]]
    assert "opportunity_matching" in tasks
    assert "recommendation_generation" in tasks


def test_verification_marks_local(fake_streamlit):
    # No API key + local fallback explicitly allowed: the deterministic
    # sweep runs and is labelled as a local placeholder.
    settings = _settings(anthropic_api_key=None, allow_local_placeholders=True)
    report = verify(
        "Some draft proposal.",
        [],
        factual_claims=[],
        settings=settings,
    )
    assert report.meta["used_api"] is False
    assert report.meta["task_name"] == "verification_pass"


def test_verification_without_api_key_fails_when_fallback_disabled(fake_streamlit):
    # No API key and ALLOW_LOCAL_PLACEHOLDERS=false: the report must
    # explicitly say verification failed rather than claim the proposal
    # is verified.
    settings = _settings(anthropic_api_key=None, allow_local_placeholders=False)
    report = verify(
        "Some draft proposal.",
        [],
        factual_claims=[],
        settings=settings,
    )
    assert report.meta["used_api"] is False
    assert report.verification_status == "failed"
    assert "verification failed" in (report.meta.get("error_message") or "").lower()


def test_evidence_index_fallback_marks_local(fake_streamlit):
    proofs, profile, meta = build_evidence_index([])
    assert proofs == []
    assert meta["used_api"] is False
    assert meta["task_name"] == "evidence_index_generation"
    log = fake_streamlit.session_state["api_usage_log"]
    assert any(
        e["task_name"] == "evidence_index_generation" and e["used_api"] is False
        for e in log
    )


# ---------------------------------------------------------------------------
# 7. Proposal generation requires API unless ALLOW_LOCAL_PLACEHOLDERS=true
# ---------------------------------------------------------------------------


def test_proposal_generation_raises_when_api_unavailable(fake_streamlit):
    settings = _settings(anthropic_api_key=None, allow_local_placeholders=False)
    job = _empty_job()
    with pytest.raises(ProposalGenerationError) as exc_info:
        generate_proposal(
            job,
            [],
            {"verdict": "Proceed", "reasoning": ""},
            match_data=None,
            settings=settings,
            run_verify=False,
        )
    assert "no LLM API key" in str(exc_info.value) or "LLM API call failed" in str(exc_info.value)


def test_proposal_generation_falls_back_with_dev_flag(fake_streamlit):
    settings = _settings(
        anthropic_api_key=None,
        allow_local_placeholders=True,
    )
    job = _empty_job()
    result = generate_proposal(
        job,
        [],
        {"verdict": "Proceed", "reasoning": ""},
        match_data=None,
        settings=settings,
        run_verify=False,
    )
    meta = result["__meta__"]
    assert meta["used_api"] is False
    assert meta["status"] == "local_placeholder"
    assert result["proposal"]


def test_proposal_generation_records_api_usage_on_success(fake_streamlit, monkeypatch):
    settings = _settings(
        anthropic_api_key=VALID_ANTHROPIC_KEY,
        allow_local_placeholders=False,
    )
    job = _empty_job()

    fake_payload = {
        "proposal": "This is a proposal drafted by the fake LLM.",
        "factual_claims": [],
    }

    def _fake_call_text(**kwargs):
        assert kwargs["task_name"] == "proposal_generation"
        return llm_client.LLMCallResult(
            success=True,
            task_name="proposal_generation",
            provider="anthropic",
            model=settings.anthropic_model,
            used_api=True,
            response_text="...",
            response_json=fake_payload,
            status=llm_client.STATUS_OK,
        )

    # Patch the symbol imported by the proposal generator module.
    from app.services import proposal_generator
    monkeypatch.setattr(proposal_generator.llm_client, "call_text_llm", _fake_call_text)

    result = generate_proposal(
        job,
        [],
        {"verdict": "Proceed", "reasoning": ""},
        match_data=None,
        settings=settings,
        run_verify=False,
    )
    assert result["__meta__"]["used_api"] is True
    assert result["__meta__"]["task_name"] == "proposal_generation"
    assert "fake LLM" in result["proposal"]


# ---------------------------------------------------------------------------
# 8. Usage log entries never contain payloads or API keys
# ---------------------------------------------------------------------------


def test_usage_log_does_not_record_response_payloads(fake_streamlit, monkeypatch):
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    def _fake_anthropic_text(**kwargs):
        return ("This is a long sensitive response body.", 5, 2)

    monkeypatch.setattr(llm_client, "_anthropic_text", _fake_anthropic_text)
    llm_client.call_text_llm(
        task_name="api_check",
        system_prompt="hello",
        user_prompt="Reply with exactly: API OK",
        settings=settings,
    )
    entry = fake_streamlit.session_state["api_usage_log"][-1]
    # The usage log records only metadata.
    assert "response_text" not in entry
    assert "response_json" not in entry
    # And does NOT leak the key.
    for v in entry.values():
        assert VALID_ANTHROPIC_KEY not in str(v or "")


def test_logger_calls_do_not_emit_api_key(monkeypatch, caplog):
    """The metadata logger emits stage / status, never the API key."""
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)

    def _fake_anthropic_text(**kwargs):
        return ("API OK", 3, 1)

    monkeypatch.setattr(llm_client, "_anthropic_text", _fake_anthropic_text)
    with caplog.at_level(logging.INFO, logger="upwork_strategist.llm_client"):
        llm_client.call_text_llm(
            task_name="api_check",
            system_prompt="",
            user_prompt="ping",
            settings=settings,
        )
    for record in caplog.records:
        assert VALID_ANTHROPIC_KEY not in record.getMessage()


# ---------------------------------------------------------------------------
# 9. record_local_use appends to the session log
# ---------------------------------------------------------------------------


def test_record_local_use_appends_entry(fake_streamlit):
    llm_client.record_local_use("evidence_index_generation", note="fallback")
    log = fake_streamlit.session_state["api_usage_log"]
    assert log[-1] == {
        **log[-1],
        "task_name": "evidence_index_generation",
        "used_api": False,
        "status": "local_placeholder",
        "error_message": "fallback",
    }
