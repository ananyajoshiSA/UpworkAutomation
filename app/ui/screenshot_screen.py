"""Screen 3: Job screenshot upload and detail extraction.

The user uploads one or more screenshots of an Upwork job post; the tool
reads them and pulls out the structured job details. In the normal flow
those details are confirmed automatically in the backend and the analysis
unlocks immediately — there is no separate review step. Missing details
are kept as "Not visible"; the tool never guesses hidden information.

The manual "Confirm Details" review page is developer/admin-only and
appears only when ``SHOW_DEBUG_PANEL=true``. Provider/model labels and
fallback details likewise only appear in the Developer Debug Panel.
"""

from __future__ import annotations

import io
from typing import Any

import streamlit as st

from app.config import (
    GROQ_VISION_UNSUPPORTED_MESSAGE,
    clear_opportunity_state,
    get_settings,
)
from app.services.screenshot_parser import (
    NOT_VISIBLE,
    SCREENSHOT_FIELDS,
    confirm_fields,
    extract_fields,
    get_meta as get_screenshot_meta,
)
from app.ui import theme


# Optional clipboard-paste component. It lets a user copy a screenshot with
# the snipping tool and paste it straight into the app. It is an *optional*
# dependency: if it isn't installed the screen falls back to drag-drop/browse
# only, and headless tests (which don't ship the component) keep working.
#   pip install streamlit-paste-button
try:  # pragma: no cover - import guard, exercised only when the pkg is present
    from streamlit_paste_button import paste_image_button as _paste_image_button
except Exception:  # noqa: BLE001 - any import failure → paste simply unavailable
    _paste_image_button = None


# The uploader widget key. Stable across reruns (so Streamlit keeps the
# selected files) and unique to this screen.
UPLOADER_KEY = "screenshot_uploader"

# Session key holding the most recent clipboard-pasted screenshot as a
# ``{name, mime, bytes}`` record (same shape as an uploaded file), so it can
# survive reruns and feed the same downstream handler as the file uploader.
PASTED_KEY = "pasted_screenshots"

# User-facing message shown if a file was picked but its bytes never
# reached the server (e.g. a residual upload failure). Never exposes the
# underlying Axios/HTTP status.
UPLOAD_FAILED_MESSAGE = (
    "Screenshot upload failed. Please refresh and try again with a "
    "PNG or JPG file."
)

# Map a file extension to a MIME type when the browser didn't supply one.
_MIME_BY_SUFFIX = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


def _guess_mime(name: str) -> str:
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    return _MIME_BY_SUFFIX.get(suffix, "image/png")


def _collect_uploaded_screenshots(files: Any) -> list[dict]:
    """Read each Streamlit ``UploadedFile`` into an in-memory record.

    For every successfully-read upload we keep only ``{name, mime,
    bytes}`` in session state — the image is held in memory for this
    session and never written to disk. Files whose bytes can't be read
    (a failed/partial upload) are skipped so they don't count as present.
    """
    collected: list[dict] = []
    for uploaded_file in files or []:
        if uploaded_file is None:
            continue
        getvalue = getattr(uploaded_file, "getvalue", None)
        if not callable(getvalue):
            # Not a real UploadedFile (or an unreadable selection) — skip.
            continue
        try:
            data = getvalue()
        except Exception:  # noqa: BLE001 - treat any read error as a failed upload
            continue
        if not data:
            continue
        name = getattr(uploaded_file, "name", None) or "screenshot"
        mime = getattr(uploaded_file, "type", None) or _guess_mime(name)
        collected.append({"name": name, "mime": mime, "bytes": bytes(data)})
    return collected


