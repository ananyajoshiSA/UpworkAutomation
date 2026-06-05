"""Screen 2: Dossier folder and profile evidence.

Normal (non-technical) flow — a single button:

* **Continue to Job Screenshot** — the user points the tool at a local
  folder and clicks once. That one button runs the full backend chain
  silently behind a spinner — ``validate`` → ``read_dossier`` →
  ``create_evidence_index`` — then advances. The user never sees the
  intermediate "Validate Folder" / "Read Dossier" / "Create Evidence Index"
  steps, the file count, or the dossier-strength score. A folder that can't
  be validated/read shows one clean inline error and keeps the user on the
  page — never a traceback.

Original files never leave the user's machine. The file count, the
dossier-strength score, the separate Validate/Read/Index buttons, the
chunk/proof-point stats, per-file source types, and the scoring rubric
appear only when ``SHOW_DEBUG_PANEL=true``.
"""

from __future__ import annotations

import streamlit as st

from app.config import clear_opportunity_state, get_settings, save_dossier_path
from app.models.schemas import SOURCE_TYPE_LABELS
from app.services.dossier_reader import (
    DossierReadSummary,
    read_dossier,
    summarize_chunks,
)
from app.services.evidence_index import build_evidence_index
from app.services.folder_validator import (
    FolderValidationResult,
    SUPPORTED_EXTENSIONS,
    score_rubric_labels,
    validate,
)
from app.ui import theme


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strength_color(score: int) -> str:
    if score >= 80:
        return "green"
    if score >= 60:
        return "blue"
    if score >= 40:
        return "orange"
    return "red"


def _reset_downstream() -> None:
    """Invalidate everything that depends on the dossier contents."""
    for key in (
        "dossier_chunks",
        "evidence_index",
        "canonical_profile",
        "evidence_index_meta",
        "extracted_job_fields",
    ):
        st.session_state[key] = None
    st.session_state.dossier_read = False
    # Also drop any per-opportunity analysis (match/score/recommendation/
    # proposal/fingerprint) — it was built on the old evidence.
    clear_opportunity_state(st.session_state)


def _render_validation_clean(result: FolderValidationResult, *, debug: bool) -> None:
    if result.issues and not result.exists:
        for issue in result.issues:
            st.error(issue)
        return

    # Normal UI shows ONLY the file count. The X/100 dossier-strength score,
    # its label, the "to strengthen your dossier consider adding…" line, and
    # the "evidence collection looks solid" banner mislead non-technical
    # users, so they are gated behind SHOW_DEBUG_PANEL=true.
    if debug:
        col_files, col_strength = st.columns(2)
        col_files.metric("Dossier Files", result.total_files)
        col_strength.metric("Dossier strength", f"{result.strength_score}/100")
        color = _strength_color(result.strength_score)
        st.markdown(f"**Strength:** :{color}[{result.strength_label}]")
    else:
        st.metric("Dossier Files", result.total_files)

    for issue in result.issues:
        st.error(issue)
    for warning in result.warnings:
        st.warning(warning)

    if debug and result.missing_categories:
        st.caption(
            "To strengthen your dossier, consider adding: "
            + ", ".join(result.missing_categories)
        )

    if (
        debug
        and result.can_continue
        and not result.warnings
        and not result.issues
        and result.strength_score >= 60
    ):
        st.success("Your evidence collection looks solid.")


