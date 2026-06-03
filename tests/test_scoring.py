"""Tests for the match engine, scoring, and recommendation modules."""

from __future__ import annotations

import pytest

from app.models.schemas import ProofPoint
from app.services.match_engine import (
    CRITICAL_FIELDS,
    count_missing_critical_fields,
    evaluate,
)
from app.services.recommendation import recommend, verdict_for_score
from app.services.scoring import WEIGHTS, ScoreResult, score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _field(value: str = "Not visible", confidence: str = "low", source: str = "not visible") -> dict:
    return {"value": value, "confidence": confidence, "source": source}


def _job(**overrides) -> dict:
    base = {name: _field() for name in (
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
    )}
    for key, value in overrides.items():
        if isinstance(value, dict):
            base[key] = value
        else:
            base[key] = _field(value=str(value), confidence="medium", source="ocr extracted")
    return base


def _proof(claim_type: str, *, claim_text: str = "", skills=(), tools=(), industries=(), source_priority: int = 5) -> ProofPoint:
    return ProofPoint(
        evidence_id=f"ev_{claim_type}_{abs(hash((claim_type, claim_text)))%10**8}",
        source_file="/tmp/dossier/profile.md",
        source_type="upwork_profile",
        source_priority=source_priority,
        source_location="body#para1",
        claim_type=claim_type,
        claim_text=claim_text or claim_type,
        skills=list(skills),
        tools=list(tools),
        industries=list(industries),
        confidence="high",
    )


def _strong_evidence() -> list[ProofPoint]:
    return [
        _proof("skill", skills=["python", "react", "aws", "sql"]),
        _proof("tool", tools=["docker", "kubernetes", "postgres"]),
        _proof("industry", industries=["fintech", "healthcare"]),
        _proof("experience", claim_text="6 years building SaaS platforms"),
        _proof("project", claim_text="Built payment ledger for fintech client"),
        _proof("portfolio", claim_text="Case study: $2M revenue lift"),
        _proof("testimonial", claim_text="Excellent communicator — would hire again"),
        _proof("metric", claim_text="Reduced onboarding time by 40%"),
        _proof("positioning", claim_text="Senior Python engineer for fintech SaaS"),
        _proof("achievement", claim_text="Shipped 12 production features"),
    ]


# ---------------------------------------------------------------------------
# Existing contract tests
# ---------------------------------------------------------------------------


def test_weights_match_build_plan():
    assert WEIGHTS == {
        "profile_fit": 30,
        "portfolio_proof": 20,
        "client_quality": 20,
        "competition": 15,
        "budget_value": 15,
    }
    assert sum(WEIGHTS.values()) == 100


@pytest.mark.parametrize(
    "total, expected",
    [
        (100, "Strongly Proceed"),
        (80, "Strongly Proceed"),
        (79, "Proceed"),
        (65, "Proceed"),
        (64, "Proceed with Caution"),
        (50, "Proceed with Caution"),
        (49, "Do Not Proceed"),
        (0, "Do Not Proceed"),
    ],
)
def test_verdict_for_score_boundaries(total, expected):
    assert verdict_for_score(total) == expected


def test_score_returns_ScoreResult_with_required_shape():
    match_data = evaluate(_job(), [])
    result = score(match_data, dossier_strength=0, missing_critical_fields=5)
    assert isinstance(result, ScoreResult)
    assert set(result.sub_scores.keys()) == set(WEIGHTS.keys())
    assert result.confidence in {"HIGH", "MEDIUM", "LOW"}


# ---------------------------------------------------------------------------
# Scenario tests requested by the build plan
# ---------------------------------------------------------------------------


def test_strong_fit_yields_high_confidence_and_strong_verdict():
    job = _job(
        job_title="Senior Python Engineer for Fintech SaaS",
        job_description="Build payment APIs in Python with React frontend.",
        client_need="Need a senior engineer to ship payment ledger",
        required_skills="Python, React, AWS, SQL",
        budget_or_rate="$85/hr",
        proposal_count="3",
        payment_verification="Verified",
        client_rating="4.9",
        client_total_spend="$50k",
        hire_rate="80%",
    )
    match_data = evaluate(job, _strong_evidence())
    missing_critical = count_missing_critical_fields(job)
    assert missing_critical == 0

    result = score(match_data, dossier_strength=85, missing_critical_fields=missing_critical)
    assert result.confidence == "HIGH"
    assert result.total >= 80
    assert match_data["skill_match"]["matched"]
    assert match_data["client_quality"] == "strong"
    assert match_data["competition_level"] == "low"

    rec = recommend(result, match_data)
    assert rec["verdict"] == "Strongly Proceed"
    assert rec["reasoning"].count("\n") == 1  # two lines


