"""Upwork Proposal Strategist — Streamlit entry point.

End users never run this directly — they double-click the launcher (see
README), which opens the app in their web browser. Developers launch it via
``python desktop_app.py`` (same thing — starts the server and opens the browser)
or, after ``pip install -e .``, ``python -m streamlit run app/main.py`` — no
PYTHONPATH needed in either case, because the project is an installable package
and the launcher puts the project root on ``sys.path``. See DEVELOPER.md.
"""

from __future__ import annotations

import streamlit as st

from app.config import APP_TITLE, APP_TAGLINE, get_settings
from app.ui import (
    setup_screen,
    dossier_screen,
    screenshot_screen,
    confirmation_screen,
    analysis_screen,
    proposal_screen,
)
from app.ui import api_usage_panel, theme


# (step key, sidebar label) in flow order.
STEPS: list[tuple[str, str]] = [
    ("setup", "Setup"),
    ("dossier", "Dossier"),
    ("screenshot", "Job Screenshot"),
    ("confirmation", "Confirm Details"),
    ("analysis", "Analysis"),
    ("proposal", "Proposal"),
]

# "Confirm Details" is a developer/admin-only screen. Normal users get a
# clean Setup → Dossier → Job Screenshot → Analysis → Proposal flow; the
# extracted job fields are confirmed automatically in the backend right
# after extraction, so these steps are hidden unless SHOW_DEBUG_PANEL=true.
DEBUG_ONLY_STEPS: frozenset[str] = frozenset({"confirmation"})


def _visible_steps(show_debug: bool) -> list[tuple[str, str]]:
    """Return the steps shown in the sidebar for the current mode."""
    if show_debug:
        return list(STEPS)
    return [(key, label) for key, label in STEPS if key not in DEBUG_ONLY_STEPS]

RENDERERS = {
    "setup": setup_screen.render,
    "dossier": dossier_screen.render,
    "screenshot": screenshot_screen.render,
    "confirmation": confirmation_screen.render,
    "analysis": analysis_screen.render,
    "proposal": proposal_screen.render,
}


SESSION_DEFAULTS: dict[str, object] = {
    # Setup gate
    "api_status": None,
    "api_ok": False,
    # Navigation
    "current_step": "setup",
    # Dossier flow
    "dossier_folder_path": "",
    "dossier_validation": None,
    "dossier_chunks": None,
    "dossier_read": False,
    "dossier_read_error": False,
    "evidence_index": None,
    "canonical_profile": None,
    "evidence_index_meta": None,
    # Screenshots
    "screenshots_uploaded": False,
    "uploaded_screenshots": [],
    "pasted_screenshots": [],
    "screenshot_upload_sig": (),
    "extracted_job_fields": None,
    # Confirmation
    "confirmed_job_fields": None,
    "fields_confirmed": False,
    "current_job_fingerprint": None,
    # Analysis output
    "scoring_result": None,
    "recommendation_result": None,
    "match_data": None,
    "generated_proposal": None,
    "verified_proposal": None,
    "proposal_context": None,
    "selected_evidence_for_proposal": None,
    # API usage tracking
    "api_usage_log": [],
}


def _init_session_state() -> None:
    for key, value in SESSION_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _is_unlocked(step_key: str) -> bool:
    if step_key == "setup":
        return True
    if step_key == "dossier":
        return bool(st.session_state.get("api_ok"))
    if step_key == "screenshot":
        return bool(st.session_state.get("evidence_index"))
    if step_key == "confirmation":
        return bool(st.session_state.get("extracted_job_fields"))
    if step_key == "analysis":
        return bool(st.session_state.get("fields_confirmed"))
    if step_key == "proposal":
        return bool(
            st.session_state.get("fields_confirmed")
            and st.session_state.get("recommendation_result")
        )
    return False


def _is_completed(step_key: str) -> bool:
    if step_key == "setup":
        return bool(st.session_state.get("api_ok"))
    if step_key == "dossier":
        return bool(st.session_state.get("evidence_index"))
    if step_key == "screenshot":
        return bool(st.session_state.get("extracted_job_fields"))
    if step_key == "confirmation":
        return bool(st.session_state.get("fields_confirmed"))
    if step_key == "analysis":
        return bool(st.session_state.get("recommendation_result"))
    if step_key == "proposal":
        return bool(st.session_state.get("verified_proposal"))
    return False


def _lock_reason(step_key: str) -> str:
    if step_key == "dossier":
        return "Locked — run the API check first."
    if step_key == "screenshot":
        return "Locked — read your dossier and build proof points first."
    if step_key == "confirmation":
        return "Locked — extract job details from a screenshot first."
    if step_key == "analysis":
        return "Locked — confirm the job details first."
    if step_key == "proposal":
        return "Locked — run the analysis first."
    return "Locked."


def _step_status(step_key: str, current: str) -> str:
    if step_key == current:
        return "current"
    if not _is_unlocked(step_key):
        return "locked"
    if _is_completed(step_key):
        return "completed"
    return "available"


# Quiet glyphs that read as a clean status, not technical noise.
_STATUS_GLYPH = {
    "completed": "✓",
    "current": "●",
    "available": "○",
    "locked": "🔒",
}


def _header_chip() -> tuple[str, str]:
    if st.session_state.get("verified_proposal"):
        return "Proposal Ready", "ready"
    if st.session_state.get("recommendation_result"):
        return "Analysis Ready", "info"
    if st.session_state.get("api_ok"):
        return "API Ready", "ready"
    return "API Missing", "missing"


def _render_sidebar(show_debug: bool) -> None:
    theme.sidebar_title("Steps")
    current = st.session_state.current_step

    for index, (key, label) in enumerate(_visible_steps(show_debug), start=1):
        status = _step_status(key, current)
        unlocked = status != "locked"
        glyph = _STATUS_GLYPH.get(status, "○")
        btn_label = f"{glyph}  {index}. {label}"
        clicked = st.button(
            btn_label,
            key=f"nav_btn_{key}",
            type="primary" if status == "current" else "secondary",
            disabled=not unlocked,
            use_container_width=True,
            help=None if unlocked else _lock_reason(key),
        )
        if clicked and unlocked and status != "current":
            st.session_state.current_step = key
            st.rerun()

    st.divider()
    st.caption("✓ done · ● current · ○ available · 🔒 locked")

    # Developer-only API usage panel. Hidden unless SHOW_DEBUG_PANEL=true.
    api_usage_panel.render(key_suffix="sidebar")


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="🧭",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    theme.inject_css()
    _init_session_state()

    settings = get_settings()
    show_debug = bool(getattr(settings, "show_debug_panel", False))

    # Confirm Details is debug-only — keep normal users out of it even if a
    # stale navigation target points there.
    if not show_debug and st.session_state.current_step == "confirmation":
        st.session_state.current_step = (
            "analysis" if st.session_state.get("fields_confirmed") else "screenshot"
        )

    if not _is_unlocked(st.session_state.current_step):
        st.session_state.current_step = "setup"

    chip_label, chip_kind = _header_chip()
    theme.render_app_header(
        APP_TITLE,
        APP_TAGLINE,
        chip_label=chip_label,
        chip_kind=chip_kind,
    )

    with st.sidebar:
        _render_sidebar(show_debug)

    RENDERERS[st.session_state.current_step]()


if __name__ == "__main__":
    main()
