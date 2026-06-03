"""Scoring and Confidence Module.

Produces a 100-point score plus High/Medium/Low confidence. Weights
match the build plan:

    Profile Fit       30
    Portfolio Proof   20
    Client Quality    20
    Competition       15
    Budget / Value    15

Every component is recalculated for each opportunity from THAT
opportunity's match result. There are no fixed/default portfolio scores
and nothing is reused across opportunities:

* When the LLM opportunity matcher ran, its evidence comparison — the
  per-requirement match levels and the ``portfolio_proof_analysis``
  block — drives the deterministic bands below. The LLM never sets a
  final numeric score itself; it only supplies match signals.
* When the matcher fell back to rule-based logic, the same components
  are computed from job-relevant proof counts, so they still vary per
  opportunity.

Confidence comes from two signals — how many critical screenshot fields
are missing and how strong the dossier is. Low confidence later softens
the recommendation by one tier in :mod:`app.services.recommendation`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


WEIGHTS = {
    "profile_fit": 30,
    "portfolio_proof": 20,
    "client_quality": 20,
    "competition": 15,
    "budget_value": 15,
}

# Recorded on every component so the provenance is auditable: signals
# come from the LLM match result, the numeric value is deterministic.
_SCORE_SOURCE = "llm_match_result + deterministic_scoring"


_CLIENT_QUALITY_POINTS = {"strong": 20, "average": 12, "weak": 5, "unknown": 8}
_COMPETITION_POINTS = {"low": 15, "medium": 9, "high": 3, "unknown": 8}
_BUDGET_POINTS = {"high": 15, "acceptable": 11, "low": 4, "unknown": 8}

# Rating → 0..1 strength signal (used to blend LLM ratings into the
# deterministic component math).
_RATING_SIGNAL = {"strong": 1.0, "medium": 0.6, "weak": 0.3, "unknown": 0.45}
_CONF_POS = {"high": 1.0, "medium": 0.7, "low": 0.45, "unknown": 0.5}


@dataclass
class ScoreComponent:
    """One weighted component plus the explanation that justifies it."""

    value: int
    max_value: int
    short_reason: str = ""
    evidence_ids_used: list = field(default_factory=list)
    confidence: str = "low"  # per-component "high" | "medium" | "low"
    source: str = _SCORE_SOURCE


@dataclass
class ScoreResult:
    total: int
    sub_scores: dict = field(default_factory=dict)  # {component: int} (back-compat)
    confidence: str = "LOW"  # overall "HIGH" | "MEDIUM" | "LOW"
    components: dict = field(default_factory=dict)  # {component: ScoreComponent}
    job_fingerprint: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _position(*signals: Any) -> float:
    vals = [float(s) for s in signals if isinstance(s, (int, float))]
    if not vals:
        return 0.5
    return _clamp(sum(vals) / len(vals), 0.0, 1.0)


def _llm_match(match_data: dict) -> dict:
    m = (match_data or {}).get("llm_match")
    return m if isinstance(m, dict) else {}


def _confidence_for(missing_critical_fields: int, dossier_strength: int) -> str:
    if missing_critical_fields >= 3 or dossier_strength < 40:
        return "LOW"
    if missing_critical_fields == 0 and dossier_strength > 70:
        return "HIGH"
    return "MEDIUM"


_CONFIDENCE_ORDER = ("HIGH", "MEDIUM", "LOW")


def _downgrade_confidence(confidence: str) -> str:
    """Lower confidence by exactly one tier (HIGH→MEDIUM→LOW, LOW stays LOW)."""
    try:
        idx = _CONFIDENCE_ORDER.index(confidence)
    except ValueError:
        return confidence
    return _CONFIDENCE_ORDER[min(idx + 1, len(_CONFIDENCE_ORDER) - 1)]


def _level_word(signal: float) -> str:
    if signal >= 0.75:
        return "strong"
    if signal >= 0.45:
        return "moderate"
    if signal > 0:
        return "limited"
    return "no"


# ---------------------------------------------------------------------------
# Portfolio Proof /20  (the component that used to be static)
# ---------------------------------------------------------------------------


def _portfolio_reason(rating: str, direct: int, adjacent: int, missing: int) -> str:
    if rating == "strong" and direct:
        base = f"Strong direct proof — {direct} matching item(s)."
    elif direct and adjacent:
        base = f"{direct} direct and {adjacent} adjacent proof point(s)."
    elif direct:
        base = f"{direct} directly matching proof point(s)."
    elif adjacent:
        base = f"{adjacent} adjacent / related proof point(s)."
    else:
        base = "Mostly generic proof for this opportunity."
    if missing:
        base += f" {missing} requirement(s) lack proof."
    return base


def _portfolio_from_llm(ppa: dict) -> ScoreComponent:
    """Map the LLM portfolio_proof_analysis block to a /20 score.

    The rating fixes a non-overlapping band; position within the band
    blends the LLM ``score_signal``, evidence richness, direct-vs-adjacent
    proof, and confidence, then deducts for missing requirements.
    """
    rating = (ppa.get("rating") or "unknown").lower()
    direct = list(ppa.get("direct_proof") or [])
    adjacent = list(ppa.get("adjacent_proof") or [])
    missing = list(ppa.get("missing_proof") or [])
    ids = [str(i) for i in (ppa.get("evidence_ids_used") or [])]
    matched_total = sum(
        len(ppa.get(k) or [])
        for k in (
            "matched_portfolio_items", "matched_projects", "matched_testimonials",
            "matched_work_history", "matched_skills", "matched_tools",
        )
    )
    conf = (ppa.get("confidence") or "low").lower()
    try:
        signal = float(ppa.get("score_signal"))
    except (TypeError, ValueError):
        signal = None

    # Grounding gate. ``evidence_ids_used`` is the ONLY field validated
    # against the real evidence subset (match_engine._coerce_evidence_ids
    # filters it against allowed_ids); the proof *lists* below are
    # free-text and could be model-hallucinated. So when no validated
    # evidence id backs this opportunity, those free-text lists are
    # advisory only: they cannot select a "strong"/"medium" band or the
    # direct-proof sub-band, and the most the component can earn is the
    # low "some signal" band. This prevents a fabricated
    # direct_proof:["Built X"] with empty evidence_ids_used from landing
    # in the 17-20 band — closing the audit's grounding gap while still
    # letting genuinely-evidenced proof score across the full range.
    n_ids = len(set(ids))
    grounded = n_ids > 0

    has_direct = bool(direct)
    has_adjacent = bool(adjacent)
    has_any = bool(direct or adjacent or ids or matched_total)
    if not grounded:
        if rating in ("strong", "medium"):
            rating = "weak"
        has_direct = False
        has_adjacent = False
        matched_total = 0

    # Band by rating + proof composition. Bands never overlap, so a
    # higher rating always outranks a lower one regardless of position.
    if rating == "strong":
        lo, hi = (17, 20) if has_direct else (13, 16)
    elif rating == "medium":
        lo, hi = (12, 16) if (has_direct or has_adjacent) else (8, 11)
    elif rating == "weak":
        if has_direct or has_adjacent:
            lo, hi = 7, 11
        elif has_any:
            lo, hi = 1, 6
        else:
            lo, hi = 0, 0
    else:  # unknown
        lo, hi = (1, 6) if has_any else (0, 0)

    if hi == 0:
        return ScoreComponent(
            0, 20, "No proof in your evidence matches this opportunity.",
            [], conf,
        )

    signal_pos = (signal / 100.0) if signal is not None else None
    richness = min(len(set(ids)) + matched_total, 6) / 6.0
    direct_pos = (
        1.0 if (has_direct and not has_adjacent)
        else 0.75 if has_direct
        else 0.45 if has_adjacent
        else 0.2
    )
    pos = _position(signal_pos, richness, direct_pos, _CONF_POS.get(conf, 0.5))
    pos = _clamp(pos - min(len(missing), 4) * 0.06, 0.0, 1.0)

    value = int(round(_clamp(lo + pos * (hi - lo), lo, hi)))
    reason = _portfolio_reason(rating, len(direct), len(adjacent), len(missing))
    return ScoreComponent(value, 20, reason, list(dict.fromkeys(ids))[:8], conf)


def _portfolio_from_rule(rule: dict) -> ScoreComponent:
    """Rule-based portfolio /20 — still scoped to the current opportunity.

    ``relevant_count``/``relevance`` come from the job-relevance pass in
    :mod:`app.services.match_engine`, so two different opportunities with
    the same dossier produce different portfolio scores here too.
    """
    evidence_count = int(rule.get("evidence_count") or 0)
    relevant = int(rule.get("relevant_count") or 0)
    relevance = float(rule.get("relevance") or 0.0)
    ids = [str(i) for i in (rule.get("matched_ids") or [])]

    if evidence_count == 0:
        return ScoreComponent(
            0, 20, "No portfolio or project proof in your evidence yet.", [], "low",
        )

    effective = relevant + 0.4 * max(0, evidence_count - relevant)
    if relevant >= 3 and relevance >= 0.5:
        lo, hi = 14, 18
    elif effective >= 2.5:
        lo, hi = 11, 15
    elif effective >= 1.5:
        lo, hi = 7, 11
    elif relevant >= 1:
        lo, hi = 5, 9
    else:
        lo, hi = 1, 5

    pos = _clamp(min(effective, 5) / 5.0 * 0.6 + relevance * 0.4, 0.0, 1.0)
    value = int(round(_clamp(lo + pos * (hi - lo), lo, hi)))

    if relevant >= 1:
        reason = (
            f"{relevant} of {evidence_count} proof point(s) relevant to this opportunity."
        )
    else:
        reason = (
            f"{evidence_count} proof point(s), but none clearly match this opportunity."
        )
    conf = "medium" if relevant >= 2 else "low"
    return ScoreComponent(value, 20, reason, ids[:8], conf)


def _portfolio_component(match_data: dict) -> ScoreComponent:
    llm = _llm_match(match_data)
    if "portfolio_proof_analysis" in llm:
        return _portfolio_from_llm(llm.get("portfolio_proof_analysis") or {})
    return _portfolio_from_rule((match_data or {}).get("portfolio_proof_match") or {})


# ---------------------------------------------------------------------------
# Profile Fit /30  (skill + industry + experience, all opportunity-relative)
# ---------------------------------------------------------------------------


def _dim_signal(llm: dict, md: dict, dim: str) -> float:
    """0..1 signal for a dimension, blending rule score with LLM rating."""
    rule_score = (md.get(dim) or {}).get("score")
    rating = (llm.get(dim) or {}).get("rating")
    if rating in _RATING_SIGNAL:
        llm_sig = _RATING_SIGNAL[rating]
        if isinstance(rule_score, (int, float)):
            return _clamp((float(rule_score) + llm_sig) / 2, 0.0, 1.0)
        return llm_sig
    if isinstance(rule_score, (int, float)):
        return _clamp(float(rule_score), 0.0, 1.0)
    return 0.0


def _skill_signal(llm: dict, md: dict) -> tuple[float, list[str]]:
    """Skill coverage 0..1 + the evidence ids that backed it.

    Prefers the LLM per-requirement analysis (direct/adjacent/weak/
    missing); falls back to the rule-based skill coverage score.
    """
    rsa = llm.get("required_skill_analysis")
    if isinstance(rsa, list) and rsa:
        weight = {"direct": 1.0, "adjacent": 0.5, "weak": 0.2, "missing": 0.0}
        total = sum(weight.get((r or {}).get("match_level"), 0.0) for r in rsa)
        ids: list[str] = []
        for r in rsa:
            for ev in (r or {}).get("matching_evidence_ids") or []:
                if ev not in ids:
                    ids.append(str(ev))
        return _clamp(total / max(len(rsa), 1), 0.0, 1.0), ids[:8]

    rule_score = float((md.get("skill_match") or {}).get("score", 0.0) or 0.0)
    rating = (llm.get("skill_match") or {}).get("rating")
    if rating in _RATING_SIGNAL:
        rule_score = (rule_score + _RATING_SIGNAL[rating]) / 2
    return _clamp(rule_score, 0.0, 1.0), []


def _profile_fit_component(match_data: dict) -> ScoreComponent:
    md = match_data or {}
    llm = _llm_match(md)

    skill_sig, skill_ids = _skill_signal(llm, md)
    industry_sig = _dim_signal(llm, md, "industry_match")
    experience_sig = _dim_signal(llm, md, "experience_match")

    value = int(round(_clamp(skill_sig * 15 + industry_sig * 8 + experience_sig * 7, 0, 30)))
    reason = (
        f"Skills {_level_word(skill_sig)}, industry {_level_word(industry_sig)}, "
        f"experience {_level_word(experience_sig)} overlap with this opportunity."
    )
    if skill_ids:
        conf = "high" if skill_sig >= 0.66 else "medium"
    else:
        conf = "medium" if skill_sig > 0 else "low"
    return ScoreComponent(value, 30, reason, skill_ids, conf)


# ---------------------------------------------------------------------------
# Client Quality /20, Competition /15, Budget / Value /15
# ---------------------------------------------------------------------------


_CLIENT_REASON = {
    "strong": "Client signals look strong (verified / rating / spend).",
    "average": "Client signals are average.",
    "weak": "Client signals are weak.",
    "unknown": "Client details weren't visible, so this is a neutral estimate.",
}
_COMPETITION_REASON = {
    "low": "Low competition — few proposals so far.",
    "medium": "Moderate competition.",
    "high": "High competition — many proposals already submitted.",
    "unknown": "Proposal count not visible.",
}
_BUDGET_REASON = {
    "high": "Budget is at or above your target range.",
    "acceptable": "Budget is within an acceptable range.",
    "low": "Budget is below your target range.",
    "unknown": "Budget or rate not visible.",
}


def _client_component(match_data: dict) -> ScoreComponent:
    md = match_data or {}
    key = md.get("client_quality", "unknown")
    if key not in _CLIENT_QUALITY_POINTS:
        key = "unknown"
    lm = _llm_match(md).get("client_quality") or {}
    # If the screenshot gave no client signals, fall back to the LLM read.
    if key == "unknown":
        mapping = {"strong": "strong", "medium": "average", "weak": "weak"}
        if lm.get("rating") in mapping:
            key = mapping[lm["rating"]]
    value = _CLIENT_QUALITY_POINTS[key]
    conf = "low" if key == "unknown" else "medium"
    ids = [str(i) for i in (lm.get("evidence_ids_used") or [])]
    return ScoreComponent(value, 20, _CLIENT_REASON[key], ids, conf)


def _competition_component(match_data: dict) -> ScoreComponent:
    key = (match_data or {}).get("competition_level", "unknown")
    if key not in _COMPETITION_POINTS:
        key = "unknown"
    conf = "low" if key == "unknown" else "medium"
    return ScoreComponent(_COMPETITION_POINTS[key], 15, _COMPETITION_REASON[key], [], conf)


def _budget_component(match_data: dict) -> ScoreComponent:
    key = (match_data or {}).get("budget_match", "unknown")
    if key not in _BUDGET_POINTS:
        key = "unknown"
    conf = "low" if key == "unknown" else "medium"
    return ScoreComponent(_BUDGET_POINTS[key], 15, _BUDGET_REASON[key], [], conf)


# ---------------------------------------------------------------------------
# Beginner Job Evaluator adjustments  (deterministic, applied AFTER matching)
# ---------------------------------------------------------------------------


def _apply_beginner_adjustments(
    components: dict[str, ScoreComponent],
    confidence: str,
    beginner_eval: dict,
) -> str:
    """Fold the beginner checklist into the component scores + confidence.

    These are deterministic rules layered on top of the LLM-informed
    component math — the LLM never decides these numbers:

    * Payment not verified → heavily reduce Client Quality.
    * Proposal count 50+ → heavily reduce Competition.
    * Proposal count under 15 → improve Competition.
    * Posted today/yesterday → small freshness boost to Competition.
    * Posted 3+ days ago / Expert level / missing beginner fields →
      lower overall confidence by one tier.

    ``components`` is mutated in place; the (possibly downgraded)
    confidence is returned.
    """
    signals = (beginner_eval or {}).get("score_signals") or {}

    client = components["client_quality"]
    competition = components["competition"]

    if signals.get("payment_not_verified"):
        client.value = min(client.value, 3)
        client.short_reason = (
            "Payment is not verified — high risk of not getting paid."
        )
        client.confidence = "medium"

    if signals.get("proposals_50_plus"):
        competition.value = min(competition.value, 2)
        competition.short_reason = (
            "50+ proposals — competition is too high for a beginner profile."
        )
        competition.confidence = "medium"
    elif signals.get("proposals_under_15"):
        boosted = min(competition.max_value, max(competition.value, 11))
        if boosted != competition.value:
            competition.value = boosted
            competition.short_reason = (
                "Under 15 proposals — competition is still favorable for a beginner."
            )
        if signals.get("posted_fresh"):
            competition.value = min(competition.max_value, competition.value + 1)

    if (
        signals.get("posted_stale")
        or signals.get("expert_level")
        or (beginner_eval or {}).get("missing_fields")
    ):
        confidence = _downgrade_confidence(confidence)

    return confidence


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score(
    match_data: dict,
    dossier_strength: int,
    missing_critical_fields: int,
    *,
    beginner_eval: dict | None = None,
) -> ScoreResult:
    """Return the weighted score + confidence for the supplied match data.

    Each of the five components is recomputed from ``match_data`` (which
    carries this opportunity's job fingerprint and, when available, the
    LLM evidence-comparison signals). ``total`` always equals the sum of
    the component values.

    When ``beginner_eval`` (the output of
    :func:`app.services.beginner_evaluator.evaluate`) is supplied, its
    deterministic signals adjust the Client Quality / Competition
    components and the overall confidence. Omitting it leaves scoring
    exactly as it was, so callers that don't run the beginner checklist
    are unaffected.
    """
    components: dict[str, ScoreComponent] = {
        "profile_fit": _profile_fit_component(match_data),
        "portfolio_proof": _portfolio_component(match_data),
        "client_quality": _client_component(match_data),
        "competition": _competition_component(match_data),
        "budget_value": _budget_component(match_data),
    }
    confidence = _confidence_for(missing_critical_fields, dossier_strength)
    if beginner_eval:
        confidence = _apply_beginner_adjustments(components, confidence, beginner_eval)
    sub_scores = {key: comp.value for key, comp in components.items()}
    total = sum(sub_scores.values())
    return ScoreResult(
        total=total,
        sub_scores=sub_scores,
        confidence=confidence,
        components=components,
        job_fingerprint=str((match_data or {}).get("job_fingerprint") or ""),
    )
