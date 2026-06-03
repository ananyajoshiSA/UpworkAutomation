"""Tests for the beginner-safe Upwork Job Evaluator checklist.

Cover the evaluator's own rules (Instant No / Apply Confidently / Proceed
With Caution / missing-field safety), its deterministic influence on
scoring and the recommendation verdict, and the clean-vs-debug UI surface.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.models.schemas import ProofPoint
from app.services import beginner_evaluator as be
from app.services.beginner_evaluator import (
    APPLY_CONFIDENTLY,
    DO_NOT_PROCEED,
    PROCEED_WITH_CAUTION,
    REASON_PAYMENT_NOT_VERIFIED,
    REASON_PROPOSALS_50_PLUS,
    evaluate,
)
from app.services.match_engine import evaluate as match_evaluate
from app.services.recommendation import recommend
from app.services.scoring import score


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_FIELD_NAMES = (
    "job_title", "job_description", "client_need", "required_deliverables",
    "required_skills", "budget_or_rate", "project_type", "experience_level",
    "project_duration", "posted_date", "proposal_count", "payment_verification",
    "client_rating", "client_total_spend", "hire_rate", "client_location",
    "connects_required",
)


def _field(value: str = "Not visible", confidence: str = "low", source: str = "not visible") -> dict:
    return {"value": value, "confidence": confidence, "source": source}


def _job(**overrides) -> dict:
    base = {name: _field() for name in _FIELD_NAMES}
    for key, value in overrides.items():
        if isinstance(value, dict):
            base[key] = value
        else:
            base[key] = _field(value=str(value), confidence="medium", source="ocr extracted")
    return base


def _safe_job(**overrides) -> dict:
    """A job that satisfies every Apply-Confidently condition by default."""
    job = _job(
        job_title="Senior Python Engineer",
        required_skills="Python, React",
        budget_or_rate="$80/hr",
        payment_verification="Payment verified",
        proposal_count="4",
        posted_date="yesterday",
        experience_level="Intermediate",
        client_rating="4.9 of 5",
    )
    for key, value in overrides.items():
        job[key] = value if isinstance(value, dict) else _field(
            value=str(value), confidence="medium", source="ocr extracted"
        )
    return job


def _proof(claim_type: str, *, claim_text: str = "", skills=(), tools=(), industries=()) -> ProofPoint:
    return ProofPoint(
        evidence_id=f"ev_{claim_type}_{abs(hash((claim_type, claim_text))) % 10**8}",
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
        _proof("skill", skills=["python", "react", "aws", "sql"]),
        _proof("tool", tools=["docker", "postgres"]),
        _proof("industry", industries=["fintech"]),
        _proof("experience", claim_text="6 years building SaaS platforms"),
        _proof("project", claim_text="Built payment ledger for fintech client"),
        _proof("portfolio", claim_text="Case study: $2M revenue lift"),
        _proof("positioning", claim_text="Senior Python engineer for fintech SaaS"),
    ]


# ---------------------------------------------------------------------------
# 1 & 2. Instant No rules → Do Not Proceed (override positive signals)
# ---------------------------------------------------------------------------


def test_payment_not_verified_is_instant_no():
    result = evaluate(_safe_job(payment_verification="Payment not verified"))
    assert result["result"] == DO_NOT_PROCEED
    assert result["instant_no"] is True
    assert REASON_PAYMENT_NOT_VERIFIED in result["reasons"]
    assert result["triggered_rule"] == "instant_no:payment_not_verified"


@pytest.mark.parametrize("count", ["50", "50+", "62", "More than 50 proposals"])
def test_fifty_plus_proposals_is_instant_no(count):
    result = evaluate(_safe_job(proposal_count=count))
    assert result["result"] == DO_NOT_PROCEED
    assert result["instant_no"] is True
    assert REASON_PROPOSALS_50_PLUS in result["reasons"]


def test_instant_no_overrides_otherwise_perfect_job():
    # Everything else is green-light, but unverified payment must still block.
    result = evaluate(_safe_job(payment_verification="Not verified"))
    assert result["result"] == DO_NOT_PROCEED


def test_both_instant_no_reasons_surface():
    result = evaluate(
        _safe_job(payment_verification="unverified", proposal_count="80")
    )
    assert result["instant_no"] is True
    assert len(result["reasons"]) == 2
    assert REASON_PAYMENT_NOT_VERIFIED in result["reasons"]
    assert REASON_PROPOSALS_50_PLUS in result["reasons"]


# ---------------------------------------------------------------------------
# 3. Apply Confidently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("posted", ["today", "yesterday", "Posted today", "3 hours ago"])
@pytest.mark.parametrize("level", ["Entry level", "Intermediate"])
def test_apply_confidently_when_all_green(posted, level):
    result = evaluate(_safe_job(posted_date=posted, experience_level=level, proposal_count="6"))
    assert result["result"] == APPLY_CONFIDENTLY
    assert result["instant_no"] is False
    assert 1 <= len(result["reasons"]) <= 2
    assert result["triggered_rule"] == "apply_confidently:all_conditions_met"


def test_apply_confidently_blocked_by_low_rating_warning():
    # All four green-light conditions hold, but a sub-4.0 client rating is a
    # warning, so the result must be cautious rather than confident.
    result = evaluate(_safe_job(client_rating="3.4 of 5"))
    assert result["result"] == PROCEED_WITH_CAUTION


# ---------------------------------------------------------------------------
# 4-7. Proceed With Caution warnings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", ["15", "20", "49"])
def test_proposals_15_to_49_is_caution(count):
    result = evaluate(_safe_job(proposal_count=count))
    assert result["result"] == PROCEED_WITH_CAUTION
    assert any(w["key"] == "proposals_15_49" for w in result["warnings"])
    assert any("very strong" in r for r in result["reasons"])


@pytest.mark.parametrize("posted", ["3 days ago", "5 days ago", "2 weeks ago", "last month"])
def test_posted_3_plus_days_is_caution(posted):
    result = evaluate(_safe_job(posted_date=posted))
    assert result["result"] == PROCEED_WITH_CAUTION
    assert any(w["key"] == "posted_3_days_plus" for w in result["warnings"])
    assert result["reduce_confidence"] is True


def test_client_rating_below_4_is_caution():
    result = evaluate(_safe_job(client_rating="3.8 of 5"))
    assert result["result"] == PROCEED_WITH_CAUTION
    assert any(w["key"] == "client_rating_below_4" for w in result["warnings"])
    assert result["fields"]["client_rating"]["warning"] is True


def test_expert_level_is_caution():
    result = evaluate(_safe_job(experience_level="Expert"))
    assert result["result"] == PROCEED_WITH_CAUTION
    assert any(w["key"] == "expert_level" for w in result["warnings"])
    assert result["reduce_confidence"] is True


def test_caution_requires_payment_verified_and_under_50():
    # 15-49 proposals only routes to Caution because payment is verified and
    # the count is < 50 (otherwise an Instant No would have fired).
    result = evaluate(_safe_job(proposal_count="30"))
    assert result["instant_no"] is False
    assert result["result"] == PROCEED_WITH_CAUTION


# ---------------------------------------------------------------------------
# 5 (spec). Missing fields are marked Not visible, never guessed, never crash
# ---------------------------------------------------------------------------


def test_all_fields_missing_does_not_crash_and_marks_not_visible():
    result = evaluate(_job())  # every field "Not visible"
    assert result["instant_no"] is False  # missing payment is NOT treated as unverified
    assert set(result["missing_fields"]) == {
        "payment_verification", "proposal_count", "posted_date",
        "client_rating", "experience_level",
    }
    assert result["fields"]["payment_verification"]["result"] == "not_visible"
    assert result["fields"]["proposal_count"]["bucket"] == "not_visible"
    assert result["fields"]["posted_age"]["bucket"] == "not_visible"
    assert result["reduce_confidence"] is True
    assert result["missing_info_note"]


def test_evaluate_handles_empty_and_none_inputs():
    assert evaluate({})["result"] in {
        APPLY_CONFIDENTLY, PROCEED_WITH_CAUTION, DO_NOT_PROCEED
    }
    assert evaluate(None)["result"] == PROCEED_WITH_CAUTION  # no data → cautious default


def test_missing_payment_is_not_an_instant_no():
    # A cropped screenshot with no payment badge must not be guessed as unverified.
    result = evaluate(_safe_job(payment_verification="Not visible"))
    assert result["instant_no"] is False
    assert "payment_verification" in result["missing_fields"]


# ---------------------------------------------------------------------------
# Parser robustness (buckets are predictable across real Upwork phrasings)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, bucket",
    [
        ("Less than 5", "low"),
        ("5 to 10", "low"),
        ("12", "low"),
        ("15 to 20", "high"),
        ("20 to 50", "high"),
        ("50+", "too_high"),
        ("Not visible", "not_visible"),
    ],
)
def test_proposal_bucket_parsing(value, bucket):
    assert be._proposal_bucket(be._proposal_count(value)) == bucket


@pytest.mark.parametrize(
    "value, bucket",
    [
        ("today", "fresh"),
        ("yesterday", "fresh"),
        ("2 days ago", "recent"),
        ("3 days ago", "stale"),
        ("2 weeks ago", "stale"),
        ("Not visible", "not_visible"),
    ],
)
def test_posted_bucket_parsing(value, bucket):
    assert be._posted_bucket(be._posted_age_days(value)) == bucket


@pytest.mark.parametrize(
    "value, status",
    [
        ("Payment verified", "verified"),
        ("Verified", "verified"),
        ("Payment not verified", "not_verified"),
        ("Unverified", "not_verified"),
        ("Not visible", "not_visible"),
    ],
)
def test_payment_status_parsing(value, status):
    assert be._payment_status(value) == status


# ---------------------------------------------------------------------------
# 6. Scoring integration (deterministic, after matching)
# ---------------------------------------------------------------------------


def test_payment_not_verified_heavily_reduces_client_quality():
    job = _safe_job(payment_verification="Not verified", client_total_spend="$50k", hire_rate="80%")
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]

    without = score(md, dossier_strength=85, missing_critical_fields=0)
    withb = score(md, dossier_strength=85, missing_critical_fields=0, beginner_eval=beginner)

    assert withb.sub_scores["client_quality"] <= 3
    assert withb.sub_scores["client_quality"] < without.sub_scores["client_quality"]


def test_fifty_plus_proposals_heavily_reduces_competition():
    job = _safe_job(proposal_count="60")
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]
    withb = score(md, dossier_strength=85, missing_critical_fields=0, beginner_eval=beginner)
    assert withb.sub_scores["competition"] <= 2


def test_under_15_proposals_improves_competition():
    job = _safe_job(proposal_count="8", posted_date="2 days ago")  # not fresh → isolate the boost
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]

    without = score(md, dossier_strength=85, missing_critical_fields=0)
    withb = score(md, dossier_strength=85, missing_critical_fields=0, beginner_eval=beginner)

    assert withb.sub_scores["competition"] > without.sub_scores["competition"]
    assert withb.sub_scores["competition"] >= 11


def test_expert_level_downgrades_confidence():
    job = _safe_job(experience_level="Expert")
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]

    without = score(md, dossier_strength=85, missing_critical_fields=0)
    withb = score(md, dossier_strength=85, missing_critical_fields=0, beginner_eval=beginner)

    order = ["HIGH", "MEDIUM", "LOW"]
    assert order.index(withb.confidence) >= order.index(without.confidence)
    assert withb.confidence != without.confidence


# ---------------------------------------------------------------------------
# 7. Recommendation integration
# ---------------------------------------------------------------------------


class _FakeScore:
    def __init__(self, total, confidence="HIGH"):
        self.total = total
        self.confidence = confidence
        self.sub_scores = {k: 0 for k in (
            "profile_fit", "portfolio_proof", "client_quality", "competition", "budget_value"
        )}
        self.job_fingerprint = "fp"


def test_instant_no_overrides_high_profile_match_recommendation():
    # Strong match + high score, but 50+ proposals must force Do Not Proceed.
    job = _safe_job(proposal_count="60")
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]
    high_score = _FakeScore(total=92, confidence="HIGH")

    rec = recommend(high_score, md, beginner_eval=beginner)
    assert rec["verdict"] == DO_NOT_PROCEED
    # The user sees the beginner safety reason as the reasoning.
    assert REASON_PROPOSALS_50_PLUS in rec["why"] or any(
        REASON_PROPOSALS_50_PLUS in c for c in rec["concerns"]
    )


def test_caution_caps_a_strong_verdict():
    job = _safe_job(experience_level="Expert")  # warning → Proceed With Caution
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]
    high_score = _FakeScore(total=92, confidence="HIGH")

    rec = recommend(high_score, md, beginner_eval=beginner)
    assert rec["verdict"] == "Proceed with Caution"  # capped down from Strongly Proceed


def test_apply_confidently_keeps_strong_verdict():
    job = _safe_job()  # all green
    md = match_evaluate(job, _strong_evidence())
    beginner = md["beginner_evaluation"]
    assert beginner["result"] == APPLY_CONFIDENTLY
    high_score = _FakeScore(total=92, confidence="HIGH")

    rec = recommend(high_score, md, beginner_eval=beginner)
    assert rec["verdict"] == "Strongly Proceed"  # not capped


def test_recommendation_reasoning_stays_within_two_lines():
    for job in (
        _safe_job(payment_verification="Not verified", proposal_count="70"),  # instant no
        _safe_job(experience_level="Expert"),                                  # caution
        _safe_job(),                                                           # apply confidently
    ):
        md = match_evaluate(job, _strong_evidence())
        beginner = md["beginner_evaluation"]
        rec = recommend(_FakeScore(total=70), md, beginner_eval=beginner)
        assert rec["why"].count("\n") <= 1
        assert rec["reasoning"].count("\n") <= 1


def test_recommend_without_beginner_eval_is_unchanged():
    # Backwards-compatibility guard: omitting beginner_eval must not alter the
    # verdict the score alone produces.
    job = _safe_job(proposal_count="60")  # would be Instant No if beginner ran
    md = match_evaluate(job, _strong_evidence())
    rec = recommend(_FakeScore(total=92, confidence="HIGH"), md)
    assert rec["verdict"] == "Strongly Proceed"  # no beginner override applied


# ---------------------------------------------------------------------------
# 8. UI: clean card hides internals; debug breakdown shows them
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.texts: list[str] = []

    def _record(self, *args, **kwargs):
        for a in args:
            if isinstance(a, str):
                self.texts.append(a)
        return _Ctx()

    def __getattr__(self, name):
        return lambda *a, **kw: self._record(*a, **kw)


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def fake_streamlit(monkeypatch):
    from app.ui import output_screen, theme

    fake = _Recorder()
    monkeypatch.setattr(output_screen, "st", fake, raising=True)
    monkeypatch.setattr(theme, "st", fake, raising=True)
    return fake


def test_beginner_card_clean_hides_internals(fake_streamlit):
    from app.ui import output_screen

    beginner = evaluate(_safe_job(payment_verification="Not verified"))
    output_screen.render_beginner_check_card(beginner, show_debug=False)

    joined = "\n".join(fake_streamlit.texts)
    assert "Beginner Job Check" in joined
    assert DO_NOT_PROCEED in joined
    assert REASON_PAYMENT_NOT_VERIFIED in joined
    # No rule-engine internals / raw field buckets leak on the clean surface.
    for forbidden in (
        "Beginner evaluator breakdown",
        "Triggered rule",
        "Proposal count bucket",
        "Posted age bucket",
        "not_verified",
        "instant_no",
    ):
        assert forbidden not in joined


def test_beginner_card_debug_shows_breakdown(fake_streamlit):
    from app.ui import output_screen

    beginner = evaluate(_safe_job(experience_level="Expert", proposal_count="20"))
    output_screen.render_beginner_check_card(beginner, show_debug=True)

    joined = "\n".join(fake_streamlit.texts)
    assert "Beginner evaluator breakdown" in joined
    assert "Payment verification" in joined
    assert "Proposal count bucket" in joined
    assert "Posted age bucket" in joined
    assert "Experience level warning" in joined
    assert "Triggered rule" in joined


def test_beginner_card_reasons_capped_at_two(fake_streamlit):
    from app.ui import output_screen

    # A job with multiple warnings still shows at most two reasons cleanly.
    beginner = evaluate(
        _safe_job(experience_level="Expert", proposal_count="30", client_rating="3.2 of 5", posted_date="5 days ago")
    )
    assert len(beginner["reasons"]) <= 2
    output_screen.render_beginner_check_card(beginner, show_debug=False)
    # Count the rendered bullet reason lines (st.write("- ...")).
    bullet_lines = [t for t in fake_streamlit.texts if t.startswith("- ")]
    assert len(bullet_lines) <= 2