def _render_debug_panel(result: FolderValidationResult | None) -> None:
    settings = get_settings()
    meta = st.session_state.get("evidence_index_meta") or {}
    proofs = st.session_state.get("evidence_index")
    with st.expander("Developer Debug Panel", expanded=False):
        st.markdown(
            f"**Provider:** `{settings.llm_provider}` • "
            f"**Model:** `{settings.active_model}`"
        )
        chunks = st.session_state.get("dossier_chunks")
        if st.session_state.get("dossier_read") and chunks is not None:
            summary = summarize_chunks(chunks)
            st.markdown(
                f"**Dossier read:** {summary.files_processed} file(s), "
                f"{summary.chunks_extracted} chunk(s), "
                f"{summary.failed_files} failed."
            )
            for f in summary.files:
                label = SOURCE_TYPE_LABELS.get(f.source_type, f.source_type)
                line = (
                    f"- `{f.file_path}` — {label} · status: **{f.status}** · "
                    f"chunks: **{f.chunk_count}**"
                )
                if f.warning:
                    line += f" · ⚠ {f.warning}"
                st.write(line)

        if proofs is not None and meta:
            st.markdown(
                f"**Evidence index source:** "
                + ("LLM API" if meta.get("used_api") else "local fallback")
            )
            if meta.get("error_message"):
                st.caption(f"Reason: {meta['error_message']}")
            claim_counts: dict[str, int] = {}
            for p in proofs:
                claim_counts[p.claim_type] = claim_counts.get(p.claim_type, 0) + 1
            if claim_counts:
                st.markdown("**Proof points by claim type:**")
                for claim_type, count in sorted(
                    claim_counts.items(), key=lambda kv: (-kv[1], kv[0])
                ):
                    st.write(f"- {claim_type}: **{count}**")

        if result is None:
            return

        if result.source_type_counts:
            st.markdown("**Source-type breakdown:**")
            for source_type, count in sorted(
                result.source_type_counts.items(), key=lambda kv: (-kv[1], kv[0])
            ):
                label = SOURCE_TYPE_LABELS.get(source_type, source_type)
                st.write(f"- {label}: **{count}**")

        labels = score_rubric_labels()
        if labels:
            st.markdown("**Score breakdown:**")
            for key, label in labels.items():
                awarded = result.score_breakdown.get(key, 0)
                st.write(f"- {label}: **{awarded}**")

        if result.files:
            st.markdown(f"**Files detected ({result.total_files}):**")
            for record in result.files:
                label = (
                    SOURCE_TYPE_LABELS.get(record.source_type, record.source_type)
                    if record.source_type
                    else "—"
                )
                icon = (
                    "✓"
                    if record.supported and record.readable
                    else ("⊘" if not record.supported else "✗")
                )
                st.write(f"{icon} `{record.relative_path}` — {label}")
            st.caption(
                "Supported extensions: " + ", ".join(sorted(SUPPORTED_EXTENSIONS))
            )


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


def _card_select_folder(debug: bool) -> None:
    with st.container(border=True):
        theme.section_label("Step 1 · Select dossier folder")
        st.text_input(
            "Dossier folder path",
            placeholder="/absolute/path/to/your/profile-evidence-folder",
            key="dossier_folder_path",
            help="Original files never leave your machine.",
        )
        path = (st.session_state.get("dossier_folder_path") or "").strip()
        if st.button(
            "Validate Folder",
            key="validate_folder_btn",
            disabled=not path,
            help="Enter a folder path first." if not path else None,
        ):
            st.session_state.dossier_validation = validate(path)
            _reset_downstream()

        result: FolderValidationResult | None = st.session_state.get(
            "dossier_validation"
        )
        if result is not None:
            _render_validation_clean(result, debug=debug)
        else:
            st.caption("After validation you'll see how many files were found.")


def _resolve_dossier_path(result: FolderValidationResult | None) -> str:
    """Folder path to read: prefer the typed path, fall back to validation."""
    path = (st.session_state.get("dossier_folder_path") or "").strip()
    if path:
        return path
    if result is not None and result.exists:
        return result.folder
    return ""


