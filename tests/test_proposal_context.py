"""Tests for the compact proposal context builder and the size-aware
retry path used by :mod:`app.services.proposal_generator`.

These tests confirm the behaviors the user asked for after the
provider rate-limit / oversized-request failures:

* the proposal context builder limits evidence to 20 by default,
* oversized contexts are reduced before the LLM call,
* full dossier chunks are never included in the proposal context,
* a rate / token / 429 error triggers exactly one retry with a smaller
  context,
* the sanitized error message never contains a provider organization
  ID,
* the API usage log records ``evidence_points_sent`` and
  ``compact_context_chars`` for the proposal_generation task,
* the verification pass receives only the compact evidence subset,
  not the full evidence index.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models.schemas import ProofPoint
from app.services import llm_client
from app.services.proposal_generator import (
    ProposalGenerationError,
    build_proposal_context,
    generate as generate_proposal,
)


# ---------------------------------------------------------------------------
# Streamlit shim so the session-level usage log is inspectable
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
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = dict(
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-1234567890abcdef",
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
    )
    base.update(overrides)
    return Settings(**base)


def _proof(
    claim_type: str,
    *,
    claim_text: str = "",
    skills=(),
    tools=(),
    industries=(),
    source_priority: int = 5,
    confidence: str = "medium",
    suffix: str = "",
) -> ProofPoint:
    return ProofPoint(
        evidence_id=f"ev_{claim_type}_{suffix or abs(hash((claim_type, claim_text))) % 10 ** 6}",
        source_file="/tmp/dossier/proofs.md",
        source_type="upwork_profile",
        source_priority=source_priority,
        source_location="body#para1",
        claim_type=claim_type,
        claim_text=claim_text or claim_type,
        skills=list(skills),
        tools=list(tools),
        industries=list(industries),
        confidence=confidence,
    )


def _make_large_evidence(count: int) -> list[ProofPoint]:
    """Build ``count`` proof points whose claim_text is intentionally long.

    Each proof carries ~800 characters of free text. With the default
    20-point / 15000-char limits, the builder must drop most of them.
    """
    long_blob = (
        "This is a deliberately long claim text designed to inflate the "
        "compact proposal context past the configured character cap. "
        "It contains many sentences mimicking dossier prose, including "
        "metrics like 35% improvements and $250k revenue lifts, tools "
        "like Python, Django, Postgres, AWS, Kubernetes, Docker, and "
        "Terraform, plus repeated context to ensure the JSON payload is "
        "verbose enough to trigger truncation behavior in the builder."
    )
    proofs: list[ProofPoint] = []
    proofs.append(
        _proof(
            "positioning",
            claim_text="Senior Python engineer for fintech SaaS",
            suffix="positioning",
        )
    )
    proofs.append(
        _proof(
            "skill",
            claim_text="Python, Django, FastAPI, React, SQL",
            skills=["python", "django", "react", "sql"],
            suffix="skill",
        )
    )
    proofs.append(
        _proof(
            "tool",
            claim_text="Docker, Kubernetes, Postgres",
            tools=["docker", "kubernetes", "postgres"],
            suffix="tool",
        )
    )
    for idx in range(count):
        proofs.append(
            _proof(
                "experience",
                claim_text=f"#{idx} {long_blob}",
                source_priority=10 + (idx % 5),
                confidence="low" if idx % 4 == 0 else "medium",
                suffix=f"exp{idx}",
            )
        )
    return proofs


def _job(required_skills: str = "Python, React, AWS") -> dict:
    field_keys = (
        "job_title",
        "job_description",
        "client_need",
        "required_deliverables",
        "required_skills",
        "budget_or_rate",
        "project_type",
        "experience_level",
        "project_duration",
        "proposal_count",
        "payment_verification",
        "client_rating",
        "client_total_spend",
        "hire_rate",
        "client_location",
        "connects_required",
    )
    job = {
        name: {"value": "Not visible", "confidence": "low", "source": "not visible"}
        for name in field_keys
    }
    job["job_title"] = {
        "value": "Senior Python Engineer",
        "confidence": "high",
        "source": "ocr extracted",
    }
    job["required_skills"] = {
        "value": required_skills,
        "confidence": "high",
        "source": "ocr extracted",
    }
    job["client_need"] = {
        "value": "Ship a payment ledger and dashboards",
        "confidence": "medium",
        "source": "ocr extracted",
    }
    return job


def _match_data(matched_skills=("python", "react")) -> dict:
    return {
        "skill_match": {
            "score": 0.66,
            "matched": list(matched_skills),
            "missing": [],
        },
        "industry_match": {"score": 0.0, "matched": []},
        "experience_match": {"score": 0.7, "evidence_count": 4},
        "portfolio_proof_match": {"score": 0.5, "evidence_count": 3},
        "budget_match": "acceptable",
        "competition_level": "low",
        "client_quality": "strong",
        "proposal_angle": "Lead with payment ledger experience",
        "risk_level": "low",
        "missing_critical_fields": [],
        "evidence_count": 50,
    }


# ---------------------------------------------------------------------------
# 1. Builder caps evidence at 20 by default
# ---------------------------------------------------------------------------


def test_build_proposal_context_caps_evidence_at_20_by_default():
    proofs = _make_large_evidence(200)
    context = build_proposal_context(
        _job(),
        proofs,
        match_result=_match_data(),
        recommendation_result={"verdict": "Proceed", "proposal_angle": "Lead with overlap"},
    )
    assert len(context["evidence"]) <= 20
    assert context["__meta__"]["evidence_points_selected"] <= 20
    assert context["__meta__"]["max_evidence_points"] == 20


def test_build_proposal_context_respects_custom_limit():
    proofs = _make_large_evidence(40)
    context = build_proposal_context(
        _job(),
        proofs,
        match_result=_match_data(),
        max_evidence_points=5,
    )
    assert len(context["evidence"]) == 5


# ---------------------------------------------------------------------------
# 2. Oversized context is reduced
# ---------------------------------------------------------------------------


def test_oversized_context_is_reduced_under_char_cap():
    proofs = _make_large_evidence(60)
    context = build_proposal_context(
        _job(),
        proofs,
        match_result=_match_data(),
        max_evidence_points=20,
        max_context_chars=4000,
    )
    assert context["__meta__"]["approx_context_chars"] <= 4000 * 1.05
    assert context["__meta__"]["evidence_points_selected"] < 20


# ---------------------------------------------------------------------------
# 3. Full dossier chunks are never embedded in the proposal context
# ---------------------------------------------------------------------------


def test_full_dossier_text_is_not_included_in_proposal_context():
    secret_marker = "ULTRA_SECRET_DOSSIER_BODY_MARKER_DO_NOT_LEAK"
    big_text = secret_marker + " " + ("filler " * 400)
    proof = _proof(
        "experience",
        claim_text=big_text,
        suffix="leak",
    )
    proofs = [proof] + _make_large_evidence(15)
    context = build_proposal_context(
        _job(),
        proofs,
        match_result=_match_data(),
        max_context_chars=6000,
    )
    # The truncation cap is well below the marker-plus-filler length,
    # so even when this single proof is selected its body must be cut.
    serialized = repr(context["evidence"])
    # The marker may survive (it's at the start of the trimmed text),
    # but the trailing filler must NOT — that's how we know we did not
    # ship the full dossier-sized body. We assert the trimmed item is
    # capped at the documented size.
    for item in context["evidence"]:
        assert len(item["claim"]) <= 260  # _CLAIM_TEXT_CAP_DEFAULT + ellipsis slack
    # Source file paths are not leaked into the prompt-bound dict either.
    for item in context["evidence"]:
        assert "source_file" not in item


# ---------------------------------------------------------------------------
# 4. Rate / token / 429 errors trigger ONE retry with a smaller context
# ---------------------------------------------------------------------------


def test_size_error_triggers_single_retry_with_smaller_context(
    fake_streamlit, monkeypatch
):
    from app.services import proposal_generator

    settings = _settings()
    job = _job()
    proofs = _make_large_evidence(80)

    call_log: list[dict] = []

    def _fake_call_text(**kwargs):
        call_log.append(kwargs)
        attempt_number = len(call_log)
        if attempt_number == 1:
            return llm_client.LLMCallResult(
                success=False,
                task_name="proposal_generation",
                provider="anthropic",
                model=settings.anthropic_model,
                used_api=True,
                status=llm_client.STATUS_QUOTA,
                error_message=(
                    "RateLimitError 429: requested tokens exceed the provider "
                    "token-per-minute limit. organization-id=org-ABCDEFGHIJK"
                ),
            )
        return llm_client.LLMCallResult(
            success=True,
            task_name="proposal_generation",
            provider="anthropic",
            model=settings.anthropic_model,
            used_api=True,
            response_text="...",
            response_json={
                "proposal": "Compact proposal generated on retry.",
                "factual_claims": [],
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(
        proposal_generator.llm_client, "call_text_llm", _fake_call_text
    )

    result = generate_proposal(
        job,
        proofs,
        {"verdict": "Proceed", "proposal_angle": "Lead with payment ledger"},
        match_data=_match_data(),
        settings=settings,
        run_verify=False,
    )

    # Exactly one retry — two calls in total.
    assert len(call_log) == 2

    # The retry call used a strictly smaller max_tokens.
    assert call_log[1]["max_tokens"] < call_log[0]["max_tokens"]

    # The retry serialized fewer evidence points.
    first_evidence_block = call_log[0]["user_prompt"]
    second_evidence_block = call_log[1]["user_prompt"]
    assert len(second_evidence_block) < len(first_evidence_block)

    assert result["__meta__"]["used_api"] is True
    assert result["__meta__"]["retry_used"] is True
    assert result["__meta__"]["evidence_points_sent"] <= 8


def test_size_error_failure_after_retry_raises_sanitized_error(
    fake_streamlit, monkeypatch
):
    from app.services import proposal_generator

    settings = _settings()
    job = _job()
    proofs = _make_large_evidence(80)

    raw_error = (
        "RateLimitError 429: tokens per minute exceeded. "
        "organization-id=org-ABCDEFGHIJKL details: org_id org-XYZ123ABC456"
    )

    def _always_fail(**kwargs):
        return llm_client.LLMCallResult(
            success=False,
            task_name="proposal_generation",
            provider="openai",
            model="gpt-4.1",
            used_api=True,
            status=llm_client.STATUS_QUOTA,
            error_message=raw_error,
        )

    monkeypatch.setattr(
        proposal_generator.llm_client, "call_text_llm", _always_fail
    )

    with pytest.raises(ProposalGenerationError) as exc_info:
        generate_proposal(
            job,
            proofs,
            {"verdict": "Proceed"},
            match_data=_match_data(),
            settings=settings,
            run_verify=False,
        )

    err = exc_info.value
    assert "too large" in str(err).lower()
    # The sanitized error never contains the raw organization ID.
    assert "org-ABCDEFGHIJKL" not in (err.sanitized_error or "")
    assert "org-XYZ123ABC456" not in (err.sanitized_error or "")
    # And the public message never leaks it either.
    assert "org-" not in str(err)

    # The meta the UI surfaces records the retry attempt.
    assert err.meta.get("retry_used") is True


# ---------------------------------------------------------------------------
# 5. API usage log records evidence_points_sent and compact_context_chars
# ---------------------------------------------------------------------------


def test_api_usage_log_records_compact_context_metadata(
    fake_streamlit, monkeypatch
):
    from app.services import proposal_generator

    settings = _settings()
    job = _job()
    proofs = _make_large_evidence(40)

    def _fake_ok(**kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name="proposal_generation",
            provider="anthropic",
            model=settings.anthropic_model,
            used_api=True,
            response_text="...",
            response_json={
                "proposal": "OK proposal.",
                "factual_claims": [],
            },
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(
        proposal_generator.llm_client, "call_text_llm", _fake_ok
    )

    generate_proposal(
        job,
        proofs,
        {"verdict": "Proceed"},
        match_data=_match_data(),
        settings=settings,
        run_verify=False,
    )

    log = fake_streamlit.session_state["api_usage_log"]
    entries = [e for e in log if e["task_name"] == "proposal_generation"]
    assert entries, "proposal_generation entry must be recorded"
    last = entries[-1]
    assert "evidence_points_sent" in last
    assert isinstance(last["evidence_points_sent"], int)
    assert last["evidence_points_sent"] <= 20
    assert "compact_context_chars" in last
    assert isinstance(last["compact_context_chars"], int)
    assert last["compact_context_chars"] > 0
    assert "retry_used" in last
    assert last["retry_used"] is False

    # Forbidden fields never appear in the usage log entry.
    for forbidden in ("response_text", "response_json", "prompt", "raw_proposal"):
        assert forbidden not in last


# ---------------------------------------------------------------------------
# 6. sanitize_error_message strips organization IDs
# ---------------------------------------------------------------------------


def test_sanitize_error_strips_organization_ids():
    raw = (
        "Error 429 RateLimitError: tokens-per-minute exceeded. "
        "organization-id=org-ABC123DEF456 details: openai-organization=org-XYZ987"
    )
    cleaned = llm_client.sanitize_error_message(raw)
    assert "org-ABC123DEF456" not in cleaned
    assert "org-XYZ987" not in cleaned
    assert "429" in cleaned
    assert "tokens-per-minute" in cleaned.lower()


# ---------------------------------------------------------------------------
# 7. Verification pass receives only the compact evidence subset
# ---------------------------------------------------------------------------


def test_verification_pass_receives_compact_evidence_only(
    fake_streamlit, monkeypatch
):
    from app.services import proposal_generator

    settings = _settings()
    job = _job()
    proofs = _make_large_evidence(80)
    full_count = len(proofs)

    def _fake_ok(**kwargs):
        return llm_client.LLMCallResult(
            success=True,
            task_name="proposal_generation",
            provider="anthropic",
            model=settings.anthropic_model,
            used_api=True,
            response_text="...",
            response_json={
                "proposal": "Compact proposal.",
                "factual_claims": [],
            },
            status=llm_client.STATUS_OK,
        )

    captured: dict = {}

    def _fake_verify(proposal, evidence, factual_claims=None):
        captured["evidence"] = list(evidence or [])
        captured["proposal"] = proposal
        captured["factual_claims"] = factual_claims
        # Return a minimal report-shaped object.
        from app.services.verification import VerificationReport

        return VerificationReport(cleaned_proposal=proposal)

    monkeypatch.setattr(
        proposal_generator.llm_client, "call_text_llm", _fake_ok
    )
    monkeypatch.setattr(
        proposal_generator, "run_verification", _fake_verify
    )

    generate_proposal(
        job,
        proofs,
        {"verdict": "Proceed"},
        match_data=_match_data(),
        settings=settings,
        run_verify=True,
    )

    assert "evidence" in captured
    # The compact subset is strictly smaller than the full evidence
    # index and is bounded by the configured max_evidence_points cap.
    assert 0 < len(captured["evidence"]) <= settings.max_proposal_evidence_points
    assert len(captured["evidence"]) < full_count
