"""Folder Validator.

Scans a user-supplied dossier folder, classifies each readable file into a
source type, and reports a dossier strength score that reflects the
overall quality and completeness of the evidence collection.

The validator is intentionally flexible: it does not require any single
file (resume, profile, etc.) to be present. A dossier is valid as long
as at least one readable supported file exists.

Filename stems and a small text sample from plain-text files are used
for classification signals. Full PDF / DOCX / image content is not read
here — see app/services/dossier_reader.py for full extraction.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.models.schemas import (
    SOURCE_PRIORITY,
    SOURCE_TYPE_LABELS,
    SourceType,
)
from app.utils.file_utils import iter_contained_files


SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".docx", ".txt", ".md", ".json", ".csv", ".png", ".jpg", ".jpeg"}
)

TEXT_LIKE_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".json", ".csv"})

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg"})

WARNING_SCORE_THRESHOLD = 30
THIN_DOSSIER_THRESHOLD = 40

STRENGTH_LABELS: tuple[tuple[int, str], ...] = (
    (80, "Strong evidence collection"),
    (60, "Good evidence collection"),
    (40, "Usable but needs stronger proof"),
    (0, "Thin dossier"),
)


# Bucket key, label, weight. Scoring is rule-driven (see _score below) so
# different buckets can use different signals (source type vs. keyword vs.
# diversity count).
_BUCKETS: tuple[tuple[str, str, int], ...] = (
    ("structured_profile_data", "Structured profile data", 20),
    ("positioning_and_offer", "Positioning and offer clarity", 15),
    ("work_history_background", "Work history and background", 15),
    ("skills_tools_services", "Skills, tools, and service evidence", 15),
    ("proof_credibility", "Proof and credibility", 15),
    ("proposal_preferences_pricing", "Proposal preferences and pricing", 10),
    ("completeness_diversity", "Completeness and diversity of sources", 10),
)


# Filename / content keyword patterns mapped to source types. The order
# matters: more specific patterns are checked before general ones.
_FILENAME_RULES: tuple[tuple[SourceType, tuple[str, ...]], ...] = (
    ("linkedin_optimization", ("linkedin optimization", "linkedin optimisation", "linkedin rewrite")),
    ("skillarbitrage_dossier_roadmap", ("skillarbitrage", "dossier", "roadmap")),
    ("offer_blueprint", ("offer blueprint", "offer-blueprint", "offer_blueprint", "blueprint", "selected offer")),
    ("upwork_profile", ("upwork",)),
    ("linkedin_profile", ("linkedin",)),
    ("discovery_call_transcript", ("transcript", "discovery call", "discovery-call", "discovery_call", "call notes")),
    ("resume_or_cv", ("resume", "curriculum vitae", "cv")),
    ("testimonial_or_review", ("testimonial", "review", "feedback", "endorsement", "recommendation letter")),
    ("portfolio_or_case_study", ("portfolio", "case study", "case-study", "case_study", "work sample", "work_sample", "sample")),
    ("past_proposal", ("proposal sample", "proposal-sample", "past proposal", "previous proposal", "proposal example", "proposal")),
    ("pricing_or_service_package", ("pricing", "rate card", "rate-card", "service package", "service-package", "service stack", "service-stack", "package", "rates")),
    ("certification_or_course", ("certificate", "certification", "course completion", "course-completion", "course_completion", "course", "credential")),
    ("client_research", ("client avatar", "client-avatar", "target client", "target-client", "icp ", " icp", "icp_", "ideal client")),
    ("niche_research", ("niche", "market research", "market-research", "industry research", "industry-research")),
    ("personal_branding", ("personal brand", "personal-brand", "headline", "bio", "about section", "about-section", "branding")),
    ("strategy_document", ("strategy", "positioning", "plan")),
    ("notes_or_misc_profile_context", ("notes", "scratch")),
)


@dataclass
class FileRecord:
    """One file's place in the dossier."""

    relative_path: str
    extension: str
    supported: bool
    readable: bool
    source_type: Optional[SourceType] = None
    source_priority: Optional[int] = None
    extraction_status: str = "pending"
    modified_at: Optional[str] = None
    note: Optional[str] = None