def _capture_pasted_screenshot() -> list[dict]:
    """Render the clipboard-paste button and return the pasted image record(s).

    Returns the persisted pasted screenshot as a ``[{name, mime, bytes}]``
    list (same shape as an uploaded file) so it can be merged with the file
    uploader and feed the identical downstream handler. When a fresh image is
    pasted it replaces the previously-pasted one. If the component isn't
    installed, nothing is rendered and any previously-pasted image is kept.
    """
    if _paste_image_button is None:
        # Component not installed — drag-drop/browse remains fully functional.
        return list(st.session_state.get(PASTED_KEY) or [])

    try:
        paste_result = _paste_image_button(
            "📋 Paste screenshot from clipboard",
            key="screenshot_paste_button",
            errors="ignore",
        )
    except Exception:  # noqa: BLE001 - component unavailable (e.g. headless) → upload-only
        # Rendering the clipboard widget must never break the screen; fall back
        # to upload-only and keep any image pasted on a previous run.
        return list(st.session_state.get(PASTED_KEY) or [])
    image = getattr(paste_result, "image_data", None)
    if image is not None:
        # PIL image → PNG bytes, the same {name, mime, bytes} record an upload
        # produces. Held in memory only; never written to disk.
        try:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            st.session_state[PASTED_KEY] = [
                {
                    "name": "pasted-screenshot.png",
                    "mime": "image/png",
                    "bytes": buffer.getvalue(),
                }
            ]
        except Exception:  # noqa: BLE001 - a bad paste must never crash the screen
            pass
    return list(st.session_state.get(PASTED_KEY) or [])


def _signature(screenshots: list[dict]) -> tuple[tuple[str, int], ...]:
    """A light fingerprint of the uploaded set used to detect new uploads.

    Built from (name, byte-length) per file — enough to notice a swapped
    or added screenshot without hashing or logging the image itself.
    """
    return tuple((s["name"], len(s["bytes"])) for s in screenshots)


def _clear_stale_analysis() -> None:
    """Drop everything derived from a previous screenshot.

    Clears the extracted job fields plus every per-opportunity result
    (confirmed fields, match, score, recommendation, fingerprint,
    proposals) so a prior opportunity's analysis can never leak into a
    newly-uploaded one.
    """
    st.session_state.extracted_job_fields = None
    clear_opportunity_state(st.session_state)


def _reset_after_extract() -> None:
    """Clear every per-opportunity result before the new screenshot's
    job details are confirmed, so a previous opportunity's confirmed
    fields, match, score, recommendation, fingerprint, or proposal can
    never leak into this one."""
    clear_opportunity_state(st.session_state)


def _auto_confirm(extracted: dict) -> None:
    """Confirm the extracted job details in the backend (normal flow).

    No user review: extracted values become the confirmed values, with any
    missing field kept as "Not visible". This unlocks the analysis without
    a separate confirmation page.
    """
    st.session_state.confirmed_job_fields = confirm_fields(extracted)
    st.session_state.fields_confirmed = True


def _render_debug_panel(meta: dict) -> None:
    settings = get_settings()
    with st.expander("Developer Debug Panel", expanded=False):
        st.markdown(
            f"**Provider:** `{settings.llm_provider}` • "
            f"**Model:** `{settings.active_model}` • "
            f"**Task:** `screenshot_extraction`"
        )
        st.markdown(
            "**Vision extraction:** "
            + ("LLM API used" if meta.get("used_api") else "local fallback")
        )
        if meta.get("error_message"):
            st.caption(f"Reason: {meta['error_message']}")


def _render_debug_handoff(extracted: dict) -> None:
    """Developer/admin handoff: review the fields on the Confirm Details page."""
    with st.container(border=True):
        theme.section_label("Detected details")
        visible = sum(
            1
            for key in SCREENSHOT_FIELDS
            if (extracted.get(key) or {}).get("value")
            and (extracted.get(key) or {}).get("value") != NOT_VISIBLE
        )
        st.success(f"{visible} of {len(SCREENSHOT_FIELDS)} details captured.")
        st.caption("Review and correct them on the next step.")
        if st.button(
            "Continue to Confirm Details",
            type="primary",
            key="continue_to_confirmation_btn",
        ):
            st.session_state.current_step = "confirmation"
            st.rerun()

    _render_debug_panel(get_screenshot_meta(extracted))


