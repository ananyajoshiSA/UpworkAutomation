"""Screen 6: Proposal.

Generates a grounded proposal from the analysis results and presents it
in a copy-friendly way. Only a small, relevance-filtered subset of the
dossier is ever sent to the AI — the full dossier is never sent, and
evidence IDs / raw API details never appear in the client-facing
proposal. Technical details are gated behind ``SHOW_DEBUG_PANEL=true``.
"""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from app.config import get_settings
from app.services.proposal_generator import (
    ProposalGenerationError,
    build_proposal_context,
    generate as generate_proposal,
)
from app.ui import output_screen, theme


def _copy_button(text: str) -> None:
    """A real one-click copy button (works in deployed Streamlit)."""
    safe = json.dumps(text).replace("</", "<\\/")
    html = f"""
    <button id="ups-copy"
        style="border:none;border-radius:10px;background:#2563EB;color:#fff;
               font-weight:600;font-size:0.9rem;padding:0.55rem 1.1rem;
               cursor:pointer;font-family:inherit;">
        Copy Proposal
    </button>
    <span id="ups-copy-msg" style="margin-left:10px;color:#137333;
        font-size:0.85rem;font-family:inherit;"></span>
    <script>
    const btn = document.getElementById('ups-copy');
    const msg = document.getElementById('ups-copy-msg');
    btn.addEventListener('click', async () => {{
        try {{
            await navigator.clipboard.writeText({safe});
            msg.textContent = 'Copied to clipboard';
        }} catch (e) {{
            msg.textContent = 'Press Ctrl/Cmd + C to copy';
        }}
    }});
    </script>
    """
    components.html(html, height=48)


def _render_proposal(proposal_result: dict, show_debug: bool, *, settings) -> None:
    prop_meta = proposal_result.get("__meta__") or {}
    verify_meta = proposal_result.get("verification_meta") or {}
    output_screen.render_clean_stage_banner(
        output_screen._stage_user_state(prop_meta),
        output_screen._stage_user_state(verify_meta),
    )

    proposal_text = proposal_result.get("proposal", "")

    # Grounding gate: when the verification pass FAILED, the draft was not
    # confirmed against the dossier evidence, so we must not present it as a
    # ready-to-send proposal. Show a clear failure notice instead of the
    # ungated copy box — the user can re-run once the AI service recovers.
    verification_failed = (
        proposal_result.get("verification_status") == "failed"
    )
    if verification_failed:
        with st.container(border=True):
            theme.section_label("Proposal not verified")
            st.error(
                "The grounding check could not confirm this draft against "
                "your dossier evidence, so it is not shown. This usually "
                "means the AI service failed during verification. Please try "
                "generating again."
            )
        if show_debug:
            _render_debug_panel(proposal_result, settings=settings)
        return

    with st.container(border=True):
        theme.section_label("Your proposal")
        st.text_area(
            "Proposal",
            value=proposal_text,
            height=320,
            key="proposal_text_area",
            label_visibility="collapsed",
            help="Safe to copy-paste into Upwork.",
        )
        col_copy, col_dl = st.columns([1, 1])
        with col_copy:
            _copy_button(proposal_text)
        with col_dl:
            st.download_button(
                "Download as .txt",
                data=proposal_text,
                file_name="upwork_proposal.txt",
                mime="text/plain",
                key="download_proposal_btn",
            )

    missing_info_list = proposal_result.get("flagged_as_missing") or []
    if missing_info_list:
        with st.container(border=True):
            theme.section_label("Add to your dossier")
            st.caption("Including these would let future proposals say more:")
            for hint in missing_info_list:
                st.write(f"- {hint}")

    if show_debug:
        _render_debug_panel(proposal_result, settings=settings)