@dataclass
class FolderValidationResult:
    folder: str
    exists: bool = False
    files: list[FileRecord] = field(default_factory=list)
    last_modified: dict[str, str] = field(default_factory=dict)
    strength_score: int = 0
    score_breakdown: dict[str, int] = field(default_factory=dict)
    source_type_counts: dict[str, int] = field(default_factory=dict)
    missing_categories: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # --- backwards-compatible / convenience views ---

    @property
    def readable_files(self) -> list[str]:
        return [
            f.relative_path for f in self.files if f.supported and f.readable
        ]

    @property
    def unsupported_files(self) -> list[str]:
        return [f.relative_path for f in self.files if not f.supported]

    @property
    def readable_count(self) -> int:
        return len(self.readable_files)

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def supported_count(self) -> int:
        return sum(1 for f in self.files if f.supported)

    @property
    def unsupported_count(self) -> int:
        return sum(1 for f in self.files if not f.supported)

    @property
    def is_empty(self) -> bool:
        return not self.files

    @property
    def below_threshold(self) -> bool:
        return self.strength_score < WARNING_SCORE_THRESHOLD

    @property
    def can_continue(self) -> bool:
        return self.exists and self.readable_count > 0

    @property
    def strength_label(self) -> str:
        for threshold, label in STRENGTH_LABELS:
            if self.strength_score >= threshold:
                return label
        return "Thin dossier"


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


_STRUCTURED_PROFILE_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "title",
        "positioning",
        "skills",
        "tools",
        "services",
        "service_stack",
        "target_client",
        "work_history",
        "proposal_preferences",
        "selected_offer",
        "offer",
        "pricing",
        "deliverables",
        "industries",
    }
)


def _is_placeholder(value: object) -> bool:
    """Return True if a JSON value looks like an unfilled template slot."""
    if value is None:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return True
        return s in {
            "tbd",
            "todo",
            "todo:",
            "placeholder",
            "fill in",
            "fill-in",
            "fillme",
            "n/a",
            "na",
            "example",
            "your name",
            "your title",
            "<insert>",
            "<replace>",
        }
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _classify_json(data: object) -> tuple[SourceType, Optional[str]]:
    """Decide which JSON source type a parsed payload looks like."""
    if not isinstance(data, dict):
        return "generic_profile_document", None

    # Map each lowercased profile key back to the value under the original
    # (possibly differently-cased) JSON key, so "Name"/"Skills" are matched.
    lower_to_value: dict[str, object] = {}
    for raw_key, value in data.items():
        if isinstance(raw_key, str):
            lower_to_value.setdefault(raw_key.lower(), value)
    profile_hits = set(lower_to_value) & _STRUCTURED_PROFILE_KEYS

    if profile_hits:
        # Treat as a real profile only if at least one of the PROFILE-SHAPED
        # keys actually has data. A template whose name/skills/etc. are all
        # empty is a blank template even if some unrelated metadata key
        # (e.g. "version"/"schema") is populated — so we check profile_hits,
        # not every key in the document.
        profile_has_data = any(
            not _is_placeholder(lower_to_value.get(k)) for k in profile_hits
        )
        if profile_has_data:
            return "structured_profile_json", None
        return "dossier_template_json", "All structured fields look empty."

    return "generic_profile_document", None


def _read_text_sample(path: Path, limit_bytes: int = 8192) -> str:
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(limit_bytes)
    except OSError:
        return ""
    return chunk.decode("utf-8", errors="ignore")


def _normalize_for_match(text: str) -> str:
    return text.lower().replace("_", " ").replace("-", " ")


def classify_source(
    path: Path,
    text_sample: str = "",
    json_payload: object = None,
) -> tuple[SourceType, Optional[str]]:
    """Classify a single file as the best-matching source type.

    Returns (source_type, optional_note). The note is surfaced to the UI
    when classification adds useful context (e.g., template JSON).
    Unknown but readable files fall through to ``unknown_supported_file``.
    """
    ext = path.suffix.lower()

    # JSON gets a content-aware classifier.
    if ext == ".json" and json_payload is not None:
        return _classify_json(json_payload)
    if ext == ".json":
        return "generic_profile_document", None

    stem = _normalize_for_match(path.stem)
    sample = _normalize_for_match(text_sample[:2048]) if text_sample else ""

    for source_type, patterns in _FILENAME_RULES:
        for pattern in patterns:
            if pattern in stem:
                return source_type, None
    # Content-only fallback: look in the text sample for a few high-signal hits.
    for source_type, patterns in _FILENAME_RULES:
        for pattern in patterns:
            if pattern in sample:
                return source_type, None

    # Image files with no naming signal are still useful as work samples.
    if ext in IMAGE_EXTENSIONS:
        return "portfolio_or_case_study", "Image file — treated as a sample."

    return "unknown_supported_file", None


# ---------------------------------------------------------------------------
# Strength scoring
# ---------------------------------------------------------------------------


