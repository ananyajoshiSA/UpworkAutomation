"""Tests for the dossier folder validator.

The validator must treat the dossier folder as a flexible evidence
collection. These tests check the behavior described in the build plan:
classification, scoring, warnings, source-type breakdown, and failure
tolerance.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.services.folder_validator import (
    FolderValidationResult,
    SUPPORTED_EXTENSIONS,
    WARNING_SCORE_THRESHOLD,
    classify_source,
    validate,
)


# ---------------------------------------------------------------------------
# Shape / smoke tests
# ---------------------------------------------------------------------------


def test_folder_validation_result_default_shape():
    result = FolderValidationResult(folder="/tmp/example")
    assert result.folder == "/tmp/example"
    assert result.exists is False
    assert result.files == []
    assert result.last_modified == {}
    assert result.strength_score == 0
    assert result.score_breakdown == {}
    assert result.source_type_counts == {}
    assert result.missing_categories == []
    assert result.issues == []
    assert result.warnings == []
    assert result.readable_count == 0
    assert result.total_files == 0
    assert result.is_empty is True
    assert result.strength_label == "Thin dossier"


def test_validate_missing_folder(tmp_path: Path):
    missing = tmp_path / "does_not_exist"
    result = validate(missing)
    assert result.exists is False
    assert result.can_continue is False
    assert result.issues
    assert "does not exist" in result.issues[0].lower()


def test_validate_path_is_file_not_directory(tmp_path: Path):
    file_path = tmp_path / "lone.txt"
    file_path.write_text("hello")
    result = validate(file_path)
    assert result.exists is False
    assert any("not a directory" in i.lower() for i in result.issues)


def test_validate_empty_folder_warns_and_blocks_continue(tmp_path: Path):
    result = validate(tmp_path)
    assert result.exists is True
    assert result.readable_count == 0
    assert result.is_empty is True
    assert result.can_continue is False
    assert result.strength_score == 0
    assert any("empty" in w.lower() for w in result.warnings)


def test_validate_accepts_str_path(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    result = validate(str(tmp_path))
    assert result.exists is True
    assert result.readable_count == 1


def test_validate_reports_modified_date_range(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("alpha")
    b.write_text("beta")
    os.utime(a, (1_700_000_000, 1_700_000_000))
    os.utime(b, (1_710_000_000, 1_710_000_000))

    result = validate(tmp_path)

    assert "earliest" in result.last_modified
    assert "latest" in result.last_modified
    assert result.last_modified["earliest"] <= result.last_modified["latest"]


def test_validate_walks_subdirectories(tmp_path: Path):
    sub = tmp_path / "nested" / "deeper"
    sub.mkdir(parents=True)
    (sub / "resume.pdf").write_bytes(b"stub")
    result = validate(tmp_path)
    assert any(f.endswith("resume.pdf") for f in result.readable_files)


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("resume.pdf", "resume_or_cv"),
        ("My_CV_2024.docx", "resume_or_cv"),
        ("linkedin_profile_export.pdf", "linkedin_profile"),
        ("LinkedIn-Optimization-Notes.docx", "linkedin_optimization"),
        ("upwork_profile.pdf", "upwork_profile"),
        ("discovery_call_transcript.txt", "discovery_call_transcript"),
        ("skillarbitrage_dossier.pdf", "skillarbitrage_dossier_roadmap"),
        ("offer_blueprint.docx", "offer_blueprint"),
        ("client_testimonial.pdf", "testimonial_or_review"),
        ("portfolio_case_study.pdf", "portfolio_or_case_study"),
        ("rate-card.pdf", "pricing_or_service_package"),
        ("certificate_python.pdf", "certification_or_course"),
        ("niche-research.md", "niche_research"),
        ("client_avatar.md", "client_research"),
        ("personal-brand.md", "personal_branding"),
        ("strategy_2026.docx", "strategy_document"),
        ("session_notes.txt", "notes_or_misc_profile_context"),
        ("mystery.pdf", "unknown_supported_file"),
    ],
)
def test_classify_source_from_filename(tmp_path: Path, filename: str, expected: str):
    path = tmp_path / filename
    path.write_bytes(b"stub")
    source_type, _note = classify_source(path)
    assert source_type == expected


def test_classify_structured_profile_json(tmp_path: Path):
    payload = {
        "name": "A. Builder",
        "title": "Independent strategist",
        "skills": ["python", "writing"],
        "target_client": "B2B SaaS founders",
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload))
    source_type, _note = classify_source(path, json_payload=payload)
    assert source_type == "structured_profile_json"


def test_classify_template_json(tmp_path: Path):
    payload = {
        "name": "",
        "title": "",
        "skills": [],
        "services": [],
    }
    path = tmp_path / "dossier_template.json"
    path.write_text(json.dumps(payload))
    source_type, note = classify_source(path, json_payload=payload)
    assert source_type == "dossier_template_json"
    assert note and "empty" in note.lower()


def test_classify_generic_json(tmp_path: Path):
    payload = {"random_key": "random_value", "another": 42}
    path = tmp_path / "random.json"
    path.write_text(json.dumps(payload))
    source_type, _note = classify_source(path, json_payload=payload)
    assert source_type == "generic_profile_document"


# ---------------------------------------------------------------------------
# Scenario tests
# ---------------------------------------------------------------------------


def test_folder_with_only_resume_warns_thin(tmp_path: Path):
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4 stub")
    result = validate(tmp_path)
    assert result.readable_count == 1
    assert result.source_type_counts.get("resume_or_cv") == 1
    assert any("thin" in w.lower() for w in result.warnings)


def test_strong_evidence_collection_scores_high(tmp_path: Path):
    (tmp_path / "profile.json").write_text(
        json.dumps(
            {
                "name": "Alex Worker",
                "title": "AI content strategist",
                "skills": ["llm", "content design"],
                "tools": ["notion", "figma"],
                "target_client": "Series A SaaS",
                "services": ["content systems"],
                "pricing": {"hourly": 150},
            }
        )
    )
    (tmp_path / "skillarbitrage_dossier.pdf").write_bytes(b"stub")
    (tmp_path / "offer_blueprint.docx").write_bytes(b"stub")
    (tmp_path / "portfolio_case_study.pdf").write_bytes(b"stub")
    (tmp_path / "client_testimonial.docx").write_bytes(b"stub")
    (tmp_path / "results.txt").write_text(
        "Drove 40% revenue growth and $1M ROI for SaaS clients"
    )
    (tmp_path / "rate-card.pdf").write_bytes(b"stub")

    result = validate(tmp_path)

    assert result.strength_score >= 80
    assert result.strength_label == "Strong evidence collection"
    assert result.score_breakdown["structured_profile_data"] == 20
    assert result.score_breakdown["positioning_and_offer"] == 15
    assert result.score_breakdown["proof_credibility"] == 15
    assert result.score_breakdown["proposal_preferences_pricing"] == 10
    assert result.score_breakdown["completeness_diversity"] == 10
    assert not result.below_threshold


def test_transcript_plus_linkedin_combo(tmp_path: Path):
    (tmp_path / "discovery-call-transcript.txt").write_text(
        "Client said the brand voice should be warm."
    )
    (tmp_path / "linkedin_profile.pdf").write_bytes(b"stub")
    result = validate(tmp_path)
    assert result.source_type_counts.get("discovery_call_transcript") == 1
    assert result.source_type_counts.get("linkedin_profile") == 1
    assert result.score_breakdown["work_history_background"] == 15


def test_portfolio_testimonial_proof_files(tmp_path: Path):
    (tmp_path / "portfolio_case_study.pdf").write_bytes(b"stub")
    (tmp_path / "client_testimonial.docx").write_bytes(b"stub")
    (tmp_path / "certificate_python.pdf").write_bytes(b"stub")
    (tmp_path / "results.md").write_text("Delivered 3x ROI in 60 days.")
    result = validate(tmp_path)
    assert result.score_breakdown["proof_credibility"] == 15
    # Three distinct useful source types (portfolio, testimonial, cert).
    assert result.score_breakdown["completeness_diversity"] == 10


def test_unknown_supported_files_are_kept(tmp_path: Path):
    (tmp_path / "mystery.pdf").write_bytes(b"stub")
    (tmp_path / "random_thoughts.md").write_text("Some musings.")
    result = validate(tmp_path)
    assert result.readable_count == 2
    counts = result.source_type_counts
    assert counts.get("unknown_supported_file", 0) + counts.get(
        "notes_or_misc_profile_context", 0
    ) >= 1


def test_unsupported_files_listed_but_do_not_break(tmp_path: Path):
    (tmp_path / "resume.pdf").write_bytes(b"stub")
    (tmp_path / "archive.zip").write_bytes(b"PK stub")
    (tmp_path / "deck.pptx").write_bytes(b"stub")
    result = validate(tmp_path)
    assert result.readable_count == 1
    assert set(result.unsupported_files) == {"archive.zip", "deck.pptx"}
    assert "archive.zip" not in result.readable_files


def test_mixed_evidence_collection_scores_medium(tmp_path: Path):
    (tmp_path / "resume.pdf").write_bytes(b"stub")
    (tmp_path / "case_study.pdf").write_bytes(b"stub")
    (tmp_path / "rate-card.txt").write_text("Hourly rate: $120")
    result = validate(tmp_path)
    assert 40 <= result.strength_score < 80
    assert result.strength_label in {
        "Good evidence collection",
        "Usable but needs stronger proof",
    }


def test_source_breakdown_counts_match_files(tmp_path: Path):
    (tmp_path / "resume.pdf").write_bytes(b"stub")
    (tmp_path / "old_resume.pdf").write_bytes(b"stub")
    (tmp_path / "linkedin_profile.pdf").write_bytes(b"stub")
    result = validate(tmp_path)
    assert result.source_type_counts.get("resume_or_cv") == 2
    assert result.source_type_counts.get("linkedin_profile") == 1


def test_json_structure_detected_as_profile(tmp_path: Path):
    payload = {
        "name": "Alex",
        "title": "Strategist",
        "skills": ["a", "b"],
    }
    (tmp_path / "profile.json").write_text(json.dumps(payload))
    result = validate(tmp_path)
    assert result.source_type_counts.get("structured_profile_json") == 1
    assert result.score_breakdown["structured_profile_data"] == 20


def test_file_extraction_failure_does_not_crash_validation(tmp_path: Path, monkeypatch):
    # An empty / corrupt JSON file should not raise, just be flagged.
    (tmp_path / "broken.json").write_text("{not valid json")
    (tmp_path / "resume.pdf").write_bytes(b"stub")
    result = validate(tmp_path)
    assert result.readable_count == 2
    broken_record = next(
        f for f in result.files if f.relative_path == "broken.json"
    )
    # Classification of a broken JSON falls back to a non-crashing default.
    assert broken_record.source_type is not None


def test_below_threshold_warning(tmp_path: Path):
    (tmp_path / "untitled.pdf").write_bytes(b"stub")
    result = validate(tmp_path)
    if result.strength_score < WARNING_SCORE_THRESHOLD:
        assert any("below the recommended" in w.lower() for w in result.warnings)


def test_supported_extensions_now_include_md_json_csv():
    for ext in (".md", ".json", ".csv"):
        assert ext in SUPPORTED_EXTENSIONS