def _render_debug_panel(proposal_result: dict, *, settings) -> None:
    prop_meta = proposal_result.get("__meta__") or {}
    verify_meta = proposal_result.get("verification_meta") or {}
    with st.expander("Developer Debug Panel", expanded=False):
        if prop_meta.get("used_api"):
            st.success(
                f"`proposal_generation` used LLM API "
                f"(`{prop_meta.get('provider')}` / `{prop_meta.get('model')}`)."
            )
        else:
            st.warning("LOCAL PLACEHOLDER — API NOT USED for `proposal_generation`.")
        st.caption(
            f"Evidence points sent: `{prop_meta.get('evidence_points_sent', 0)}` • "
            f"Approx context chars: `{prop_meta.get('compact_context_chars', 0)}` • "
            f"Retry used: `{'yes' if prop_meta.get('retry_used') else 'no'}`"
        )
        if verify_meta.get("used_api"):
            st.success(
                f"Verification used LLM API "
                f"(`{verify_meta.get('provider')}` / `{verify_meta.get('model')}`)."
            )
        claims = proposal_result.get("factual_claims") or []
        st.caption(f"Citation-backed claims surviving verification: {len(claims)}")


def render() -> None:
    if not st.session_state.get("fields_confirmed"):
        debug = bool(getattr(get_settings(), "show_debug_panel", False))
        if debug:
            st.error("This step is locked. Confirm the job details first.")
            if st.button(
                "Back to Confirm Details", key="back_to_confirm_from_proposal"
            ):
                st.session_state.current_step = "confirmation"
                st.rerun()
        else:
            st.error(
                "This step is locked. Extract job details from a screenshot first."
            )
            if st.button(
                "Back to Job Screenshot", key="back_to_screenshot_from_proposal"
            ):
                st.session_state.current_step = "screenshot"
                st.rerun()
        return

    recommendation = st.session_state.get("recommendation_result")
    if not recommendation:
        st.info("Run the analysis first so the proposal can build on it.")
        if st.button("Go to Analysis", key="go_to_analysis_from_proposal"):
            st.session_state.current_step = "analysis"
            st.rerun()
        return

    settings = get_settings()
    show_debug = bool(getattr(settings, "show_debug_panel", False))

    confirmed_job = st.session_state.get("confirmed_job_fields") or {}
    evidence_index = st.session_state.get("evidence_index") or []
    match_data = st.session_state.get("match_data")

    st.subheader("Proposal")
    st.write("Generate a grounded proposal built from your real evidence.")

    do_not_proceed = recommendation.get("verdict") == "Do Not Proceed"

    with st.container(border=True):
        theme.section_label("Generate")

        override_ok = True
        if do_not_proceed:
            st.warning(
                "This opportunity is not strongly recommended. Generate anyway?"
            )
            override_ok = st.checkbox(
                "Yes, generate the proposal anyway", key="proposal_override_checkbox"
            )

        gate_blocked = do_not_proceed and not override_ok
        generate_clicked = st.button(
            "Generate Proposal",
            type="primary",
            disabled=gate_blocked,
            key="generate_proposal_btn",
            help=(
                "Drafts a grounded proposal using only a small, relevant subset "
                "of your evidence."
            ),
        )

        if show_debug:
            preview = build_proposal_context(
                confirmed_job,
                evidence_index,
                match_result=match_data,
                recommendation_result=recommendation,
                max_evidence_points=settings.max_proposal_evidence_points,
                max_context_chars=settings.max_proposal_context_chars,
            )
            pmeta = preview.get("__meta__") or {}
            st.caption(
                f"Debug — evidence points selected: "
                f"{pmeta.get('evidence_points_selected', 0)} • "
                f"approx context chars: {pmeta.get('approx_context_chars', 0)}"
            )

        if generate_clicked and not gate_blocked:
            # Proposal length is always auto (the backend picks the band from
            # the job). The user-facing length dropdown was removed.
            try:
                with st.spinner("Writing your proposal…"):
                    result = generate_proposal(
                        confirmed_job,
                        evidence_index,
                        recommendation,
                        match_data=match_data,
                        complexity=None,
                    )
            except ProposalGenerationError as exc:
                st.session_state.generated_proposal = None
                st.session_state.verified_proposal = None
                st.error(output_screen.USER_FACING_ERROR)
                if show_debug and exc.sanitized_error:
                    st.caption(f"Provider reason: {exc.sanitized_error}")
                result = None

            if result is not None:
                st.session_state.generated_proposal = {
                    "proposal": result.get("draft_proposal")
                    or result.get("proposal", ""),
                    "word_count": result.get("word_count", 0),
                }
                st.session_state.verified_proposal = result

    proposal_result = st.session_state.get("verified_proposal")
    if proposal_result:
        _render_proposal(proposal_result, show_debug, settings=settings)