_SOURCE_TO_BUCKETS: dict[str, tuple[str, ...]] = {
    "structured_profile_json": ("structured_profile_data",),
    "dossier_template_json": (),
    "skillarbitrage_dossier_roadmap": ("structured_profile_data", "positioning_and_offer"),
    "linkedin_optimization": ("positioning_and_offer", "work_history_background"),
    "offer_blueprint": ("positioning_and_offer",),
    "upwork_profile": ("work_history_background", "skills_tools_services"),
    "linkedin_profile": ("work_history_background",),
    "discovery_call_transcript": ("work_history_background", "positioning_and_offer"),
    "resume_or_cv": ("work_history_background",),
    "pricing_or_service_package": ("proposal_preferences_pricing", "skills_tools_services"),
    "portfolio_or_case_study": ("proof_credibility", "skills_tools_services"),
    "testimonial_or_review": ("proof_credibility",),
    "certification_or_course": ("proof_credibility", "skills_tools_services"),
    "past_proposal": ("proposal_preferences_pricing",),
    "client_research": ("positioning_and_offer",),
    "niche_research": ("positioning_and_offer",),
    "personal_branding": ("positioning_and_offer",),
    "strategy_document": ("positioning_and_offer",),
    "notes_or_misc_profile_context": (),
    "generic_profile_document": (),
    "unknown_supported_file": (),
}


_SKILL_TOOL_KEYWORDS = (
    "skill", "skills", "tool", "tools", "stack", "tech stack",
    "service", "services", "deliverable", "deliverables", "capability",
    "capabilities", "expertise", "specialty", "speciality",
)

_PROOF_KEYWORDS = (
    "result", "results", "metric", "kpi", "roi", "growth", "revenue",
    "uplift", "conversion", "increase", "decrease", "%", "$",
    "testimonial", "review", "feedback",
)

_PRICING_KEYWORDS = (
    "rate", "rates", "pricing", "package", "tone", "voice",
    "deposit", "retainer", "hourly", "fixed", "budget", "fee",
)


def _bucket_count(records: list[FileRecord], bucket_key: str) -> int:
    return sum(
        1
        for r in records
        if r.source_type and bucket_key in _SOURCE_TO_BUCKETS.get(r.source_type, ())
    )


def _score(
    records: list[FileRecord],
    text_corpus: str,
) -> tuple[int, dict[str, int]]:
    """Compute the 7-bucket strength score (max 100)."""
    breakdown: dict[str, int] = {}
    total = 0

    has_structured = any(
        r.source_type == "structured_profile_json" for r in records
    )
    breakdown["structured_profile_data"] = 20 if has_structured else 0

    has_positioning = _bucket_count(records, "positioning_and_offer") > 0
    breakdown["positioning_and_offer"] = 15 if has_positioning else 0

    has_history = _bucket_count(records, "work_history_background") > 0
    breakdown["work_history_background"] = 15 if has_history else 0

    has_skills_doc = _bucket_count(records, "skills_tools_services") > 0
    skills_in_corpus = any(kw in text_corpus for kw in _SKILL_TOOL_KEYWORDS)
    breakdown["skills_tools_services"] = (
        15 if (has_skills_doc or skills_in_corpus) else 0
    )

    has_proof_doc = _bucket_count(records, "proof_credibility") > 0
    proof_in_corpus = any(kw in text_corpus for kw in _PROOF_KEYWORDS)
    breakdown["proof_credibility"] = (
        15 if (has_proof_doc or proof_in_corpus) else 0
    )

    has_pricing_doc = _bucket_count(records, "proposal_preferences_pricing") > 0
    pricing_in_corpus = any(kw in text_corpus for kw in _PRICING_KEYWORDS)
    breakdown["proposal_preferences_pricing"] = (
        10 if (has_pricing_doc or pricing_in_corpus) else 0
    )

    useful_source_types = {
        r.source_type
        for r in records
        if r.source_type
        and r.source_type
        not in {
            "unknown_supported_file",
            "dossier_template_json",
        }
    }
    breakdown["completeness_diversity"] = (
        10 if len(useful_source_types) >= 3 else 0
    )

    total = sum(breakdown.values())
    return min(total, 100), breakdown


def score_rubric_labels() -> dict[str, str]:
    return {key: label for key, label, _w in _BUCKETS}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _is_readable(path: Path) -> bool:
    return os.access(path, os.R_OK)


