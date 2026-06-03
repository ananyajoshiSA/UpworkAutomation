"""Tests for the LLM-backed verification pass.

These tests confirm the behaviours the user asked for:

* verification.py routes through llm_client.call_text_llm with
  task_name=verification_pass,
* the verification entry in the API usage log records used_api=true,
* unsupported claims surfaced by the LLM are removed or softened in
  the final proposal,
* visible evidence_id tokens are stripped from the verified proposal,
* local fallback only runs when ALLOW_LOCAL_PLACEHOLDERS=true,
* verification failure is clearly surfaced when the API call fails,
* the full evidence index is not embedded in the verification prompt.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.models.schemas import ProofPoint
from app.services import llm_client, verification


VALID_ANTHROPIC_KEY = "sk-ant-test-1234567890abcdef"


def _settings(**overrides) -> Settings:
    base = dict(
        llm_provider="anthropic",
        anthropic_api_key=VALID_ANTHROPIC_KEY,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
    )
    base.update(overrides)
    return Settings(**base)


def _proof(
    claim_type: str,
    *,
    claim_text: str = "",
    suffix: str = "",
) -> ProofPoint:
    return ProofPoint(
        evidence_id=f"ev_{claim_type}_{suffix or abs(hash((claim_type, claim_text))) % 10 ** 6}",
        source_file="/tmp/dossier/proofs.md",
        source_type="upwork_profile",
        source_priority=5,
        source_location="body#para1",
        claim_type=claim_type,
        claim_text=claim_text or claim_type,
    )


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
# 1. verify() routes through llm_client.call_text_llm
# ---------------------------------------------------------------------------


def test_verify_calls_central_llm_client(fake_streamlit, monkeypatch):
    captured: dict = {}

    def _fake_call_text(**kwargs):
        captured.update(kwargs)
        return llm_client.LLMCallResult(
            success=True,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={
                "verification_status": "passed",
                "supported_claims": [],
                "partially_supported_claims": [],
                "unsupported_claims": [],
                "verified_proposal": "Clean verified proposal.",
                "missing_information": [],
                "summary": "All claims supported.",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _fake_call_text)

    report = verification.verify(
        "Draft proposal mentions ev_abc123 inline.",
        [_proof("skill", claim_text="Python")],
        factual_claims=[
            {"text": "Python", "kind": "skill", "evidence_id": "ev_skill_42"}
        ],
        settings=_settings(),
    )

    assert captured["task_name"] == "verification_pass"
    assert captured["expected_json"] is True
    assert report.meta["used_api"] is True
    assert report.meta["task_name"] == "verification_pass"


# ---------------------------------------------------------------------------
# 2. Usage log records used_api=true and metadata-only fields
# ---------------------------------------------------------------------------


def test_verification_logs_used_api_true(fake_streamlit, monkeypatch):
    def _fake_call_text(**kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={
                "verification_status": "passed",
                "supported_claims": [
                    {"claim": "Python", "evidence_ids": ["ev_skill_42"]}
                ],
                "partially_supported_claims": [],
                "unsupported_claims": [],
                "verified_proposal": "Verified.",
                "missing_information": [],
                "summary": "OK",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _fake_call_text)

    verification.verify(
        "Draft.",
        [_proof("skill", claim_text="Python")],
        factual_claims=[],
        settings=_settings(),
    )

    log = fake_streamlit.session_state["api_usage_log"]
    verify_entries = [e for e in log if e["task_name"] == "verification_pass"]
    assert verify_entries, "verification_pass entry must be present"
    last = verify_entries[-1]
    assert last["used_api"] is True
    assert last["provider"] == "anthropic"
    assert last["model"] == "claude-sonnet-4-6"
    assert "claims_checked" in last
    assert "unsupported_claims_count" in last
    # The usage log must NEVER contain raw response payloads, prompts,
    # API keys, or organization IDs.
    for forbidden in ("response_text", "response_json", "prompt", "api_key"):
        assert forbidden not in last
    for value in last.values():
        assert VALID_ANTHROPIC_KEY not in str(value or "")


# ---------------------------------------------------------------------------
# 3. Unsupported claims are removed; partial claims are softened
# ---------------------------------------------------------------------------


def test_unsupported_claims_are_removed_or_softened(fake_streamlit, monkeypatch):
    def _fake_call_text(**kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={
                "verification_status": "passed_with_softening",
                "supported_claims": [
                    {"claim": "Python", "evidence_ids": ["ev_skill_42"]}
                ],
                "partially_supported_claims": [
                    {
                        "claim": "10 years of experience",
                        "reason": "evidence shows experience but not exact years",
                        "suggested_softening": "several years of experience",
                        "evidence_ids": ["ev_skill_42"],
                    }
                ],
                "unsupported_claims": [
                    {
                        "claim": "Worked with Acme Corp",
                        "reason": "no testimonial for Acme",
                        "action": "remove",
                    }
                ],
                "verified_proposal": "I bring Python and several years of experience.",
                "missing_information": ["Add an Acme testimonial."],
                "summary": "Softened one specific; removed one named-client claim.",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _fake_call_text)

    report = verification.verify(
        "Draft mentions Acme Corp and 10 years of experience.",
        [_proof("skill", claim_text="Python", suffix="42")],
        factual_claims=[],
        settings=_settings(),
    )

    assert report.verification_status == "passed_with_softening"
    assert any("Acme Corp" in c for c in report.removed_claims)
    assert any(
        s.get("softened") == "several years of experience"
        for s in report.softened_claims
    )
    assert "Acme" not in report.cleaned_proposal
    assert "several years" in report.cleaned_proposal


# ---------------------------------------------------------------------------
# 4. Evidence IDs are stripped from the verified proposal
# ---------------------------------------------------------------------------


def test_evidence_ids_are_stripped_from_verified_proposal(fake_streamlit, monkeypatch):
    leaky_proposal = (
        "Built dashboards (ev_693dc6ac4b36) and shipped a ledger "
        "[ev_other_id_99]. Mentioned ev_bare_token in passing."
    )

    def _fake_call_text(**kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={
                "verification_status": "passed",
                "supported_claims": [],
                "partially_supported_claims": [],
                "unsupported_claims": [],
                "verified_proposal": leaky_proposal,
                "missing_information": [],
                "summary": "OK",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _fake_call_text)

    report = verification.verify(
        leaky_proposal,
        [],
        factual_claims=[],
        settings=_settings(),
    )

    assert "ev_693dc6ac4b36" not in report.cleaned_proposal
    assert "ev_other_id_99" not in report.cleaned_proposal
    assert "ev_bare_token" not in report.cleaned_proposal


def test_strip_evidence_ids_helper():
    text = "Built X (ev_abc12345). Also ev_def67890 and [ev_ghi].END"
    cleaned = verification.strip_evidence_ids(text)
    assert "ev_abc12345" not in cleaned
    assert "ev_def67890" not in cleaned
    assert "ev_ghi" not in cleaned
    assert "Built X." in cleaned


# ---------------------------------------------------------------------------
# 5. Local fallback only runs when ALLOW_LOCAL_PLACEHOLDERS=true
# ---------------------------------------------------------------------------


def test_local_fallback_only_when_flag_enabled(fake_streamlit, monkeypatch):
    def _failing_call(**kwargs):
        return llm_client.LLMCallResult(
            success=False,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            status=llm_client.STATUS_CONNECTION,
            error_message="Connection refused",
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _failing_call)

    # Flag OFF — the report must say verification failed.
    strict = _settings(allow_local_placeholders=False)
    report_strict = verification.verify(
        "Draft proposal.",
        [_proof("skill", claim_text="Python")],
        factual_claims=[],
        settings=strict,
    )
    assert report_strict.verification_status == "failed"
    assert report_strict.meta["used_api"] is False
    assert "LOCAL FALLBACK" not in (report_strict.summary or "")

    # Flag ON — the deterministic sweep runs and labels itself clearly.
    permissive = _settings(allow_local_placeholders=True)
    report_local = verification.verify(
        "Draft proposal.",
        [_proof("skill", claim_text="Python")],
        factual_claims=[],
        settings=permissive,
    )
    assert report_local.meta["used_api"] is False
    assert report_local.meta["status"] == "local_placeholder"
    assert "LOCAL FALLBACK" in (report_local.summary or "")


# ---------------------------------------------------------------------------
# 6. Verification failure is clearly surfaced when API fails
# ---------------------------------------------------------------------------


def test_api_failure_is_surfaced_with_sanitized_error(fake_streamlit, monkeypatch):
    raw_err = (
        "RateLimitError 429: tokens-per-minute exceeded. "
        "organization-id=org-LEAKED12345"
    )

    def _failing_call(**kwargs):
        return llm_client.LLMCallResult(
            success=False,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            status=llm_client.STATUS_QUOTA,
            error_message=raw_err,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _failing_call)

    report = verification.verify(
        "Draft proposal.",
        [_proof("skill", claim_text="Python")],
        factual_claims=[],
        settings=_settings(allow_local_placeholders=False),
    )
    assert report.verification_status == "failed"
    # The user-visible failure message must NOT leak the organization ID.
    msg = report.meta.get("error_message") or ""
    assert "org-LEAKED12345" not in msg
    assert "org-" not in msg


# ---------------------------------------------------------------------------
# 7. Full evidence index is NOT sent to verification
# ---------------------------------------------------------------------------


def test_full_evidence_index_is_not_sent_to_verification(fake_streamlit, monkeypatch):
    # Build a single proof point whose text contains a marker that
    # would only be present if the FULL dossier body was embedded.
    secret_marker = "ULTRA_SECRET_RAW_DOSSIER_BODY_NEVER_LEAK"
    inflated = secret_marker + " " + ("filler-token " * 400)
    full_index = [
        _proof("experience", claim_text=inflated, suffix="huge"),
        _proof("skill", claim_text="Python", suffix="py"),
    ]
    # The verification pass is given only the COMPACT subset (a single
    # short proof) by its caller, never the full index above.
    compact_subset = [_proof("skill", claim_text="Python", suffix="py")]

    captured_prompt: dict = {}

    def _fake_call_text(**kwargs):
        captured_prompt["user_prompt"] = kwargs.get("user_prompt", "")
        return llm_client.LLMCallResult(
            success=True,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={
                "verification_status": "passed",
                "supported_claims": [],
                "partially_supported_claims": [],
                "unsupported_claims": [],
                "verified_proposal": "Verified.",
                "missing_information": [],
                "summary": "OK",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _fake_call_text)

    verification.verify(
        "Draft proposal.",
        compact_subset,
        factual_claims=[],
        settings=_settings(),
    )

    body = captured_prompt["user_prompt"]
    assert secret_marker not in body
    # ... and the compact subset's claim text survives.
    assert "Python" in body


# ---------------------------------------------------------------------------
# 8. confirmed_job_fields are forwarded to the prompt
# ---------------------------------------------------------------------------


def test_confirmed_job_fields_are_forwarded_to_prompt(fake_streamlit, monkeypatch):
    captured: dict = {}

    def _fake_call_text(**kwargs):
        captured["user_prompt"] = kwargs.get("user_prompt", "")
        return llm_client.LLMCallResult(
            success=True,
            task_name="verification_pass",
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={
                "verification_status": "passed",
                "supported_claims": [],
                "partially_supported_claims": [],
                "unsupported_claims": [],
                "verified_proposal": "Verified.",
                "missing_information": [],
                "summary": "OK",
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(verification.llm_client, "call_text_llm", _fake_call_text)

    verification.verify(
        "Draft proposal.",
        [],
        factual_claims=[],
        confirmed_job_fields={
            "job_title": {"value": "Senior Python Engineer", "confidence": "high"},
            "budget_or_rate": {"value": "$5,000 fixed", "confidence": "medium"},
            "missing_field": {"value": "Not visible", "confidence": "low"},
        },
        settings=_settings(),
    )

    body = captured["user_prompt"]
    assert "Senior Python Engineer" in body
    assert "$5,000 fixed" in body
    # "Not visible" fields are not forwarded — they're noise.
    assert "missing_field" not in body