def render() -> None:
    if not st.session_state.get("evidence_index"):
        st.error(
            "This step is locked. Read your dossier and create proof points first."
        )
        if st.button("Back to Dossier", key="back_to_dossier_from_screenshot"):
            st.session_state.current_step = "dossier"
            st.rerun()
        return

    settings = get_settings()
    debug = bool(getattr(settings, "show_debug_panel", False))

    st.subheader("Job Screenshot")
    st.write(
        "Upload a screenshot of the Upwork job post — or paste one you copied "
        "to the clipboard. The tool reads the job details for you, then analyzes "
        "the opportunity in one step. No manual entry needed."
    )

    with st.container(border=True):
        theme.section_label("Add screenshot")
        # Native Streamlit uploader — no custom axios/fetch, no external
        # endpoint. The bytes stay on this server in memory only.
        uploaded = st.file_uploader(
            "Drop one or more screenshots of the job post",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=UPLOADER_KEY,
            help="Files are held in this session only and never saved to disk.",
        )

        # Clipboard paste — copy a screenshot (e.g. with the snipping tool)
        # and paste it here. It produces the same {name, mime, bytes} record
        # as an upload, so both input methods feed one downstream handler.
        if _paste_image_button is not None:
            st.caption("…or paste a screenshot you copied to the clipboard:")
        pasted = _capture_pasted_screenshot()

        uploader_screenshots = _collect_uploaded_screenshots(uploaded)
        # Both inputs merge into one set the rest of the screen treats
        # uniformly (extraction already merges multiple screenshots).
        screenshots = uploader_screenshots + pasted
        st.session_state.uploaded_screenshots = screenshots
        st.session_state.screenshots_uploaded = bool(screenshots)

        # A new (or swapped) screenshot — uploaded OR pasted — invalidates any
        # analysis built from a previous one; clear it before re-analyzing.
        signature = _signature(screenshots)
        if screenshots and signature != st.session_state.get("screenshot_upload_sig"):
            _clear_stale_analysis()
        st.session_state.screenshot_upload_sig = signature

        # The widget shows the file(s) but none of their bytes reached the
        # server — surface a clean message instead of a raw upload error.
        if uploaded and not uploader_screenshots:
            st.error(UPLOAD_FAILED_MESSAGE)

        if screenshots:
            st.caption(f"{len(screenshots)} screenshot(s) ready.")
        st.caption(
            "Missing details will be marked “Not visible”. The tool won't guess "
            "hidden information."
        )

        # Groq is text-only: a Groq vision provider can't read screenshots.
        # Show a clean message and block extraction until the operator points
        # vision at a supported provider on the Setup page.
        vision_unsupported = settings.active_vision_provider == "groq"
        if vision_unsupported:
            st.warning(GROQ_VISION_UNSUPPORTED_MESSAGE)

        can_extract = bool(
            st.session_state.get("api_ok")
            and st.session_state.get("uploaded_screenshots")
            and not vision_unsupported
        )
        if can_extract:
            extract_help = None
        elif vision_unsupported:
            extract_help = (
                "Choose a vision provider that supports images "
                "(OpenAI, Anthropic, or Gemini) on the Setup page."
            )
        else:
            extract_help = "Add at least one screenshot first."

        # Normal flow merges extraction + analysis into ONE button: it reads
        # the screenshot, confirms the details in the backend, and advances
        # straight to Analysis. Debug mode keeps the granular "Extract Job
        # Details" → manual Confirm Details handoff for developers/admins.
        analyze_label = "Extract Job Details" if debug else "Analyze Opportunity"
        analyze_key = "extract_job_details_btn" if debug else "analyze_opportunity_btn"
        if st.button(
            analyze_label,
            type="primary",
            key=analyze_key,
            disabled=not can_extract,
            help=extract_help,
        ):
            # Pass raw image bytes (not file paths) to the vision parser.
            image_inputs = [
                (s["bytes"], s["mime"])
                for s in st.session_state.get("uploaded_screenshots") or []
            ]
            spinner_msg = (
                "Reading your screenshot…"
                if debug
                else "Reading your screenshot and analyzing the opportunity…"
            )
            with st.spinner(spinner_msg):
                fields = extract_fields(image_inputs)
            st.session_state.extracted_job_fields = fields
            _reset_after_extract()
            if not debug:
                # One go: auto-confirm in the backend and jump to Analysis.
                _auto_confirm(fields)
                st.session_state.current_step = "analysis"
                st.rerun()

    # Debug/admin path keeps the manual review handoff to Confirm Details.
    extracted = st.session_state.get("extracted_job_fields")
    if extracted and debug:
        _render_debug_handoff(extracted)
