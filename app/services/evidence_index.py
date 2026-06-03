"""Evidence Index.

Turns the raw dossier chunks from :mod:`app.services.dossier_reader` into
a list of source-backed proof points, then synthesises a canonical
freelancer profile from those proof points.

Design rules:

* Every claim keeps a reference to the source file, source type, source
  priority, and location. Proposals draw only from this index.
* Unknown but readable files can still yield proof points (for example,
  a notes file may contain pricing, a strategy doc may contain target
  client details, a transcript may contain strengths).
* Higher-priority sources win the canonical profile slot, but
  lower-priority evidence is preserved so it can be cited or flagged as
  ``superseded`` / ``supporting``.
* Dossier content is treated as untrusted data. Instructions inside
  dossier files are never executed and are never echoed verbatim into
  logs.
* This module never invents proof points. If a signal is not present
  in the source chunk, no proof point is emitted for it. The current
  implementation is a non-LLM keyword/JSON scanner placeholder; an
  LLM-backed extractor can replace ``_scan_text_chunk`` later without
  changing the public ``build`` / ``build_profile`` contracts.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Optional

from app.models.schemas import (
    SOURCE_PRIORITY,
    CanonicalFreelancerProfile,
    CanonicalProfileField,
    ChunkRecord,
    ClaimType,
    ExtractionConfidence,
    ProofPoint,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SECTION_HEADER = re.compile(r"^\s*(#+\s*[^\n]+|[A-Z][A-Z0-9 \-/]{3,}:?)\s*$")


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _evidence_id(file_path: str, location: str, claim_type: str, claim_text: str) -> str:
    raw = f"{file_path}|{location}|{claim_type}|{claim_text}"
    return "ev_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _split_paragraphs(text: str) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paragraphs


def _confidence_for(source_type: str) -> ExtractionConfidence:
    priority = SOURCE_PRIORITY.get(source_type, 99)
    if priority <= 4:
        return "high"
    if priority <= 10:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Keyword-based scanners for free-text chunks
# ---------------------------------------------------------------------------


_METRIC_PATTERN = re.compile(
    r"\b\d{1,4}(?:[\.,]\d+)?\s*(?:%|percent|x|×)\b"
    r"|\$\s?\d{1,3}(?:[,\d]{0,12})(?:\.\d+)?(?:\s?[kKmM])?",
)

_TESTIMONIAL_HINTS = (
    "testimonial", "review", "feedback", "client said", "they said",
    "would recommend", "highly recommend", "5 stars", "★",
)

_SKILL_LINE_HINTS = ("skills:", "skill:", "tech stack", "stack:", "technologies:")
_TOOL_LINE_HINTS = ("tools:", "toolset:", "software:", "platforms:", "platform:")
_SERVICE_LINE_HINTS = ("services:", "service:", "offerings:", "i offer", "we offer")
_DELIVERABLE_HINTS = ("deliverables:", "deliverable:", "what you get", "you receive")
_TARGET_CLIENT_HINTS = ("target client", "ideal client", "client avatar", "icp")
_OFFER_HINTS = ("selected offer", "primary offer", "headline offer", "offer:", "blueprint:")
_PRICING_HINTS = (
    "rate", "rates", "pricing", "package", "retainer", "hourly", "fixed-fee",
    "deposit", "fee:", "rate card",
)
_CERTIFICATION_HINTS = ("certified", "certification", "course completion", "credential")
_EDUCATION_HINTS = ("bachelor", "master", "phd", "b.sc", "m.sc", "diploma", "degree")
_LANGUAGE_HINTS = ("languages:", "language:", "fluent in", "native speaker")
_LOCATION_HINTS = ("based in", "located in", "location:", "timezone:", "time zone")
_AVAILABILITY_HINTS = ("availability", "hours per week", "available for")
_WEAKNESS_HINTS = ("limitation", "do not offer", "i don't", "won't accept", "out of scope")
_PORTFOLIO_HINTS = ("portfolio", "case study", "work sample", "project gallery")
_STRENGTH_HINTS = ("strength", "what i do best", "edge", "differentiator")
_POSITIONING_HINTS = ("positioning", "headline", "tagline", "i help", "i help ")
_EXPERIENCE_HINTS = (
    "years of experience", "yrs of experience", "years experience",
    "experience:", "worked at", "worked with", "previously at",
    "led", "managed", "shipped", "delivered for", "built for",
)
_PROJECT_HINTS = ("project:", "case study:", "engagement:", "client project")


def _split_csv_like(value: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,;\n•·]+", value) if s.strip()]


def _make_proof(
    *,
    chunk: ChunkRecord,
    claim_type: ClaimType,
    claim_text: str,
    location: str,
    normalized_value: Optional[str] = None,
    skills: Iterable[str] = (),
    tools: Iterable[str] = (),
    industries: Iterable[str] = (),
    metrics: Iterable[str] = (),
    confidence: Optional[ExtractionConfidence] = None,
) -> ProofPoint:
    return ProofPoint(
        evidence_id=_evidence_id(chunk.file_path, location, claim_type, claim_text),
        source_file=chunk.file_path,
        source_type=chunk.source_type,
        source_priority=chunk.source_priority,
        source_location=location,
        claim_type=claim_type,
        claim_text=_normalize(claim_text)[:600],
        normalized_value=normalized_value,
        skills=list(skills),
        tools=list(tools),
        industries=list(industries),
        metrics=list(metrics),
        confidence=confidence or _confidence_for(chunk.source_type),
    )


def _scan_text_chunk(chunk: ChunkRecord) -> list[ProofPoint]:
    """Pull proof points out of a free-text chunk using keyword heuristics."""
    if not chunk.extracted_text:
        return []

    proofs: list[ProofPoint] = []
    text = chunk.extracted_text
    base_location = (
        f"page {chunk.page_number}" if chunk.page_number is not None else "body"
    )

    paragraphs = _split_paragraphs(text)
    current_section: Optional[str] = chunk.section_name

    for idx, paragraph in enumerate(paragraphs):
        lower = paragraph.lower()
        location = f"{base_location}#para{idx + 1}"
        if current_section:
            location = f"{current_section}/{location}"

        first_line = paragraph.splitlines()[0] if paragraph else ""
        if _SECTION_HEADER.match(first_line):
            current_section = first_line.strip().strip("#").strip()

        def line_after(hint: str) -> Optional[str]:
            pos = lower.find(hint)
            if pos == -1:
                return None
            after = paragraph[pos + len(hint):].strip(" :\n")
            return after or None

        # Metrics / achievements ------------------------------------------------
        for m in _METRIC_PATTERN.finditer(paragraph):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="metric",
                    claim_text=paragraph,
                    location=location,
                    normalized_value=m.group(0),
                    metrics=[m.group(0)],
                )
            )

        # Testimonials ----------------------------------------------------------
        if any(hint in lower for hint in _TESTIMONIAL_HINTS) or paragraph.startswith('"'):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="testimonial",
                    claim_text=paragraph,
                    location=location,
                )
            )

        # Skills ----------------------------------------------------------------
        for hint in _SKILL_LINE_HINTS:
            value = line_after(hint)
            if value:
                items = _split_csv_like(value)
                if items:
                    proofs.append(
                        _make_proof(
                            chunk=chunk,
                            claim_type="skill",
                            claim_text=value,
                            location=location,
                            skills=items,
                        )
                    )
                break

        # Tools -----------------------------------------------------------------
        for hint in _TOOL_LINE_HINTS:
            value = line_after(hint)
            if value:
                items = _split_csv_like(value)
                if items:
                    proofs.append(
                        _make_proof(
                            chunk=chunk,
                            claim_type="tool",
                            claim_text=value,
                            location=location,
                            tools=items,
                        )
                    )
                break

        # Services / deliverables ---------------------------------------------
        for hint in _SERVICE_LINE_HINTS:
            value = line_after(hint)
            if value:
                proofs.append(
                    _make_proof(
                        chunk=chunk,
                        claim_type="service",
                        claim_text=value,
                        location=location,
                    )
                )
                break

        for hint in _DELIVERABLE_HINTS:
            value = line_after(hint)
            if value:
                proofs.append(
                    _make_proof(
                        chunk=chunk,
                        claim_type="deliverable",
                        claim_text=value,
                        location=location,
                    )
                )
                break

        if any(hint in lower for hint in _TARGET_CLIENT_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="target_client",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _OFFER_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="selected_offer",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _PRICING_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="pricing",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _CERTIFICATION_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="certification",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _EDUCATION_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="education",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _LANGUAGE_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="language",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _LOCATION_HINTS):
            # Route timezone phrasing to the dedicated timezone field and
            # everything else to location, instead of collapsing both into
            # the identity/name field (which previously dropped the data).
            is_timezone = ("timezone:" in lower) or ("time zone" in lower)
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="timezone" if is_timezone else "location",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _AVAILABILITY_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="availability",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _WEAKNESS_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="weakness_or_constraint",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _PORTFOLIO_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="portfolio",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _STRENGTH_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="achievement",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _POSITIONING_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="positioning",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _EXPERIENCE_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="experience",
                    claim_text=paragraph,
                    location=location,
                )
            )

        if any(hint in lower for hint in _PROJECT_HINTS):
            proofs.append(
                _make_proof(
                    chunk=chunk,
                    claim_type="project",
                    claim_text=paragraph,
                    location=location,
                )
            )

    if not proofs:
        # We still want a placeholder proof from unknown content so that
        # the file shows up in the canonical profile's source summary.
        proofs.append(
            _make_proof(
                chunk=chunk,
                claim_type="other_relevant_evidence",
                claim_text=_normalize(text)[:400],
                location=base_location,
                confidence="low",
            )
        )

    return proofs


# ---------------------------------------------------------------------------
# JSON scanner
# ---------------------------------------------------------------------------


_JSON_CLAIM_MAP: dict[str, ClaimType] = {
    "name": "identity",
    "full_name": "identity",
    "headline": "positioning",
    "title": "positioning",
    "positioning": "positioning",
    "tagline": "positioning",
    "location": "location",
    "timezone": "timezone",
    "time_zone": "timezone",
    "languages": "language",
    "selected_offer": "selected_offer",
    "offer": "selected_offer",
    "guarantee": "guarantee",
    "target_client": "target_client",
    "ideal_client": "target_client",
    "client_avatar": "target_client",
    "industries": "industry",
    "verticals": "industry",
    "services": "service",
    "service_stack": "service",
    "offerings": "service",
    "deliverables": "deliverable",
    "skills": "skill",
    "competencies": "skill",
    "tools": "tool",
    "stack": "tool",
    "platforms": "tool",
    "work_history": "work_history",
    "experience": "experience",
    "years_experience": "experience",
    "projects": "project",
    "portfolio": "portfolio",
    "case_studies": "portfolio",
    "testimonials": "testimonial",
    "reviews": "testimonial",
    "certifications": "certification",
    "credentials": "certification",
    "education": "education",
    "pricing": "pricing",
    "rates": "pricing",
    "availability": "availability",
    "proposal_preferences": "proposal_preference",
    "preferred_project_types": "proposal_preference",
    "strengths": "achievement",
    "weaknesses": "weakness_or_constraint",
    "constraints": "weakness_or_constraint",
}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(_stringify(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        return ", ".join(f"{k}: {_stringify(v)}" for k, v in value.items())
    return str(value)


def _scan_json_chunk(chunk: ChunkRecord) -> list[ProofPoint]:
    payload = chunk.json_data
    if not isinstance(payload, dict):
        return []

    proofs: list[ProofPoint] = []
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        if not isinstance(key, str):
            continue
        claim_type = _JSON_CLAIM_MAP.get(key.lower())
        if claim_type is None:
            claim_type = "other_relevant_evidence"

        claim_text = _stringify(value)
        if not claim_text:
            continue

        skills: list[str] = []
        tools: list[str] = []
        industries: list[str] = []

        if claim_type == "skill" and isinstance(value, (list, tuple)):
            skills = [str(v) for v in value if v]
        if claim_type == "tool" and isinstance(value, (list, tuple)):
            tools = [str(v) for v in value if v]
        if claim_type == "industry" and isinstance(value, (list, tuple)):
            industries = [str(v) for v in value if v]

        proofs.append(
            _make_proof(
                chunk=chunk,
                claim_type=claim_type,
                claim_text=claim_text,
                location=f"json::{key}",
                normalized_value=claim_text if isinstance(value, str) else None,
                skills=skills,
                tools=tools,
                industries=industries,
            )
        )
    return proofs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build(chunks: Iterable[ChunkRecord]) -> list[ProofPoint]:
    """Build the evidence index from extracted dossier chunks.

    Every readable chunk contributes proof points regardless of its
    source type. Unknown-type files still surface any keyword-detectable
    signals; if nothing matches, a low-confidence
    ``other_relevant_evidence`` placeholder is emitted so the file is
    not silently dropped from the canonical profile's source summary.
    """
    index: list[ProofPoint] = []
    for chunk in chunks:
        if chunk.extraction_status in {"failed", "metadata_only", "empty"}:
            continue
        if chunk.file_type == ".json" and chunk.json_data is not None:
            index.extend(_scan_json_chunk(chunk))
        else:
            index.extend(_scan_text_chunk(chunk))

    _resolve_conflicts(index)
    return index


def _resolve_conflicts(proofs: list[ProofPoint]) -> None:
    """Mark superseded / conflicting / supporting proof points.

    For each claim type we look at the highest-priority source. Lower
    priority proofs of the same claim type are marked ``supporting`` by
    default, or ``superseded`` if their normalized value disagrees with
    the winner's normalized value.
    """
    grouped: dict[ClaimType, list[ProofPoint]] = {}
    for p in proofs:
        grouped.setdefault(p.claim_type, []).append(p)

    for claim_type, items in grouped.items():
        items.sort(key=lambda p: p.source_priority)
        if not items:
            continue
        winner = items[0]
        for other in items[1:]:
            if other.source_file == winner.source_file:
                continue
            if (
                winner.normalized_value
                and other.normalized_value
                and winner.normalized_value.strip().lower()
                != other.normalized_value.strip().lower()
            ):
                other.conflict_status = "superseded"
            else:
                other.conflict_status = "supporting"


# ---------------------------------------------------------------------------
# Canonical profile synthesis
# ---------------------------------------------------------------------------


_CLAIM_TO_FIELD: dict[ClaimType, str] = {
    "identity": "name",
    "positioning": "title_or_positioning",
    "selected_offer": "selected_offer",
    "target_client": "target_client",
    "service": "services",
    "deliverable": "deliverables",
    "skill": "skills",
    "tool": "tools",
    "industry": "industries",
    "project": "portfolio_or_proof",
    "experience": "work_history",
    "work_history": "work_history",
    "metric": "achievements",
    "testimonial": "portfolio_or_proof",
    "certification": "certifications",
    "education": "education",
    "language": "languages",
    "pricing": "pricing",
    "availability": "preferred_project_types",
    "proposal_preference": "proposal_preferences",
    "weakness_or_constraint": "weaknesses_to_account_for",
    "portfolio": "portfolio_or_proof",
    "achievement": "achievements",
    "location": "location",
    "timezone": "timezone",
    "guarantee": "guarantee",
    "other_relevant_evidence": "",  # routed to source_summary only
}


_LIST_FIELDS = {
    "languages",
    "industries",
    "services",
    "deliverables",
    "skills",
    "tools",
    "work_history",
    "education",
    "certifications",
    "portfolio_or_proof",
    "achievements",
    "preferred_project_types",
    "proposal_preferences",
    "strengths",
    "weaknesses_to_account_for",
}


def _highest_confidence(proofs: list[ProofPoint]) -> ExtractionConfidence:
    rank = {"high": 3, "medium": 2, "low": 1}
    best = "low"
    for p in proofs:
        if rank[p.confidence] > rank[best]:
            best = p.confidence
    return best  # type: ignore[return-value]


def _build_field(proofs: list[ProofPoint], field_name: str) -> CanonicalProfileField:
    if not proofs:
        return CanonicalProfileField()
    proofs_sorted = sorted(proofs, key=lambda p: p.source_priority)
    primary = proofs_sorted[0]
    if field_name in _LIST_FIELDS:
        seen: list[str] = []
        for p in proofs_sorted:
            for item in (p.skills or p.tools or p.industries or []):
                if item and item not in seen:
                    seen.append(item)
            if not (p.skills or p.tools or p.industries):
                text = (p.normalized_value or p.claim_text).strip()
                if text and text not in seen:
                    seen.append(text)
        value: Any = seen if seen else primary.claim_text
    else:
        value = primary.normalized_value or primary.claim_text

    conflict_note: Optional[str] = None
    for other in proofs_sorted[1:]:
        if other.conflict_status == "superseded":
            conflict_note = (
                f"Lower-priority source {other.source_file} "
                f"disagreed and was marked superseded."
            )
            break

    return CanonicalProfileField(
        value=value,
        evidence_ids=[p.evidence_id for p in proofs_sorted],
        source_confidence=_highest_confidence(proofs_sorted),
        conflict_note=conflict_note,
    )


def synthesize_profile(
    proofs: Iterable[ProofPoint],
) -> CanonicalFreelancerProfile:
    """Build a canonical freelancer profile from the evidence index."""
    proof_list = list(proofs)
    by_field: dict[str, list[ProofPoint]] = {}

    for proof in proof_list:
        field_name = _CLAIM_TO_FIELD.get(proof.claim_type, "")
        if not field_name:
            continue
        by_field.setdefault(field_name, []).append(proof)

    profile = CanonicalFreelancerProfile()
    for field_name in (
        "name",
        "title_or_positioning",
        "location",
        "timezone",
        "languages",
        "selected_offer",
        "guarantee",
        "target_client",
        "industries",
        "services",
        "deliverables",
        "skills",
        "tools",
        "work_history",
        "education",
        "certifications",
        "portfolio_or_proof",
        "achievements",
        "pricing",
        "preferred_project_types",
        "proposal_preferences",
        "strengths",
        "weaknesses_to_account_for",
    ):
        setattr(profile, field_name, _build_field(by_field.get(field_name, []), field_name))

    # Strengths: derived from metrics + achievements claim types if not set.
    strengths_proofs = by_field.get("achievements", [])
    profile.strengths = _build_field(strengths_proofs, "strengths") if strengths_proofs else CanonicalProfileField()

    # Missing information: list canonical fields with no evidence.
    missing = [
        name
        for name in (
            "name",
            "title_or_positioning",
            "selected_offer",
            "target_client",
            "services",
            "skills",
            "tools",
            "pricing",
            "portfolio_or_proof",
            "testimonials",
        )
        if not by_field.get(name) and name != "testimonials"
    ]
    profile.missing_information = CanonicalProfileField(
        value=missing,
        source_confidence="high" if missing else "low",
    )

    # Source summary: file counts per source type.
    summary: dict[str, int] = {}
    for proof in proof_list:
        summary[proof.source_type] = summary.get(proof.source_type, 0) + 1
    profile.source_summary = CanonicalProfileField(
        value=summary,
        source_confidence="high" if summary else "low",
    )

    return profile


def build_profile(chunks: Iterable[ChunkRecord]) -> tuple[list[ProofPoint], CanonicalFreelancerProfile]:
    """Convenience wrapper: build the evidence index and canonical profile.

    This is the deterministic local path. It always uses the keyword /
    JSON scanner — it does not call any LLM. Use
    :func:`build_evidence_index` if you want the LLM-preferred path that
    records API usage in the session log.
    """
    proofs = build(chunks)
    profile = synthesize_profile(proofs)
    return proofs, profile


TASK_NAME = "evidence_index_generation"


def build_evidence_index(
    chunks: Iterable[ChunkRecord],
    *,
    allow_llm: bool = True,
) -> tuple[list[ProofPoint], CanonicalFreelancerProfile, dict]:
    """Build the evidence index, preferring LLM extraction when available.

    The LLM path is currently not implemented (a structured-evidence LLM
    prompt is a follow-up). Until that lands, this function always falls
    back to the local keyword scanner and records the stage as
    ``LOCAL PLACEHOLDER`` in the session usage log so the API Usage
    panel can flag it. The returned metadata dict carries
    ``used_api: False`` and a human-readable reason.
    """
    from app.services import llm_client  # local import to avoid cycles

    chunk_list = list(chunks or [])

    # NOTE: LLM-backed extraction will be wired up here. For now we keep
    # the deterministic local path so the rest of the flow stays usable,
    # but we record the call as a local placeholder.
    proofs = build(chunk_list)
    profile = synthesize_profile(proofs)

    reason = (
        "LLM-backed evidence extraction not yet implemented; "
        "falling back to local keyword + JSON scanner."
    )
    llm_client.record_local_use(TASK_NAME, note=reason)

    meta = {
        "task_name": TASK_NAME,
        "used_api": False,
        "status": "local_placeholder",
        "provider": None,
        "model": None,
        "error_message": reason,
        "allow_llm": allow_llm,
    }
    return proofs, profile, meta