def _load_json_safely(path: Path) -> tuple[object, Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return json.load(fh), None
    except (OSError, ValueError) as exc:
        return None, f"JSON unreadable: {exc.__class__.__name__}"


def _build_corpus(records: list[FileRecord], samples: dict[str, str]) -> str:
    parts: list[str] = []
    for r in records:
        parts.append(_normalize_for_match(Path(r.relative_path).stem))
        sample = samples.get(r.relative_path)
        if sample:
            parts.append(_normalize_for_match(sample))
    return "\n".join(parts)


def _missing_categories(breakdown: dict[str, int]) -> list[str]:
    labels = score_rubric_labels()
    return [labels[key] for key, awarded in breakdown.items() if awarded == 0]


def validate(folder_path: str | Path) -> FolderValidationResult:
    """Validate a dossier folder as a flexible evidence collection.

    Walks the folder, classifies each readable file into a source type,
    and computes a 0-100 strength score across seven evidence buckets.
    No assumption is made that any specific file type is mandatory — the
    only hard requirement is that at least one readable supported file
    exists.
    """
    folder = Path(folder_path).expanduser()
    result = FolderValidationResult(folder=str(folder))

    if not folder.exists():
        result.issues.append(f"Folder does not exist: {folder}")
        return result
    if not folder.is_dir():
        result.issues.append(f"Path is not a directory: {folder}")
        return result

    result.exists = True

    try:
        # Symlink-safe, containment-checked, count-bounded walk (shared with
        # the dossier reader). Symlinked entries and paths resolving outside
        # the folder are excluded so the validator never classifies or
        # samples content from outside the chosen folder.
        candidates = list(iter_contained_files(folder))
    except OSError as exc:
        result.issues.append(f"Unable to walk folder: {exc}")
        return result

    samples: dict[str, str] = {}
    modified_times: list[float] = []

    for path in candidates:
        rel = str(path.relative_to(folder))
        ext = path.suffix.lower()
        record = FileRecord(
            relative_path=rel,
            extension=ext,
            supported=ext in SUPPORTED_EXTENSIONS,
            readable=_is_readable(path),
        )

        try:
            mtime = path.stat().st_mtime
            record.modified_at = datetime.fromtimestamp(mtime).isoformat(
                timespec="seconds"
            )
            modified_times.append(mtime)
        except OSError:
            pass

        if not record.supported:
            record.extraction_status = "unsupported"
            result.files.append(record)
            continue

        if not record.readable:
            record.extraction_status = "unreadable"
            result.issues.append(f"Unreadable file skipped: {rel}")
            result.files.append(record)
            continue

        text_sample = ""
        json_payload: object = None

        if ext in TEXT_LIKE_EXTENSIONS:
            text_sample = _read_text_sample(path)
            if ext == ".json":
                json_payload, json_warning = _load_json_safely(path)
                if json_warning:
                    record.note = json_warning

        source_type, note = classify_source(
            path, text_sample=text_sample, json_payload=json_payload
        )
        record.source_type = source_type
        record.source_priority = SOURCE_PRIORITY[source_type]
        record.extraction_status = "scanned"
        if note and not record.note:
            record.note = note

        if text_sample:
            samples[rel] = text_sample

        result.files.append(record)

    # Source-type counts.
    counts: dict[str, int] = {}
    for r in result.files:
        if r.source_type:
            counts[r.source_type] = counts.get(r.source_type, 0) + 1
    result.source_type_counts = counts

    # Modified date range.
    if modified_times:
        earliest = datetime.fromtimestamp(min(modified_times))
        latest = datetime.fromtimestamp(max(modified_times))
        result.last_modified = {
            "earliest": earliest.isoformat(timespec="seconds"),
            "latest": latest.isoformat(timespec="seconds"),
        }

    readable_records = [r for r in result.files if r.supported and r.readable]
    corpus = _build_corpus(readable_records, samples)
    total, breakdown = _score(readable_records, corpus)
    result.strength_score = total
    result.score_breakdown = breakdown
    result.missing_categories = _missing_categories(breakdown)

    # --- Warnings ---
    if result.is_empty:
        result.warnings.append(
            "Folder is empty. You can continue, but the proposal will lack grounding."
        )
    elif not readable_records:
        result.warnings.append(
            "No readable supported files were found. "
            f"Supported extensions: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    if readable_records:
        useful_types = {
            r.source_type
            for r in readable_records
            if r.source_type
            and r.source_type
            not in {"unknown_supported_file", "dossier_template_json"}
        }
        only_resume_like = useful_types and useful_types.issubset(
            {"resume_or_cv", "linkedin_profile", "upwork_profile"}
        )
        if only_resume_like:
            result.warnings.append(
                "Your dossier is readable but thin. Add offer details, proof "
                "files, portfolio samples, testimonials, or positioning "
                "documents for stronger proposals."
            )

    if result.below_threshold and not result.is_empty:
        result.warnings.append(
            f"Dossier strength score is {result.strength_score}/100 "
            f"(below the recommended {WARNING_SCORE_THRESHOLD}). "
            "You can continue, but proposal grounding will be weak."
        )

    if result.unsupported_count and readable_records:
        result.notes.append(
            f"{result.unsupported_count} file(s) skipped as unsupported."
        )

    _ = SOURCE_TYPE_LABELS  # exported for UI consumers
    return result