def _perform_read(path: str) -> None:
    """Read the dossier folder and persist the results into session state.

    A single bad file never aborts the read (the reader degrades it to a
    ``failed`` chunk), and an unexpected top-level failure leaves the user
    with a clean error rather than a crashed app.
    """
    try:
        chunks = read_dossier(path) if path else []
    except Exception:  # noqa: BLE001 - never crash the screen on a read error
        st.session_state.dossier_chunks = []
        st.session_state.dossier_read = False
        st.session_state.dossier_read_error = True
    else:
        st.session_state.dossier_chunks = chunks
        st.session_state.dossier_read = True
        st.session_state.dossier_read_error = False
    # A fresh read invalidates anything built from a previous read.
    st.session_state.evidence_index = None
    st.session_state.canonical_profile = None
    st.session_state.evidence_index_meta = None


def _render_read_summary(summary: DossierReadSummary) -> None:
    """User-facing read result. No raw dossier text — counts only."""
    st.success("Dossier read successfully")
    col_files, col_chunks, col_failed = st.columns(3)
    col_files.metric("Files processed", summary.files_processed)
    col_chunks.metric("Chunks extracted", summary.chunks_extracted)
    col_failed.metric("Failed files", summary.failed_files)
    if summary.failed_files:
        st.warning("Some files could not be read, but the app continued.")
    st.caption("Profile evidence is held in this session only.")


def _card_read_evidence() -> None:
    with st.container(border=True):
        theme.section_label("Step 2 · Read profile evidence")
        result: FolderValidationResult | None = st.session_state.get(
            "dossier_validation"
        )
        can_read = bool(result and result.can_continue)

        if st.button(
            "Read Dossier",
            key="read_dossier_btn",
            disabled=not can_read,
            help=None if can_read else "Validate a folder first.",
        ):
            path = _resolve_dossier_path(result)
            with st.spinner("Reading your profile evidence…"):
                _perform_read(path)

        # Rendered from session state (not just on click) so the summary
        # survives Streamlit reruns.
        if st.session_state.get("dossier_read_error"):
            st.error(
                "We couldn't read that folder. Re-validate the folder and "
                "try again."
            )

        chunks = st.session_state.get("dossier_chunks")
        dossier_read = bool(st.session_state.get("dossier_read"))

        if dossier_read and chunks is not None:
            _render_read_summary(summarize_chunks(chunks))

        st.divider()

        col_index, _ = st.columns(2)
        with col_index:
            can_build = bool(dossier_read and chunks)
            if st.button(
                "Create Evidence Index",
                key="build_evidence_btn",
                disabled=not can_build,
                help=None if can_build else "Read the dossier first.",
            ):
                proofs, profile, meta = build_evidence_index(chunks)
                st.session_state.evidence_index = proofs
                st.session_state.canonical_profile = profile
                st.session_state.evidence_index_meta = meta

            proofs = st.session_state.get("evidence_index")
            if proofs:
                st.metric("Proof Points", len(proofs))
                st.caption("Evidence is ready for matching.")

        proofs = st.session_state.get("evidence_index")
        if proofs:
            st.divider()
            if st.button(
                "Continue to Job Screenshot",
                type="primary",
                key="continue_to_screenshot_btn",
            ):
                save_dossier_path(_resolve_dossier_path(result))
                st.session_state.current_step = "screenshot"
                st.rerun()


# ---------------------------------------------------------------------------
# Normal-flow continue (runs validate → read → index silently)
# ---------------------------------------------------------------------------