def test_weak_dossier_softens_recommendation():
    job = _job(
        job_title="Python developer",
        job_description="Python work",
        client_need="Need a Python developer",
        required_skills="Python",
        budget_or_rate="$60/hr",
        proposal_count="4",
        payment_verification="Verified",
        client_rating="4.8",
        client_total_spend="$20k",
        hire_rate="70%",
    )
    match_data = evaluate(job, [_proof("skill", skills=["python"])])
    result = score(match_data, dossier_strength=25, missing_critical_fields=0)
    assert result.confidence == "LOW"

    rec = recommend(result, match_data)
    raw_verdict = verdict_for_score(result.total)
    if raw_verdict == "Strongly Proceed":
        assert rec["verdict"] == "Proceed"
    elif raw_verdict == "Proceed":
        assert rec["verdict"] == "Proceed with Caution"
    elif raw_verdict == "Proceed with Caution":
        assert rec["verdict"] == "Do Not Proceed"
    else:
        assert rec["verdict"] == "Do Not Proceed"


def test_missing_proposal_count_drops_confidence_and_competition_signal():
    job = _job(
        job_title="Senior Python Engineer",
        job_description="Python + React",
        client_need="Build something",
        required_skills="Python, React",
        budget_or_rate="$70/hr",
        # proposal_count intentionally Not visible
        payment_verification="Verified",
        client_rating="4.8",
    )
    match_data = evaluate(job, _strong_evidence())
    assert match_data["competition_level"] == "unknown"
    assert "proposal_count" in match_data["missing_critical_fields"]

    missing_critical = count_missing_critical_fields(job)
    assert missing_critical >= 1

    result = score(match_data, dossier_strength=80, missing_critical_fields=missing_critical)
    # 1 missing critical with dossier above 70 stays MEDIUM (not HIGH).
    assert result.confidence == "MEDIUM"


def test_low_budget_pulls_budget_value_down_and_increases_risk():
    job = _job(
        job_title="Python dev",
        job_description="Small task",
        client_need="Need quick fix",
        required_skills="Python",
        budget_or_rate="$8/hr",
        proposal_count="3",
        payment_verification="Verified",
        client_rating="4.6",
    )
    match_data = evaluate(job, _strong_evidence())
    assert match_data["budget_match"] == "low"

    result = score(match_data, dossier_strength=80, missing_critical_fields=0)
    assert result.sub_scores["budget_value"] <= 5

    rec = recommend(result, match_data)
    assert any("budget" in c.lower() for c in rec["concerns"])


def test_high_competition_pulls_competition_score_down_and_flags_concern():
    job = _job(
        job_title="Python dev",
        job_description="API work",
        client_need="Need devs",
        required_skills="Python, React",
        budget_or_rate="$60/hr",
        proposal_count="45",
        payment_verification="Verified",
        client_rating="4.5",
        client_total_spend="$5k",
        hire_rate="40%",
    )
    match_data = evaluate(job, _strong_evidence())
    assert match_data["competition_level"] == "high"

    result = score(match_data, dossier_strength=80, missing_critical_fields=0)
    assert result.sub_scores["competition"] <= 5

    rec = recommend(result, match_data)
    assert any("competition" in c.lower() for c in rec["concerns"])


# ---------------------------------------------------------------------------
# Match-engine basics
# ---------------------------------------------------------------------------


def test_count_missing_critical_fields_counts_only_critical():
    job = _job(
        job_title="title",
        client_need="need",
        required_skills="Python",
        budget_or_rate="$50/hr",
        proposal_count="3",
    )
    assert count_missing_critical_fields(job) == 0
    job2 = _job(job_title="title")
    assert count_missing_critical_fields(job2) == len(CRITICAL_FIELDS) - 1


def test_evaluate_handles_empty_evidence_index():
    match_data = evaluate(_job(), [])
    assert match_data["skill_match"]["score"] == 0.0
    assert match_data["portfolio_proof_match"]["evidence_count"] == 0
    assert match_data["evidence_count"] == 0
