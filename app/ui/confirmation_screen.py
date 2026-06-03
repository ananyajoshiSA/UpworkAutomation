"""Screen 4: Confirm details (developer/admin only).

This screen is gated behind ``SHOW_DEBUG_PANEL=true``. Normal users never
see it — their job details are confirmed automatically in the backend
right after extraction (see :mod:`app.ui.screenshot_screen`). In debug
mode it lets a developer review/edit the extracted job fields before the
analysis runs. Every value is editable; unknown values stay as
"Not visible" so the tool never scores against a guess. Fields are
grouped into four simple sections to make review quick.
"""

from __future__ import annotations

import streamlit as st

from app.config import get_settings
from app.services.screenshot_parser import SCREENSHOT_FIELDS
from app.ui import theme


NOT_VISIBLE = "Not visible"

FIELD_LABELS: dict[str, str] = {
    "job_title": "Job title",
    "job_description": "Job description",
    "client_need": "Client need",
    "required_deliverables": "Required deliverables",
    "required_skills": "Required skills",
    "budget_or_rate": "Budget or rate",
    "project_type": "Project type",
    "experience_level": "Experience level",
    "project_duration": "Project duration",
    "posted_date": "Posted",
    "proposal_count": "Proposals submitted",
    "payment_verification": "Payment verified",
    "client_rating": "Client rating",
    "client_total_spend": "Client total spend",
    "hire_rate": "Hire rate",
    "client_location": "Client location",
    "connects_required": "Connects required",
}

# Grouped, user-friendly sections.
FIELD_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Job basics",
        [
            "job_title",
            "job_description",
            "client_need",
            "project_type",
            "experience_level",
            "project_duration",
        ],
    ),
    (
        "Client details",
        [
            "payment_verification",
            "client_rating",
            "client_total_spend",
            "hire_rate",
            "client_location",
        ],
    ),
    (
        "Budget & competition",
        ["budget_or_rate", "proposal_count", "posted_date", "connects_required"],
    ),
    (
        "Skills & requirements",
        ["required_skills", "required_deliverables"],
    ),
]

# Fields that benefit from a multi-line box.
LONG_FIELDS = {
    "job_description",
    "client_need",
    "required_deliverables",
    "required_skills",
}


def _empty_field() -> dict[str, str]:
    return {"value": NOT_VISIBLE, "confidence": "low", "source": "not visible"}


def _resolve_source(original_value: str, original_source: str, new_value: str) -> str:
    if new_value == NOT_VISIBLE:
        return "not visible"
    if original_value == NOT_VISIBLE:
        return "manually entered"
    if new_value != original_value:
        return "user corrected"
    return original_source


def _render_field(key: str, extracted: dict, debug: bool) -> None:
    label = FIELD_LABELS.get(key, key)
    field = extracted.get(key) or _empty_field()
    value = field.get("value", NOT_VISIBLE)
    widget_key = f"confirm_field__{key}"
    if key in LONG_FIELDS:
        st.text_area(label, value=value, key=widget_key, height=90)
    else:
        st.text_input(label, value=value, key=widget_key)
    if debug:
        st.caption(
            f"confidence `{field.get('confidence', 'low')}` • "
            f"source `{field.get('source', 'not visible')}`"
        )


def _commit(extracted: dict) -> None:
    confirmed: dict[str, dict[str, str]] = {}
    for key in SCREENSHOT_FIELDS:
        original = extracted.get(key) or _empty_field()
        raw_value = st.session_state.get(f"confirm_field__{key}", "")
        new_value = (raw_value or "").strip()
        if not new_value or new_value.lower() == NOT_VISIBLE.lower():
            new_value = NOT_VISIBLE
        confirmed[key] = {
            "value": new_value,
            "confidence": original.get("confidence", "low"),
            "source": _resolve_source(
                original_value=original.get("value", NOT_VISIBLE),
                original_source=original.get("source", "not visible"),
                new_value=new_value,
            ),
        }

    st.session_state.confirmed_job_fields = confirmed
    st.session_state.fields_confirmed = True
    # Force the analysis to recompute from the confirmed values. The
    # confirmed fields themselves were just set above, so we clear only
    # the derived per-opportunity outputs (and the fingerprint, so the
    # analysis screen treats this as a new opportunity).
    for k in (
        "current_job_fingerprint",
        "match_data",
        "scoring_result",
        "recommendation_result",
        "generated_proposal",
        "verified_proposal",
        "proposal_context",
        "selected_evidence_for_proposal",
    ):
        st.session_state[k] = None
    st.session_state.current_step = "analysis"


def render() -> None:
    settings = get_settings()
    debug = bool(getattr(settings, "show_debug_panel", False))

    if not debug:
        # Confirm Details is a developer/admin-only screen. Normal users never
        # reach it — job details are confirmed automatically in the backend
        # after extraction. If reached directly, bounce to the right step.
        st.session_state.current_step = (
            "analysis" if st.session_state.get("fields_confirmed") else "screenshot"
        )
        return

    extracted = st.session_state.get("extracted_job_fields")
    if not extracted:
        st.error(
            "This step is locked. Upload a screenshot and extract job details first."
        )
        if st.button("Back to Job Screenshot", key="back_to_screenshot_from_confirm"):
            st.session_state.current_step = "screenshot"
            st.rerun()
        return

    st.subheader("Confirm Details")
    st.caption("Developer/admin mode — normal users skip this step.")
    st.write(
        "Review the extracted job details and correct anything that's off. "
        "Leave unknown values as “Not visible” — analysis runs once you confirm."
    )

    for title, keys in FIELD_GROUPS:
        with st.container(border=True):
            theme.section_label(title)
            for key in keys:
                _render_field(key, extracted, debug)

    st.write("")
    if st.button("Confirm Details & Analyze", type="primary", key="confirm_fields_btn"):
        _commit(extracted)
        st.rerun()
