"""Tests for the dossier reader service.

These confirm the behaviour the "Read Dossier" button depends on:

* TXT / MD / JSON / CSV files are read into chunks.
* JSON is parsed into ``json_data``.
* Unsupported files are skipped without crashing.
* A single failing file is reported as ``failed`` but does not stop the
  rest of the read.
* ``summarize_chunks`` produces a text-free rollup with correct counts.
* No raw dossier text leaks into warnings.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.dossier_reader import (
    DossierReadSummary,
    read_dossier,
    summarize_chunks,
)


# ---------------------------------------------------------------------------
# Per-type reading
# ---------------------------------------------------------------------------


def test_txt_file_is_read_successfully(tmp_path: Path):
    (tmp_path / "resume.txt").write_text("Senior strategist with 8 years of work.")
    chunks = read_dossier(tmp_path)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.file_name == "resume.txt"
    assert chunk.file_type == ".txt"
    assert chunk.extraction_status == "ok"
    assert "Senior strategist" in chunk.extracted_text


def test_json_file_is_parsed_successfully(tmp_path: Path):
    payload = {"name": "Alex", "skills": ["python", "writing"]}
    (tmp_path / "profile.json").write_text(json.dumps(payload))
    chunks = read_dossier(tmp_path)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.file_type == ".json"
    assert chunk.extraction_status == "ok"
    assert chunk.json_data == payload


def test_markdown_section_name_is_detected(tmp_path: Path):
    (tmp_path / "positioning.md").write_text("# Positioning\nWe help SaaS founders.")
    chunks = read_dossier(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].section_name == "Positioning"


def test_image_files_are_metadata_only(tmp_path: Path):
    (tmp_path / "headshot.png").write_bytes(b"\x89PNG stub")
    chunks = read_dossier(tmp_path)
    assert len(chunks) == 1
    assert chunks[0].extraction_status == "metadata_only"
    assert chunks[0].extracted_text == ""


# ---------------------------------------------------------------------------
# Failure tolerance
# ---------------------------------------------------------------------------


def test_unsupported_files_do_not_crash_reading(tmp_path: Path):
    (tmp_path / "resume.txt").write_text("hello")
    (tmp_path / "archive.zip").write_bytes(b"PK stub")
    (tmp_path / "deck.pptx").write_bytes(b"stub")
    chunks = read_dossier(tmp_path)
    # Only the supported file is read; unsupported ones are silently skipped.
    file_names = {c.file_name for c in chunks}
    assert file_names == {"resume.txt"}


def test_failed_file_is_reported_but_does_not_stop_the_process(tmp_path: Path):
    (tmp_path / "good.txt").write_text("readable content")
    (tmp_path / "broken.json").write_text("{not valid json")
    (tmp_path / "profile.json").write_text(json.dumps({"name": "Alex"}))
    chunks = read_dossier(tmp_path)

    by_name = {c.file_name: c for c in chunks}
    # All three were processed.
    assert set(by_name) == {"good.txt", "broken.json", "profile.json"}
    # The broken file is flagged, with a warning, but the others succeeded.
    assert by_name["broken.json"].extraction_status == "failed"
    assert by_name["broken.json"].extraction_warning
    assert by_name["good.txt"].extraction_status == "ok"
    assert by_name["profile.json"].extraction_status == "ok"


def test_missing_folder_returns_empty_list(tmp_path: Path):
    assert read_dossier(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summarize_chunks_counts_files_chunks_and_failures(tmp_path: Path):
    (tmp_path / "good.txt").write_text("readable content")
    (tmp_path / "broken.json").write_text("{not valid json")
    (tmp_path / "profile.json").write_text(json.dumps({"name": "Alex"}))

    summary = summarize_chunks(read_dossier(tmp_path))

    assert isinstance(summary, DossierReadSummary)
    assert summary.files_processed == 3
    assert summary.chunks_extracted == 3
    assert summary.failed_files == 1
    failed = [f for f in summary.files if f.status == "failed"]
    assert len(failed) == 1
    assert failed[0].file_name == "broken.json"


def test_summary_of_empty_read_is_zeroed():
    summary = summarize_chunks([])
    assert summary.files_processed == 0
    assert summary.chunks_extracted == 0
    assert summary.failed_files == 0
    assert summary.files == []


def test_summary_warnings_carry_no_raw_text(tmp_path: Path):
    # A readable file whose raw text contains a sentinel; the summary must
    # never echo that text (warnings are metadata only).
    (tmp_path / "secret.txt").write_text("SENTINEL_RAW_TEXT_4242 confidential")
    (tmp_path / "broken.json").write_text("{also broken")
    summary = summarize_chunks(read_dossier(tmp_path))
    for f in summary.files:
        assert f.warning is None or "SENTINEL_RAW_TEXT_4242" not in f.warning
