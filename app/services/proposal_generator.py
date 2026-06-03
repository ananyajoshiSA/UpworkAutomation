"""Proposal Generator.

Drafts a grounded Upwork proposal *only* from the evidence index. The
generator never invents past clients, projects, metrics,
certifications, tools, or outcomes — every factual claim it emits is
tied back to a proof point's ``evidence_id``.

The current implementation is a deterministic non-LLM placeholder so
the UI can call ``generate`` end-to-end before the LLM-backed prompt
in :mod:`app.prompts.proposal_prompt` is wired up. The function
signature and return shape are stable: an LLM-backed version can
replace the body without changing callers.

Output contract::

    {
        "proposal": str,                # the drafted text
        "factual_claims": list[dict],   # each claim has an evidence_id
        "target_length": (min, max),    # word count band
        "complexity": str,              # "simple" | "standard" | "complex"
        "word_count": int,
    }
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from app.config import get_settings
from app.prompts.proposal_prompt import render_prompt as render_proposal_prompt
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.services import llm_client
from app.services.match_engine import _is_missing as is_missing
from app.services.verification import (
    strip_evidence_ids,
    verify as run_verification,
)


TASK_NAME = "proposal_generation"


# Default user-facing error message for size-related API failures. The
# raw provider error message (already sanitized of organization IDs) is
# attached separately via ``ProposalGenerationError.sanitized_error``.
SIZE_FAILURE_USER_MESSAGE = (
    "Proposal generation failed because the request was too large for "
    "the current API token limit. The app reduced context and retried "
    "once, but the API still rejected the request."
)


# Claim types we will allow into the compact proposal context, in
# preference order. Anything outside this whitelist is treated as low
# value and dropped before the LLM call.
PREFERRED_CLAIM_TYPES: tuple[str, ...] = (
    "positioning",
    "selected_offer",
    "service",
    "deliverable",
    "skill",
    "tool",
    "industry",
    "project",
    "work_history",
    "metric",
    "testimonial",
    "achievement",
    "proposal_preference",
)


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}


# Cap on a single claim's text inside the compact context. Long
# free-text claim bodies are what blow the request up.
_CLAIM_TEXT_CAP_DEFAULT = 240
_CLAIM_TEXT_CAP_SMALL = 160


class ProposalGenerationError(RuntimeError):
    """Raised when proposal generation cannot be completed.

    The UI catches this so the user sees a clear failure message rather
    than a silent fake proposal.

    ``sanitized_error`` is the provider-reported reason with
    organization IDs and other private metadata stripped. ``meta`` is a
    small dict of context the UI may surface (provider, model, evidence
    points sent, approximate context size, retry used).
    """

    def __init__(
        self,
        message: str,
        *,
        status: str = "failed",
        llm_result: Any = None,
        sanitized_error: Optional[str] = None,
        meta: Optional[dict] = None,
    ):
        super().__init__(message)
        self.status = status
        self.llm_result = llm_result
        self.sanitized_error = sanitized_error
        self.meta = meta or {}


LENGTH_BANDS: dict[str, tuple[int, int]] = {
    "simple": (100, 150),
    "standard": (150, 250),
    "complex": (250, 350),
}


FORBIDDEN_PHRASES: tuple[str, ...] = (
    "I am excited to apply",
    "I came across your job posting",
    "I believe I am the perfect fit",
    "Dear hiring manager",
    "I hope this message finds you well",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _field(confirmed_job: dict, key: str) -> str:
    entry = (confirmed_job or {}).get(key) or {}
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _present(value: str) -> Optional[str]:
    return None if is_missing(value) else value


def _proof_attr(proof: Any, name: str, default: Any = None) -> Any:
    if hasattr(proof, name):
        return getattr(proof, name)
    if isinstance(proof, dict):
        return proof.get(name, default)
    return default


def _proofs_by_type(
    evidence: Iterable, claim_types: set[str]
) -> list:
    return [p for p in evidence if _proof_attr(p, "claim_type") in claim_types]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w[\w'-]*\b", text))


def _detect_complexity(confirmed_job: dict) -> str:
    description = _field(confirmed_job, "job_description")
    duration = _field(confirmed_job, "project_duration").lower()
    experience_level = _field(confirmed_job, "experience_level").lower()

    description_len = _word_count(description) if not is_missing(description) else 0

    if "expert" in experience_level or "more than 6" in duration or description_len > 200:
        return "complex"
    if "entry" in experience_level or "less than 1" in duration or description_len < 50:
        return "simple"
    return "standard"


def _strip_forbidden(text: str) -> str:
    for phrase in FORBIDDEN_PHRASES:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        text = pattern.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _trim_to_band(text: str, band: tuple[int, int]) -> str:
    min_words, max_words = band
    words = text.split()
    if len(words) <= max_words:
        return text
    # Trim from the soft CTA backward, never from credibility/understanding.
    trimmed = " ".join(words[:max_words])
    return trimmed.rstrip(",;:- ") + "."


# ---------------------------------------------------------------------------
# Citation helper
# ---------------------------------------------------------------------------


def _claim(text: str, proof: Any, *, kind: str) -> dict[str, Any]:
    return {
        "text": text,
        "kind": kind,
        "evidence_id": _proof_attr(proof, "evidence_id"),
        "source_file": _proof_attr(proof, "source_file"),
        "claim_type": _proof_attr(proof, "claim_type"),
    }


def _bare_claim(text: str, kind: str) -> dict[str, Any]:
    return {
        "text": text,
        "kind": kind,
        "evidence_id": None,
        "source_file": None,
        "claim_type": None,
    }


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _opening(confirmed_job: dict, claims: list[dict]) -> str:
    title = _present(_field(confirmed_job, "job_title"))
    client_need = _present(_field(confirmed_job, "client_need"))
    if title and client_need:
        return f"Saw your {title} post — the priority of {client_need.lower()} matches what I work on."
    if title:
        return f"Quick note on your {title} post — sharing how I'd approach it."
    if client_need:
        return f"On your stated goal — {client_need} — here is how I would approach it."
    return "Here is how I'd approach the brief you've shared."


def _credibility(evidence: list, matched_skills: list[str], claims: list[dict]) -> str:
    parts: list[str] = []
    experience_proofs = _proofs_by_type(evidence, {"experience", "work_history"})
    if experience_proofs:
        proof = experience_proofs[0]
        text = str(_proof_attr(proof, "claim_text", "")).strip()
        if text:
            parts.append(text)
            claims.append(_claim(text, proof, kind="experience"))

    skill_proofs = _proofs_by_type(evidence, {"skill", "tool"})
    cited_skills: list[str] = []
    for proof in skill_proofs:
        for skill in (_proof_attr(proof, "skills", []) or []) + (
            _proof_attr(proof, "tools", []) or []
        ):
            if skill and skill.lower() in {s.lower() for s in matched_skills}:
                if skill not in cited_skills:
                    cited_skills.append(skill)
                    claims.append(_claim(skill, proof, kind="skill"))
        if len(cited_skills) >= 4:
            break
    if cited_skills:
        parts.append("Hands-on with " + ", ".join(cited_skills[:4]) + ".")

    achievements = _proofs_by_type(evidence, {"achievement", "metric"})
    if achievements:
        proof = achievements[0]
        text = str(_proof_attr(proof, "claim_text", "")).strip()
        if text:
            parts.append(text)
            claims.append(_claim(text, proof, kind="achievement"))

    if not parts:
        return "I'll lean on approach here — dossier evidence for this brief is light."
    return " ".join(parts)


def _understanding(confirmed_job: dict) -> str:
    need = _present(_field(confirmed_job, "client_need"))
    deliverables = _present(_field(confirmed_job, "required_deliverables"))
    description = _present(_field(confirmed_job, "job_description"))
    if need and deliverables:
        return f"As I read it, the goal is {need}, anchored on {deliverables}."
    if need:
        return f"As I read it, the goal is {need}."
    if description:
        return f"Reading the brief, the priority looks like: {description[:140]}"
    return "Happy to confirm the exact priority once you share more context."


def _approach(evidence: list, claims: list[dict]) -> str:
    tools = _proofs_by_type(evidence, {"tool"})
    services = _proofs_by_type(evidence, {"service", "deliverable"})
    bullets: list[str] = []

    if services:
        for proof in services[:2]:
            text = str(_proof_attr(proof, "claim_text", "")).strip()
            if text:
                bullets.append(text)
                claims.append(_claim(text, proof, kind="service"))

    if tools:
        proof = tools[0]
        tool_list = _proof_attr(proof, "tools", []) or []
        if tool_list:
            phrase = "Stack I'd use: " + ", ".join(tool_list[:5])
            bullets.append(phrase)
            claims.append(_claim(phrase, proof, kind="tool"))

    if not bullets:
        bullets.append("Step 1: confirm scope and success criteria with you.")
        bullets.append("Step 2: ship a small milestone you can review before we go wide.")

    return "How I'd approach it:\n- " + "\n- ".join(bullets[:3])


def _differentiator(evidence: list, claims: list[dict]) -> str:
    positioning = _proofs_by_type(evidence, {"positioning"})
    if positioning:
        proof = positioning[0]
        text = str(_proof_attr(proof, "claim_text", "")).strip()
        if text:
            claims.append(_claim(text, proof, kind="positioning"))
            return f"Why me, briefly: {text}"
    portfolio = _proofs_by_type(evidence, {"portfolio", "testimonial", "project"})
    if portfolio:
        proof = portfolio[0]
        text = str(_proof_attr(proof, "claim_text", "")).strip()
        if text:
            claims.append(_claim(text, proof, kind="portfolio"))
            return f"For context on past work: {text}"
    return "Why me: I scope tightly and only commit to outcomes I can evidence."


def _smart_question(confirmed_job: dict, match_data: Optional[dict]) -> str:
    if is_missing(_field(confirmed_job, "required_deliverables")):
        return "One question: what does done look like for the first milestone?"
    if is_missing(_field(confirmed_job, "budget_or_rate")):
        return "One question: is there a target budget shape (fixed vs. hourly) you're anchored on?"
    if is_missing(_field(confirmed_job, "project_duration")):
        return "One question: what timeline are you aiming for on phase 1?"
    if match_data and match_data.get("missing_critical_fields"):
        missing = match_data["missing_critical_fields"][0]
        nice = missing.replace("_", " ")
        return f"One question to align: could you share more on {nice}?"
    return "One question: what would make this engagement feel like a clear win three months in?"


def _soft_cta() -> str:
    return "Happy to walk through how I'd scope phase 1 if useful."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _local_generate(
    confirmed_job: dict,
    evidence_list: list,
    matched_skills: list[str],
    band: tuple[int, int],
) -> tuple[str, list[dict[str, Any]]]:
    """Run the deterministic local section assembler."""
    claims: list[dict[str, Any]] = []
    sections = [
        _opening(confirmed_job, claims),
        _credibility(evidence_list, matched_skills, claims),
        _understanding(confirmed_job),
        _approach(evidence_list, claims),
        _differentiator(evidence_list, claims),
        _smart_question(confirmed_job, None),
        _soft_cta(),
    ]
    proposal = "\n\n".join(s for s in sections if s)
    proposal = _strip_forbidden(proposal)
    proposal = _trim_to_band(proposal, band)
    return proposal, claims


def _serialize_job_for_prompt(confirmed_job: dict) -> str:
    safe: dict[str, str] = {}
    for key, entry in (confirmed_job or {}).items():
        if not isinstance(entry, dict):
            safe[key] = str(entry or "").strip()
            continue
        value = str(entry.get("value", "") or "").strip()
        safe[key] = value or "Not visible"
    return json.dumps(safe, ensure_ascii=False, indent=2)


def _serialize_evidence_for_prompt(evidence_list: list, *, claim_text_cap: int) -> str:
    """Serialize the compact evidence subset into the JSON block that the
    proposal prompt embeds. The full proof point — and the source file
    that holds it — is never serialized; only the evidence_id, source
    type, claim type, and a truncated claim text are sent."""
    out: list[dict[str, Any]] = []
    for proof in evidence_list:
        out.append(
            {
                "evidence_id": _proof_attr(proof, "evidence_id"),
                "source_type": _proof_attr(proof, "source_type"),
                "claim_type": _proof_attr(proof, "claim_type"),
                "claim": str(_proof_attr(proof, "claim_text", "") or "").strip()[
                    :claim_text_cap
                ],
            }
        )
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Compact proposal context builder
# ---------------------------------------------------------------------------


def _tokens_from(value: str) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{2,}", value or "")
        if t
    }


def _job_signal_tokens(confirmed_job: dict) -> set[str]:
    tokens: set[str] = set()
    for key in (
        "job_title",
        "job_description",
        "client_need",
        "required_deliverables",
        "required_skills",
    ):
        tokens |= _tokens_from(_field(confirmed_job, key))
    return tokens


def _score_proof(
    proof: Any,
    *,
    job_tokens: set[str],
    matched_skills_norm: set[str],
    matched_industries_norm: set[str],
    referenced_ids: set[str],
) -> float:
    """Score a proof point for relevance to the current job posting."""
    score = 0.0

    claim_type = (_proof_attr(proof, "claim_type") or "").lower()
    if claim_type in PREFERRED_CLAIM_TYPES:
        # Earlier entries in the preference list get a small extra nudge.
        rank = PREFERRED_CLAIM_TYPES.index(claim_type)
        score += 6.0 + (len(PREFERRED_CLAIM_TYPES) - rank) * 0.15
    elif claim_type == "other_relevant_evidence":
        score -= 2.0

    # Lower source_priority = higher quality source.
    source_priority = _proof_attr(proof, "source_priority", 17)
    try:
        score += max(0.0, 4.0 - (float(source_priority) / 5.0))
    except (TypeError, ValueError):
        pass

    confidence = (_proof_attr(proof, "confidence") or "low").lower()
    score += _CONFIDENCE_RANK.get(confidence, 1)

    # If the match engine had already flagged this proof point, prefer it.
    if _proof_attr(proof, "evidence_id") in referenced_ids:
        score += 5.0

    # Overlap with matched skills / industries that the match engine
    # already proved are relevant to this job.
    skill_overlap = 0
    for entry in _proof_attr(proof, "skills", []) or []:
        if str(entry).strip().lower() in matched_skills_norm:
            skill_overlap += 1
    for entry in _proof_attr(proof, "tools", []) or []:
        if str(entry).strip().lower() in matched_skills_norm:
            skill_overlap += 1
    score += min(skill_overlap, 4) * 1.5

    industry_overlap = 0
    for entry in _proof_attr(proof, "industries", []) or []:
        if str(entry).strip().lower() in matched_industries_norm:
            industry_overlap += 1
    score += min(industry_overlap, 3) * 1.0

    # Overlap with raw job text tokens.
    claim_text = str(_proof_attr(proof, "claim_text", "") or "").lower()
    if claim_text and job_tokens:
        claim_tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9+.#-]{2,}", claim_text))
        claim_tokens = {t.lower() for t in claim_tokens}
        token_overlap = len(claim_tokens & job_tokens)
        score += min(token_overlap, 6) * 0.5

    # Penalize superseded conflicts.
    if _proof_attr(proof, "conflict_status") == "superseded":
        score -= 3.0

    return score


def _collect_referenced_evidence_ids(match_result: Any) -> set[str]:
    """If the match engine attached evidence_ids, surface them here.

    Today the match engine does not pass evidence_ids through, but the
    builder still accepts the data structure so a future LLM-backed
    matcher can opt in without changing call sites.
    """
    if not match_result or not isinstance(match_result, dict):
        return set()
    ids: set[str] = set()
    for candidate in (
        match_result.get("evidence_ids"),
        match_result.get("referenced_evidence_ids"),
        (match_result.get("skill_match") or {}).get("evidence_ids"),
    ):
        if not candidate:
            continue
        if isinstance(candidate, (list, tuple, set)):
            for ev in candidate:
                if ev:
                    ids.add(str(ev))
    return ids


def _trim_claim_text(value: str, cap: int) -> str:
    text = (value or "").strip()
    if len(text) <= cap:
        return text
    return text[: max(cap - 1, 1)].rstrip() + "…"


def _shorten_claim_texts(proofs: list, cap: int) -> list:
    """Return shallow copies of proof points with claim_text truncated to ``cap``.

    Pydantic ``ProofPoint`` instances are not mutated. Dicts are copied.
    """
    shortened: list = []
    for proof in proofs:
        claim_text = str(_proof_attr(proof, "claim_text", "") or "")
        if len(claim_text) <= cap:
            shortened.append(proof)
            continue
        if hasattr(proof, "model_copy"):
            try:
                shortened.append(
                    proof.model_copy(update={"claim_text": _trim_claim_text(claim_text, cap)})
                )
                continue
            except Exception:  # noqa: BLE001
                pass
        if isinstance(proof, dict):
            copy = dict(proof)
            copy["claim_text"] = _trim_claim_text(claim_text, cap)
            shortened.append(copy)
        else:
            shortened.append(proof)
    return shortened


def _drop_lowest_confidence_first(proofs: list, target_count: int) -> list:
    """Trim the proof list down to ``target_count`` items.

    Lower-confidence and lower-source-priority proofs are dropped first.
    """
    if len(proofs) <= target_count:
        return list(proofs)
    indexed = list(enumerate(proofs))
    indexed.sort(
        key=lambda pair: (
            _CONFIDENCE_RANK.get(
                (_proof_attr(pair[1], "confidence") or "low").lower(), 1
            ),
            -float(_proof_attr(pair[1], "source_priority", 17) or 17),
            -pair[0],
        ),
        reverse=True,
    )
    keep = sorted(idx for idx, _ in indexed[:target_count])
    return [proofs[i] for i in keep]


def _select_relevant_proofs(
    evidence_index: Iterable,
    confirmed_job: dict,
    match_result: Optional[dict],
    *,
    max_points: int,
) -> list:
    """Return up to ``max_points`` proof points ranked by relevance."""
    proofs = [p for p in (evidence_index or [])]
    if not proofs:
        return []

    matched_skills_norm: set[str] = set()
    matched_industries_norm: set[str] = set()
    if isinstance(match_result, dict):
        for entry in (match_result.get("skill_match") or {}).get("matched") or []:
            matched_skills_norm.add(str(entry).strip().lower())
        for entry in (match_result.get("industry_match") or {}).get("matched") or []:
            matched_industries_norm.add(str(entry).strip().lower())

    referenced_ids = _collect_referenced_evidence_ids(match_result)
    job_tokens = _job_signal_tokens(confirmed_job)

    scored = sorted(
        proofs,
        key=lambda p: _score_proof(
            p,
            job_tokens=job_tokens,
            matched_skills_norm=matched_skills_norm,
            matched_industries_norm=matched_industries_norm,
            referenced_ids=referenced_ids,
        ),
        reverse=True,
    )

    selected: list = []
    seen_ids: set[str] = set()
    for proof in scored:
        if len(selected) >= max_points:
            break
        ev_id = _proof_attr(proof, "evidence_id")
        if ev_id and ev_id in seen_ids:
            continue
        if ev_id:
            seen_ids.add(ev_id)
        selected.append(proof)
    return selected


def build_proposal_context(
    confirmed_job_fields: dict,
    evidence_index: Iterable,
    match_result: Optional[dict] = None,
    recommendation_result: Optional[dict] = None,
    max_evidence_points: int = 20,
    max_context_chars: int = 15000,
) -> dict[str, Any]:
    """Build the compact context that the proposal LLM call will see.

    The function only ever returns metadata-shaped data:

    * confirmed job fields (already small)
    * recommendation verdict + best proposal angle
    * top-N relevant evidence points (no source_file leakage, no full
      dossier text)
    * matched skills / tools / industries / services from the match
      engine
    * the selected offer if relevant
    * pricing only if pricing surfaced as relevant for this job
    * missing-info warnings from the match engine and recommendation

    The full evidence index, raw dossier chunks, transcript text, or
    PDF text is never embedded.
    """
    confirmed_job_fields = confirmed_job_fields or {}
    match_result = match_result or {}
    recommendation_result = recommendation_result or {}

    max_points = max(1, int(max_evidence_points or 1))
    max_chars = max(1024, int(max_context_chars or 1024))

    selected = _select_relevant_proofs(
        evidence_index,
        confirmed_job_fields,
        match_result,
        max_points=max_points,
    )

    # Always start by shortening long claim texts.
    selected = _shorten_claim_texts(selected, _CLAIM_TEXT_CAP_DEFAULT)

    # Skills / tools / industries / services pulled from match engine
    # (which already filtered by the job's required skills).
    skill_match = match_result.get("skill_match") or {}
    matched_skills = [str(s) for s in (skill_match.get("matched") or [])]
    missing_skills = [str(s) for s in (skill_match.get("missing") or [])]
    industry_match = match_result.get("industry_match") or {}
    matched_industries = [str(s) for s in (industry_match.get("matched") or [])]

    # Tools surfaced inside the selected evidence — small list only.
    tool_set: list[str] = []
    service_set: list[str] = []
    for proof in selected:
        for tool in _proof_attr(proof, "tools", []) or []:
            tool = str(tool).strip()
            if tool and tool not in tool_set:
                tool_set.append(tool)
                if len(tool_set) >= 12:
                    break
        if _proof_attr(proof, "claim_type") in {"service", "deliverable"}:
            text = str(_proof_attr(proof, "claim_text", "") or "").strip()
            if text and text not in service_set:
                service_set.append(text)

    # Selected offer / pricing only if a proof of that type made the cut.
    selected_offer: Optional[str] = None
    for proof in selected:
        if _proof_attr(proof, "claim_type") == "selected_offer":
            selected_offer = _trim_claim_text(
                str(_proof_attr(proof, "claim_text", "") or ""), 200
            )
            break

    pricing: Optional[str] = None
    job_budget = _field(confirmed_job_fields, "budget_or_rate")
    if not is_missing(job_budget):
        for proof in evidence_index or []:
            if _proof_attr(proof, "claim_type") == "pricing":
                pricing = _trim_claim_text(
                    str(_proof_attr(proof, "claim_text", "") or ""), 200
                )
                break

    # Missing-info warnings: critical screenshot fields + recommendation concerns.
    missing_info: list[str] = []
    for key in match_result.get("missing_critical_fields") or []:
        missing_info.append(f"Job field missing from screenshot: {key}")
    if missing_skills:
        missing_info.append(
            "No demonstrated overlap yet for: " + ", ".join(missing_skills[:5])
        )

    context: dict[str, Any] = {
        "confirmed_job_fields": _compact_job_fields(confirmed_job_fields),
        "recommendation": {
            "verdict": recommendation_result.get("verdict"),
            "proposal_angle": recommendation_result.get("proposal_angle")
            or match_result.get("proposal_angle"),
        },
        "matched_skills": matched_skills[:12],
        "matched_industries": matched_industries[:8],
        "tools": tool_set,
        "services": service_set[:6],
        "selected_offer": selected_offer,
        "pricing": pricing,
        "missing_info": missing_info[:6],
        "evidence": _evidence_summaries(selected, cap=_CLAIM_TEXT_CAP_DEFAULT),
    }

    approx_chars = _approx_chars(context)
    # If still too large, shrink. Order matters: drop evidence points
    # first (the big driver), then reduce claim-text caps, then drop
    # low-confidence evidence aggressively.
    cap = _CLAIM_TEXT_CAP_DEFAULT
    while approx_chars > max_chars and len(selected) > 8:
        new_count = 12 if len(selected) > 12 else 8
        selected = _drop_lowest_confidence_first(selected, new_count)
        context["evidence"] = _evidence_summaries(selected, cap=cap)
        approx_chars = _approx_chars(context)

    if approx_chars > max_chars:
        cap = _CLAIM_TEXT_CAP_SMALL
        selected = _shorten_claim_texts(selected, cap)
        context["evidence"] = _evidence_summaries(selected, cap=cap)
        approx_chars = _approx_chars(context)

    while approx_chars > max_chars and len(selected) > 1:
        selected = _drop_lowest_confidence_first(selected, max(1, len(selected) - 2))
        context["evidence"] = _evidence_summaries(selected, cap=cap)
        approx_chars = _approx_chars(context)

    context["__meta__"] = {
        "evidence_points_selected": len(selected),
        "approx_context_chars": approx_chars,
        "claim_text_cap": cap,
        "max_evidence_points": max_points,
        "max_context_chars": max_chars,
    }
    # The compact subset is also returned as Python objects so the
    # verification pass can use the same trimmed list.
    context["__compact_evidence__"] = selected
    return context


def _compact_job_fields(confirmed_job: dict) -> dict[str, str]:
    """Return job fields as a flat dict, dropping unfilled values."""
    out: dict[str, str] = {}
    for key, entry in (confirmed_job or {}).items():
        if isinstance(entry, dict):
            value = str(entry.get("value", "") or "").strip()
        else:
            value = str(entry or "").strip()
        if value and not is_missing(value):
            out[key] = value
    return out


def _evidence_summaries(proofs: list, *, cap: int) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for proof in proofs:
        summaries.append(
            {
                "evidence_id": _proof_attr(proof, "evidence_id"),
                "claim_type": _proof_attr(proof, "claim_type"),
                "source_type": _proof_attr(proof, "source_type"),
                "claim": _trim_claim_text(
                    str(_proof_attr(proof, "claim_text", "") or ""), cap
                ),
            }
        )
    return summaries


def _approx_chars(context: dict) -> int:
    """Cheap proxy for the eventual prompt size — JSON length of the
    compact context plus a small fixed allowance for prompt scaffolding."""
    serializable = {k: v for k, v in context.items() if not k.startswith("__")}
    try:
        return len(json.dumps(serializable, ensure_ascii=False))
    except (TypeError, ValueError):
        return sum(len(str(v)) for v in serializable.values())


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


def _is_size_error(result: Any) -> bool:
    if result is None:
        return False
    status = getattr(result, "status", None)
    # A context-window overflow is unambiguously a size problem; a quota /
    # rate-limit (429, tokens-per-minute) is also worth one shrink-retry on
    # the proposal path since a smaller request may slip under a TPM cap.
    if status in (
        getattr(llm_client, "STATUS_CONTEXT_OVERFLOW", "context_overflow"),
        llm_client.STATUS_QUOTA,
    ):
        return True
    message = (getattr(result, "error_message", "") or "").lower()
    return any(
        marker in message
        for marker in (
            "429",
            "rate limit",
            "rate_limit",
            "tokens per minute",
            "context length",
            "context_length",
            "context window",
            "too many tokens",
            "token limit",
            "request too large",
        )
    )


def _try_llm_generate(
    confirmed_job: dict,
    compact_evidence: list,
    band: tuple[int, int],
    complexity: str,
    *,
    claim_text_cap: int,
    max_output_tokens: int,
) -> tuple[Optional[str], list[dict[str, Any]], Any]:
    """Call the LLM via the central client. Returns (proposal, claims, llm_result)."""
    job_block = _serialize_job_for_prompt(confirmed_job)
    evidence_block = _serialize_evidence_for_prompt(
        compact_evidence, claim_text_cap=claim_text_cap
    )

    user_prompt = render_proposal_prompt(
        job_block=job_block,
        evidence_block=evidence_block,
        target_length=complexity,
        target_band=band,
    )
    result = llm_client.call_text_llm(
        task_name=TASK_NAME,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        expected_json=True,
        max_tokens=max_output_tokens,
    )
    if not result.success or not isinstance(result.response_json, dict):
        return None, [], result

    payload = result.response_json
    proposal_text = str(payload.get("proposal", "") or "").strip()
    claims_raw = payload.get("factual_claims") or []
    claims: list[dict[str, Any]] = []
    if isinstance(claims_raw, list):
        for item in claims_raw:
            if not isinstance(item, dict):
                continue
            claims.append(
                {
                    "text": str(item.get("text") or item.get("claim") or "").strip(),
                    "kind": str(item.get("kind") or item.get("claim_type") or "claim"),
                    "evidence_id": item.get("evidence_id"),
                    "source_file": item.get("source_file"),
                    "claim_type": item.get("claim_type"),
                }
            )
    if not proposal_text:
        return None, claims, result
    return proposal_text, claims, result


def generate(
    confirmed_job: dict,
    evidence_index: Iterable,
    recommendation: dict,
    *,
    match_data: Optional[dict] = None,
    complexity: Optional[str] = None,
    run_verify: bool = True,
    settings=None,
) -> dict[str, Any]:
    """Draft a grounded proposal.

    The default path calls the configured LLM via
    :mod:`app.services.llm_client` with task ``proposal_generation``.
    Only a compact, relevance-filtered subset of the evidence index is
    ever serialized into the prompt — the full dossier, raw transcript
    text, or PDF body is never sent.

    If the first call fails because the request was too large for the
    provider's rate / token / context limits, the call is retried once
    with a smaller context (8 evidence points, 8000 chars,
    500 output tokens). If retry also fails this raises
    :class:`ProposalGenerationError` with a sanitized message — the UI
    catches it and shows a clean error instead of the raw provider
    response.

    If the LLM call fails *and* ``ALLOW_LOCAL_PLACEHOLDERS`` is true in
    settings, a deterministic local draft is produced and clearly
    flagged as a placeholder.
    """
    settings = settings or get_settings()
    evidence_list = list(evidence_index or [])
    complexity = complexity or _detect_complexity(confirmed_job)
    band = LENGTH_BANDS.get(complexity, LENGTH_BANDS["standard"])

    matched_skills: list[str] = []
    if match_data:
        matched_skills = list(match_data.get("skill_match", {}).get("matched", []) or [])

    primary_context = build_proposal_context(
        confirmed_job,
        evidence_list,
        match_result=match_data,
        recommendation_result=recommendation,
        max_evidence_points=settings.max_proposal_evidence_points,
        max_context_chars=settings.max_proposal_context_chars,
    )

    used_api = False
    llm_status = "skipped"
    llm_provider = None
    llm_model = None
    llm_error: Optional[str] = None
    sanitized_error: Optional[str] = None

    proposal: Optional[str] = None
    claims: list[dict[str, Any]] = []
    llm_result = None
    retry_used = False
    chosen_context = primary_context

    if settings.has_api_key:
        compact_meta = primary_context["__meta__"]
        compact_evidence = primary_context["__compact_evidence__"]
        proposal, claims, llm_result = _try_llm_generate(
            confirmed_job,
            compact_evidence,
            band,
            complexity,
            claim_text_cap=compact_meta["claim_text_cap"],
            max_output_tokens=settings.proposal_max_output_tokens,
        )
        if llm_result is not None:
            llm_status = llm_result.status
            llm_provider = llm_result.provider
            llm_model = llm_result.model
            llm_error = llm_result.error_message
            sanitized_error = llm_client.sanitize_error_message(llm_error)
            used_api = bool(llm_result.used_api and proposal)

        # If the first try failed because the request was too large,
        # shrink and retry ONCE with the hard fallback settings.
        if proposal is None and _is_size_error(llm_result):
            retry_used = True
            retry_context = build_proposal_context(
                confirmed_job,
                evidence_list,
                match_result=match_data,
                recommendation_result=recommendation,
                max_evidence_points=8,
                max_context_chars=8000,
            )
            chosen_context = retry_context
            retry_meta = retry_context["__meta__"]
            retry_evidence = retry_context["__compact_evidence__"]
            proposal, claims, llm_result = _try_llm_generate(
                confirmed_job,
                retry_evidence,
                band,
                complexity,
                claim_text_cap=retry_meta["claim_text_cap"],
                max_output_tokens=500,
            )
            if llm_result is not None:
                llm_status = llm_result.status
                llm_provider = llm_result.provider
                llm_model = llm_result.model
                llm_error = llm_result.error_message
                sanitized_error = llm_client.sanitize_error_message(llm_error)
                used_api = bool(llm_result.used_api and proposal)

    final_meta = chosen_context["__meta__"]
    evidence_points_sent = final_meta["evidence_points_selected"] if used_api else 0
    compact_context_chars = final_meta["approx_context_chars"] if used_api else 0

    # Record proposal-specific metadata onto the most recent
    # proposal_generation entry that the central client wrote. Never
    # log raw prompts, raw dossier text, raw proposals, or API keys.
    if settings.has_api_key:
        llm_client.extend_last_entry(
            TASK_NAME,
            {
                "evidence_points_sent": final_meta["evidence_points_selected"],
                "compact_context_chars": final_meta["approx_context_chars"],
                "retry_used": retry_used,
                "sanitized_error_message": sanitized_error,
            },
        )

    if proposal is None:
        if not settings.allow_local_placeholders:
            # Failure path — surface a clean message to the UI. The
            # provider error (already sanitized of organization IDs) is
            # attached separately for debug surfaces.
            if not settings.has_api_key:
                message = (
                    "Proposal generation failed because no LLM API key is "
                    "configured. Set ANTHROPIC_API_KEY or OPENAI_API_KEY in "
                    "your .env, or enable ALLOW_LOCAL_PLACEHOLDERS=true to "
                    "run the deterministic local draft for development."
                )
            elif retry_used:
                message = SIZE_FAILURE_USER_MESSAGE
            elif _is_size_error(llm_result):
                message = SIZE_FAILURE_USER_MESSAGE
            else:
                message = "Proposal generation failed because the LLM API call failed."

            raise ProposalGenerationError(
                message,
                status=llm_status,
                llm_result=llm_result,
                sanitized_error=sanitized_error,
                meta={
                    "task_name": TASK_NAME,
                    "provider": llm_provider,
                    "model": llm_model,
                    "evidence_points_sent": final_meta["evidence_points_selected"],
                    "compact_context_chars": final_meta["approx_context_chars"],
                    "retry_used": retry_used,
                },
            )

        # Local placeholder path — explicitly opted in via env flag.
        proposal, claims = _local_generate(
            confirmed_job, evidence_list, matched_skills, band
        )
        llm_client.record_local_use(
            TASK_NAME,
            note="ALLOW_LOCAL_PLACEHOLDERS=true; deterministic draft used.",
        )

    proposal = _strip_forbidden(proposal)
    proposal = _trim_to_band(proposal, band)

    result: dict[str, Any] = {
        "draft_proposal": proposal,
        "proposal": proposal,
        "factual_claims": claims,
        "target_length": band,
        "complexity": complexity,
        "word_count": _word_count(proposal),
        "__meta__": {
            "task_name": TASK_NAME,
            "used_api": used_api,
            "status": llm_status if used_api else "local_placeholder",
            "provider": llm_provider,
            "model": llm_model,
            "error_message": sanitized_error if not used_api else None,
            "sanitized_error_message": sanitized_error,
            "evidence_points_sent": evidence_points_sent,
            "compact_context_chars": compact_context_chars,
            "retry_used": retry_used,
        },
    }

    # The verification pass receives ONLY the compact evidence subset —
    # never the full evidence index.
    compact_for_verify = chosen_context.get("__compact_evidence__") or []

    if run_verify:
        try:
            report = run_verification(
                proposal,
                compact_for_verify,
                factual_claims=claims,
                confirmed_job_fields=confirmed_job,
                settings=settings,
            )
        except TypeError:
            # Backwards-compat: callers (or tests) may monkey-patch
            # run_verification with the older positional-only signature.
            report = run_verification(
                proposal, compact_for_verify, factual_claims=claims
            )
        verification_status = getattr(report, "verification_status", "skipped")
        # Grounding gate: when verification FAILED the draft was never
        # confirmed against the evidence, so we must not surface it as a
        # ready proposal. Blank the client-facing text (the UI shows a
        # clear "not verified" notice) rather than passing unverified
        # output downstream as if it had been checked.
        if verification_status == "failed":
            verified_text = ""
        else:
            verified_text = report.cleaned_proposal or proposal
        # Evidence IDs are useful internally but never belong in the
        # client-facing proposal copy.
        verified_text = strip_evidence_ids(verified_text)
        result.update(
            {
                "proposal": verified_text,
                "verified_proposal": verified_text,
                "factual_claims": report.surviving_claims,
                "word_count": _word_count(verified_text),
                "removed_claims": report.removed_claims,
                "softened_claims": report.softened_claims,
                "flagged_as_missing": report.flagged_as_missing,
                "supported_claims": getattr(report, "supported_claims", []),
                "partially_supported_claims": getattr(
                    report, "partially_supported_claims", []
                ),
                "unsupported_claims": getattr(report, "unsupported_claims", []),
                "verification_status": getattr(
                    report, "verification_status", "skipped"
                ),
                "verification_summary": getattr(report, "summary", ""),
                "verification_meta": getattr(report, "meta", {}),
            }
        )
    else:
        # Always strip evidence IDs from the visible proposal even when
        # verification is skipped — they must never reach the client copy.
        stripped = strip_evidence_ids(proposal)
        result.update(
            {
                "proposal": stripped,
                "verified_proposal": stripped,
                "word_count": _word_count(stripped),
                "removed_claims": [],
                "softened_claims": [],
                "flagged_as_missing": [],
                "supported_claims": [],
                "partially_supported_claims": [],
                "unsupported_claims": [],
                "verification_status": "skipped",
                "verification_summary": "Verification pass was not run.",
                "verification_meta": {
                    "task_name": "verification_pass",
                    "used_api": False,
                    "status": "skipped",
                    "error_message": "Verification pass was not run.",
                    "claims_checked": 0,
                    "unsupported_claims_count": 0,
                },
            }
        )

    return result