def _continue_chain() -> None:
    """Run the full dossier pipeline silently, then advance.

    Wires the single normal-mode "Continue to Job Screenshot" button to the
    backend chain in order — ``validate()`` → ``read_dossier()`` →
    ``create_evidence_index()`` — behind one spinner, so a non-technical
    user never sees the intermediate Validate / Read / Index steps. The
    pipeline order is unchanged; only the on-screen surface collapsed to one
    action.

    A folder that can't be validated (bad path, empty folder, or no readable
    files) — or a read that yields nothing — stops the chain: the user stays
    on the page and sees one clean error, never a stack trace.
    """
    path = (st.session_state.get("dossier_folder_path") or "").strip()
    with st.spinner("Preparing your profile evidence…"):
        # 1. Validate the typed folder. A re-run invalidates any evidence
        #    built from a previously selected folder.
        result = validate(path) if path else None
        st.session_state.dossier_validation = result
        _reset_downstream()
        # 2. Read + 3. build the evidence index only when the folder is usable.
        if result is not None and result.can_continue:
            _perform_read(path)
            chunks = st.session_state.get("dossier_chunks")
            if chunks:
                proofs, profile, meta = build_evidence_index(chunks)
                st.session_state.evidence_index = proofs
                st.session_state.canonical_profile = profile
                st.session_state.evidence_index_meta = meta
    if st.session_state.get("evidence_index"):
        # Remember this folder for next launch — only persisted once it worked.
        save_dossier_path(path)
        st.session_state.current_step = "screenshot"
        st.rerun()
    else:
        st.error("Couldn't read that folder. Check the path and try again.")


def _card_continue_normal() -> None:
    """Single-button dossier card for the non-technical flow.

    One text box + one "Continue to Job Screenshot" button. The button runs
    the whole backend chain silently behind a spinner — ``validate()`` →
    ``read_dossier()`` → ``create_evidence_index()`` — then advances. There
    is no file count, no strength score, and no separate Validate / Read /
    Index buttons; those are all debug-only.
    """
    with st.container(border=True):
        theme.section_label("Select dossier folder")
        st.text_input(
            "Dossier folder path",
            placeholder="/absolute/path/to/your/profile-evidence-folder",
            key="dossier_folder_path",
            help="Original files never leave your machine.",
        )
        path = (st.session_state.get("dossier_folder_path") or "").strip()
        # The single button runs validate + read + index, then advances (or,
        # on failure, leaves a clean inline error via _continue_chain).
        if st.button(
            "Continue to Job Screenshot",
            type="primary",
            key="continue_to_screenshot_btn",
            disabled=not path,
            help="Enter a folder path first." if not path else None,
        ):
            _continue_chain()
        else:
            st.caption(
                "Point to a local folder of your profile evidence, then continue."
            )


# ---------------------------------------------------------------------------
# Public render
# ---------------------------------------------------------------------------


def render() -> None:
    if not st.session_state.get("api_ok"):
        st.error("This step is locked. Run the API check on the Setup screen first.")
        if st.button("Back to Setup", key="back_to_setup_from_dossier"):
            st.session_state.current_step = "setup"
            st.rerun()
        return

    settings = get_settings()
    debug = bool(getattr(settings, "show_debug_panel", False))

    # Remembered dossier path: the first time this screen renders in a session,
    # seed the (still-empty) text box from the last-used folder saved in .env so
    # the user doesn't retype it each launch. main._init_session_state pre-seeds
    # this key to "", so a plain "not in session_state" check never fires — we
    # guard with a one-shot flag and only fill when the box is empty, never
    # overwriting a path the user typed. Runs before the text_input is created.
    if not st.session_state.get("_dossier_path_seeded"):
        st.session_state["_dossier_path_seeded"] = True
        saved_path = (getattr(settings, "dossier_folder_path", None) or "").strip()
        current_path = (st.session_state.get("dossier_folder_path") or "").strip()
        if saved_path and not current_path:
            st.session_state["dossier_folder_path"] = saved_path

    st.subheader("Dossier")
    st.write(
        "Point the tool at a local folder of your profile evidence — résumé, "
        "case studies, portfolio, transcripts. Your files stay on your machine."
    )

    if debug:
        # Developer/admin mode keeps the granular Validate Folder / Read
        # Dossier / Create Evidence Index buttons, the file count, strength
        # score, chunk/proof-point stats, and the debug panel.
        _card_select_folder(debug)
        _card_read_evidence()
        _render_debug_panel(st.session_state.get("dossier_validation"))
    else:
        # Normal mode: one text box + one button. The button silently runs
        # validate → read → index and lands the user on the Job Screenshot
        # step (no file count, no strength score, no extra buttons).
        _card_continue_normal()
