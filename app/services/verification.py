"""Verification Pass.

Runs after :mod:`app.services.proposal_generator` and before the
proposal is shown to the user. Every factual claim in the draft is
checked against the compact evidence subset that produced the draft.

The default path calls the configured LLM via
:mod:`app.services.llm_client` with task ``verification_pass``. The LLM
returns a structured verdict per claim and a rewritten
``verified_proposal`` with unsupported claims removed or softened.

If the LLM call fails the behaviour depends on the
``ALLOW_LOCAL_PLACEHOLDERS`` flag:

* ``false`` (default) — the report is returned with ``meta["status"] =
  "failed"`` and ``cleaned_proposal`` left unchanged. The UI then
  surfaces a clear "verification failed" message instead of pretending
  the proposal is verified.
* ``true`` — a deterministic claim sweep is used as a clearly-labeled
  local fallback.

The function never logs raw dossier text, full prompts, or the API key.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.config import Settings, get_settings
from app.prompts.system_prompt import SYSTEM_PROMPT
from app.prompts.verification_prompt import render_prompt as render_verification_prompt
from app.services import llm_client
from app.utils.logging_utils import get_logger


_logger: logging.Logger = get_logger("upwork_strategist.verification")


TASK_NAME = "verification_pass"


# ---------------------------------------------------------------------------
# Patterns for the local deterministic fallback
# ---------------------------------------------------------------------------


_YEARS_RE = re.compile(
    r"(\d+(?:\.\d+)?\+?)\s*(years?|yrs?)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(\d+(?:[\.,]\d+)?)\s*%")
_DOLLAR_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kKmM]?\b"
    r"|\b\d+(?:[\.,]\d+)?\s?[kKmM]\b"
)
_INTEGER_METRIC_RE = re.compile(
    r"\b(\d{2,})\s+(features|projects|clients|customers|users|hires|engagements|releases|deals|cases)\b",
    re.IGNORECASE,
)


# Evidence-id pattern. Matches free-text references like
# ``(ev_abc12345)``, ``[ev_abc12345]``, or bare ``ev_abc12345``.
_EVIDENCE_ID_INLINE_RE = re.compile(
    r"\s*[\(\[]\s*ev_[A-Za-z0-9_-]+\s*[\)\]]"
)
_EVIDENCE_ID_BARE_RE = re.compile(r"\bev_[A-Za-z0-9_-]+\b")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _local_meta(note: str, *, status: str = "local_placeholder") -> dict:
    return {
        "task_name": TASK_NAME,
        "used_api": False,
        "status": status,
        "provider": None,
        "model": None,
        "error_message": note,
        "claims_checked": 0,
        "unsupported_claims_count": 0,
    }


@dataclass
class VerificationReport:
    cleaned_proposal: str
    removed_claims: list[str] = field(default_factory=list)
    softened_claims: list[dict[str, str]] = field(default_factory=list)
    flagged_as_missing: list[str] = field(default_factory=list)
    surviving_claims: list[dict[str, Any]] = field(default_factory=list)
    supported_claims: list[dict[str, Any]] = field(default_factory=list)
    partially_supported_claims: list[dict[str, Any]] = field(default_factory=list)
    unsupported_claims: list[dict[str, Any]] = field(default_factory=list)
    verification_status: str = "skipped"
    summary: str = ""
    meta: dict = field(default_factory=lambda: _local_meta(
        "Verification pass has not run yet."
    ))


# ---------------------------------------------------------------------------
# Evidence-id stripping
# ---------------------------------------------------------------------------


def strip_evidence_ids(text: str) -> str:
    """Remove inline ``(ev_xxx)`` / ``[ev_xxx]`` / bare ``ev_xxx`` tokens.

    Evidence IDs are useful internally (citations, claim verification)
    but must never appear in the client-facing proposal.
    """
    if not text:
        return text
    cleaned = _EVIDENCE_ID_INLINE_RE.sub("", text)
    cleaned = _EVIDENCE_ID_BARE_RE.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proof_attr(proof: Any, name: str, default: Any = None) -> Any:
    if hasattr(proof, name):
        return getattr(proof, name)
    if isinstance(proof, dict):
        return proof.get(name, default)
    return default


def _evidence_text_corpus(evidence: Iterable) -> str:
    parts: list[str] = []
    for proof in evidence:
        parts.append(str(_proof_attr(proof, "claim_text", "")))
        parts.append(str(_proof_attr(proof, "normalized_value", "") or ""))
        for attr in ("skills", "tools", "industries", "metrics"):
            for item in _proof_attr(proof, attr, []) or []:
                parts.append(str(item))
    return " \n".join(parts).lower()


def _evidence_id_set(evidence: Iterable) -> set[str]:
    return {
        _proof_attr(p, "evidence_id")
        for p in evidence
        if _proof_attr(p, "evidence_id")
    }


def _drop_sentence_containing(text: str, needle: str) -> tuple[str, Optional[str]]:
    """Drop the sentence containing ``needle``.

    Returns ``(new_text, removed_fragment)`` only when the text actually
    changed; otherwise ``(text, None)`` so callers never report a claim as
    "removed" while leaving it in the proposal.
    """
    if not needle or needle not in text:
        return text, None
    pieces = re.split(r"(?<=[.!?\n])\s+", text)
    for idx, piece in enumerate(pieces):
        if needle in piece:
            removed = piece.strip()
            pieces.pop(idx)
            return " ".join(p for p in pieces if p).strip(), removed
    cleaned = text.replace(needle, "").strip()
    if cleaned == text:
        return text, None
    return cleaned, needle


def _replace_once(text: str, needle: str, replacement: str) -> str:
    return text.replace(needle, replacement, 1)


def _number_token_in_corpus(number_token: str, corpus: str) -> bool:
    """True if ``number_token`` appears as a whole number in ``corpus``.

    Guards against the bare-substring false-positive where a digit (e.g.
    "5") is considered "supported" merely because it occurs inside an
    unrelated token ("HTML5", "$50k", "Python 3.5"). The token is matched
    bounded by non-digit characters so "5" matches "5 years" / "(5)" but
    not "50" or "HTML5".
    """
    token = (number_token or "").strip().lower()
    if not token:
        return False
    pattern = r"(?<![0-9.,])" + re.escape(token) + r"(?![0-9])"
    return re.search(pattern, corpus) is not None


def _compact_evidence_for_prompt(
    evidence: Iterable, *, claim_text_cap: int = 240
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for proof in evidence:
        text = str(_proof_attr(proof, "claim_text", "") or "").strip()
        if len(text) > claim_text_cap:
            text = text[: max(claim_text_cap - 1, 1)].rstrip() + "…"
        out.append(
            {
                "evidence_id": _proof_attr(proof, "evidence_id"),
                "source_type": _proof_attr(proof, "source_type"),
                "claim_type": _proof_attr(proof, "claim_type"),
                "claim": text,
            }
        )
    return out


def _compact_job_for_prompt(confirmed_job: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, entry in (confirmed_job or {}).items():
        if isinstance(entry, dict):
            value = str(entry.get("value", "") or "").strip()
        else:
            value = str(entry or "").strip()
        if value and value.lower() != "not visible":
            out[key] = value
    return out


def _compact_claims_for_prompt(
    factual_claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for claim in factual_claims or []:
        if not isinstance(claim, dict):
            continue
        out.append(
            {
                "text": str(claim.get("text") or "").strip(),
                "kind": claim.get("kind"),
                "claim_type": claim.get("claim_type"),
                "evidence_id": claim.get("evidence_id"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Deterministic local fallback sweeps
# ---------------------------------------------------------------------------


def _sweep_factual_claims(
    proposal: str,
    factual_claims: list[dict[str, Any]],
    evidence_ids: set[str],
) -> tuple[str, list[str], list[dict[str, Any]]]:
    removed: list[str] = []
    surviving: list[dict[str, Any]] = []
    for claim in factual_claims or []:
        ev_id = claim.get("evidence_id")
        text = (claim.get("text") or "").strip()
        if ev_id and ev_id in evidence_ids:
            surviving.append(claim)
            continue
        if text:
            new_proposal, dropped = _drop_sentence_containing(proposal, text)
            if dropped is not None:
                # The proposal text actually changed — report the exact
                # fragment that was removed.
                proposal = new_proposal
                removed.append(dropped)
            else:
                # The claim wording was not a literal substring, so the
                # sentence could not be excised. Report it honestly as an
                # unsupported claim that needs manual review rather than
                # logging a phantom "removed" while leaving it in place.
                removed.append(text)
        else:
            removed.append(claim.get("kind", "unknown claim"))
    return proposal, removed, surviving


def _sweep_unattributed_specifics(
    proposal: str, evidence_corpus: str
) -> tuple[str, list[dict[str, str]]]:
    softened: list[dict[str, str]] = []

    def soften(pattern: re.Pattern[str], replacement: str) -> None:
        nonlocal proposal
        for match in list(pattern.finditer(proposal)):
            snippet = match.group(0)
            if snippet.lower() in evidence_corpus:
                continue
            digit_token = match.group(1) if match.groups() else snippet
            # Match the number as a WHOLE token in the corpus, not as a bare
            # substring: otherwise "5 years" would be treated as supported
            # whenever "5" appears anywhere (e.g. "HTML5", "$50k",
            # "Python 3.5"). Require the exact digit token bounded by
            # non-alphanumeric characters.
            if digit_token and _number_token_in_corpus(digit_token, evidence_corpus):
                continue
            proposal = _replace_once(proposal, snippet, replacement)
            softened.append({"original": snippet, "softened": replacement})

    soften(_YEARS_RE, "several years")
    soften(_PERCENT_RE, "a meaningful share")
    soften(_DOLLAR_RE, "a meaningful amount")
    soften(_INTEGER_METRIC_RE, "a number of")
    return proposal, softened


def _missing_info_suggestions(
    removed: list[str],
    softened: list[dict[str, str]],
    factual_claims: list[dict[str, Any]],
    evidence: list,
) -> list[str]:
    suggestions: list[str] = []
    if removed:
        suggestions.append(
            "Some claims were removed because no proof point supported them. "
            "Add a matching file to the dossier (testimonial, case study, or "
            "portfolio entry) if you want these claims to stand."
        )
    if any(s["softened"] == "several years" for s in softened):
        suggestions.append(
            "Years-of-experience figure was softened. Add a resume or LinkedIn "
            "summary that states the exact number."
        )
    if any(s["softened"] in {"a meaningful share", "a meaningful amount"} for s in softened):
        suggestions.append(
            "A specific metric was softened. Add a case study or testimonial "
            "that documents the number you want to cite."
        )
    if any(s["softened"] == "a number of" for s in softened):
        suggestions.append(
            "A specific count was softened. Add a portfolio breakdown that "
            "shows the exact count."
        )

    claim_types_present = {
        _proof_attr(p, "claim_type") for p in evidence if _proof_attr(p, "claim_type")
    }
    for needed, hint in (
        ("testimonial", "No client testimonials in the dossier — add one to support social-proof phrases."),
        ("portfolio", "No portfolio entries in the dossier — add at least one to support project references."),
        ("certification", "No certifications in the dossier — add credentials to support cert-related claims."),
    ):
        if needed not in claim_types_present and any(
            c.get("kind") == needed for c in factual_claims or []
        ):
            suggestions.append(hint)
    return suggestions


def _local_fallback(
    proposal: str,
    evidence_list: list,
    factual_claims: list[dict[str, Any]],
    *,
    note: str,
    status: str,
) -> VerificationReport:
    """Run the deterministic claim sweep as a labelled local fallback."""
    evidence_ids = _evidence_id_set(evidence_list)
    corpus = _evidence_text_corpus(evidence_list)

    proposal_after_claims, removed, surviving = _sweep_factual_claims(
        proposal, factual_claims or [], evidence_ids
    )
    proposal_after_specifics, softened = _sweep_unattributed_specifics(
        proposal_after_claims, corpus
    )
    cleaned = re.sub(r"[ \t]{2,}", " ", proposal_after_specifics)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    cleaned = strip_evidence_ids(cleaned)

    suggestions = _missing_info_suggestions(
        removed, softened, factual_claims or [], evidence_list
    )

    meta = _local_meta(note, status=status)
    meta["claims_checked"] = len(factual_claims or [])
    meta["unsupported_claims_count"] = len(removed)

    llm_client.record_local_use(TASK_NAME, note=note)

    if removed or softened:
        verification_status = "passed_with_softening"
    else:
        verification_status = "passed"

    return VerificationReport(
        cleaned_proposal=cleaned,
        removed_claims=removed,
        softened_claims=softened,
        flagged_as_missing=suggestions,
        surviving_claims=surviving,
        supported_claims=[
            {"claim": c.get("text") or c.get("kind") or "", "evidence_ids": [c.get("evidence_id")] if c.get("evidence_id") else []}
            for c in surviving
        ],
        partially_supported_claims=[
            {
                "claim": s.get("original", ""),
                "reason": "Specific value not present in evidence.",
                "suggested_softening": s.get("softened", ""),
                "evidence_ids": [],
            }
            for s in softened
        ],
        unsupported_claims=[
            {"claim": r, "reason": "No matching evidence point.", "action": "remove"}
            for r in removed
        ],
        verification_status=verification_status,
        summary=(
            f"LOCAL FALLBACK — LLM verification not used. "
            f"Removed {len(removed)} claim(s); softened {len(softened)} value(s)."
        ),
        meta=meta,
    )


def _failure_report(
    proposal: str,
    *,
    note: str,
    status: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    error_message: Optional[str] = None,
    claims_checked: int = 0,
) -> VerificationReport:
    """Return a report that explicitly says verification did not run.

    The proposal text is returned unchanged but evidence-ids are still
    stripped so the UI's main copy box never leaks them.
    """
    meta = {
        "task_name": TASK_NAME,
        "used_api": False,
        "status": status,
        "provider": provider,
        "model": model,
        "error_message": llm_client.sanitize_error_message(error_message) or note,
        "claims_checked": claims_checked,
        "unsupported_claims_count": 0,
    }
    return VerificationReport(
        cleaned_proposal=strip_evidence_ids(proposal),
        verification_status="failed",
        summary=note,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# LLM-backed verification
# ---------------------------------------------------------------------------


def _parse_llm_payload(
    payload: Any, fallback_proposal: str
) -> dict[str, Any]:
    """Coerce the LLM response into the expected JSON shape."""
    if not isinstance(payload, dict):
        return {
            "verification_status": "failed",
            "supported_claims": [],
            "partially_supported_claims": [],
            "unsupported_claims": [],
            "verified_proposal": fallback_proposal,
            "missing_information": [],
            "summary": "Verification response was not a JSON object.",
        }

    def _as_list(value: Any) -> list:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    verified = str(payload.get("verified_proposal") or "").strip()
    if not verified:
        verified = fallback_proposal

    status_raw = str(payload.get("verification_status") or "").strip().lower()
    if status_raw not in {"passed", "passed_with_softening", "failed"}:
        # Infer from claim counts if the model omitted the field.
        partials = _as_list(payload.get("partially_supported_claims"))
        unsup = _as_list(payload.get("unsupported_claims"))
        if not partials and not unsup:
            status_raw = "passed"
        elif unsup and len(unsup) >= 3:
            status_raw = "failed"
        else:
            status_raw = "passed_with_softening"

    return {
        "verification_status": status_raw,
        "supported_claims": _as_list(payload.get("supported_claims")),
        "partially_supported_claims": _as_list(payload.get("partially_supported_claims")),
        "unsupported_claims": _as_list(payload.get("unsupported_claims")),
        "verified_proposal": verified,
        "missing_information": _as_list(payload.get("missing_information")),
        "summary": str(payload.get("summary") or "").strip(),
    }


def _claims_checked_count(parsed: dict[str, Any]) -> int:
    return (
        len(parsed.get("supported_claims") or [])
        + len(parsed.get("partially_supported_claims") or [])
        + len(parsed.get("unsupported_claims") or [])
    )


def _call_verification_llm(
    *,
    proposal: str,
    evidence_list: list,
    factual_claims: list[dict[str, Any]],
    confirmed_job_fields: dict,
    settings: Settings,
) -> Any:
    compact_evidence = _compact_evidence_for_prompt(evidence_list)
    compact_job = _compact_job_for_prompt(confirmed_job_fields)
    compact_claims = _compact_claims_for_prompt(factual_claims)
    referenced_ids = sorted(
        {
            c.get("evidence_id")
            for c in compact_claims
            if c.get("evidence_id")
        }
    )

    user_prompt = render_verification_prompt(
        proposal_block=proposal,
        factual_claims_block=json.dumps(compact_claims, ensure_ascii=False, indent=2),
        evidence_block=json.dumps(compact_evidence, ensure_ascii=False, indent=2),
        confirmed_job_block=json.dumps(compact_job, ensure_ascii=False, indent=2),
        referenced_ids_block=json.dumps(referenced_ids, ensure_ascii=False),
    )

    return llm_client.call_text_llm(
        task_name=TASK_NAME,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        expected_json=True,
        max_tokens=1500,
        settings=settings,
    )


def _missing_info_from_payload(
    parsed: dict[str, Any], evidence_list: list, factual_claims: list[dict[str, Any]]
) -> list[str]:
    suggestions: list[str] = []
    for item in parsed.get("missing_information") or []:
        if isinstance(item, str) and item.strip():
            suggestions.append(item.strip())
        elif isinstance(item, dict) and item.get("hint"):
            suggestions.append(str(item["hint"]).strip())

    # Add dossier-shape suggestions for claim types missing from evidence.
    claim_types_present = {
        _proof_attr(p, "claim_type") for p in evidence_list if _proof_attr(p, "claim_type")
    }
    for needed, hint in (
        ("testimonial", "No client testimonials in the dossier — add one to support social-proof phrases."),
        ("portfolio", "No portfolio entries in the dossier — add at least one to support project references."),
        ("certification", "No certifications in the dossier — add credentials to support cert-related claims."),
    ):
        if needed not in claim_types_present and any(
            (c.get("kind") == needed or c.get("claim_type") == needed)
            for c in factual_claims or []
        ):
            if hint not in suggestions:
                suggestions.append(hint)
    return suggestions[:8]


def _report_from_llm(
    *,
    parsed: dict[str, Any],
    llm_result: Any,
    evidence_list: list,
    factual_claims: list[dict[str, Any]],
) -> VerificationReport:
    verified = strip_evidence_ids(parsed.get("verified_proposal") or "")

    supported = [
        c for c in parsed.get("supported_claims") or [] if isinstance(c, (dict, str))
    ]
    partials = [
        c for c in parsed.get("partially_supported_claims") or [] if isinstance(c, dict)
    ]
    unsup = [
        c for c in parsed.get("unsupported_claims") or [] if isinstance(c, dict)
    ]

    removed = [str(c.get("claim") or "").strip() for c in unsup if c.get("action") != "soften" and (c.get("claim") or "").strip()]
    softened = [
        {
            "original": str(c.get("claim") or "").strip(),
            "softened": str(c.get("suggested_softening") or "").strip(),
        }
        for c in partials
        if (c.get("claim") or "").strip()
    ]
    # Unsupported claims marked "soften" count as softenings too.
    for c in unsup:
        if c.get("action") == "soften" and (c.get("claim") or "").strip():
            softened.append(
                {
                    "original": str(c.get("claim") or "").strip(),
                    "softened": "(softened)",
                }
            )

    # Grounding gate: a claim only "survives" if its evidence_id is BOTH
    # echoed by the verifier model AND present in the real evidence subset
    # that produced the proposal. Both the generator and the verifier are
    # untrusted, so the only authoritative source of real ids is
    # ``evidence_list`` — without this intersection a fabricated
    # evidence_id that the verifier merely repeats would be treated as
    # grounded, defeating the "grounded only in real evidence" guarantee.
    # (The deterministic local fallback already does this; this aligns the
    # LLM path with it.)
    real_evidence_ids = _evidence_id_set(evidence_list)
    surviving: list[dict[str, Any]] = []
    supported_evidence_ids: set[str] = set()
    for entry in supported:
        if isinstance(entry, dict):
            for ev in entry.get("evidence_ids") or []:
                if ev and str(ev) in real_evidence_ids:
                    supported_evidence_ids.add(str(ev))
    for claim in factual_claims or []:
        ev_id = claim.get("evidence_id")
        if ev_id and ev_id in supported_evidence_ids and ev_id in real_evidence_ids:
            surviving.append(claim)

    missing_info = _missing_info_from_payload(parsed, evidence_list, factual_claims)

    claims_checked = _claims_checked_count(parsed)
    unsupported_count = len(unsup) + len(partials)

    sanitized_error = llm_client.sanitize_error_message(
        getattr(llm_result, "error_message", None)
    )

    meta = {
        "task_name": TASK_NAME,
        "used_api": True,
        "status": getattr(llm_result, "status", "ok"),
        "provider": getattr(llm_result, "provider", None),
        "model": getattr(llm_result, "model", None),
        "error_message": sanitized_error,
        "claims_checked": claims_checked,
        "unsupported_claims_count": unsupported_count,
    }

    # Attach the verification metadata to the most recent usage-log
    # entry so the API Usage panel sees claim counts without us sending
    # raw text. We also re-assert used_api / provider / model so the
    # entry is correct even when a test bypasses the central finalize
    # step by monkey-patching the provider call directly.
    try:
        llm_client.extend_last_entry(
            TASK_NAME,
            {
                "used_api": True,
                "provider": getattr(llm_result, "provider", None),
                "model": getattr(llm_result, "model", None),
                "status": getattr(llm_result, "status", "ok"),
                "claims_checked": claims_checked,
                "unsupported_claims_count": unsupported_count,
            },
        )
    except Exception:  # pragma: no cover
        pass

    return VerificationReport(
        cleaned_proposal=verified,
        removed_claims=removed,
        softened_claims=softened,
        flagged_as_missing=missing_info,
        surviving_claims=surviving,
        supported_claims=[c for c in supported if isinstance(c, dict)],
        partially_supported_claims=partials,
        unsupported_claims=unsup,
        verification_status=parsed.get("verification_status") or "passed",
        summary=parsed.get("summary") or "",
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def verify(
    proposal: str,
    evidence: Iterable,
    factual_claims: Optional[list[dict[str, Any]]] = None,
    *,
    confirmed_job_fields: Optional[dict] = None,
    settings: Optional[Settings] = None,
) -> VerificationReport:
    """Run the verification pass over a draft proposal.

    The default path calls the LLM via the central client. If the call
    fails the behaviour depends on the ``ALLOW_LOCAL_PLACEHOLDERS``
    flag — when false, the report is marked failed; when true a
    deterministic sweep is used as a labelled fallback.
    """
    settings = settings or get_settings()
    evidence_list = list(evidence or [])
    claims_list = list(factual_claims or [])
    confirmed_job_fields = confirmed_job_fields or {}

    # No API key available — skip the LLM call entirely.
    if not settings.has_api_key:
        if settings.allow_local_placeholders:
            return _local_fallback(
                proposal,
                evidence_list,
                claims_list,
                note=(
                    "LOCAL FALLBACK — LLM verification not used. "
                    "ALLOW_LOCAL_PLACEHOLDERS=true; deterministic claim sweep ran."
                ),
                status="local_placeholder",
            )
        return _failure_report(
            proposal,
            note=(
                "Verification failed because no LLM API key is configured. "
                "Set ANTHROPIC_API_KEY or OPENAI_API_KEY, or enable "
                "ALLOW_LOCAL_PLACEHOLDERS=true for the deterministic local "
                "claim sweep."
            ),
            status=llm_client.STATUS_NO_API,
            claims_checked=len(claims_list),
        )

    llm_result = _call_verification_llm(
        proposal=proposal,
        evidence_list=evidence_list,
        factual_claims=claims_list,
        confirmed_job_fields=confirmed_job_fields,
        settings=settings,
    )

    success = bool(
        getattr(llm_result, "success", False)
        and isinstance(getattr(llm_result, "response_json", None), dict)
    )
    if success:
        parsed = _parse_llm_payload(
            getattr(llm_result, "response_json", None), fallback_proposal=proposal
        )
        return _report_from_llm(
            parsed=parsed,
            llm_result=llm_result,
            evidence_list=evidence_list,
            factual_claims=claims_list,
        )

    # LLM call failed.
    sanitized = llm_client.sanitize_error_message(
        getattr(llm_result, "error_message", None)
    )
    if settings.allow_local_placeholders:
        return _local_fallback(
            proposal,
            evidence_list,
            claims_list,
            note=(
                "LOCAL FALLBACK — LLM verification call failed; deterministic "
                "claim sweep ran instead. "
                + (f"Provider reason: {sanitized}" if sanitized else "")
            ).strip(),
            status="local_placeholder",
        )

    return _failure_report(
        proposal,
        note="Verification failed because the LLM API call failed.",
        status=getattr(llm_result, "status", "failed") or "failed",
        provider=getattr(llm_result, "provider", None),
        model=getattr(llm_result, "model", None),
        error_message=sanitized,
        claims_checked=len(claims_list),
    )


__all__ = [
    "VerificationReport",
    "TASK_NAME",
    "verify",
    "strip_evidence_ids",
]
