"""Tests that the opportunity analysis + scoring is genuinely dynamic.

These guard the core fix: the Portfolio Proof score (and the rest of the
breakdown) must be computed by comparing each uploaded opportunity
against the evidence collection — never a fixed/default value reused
across opportunities.
"""

from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.models.schemas import ProofPoint
from app.services import llm_client, match_engine
from app.services.match_engine import build_opportunity_profile, evaluate, job_fingerprint
from app.services.scoring import WEIGHTS, score


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _field(value="Not visible", confidence="low", source="not visible") -> dict:
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


def _proof(claim_type, *, claim_text="", skills=(), tools=(), industries=()) -> ProofPoint:
    return ProofPoint(
        evidence_id=f"ev_{claim_type}_{abs(hash((claim_type, claim_text))) % 10 ** 8}",
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


def _fintech_dossier():
    return [
        _proof("skill", skills=["python", "react", "aws", "sql"]),
        _proof("tool", tools=["docker", "kubernetes", "postgres"]),
        _proof("industry", industries=["fintech", "payments"]),
        _proof("experience", claim_text="6 years building fintech payment platforms"),
        _proof("project", claim_text="Built a payment ledger for a fintech client"),
        _proof("portfolio", claim_text="Case study: fintech payments dashboard"),
        _proof("testimonial", claim_text="Shipped our payments API on time"),
        _proof("positioning", claim_text="Senior Python engineer for fintech SaaS"),
    ]


def _fintech_job():
    return _job(
        job_title="Senior Python Engineer for Fintech Payments",
        job_description="Build a payment ledger and payments API in Python.",
        client_need="Need a senior engineer to ship a fintech payment ledger",
        required_skills="Python, React, AWS, SQL",
        budget_or_rate="$85/hr",
        proposal_count="4",
        payment_verification="Verified",
        client_rating="4.9",
        client_total_spend="$50k",
        hire_rate="80%",
    )


def _logo_job():
    return _job(
        job_title="Logo and brand identity for a bakery",
        job_description="Design a logo, color palette, and brand guide for a new bakery.",
        client_need="Need a designer to craft a bakery brand identity",
        required_skills="Logo Design, Illustrator, Branding",
        budget_or_rate="$85/hr",
        proposal_count="4",
        payment_verification="Verified",
        client_rating="4.9",
        client_total_spend="$50k",
        hire_rate="80%",
    )


# ---------------------------------------------------------------------------
# 1. Portfolio Proof is not hardcoded — it differs across opportunities
# ---------------------------------------------------------------------------


def test_portfolio_proof_differs_across_opportunities_same_dossier():
    dossier = _fintech_dossier()

    md_fintech = evaluate(_fintech_job(), dossier)  # rule-based path (no settings)
    md_logo = evaluate(_logo_job(), dossier)

    s_fintech = score(md_fintech, dossier_strength=80, missing_critical_fields=0)
    s_logo = score(md_logo, dossier_strength=80, missing_critical_fields=0)

    pf_fintech = s_fintech.sub_scores["portfolio_proof"]
    pf_logo = s_logo.sub_scores["portfolio_proof"]

    # The same dossier scored against two very different opportunities must
    # NOT yield the same Portfolio Proof score (the original bug).
    assert pf_fintech != pf_logo
    # The opportunity the evidence actually supports scores higher.
    assert pf_fintech > pf_logo
    # And the overall totals differ too.
    assert s_fintech.total != s_logo.total


def test_portfolio_proof_match_carries_relevance_signal():
    md = evaluate(_fintech_job(), _fintech_dossier())
    ppm = md["portfolio_proof_match"]
    assert "relevant_count" in ppm
    assert "relevance" in ppm
    # At least one fintech/payment proof is relevant to the fintech job.
    assert ppm["relevant_count"] >= 1


# ---------------------------------------------------------------------------
# 2. Job fingerprint
# ---------------------------------------------------------------------------


def test_two_different_opportunities_have_different_fingerprints():
    fp_a = job_fingerprint(_fintech_job())
    fp_b = job_fingerprint(_logo_job())
    assert fp_a != fp_b


def test_same_opportunity_has_stable_fingerprint():
    job = _fintech_job()
    assert job_fingerprint(job) == job_fingerprint(_fintech_job())


def test_match_result_carries_job_fingerprint():
    job = _fintech_job()
    md = evaluate(job, _fintech_dossier())
    assert md["job_fingerprint"] == job_fingerprint(job)


# ---------------------------------------------------------------------------
# 3. Opportunity profile
# ---------------------------------------------------------------------------


def test_opportunity_profile_extracts_compact_view():
    profile = build_opportunity_profile(_fintech_job())
    assert profile["opportunity_title"].startswith("Senior Python")
    assert "python" in [s.lower() for s in profile["required_skills"]]
    assert profile["budget_or_rate"] == "$85/hr"
    assert profile["client_quality_indicators"]  # client signals captured
    assert "proposal_count" not in profile["missing_fields"]  # it is visible here


# ---------------------------------------------------------------------------
# 4. Total always equals the sum of the components
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job_factory", [_fintech_job, _logo_job])
def test_total_equals_sum_of_components(job_factory):
    md = evaluate(job_factory(), _fintech_dossier())
    result = score(md, dossier_strength=70, missing_critical_fields=0)

    assert result.total == sum(result.sub_scores.values())
    assert result.total == sum(c.value for c in result.components.values())
    # Every component is recorded with its provenance.
    assert set(result.components.keys()) == set(WEIGHTS.keys())
    for key, comp in result.components.items():
        assert comp.max_value == WEIGHTS[key]
        assert 0 <= comp.value <= comp.max_value
        assert comp.source == "llm_match_result + deterministic_scoring"
        assert comp.confidence in {"high", "medium", "low", "unknown"}
        assert isinstance(comp.evidence_ids_used, list)


# ---------------------------------------------------------------------------
# 5. No raw dossier text (or API key) is logged through matching
# ---------------------------------------------------------------------------


def test_raw_dossier_text_is_not_logged_through_matching(
    fake_streamlit, monkeypatch, caplog
):
    settings = Settings(
        llm_provider="anthropic",
        anthropic_api_key=VALID_ANTHROPIC_KEY,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
    )
    secret = "ULTRA_SECRET_DOSSIER_MARKER_DO_NOT_LOG"
    dossier = _fintech_dossier() + [_proof("experience", claim_text=secret)]

    def _fake_anthropic_text(**kwargs):
        # The provider call legitimately receives compact snippets; the
        # requirement is that they are never written to logs.
        return ('{"portfolio_proof_analysis": {"rating": "medium"}}', 5, 2)

    monkeypatch.setattr(llm_client, "_anthropic_text", _fake_anthropic_text)

    with caplog.at_level(logging.INFO, logger="upwork_strategist.llm_client"):
        evaluate(_fintech_job(), dossier, settings=settings)

    for record in caplog.records:
        msg = record.getMessage()
        assert secret not in msg
        assert VALID_ANTHROPIC_KEY not in msg

    for entry in fake_streamlit.session_state["api_usage_log"]:
        for value in entry.values():
            assert secret not in str(value or "")
            assert VALID_ANTHROPIC_KEY not in str(value or "")
        # The usage log is metadata-only.
        assert "response_text" not in entry
        assert "response_json" not in entry
        assert "user_prompt" not in entry
