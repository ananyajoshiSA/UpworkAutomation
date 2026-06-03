"""Shared output helpers for the Analysis and Proposal screens.

This module holds the small, reusable pieces both screens rely on:

* clean, user-facing status copy that never leaks provider/model names,
  task names, prompt sizes, or raw error text;
* the mapping from an internal stage-meta dict to a clean user state;
* the developer-only stage-detail renderer (used only when
  ``SHOW_DEBUG_PANEL=true``).

Keeping these here means the visible screens stay focused on layout while
the "what may the user see" rules live in one place.
"""

from __future__ import annotations

import streamlit as st

from app.ui import theme


SUB_SCORE_LABELS = {
    "profile_fit": "Profile Fit",
    "portfolio_proof": "Portfolio Proof",
    "client_quality": "Client Quality",
    "competition": "Competition",
    "budget_value": "Budget / Value",
}


# Beginner-checklist result → status-chip kind.
_BEGINNER_RESULT_CHIP = {
    "Apply Confidently": "ready",
    "Proceed With Caution": "neutral",
    "Do Not Proceed": "missing",
}


# Clean user-facing strings — surfaced regardless of provider/status.
USER_FACING_ERROR = (
    "Analysis could not be completed because the AI service failed. "
    "Please try again."
)
USER_FACING_BASIC_MODE = "Analysis completed in basic mode."


def confidence_badge(label: str) -> str:
    color = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(label, "⚪")
    return f"{color} {label}"


# Backwards-compatible private alias (kept for any existing imports).
_confidence_badge = confidence_badge


def _stage_user_state(meta: dict) -> str:
    """Map an internal stage meta dict to a user-facing state.

    Returns one of: ``"ok"`` (API ran cleanly), ``"basic"`` (deterministic
    fallback used), ``"error"`` (API failed and no fallback allowed).
    """
    meta = meta or {}
    if meta.get("used_api"):
        return "ok"
    if (meta.get("status") or "") == "local_placeholder":
        return "basic"
    return "error"


def _render_stage_banner_clean(state: str) -> None:
    """Render a single clean user-facing banner per stage.

    Never names the provider, model, task name, or raw error reason on
    the main UI. Technical details are only available behind the debug
    panel.
    """
    if state == "ok":
        return
    if state == "basic":
        st.info(USER_FACING_BASIC_MODE)
        return
    st.error(USER_FACING_ERROR)


def render_clean_stage_banner(*states: str) -> None:
    """Render one consolidated clean banner covering several stages."""
    if "error" in states:
        st.error(USER_FACING_ERROR)
    elif "basic" in states:
        st.info(USER_FACING_BASIC_MODE)


def _render_debug_stage_details(*, match_meta: dict, rec_meta: dict) -> None:
    """Render technical per-stage details (debug panel only)."""

    def _row(meta: dict, label: str, task_name: str) -> None:
        used_api = bool(meta.get("used_api"))
        status = meta.get("status") or "—"
        provider = meta.get("provider") or "—"
        model = meta.get("model") or "—"
        error_message = meta.get("error_message") or ""
        if used_api:
            st.success(f"`{task_name}` used LLM API (`{provider}` / `{model}`).")
        elif status == "local_placeholder":
            st.warning(
                f"LOCAL FALLBACK — API NOT USED for `{task_name}`. "
                + (error_message or f"{label} reverted to deterministic logic.")
            )
        else:
            st.error(
                f"LLM call for `{task_name}` failed (`{status}`). "
                + (error_message or f"{label} could not run via the API.")
            )

    _row(match_meta or {}, "Opportunity matching", "opportunity_matching")
    _row(rec_meta or {}, "Recommendation reasoning", "recommendation_generation")


def _render_debug_score_components(score_result) -> None:
    """Render per-component score provenance (debug panel only).

    Shows the evidence IDs, per-component confidence, and ``source`` that
    are intentionally hidden from the normal UI. ``score_result`` may be a
    plain object without ``components`` (older callers / tests) — those
    are skipped gracefully.
    """
    components = getattr(score_result, "components", {}) or {}
    if not components:
        return
    st.markdown("**Score components:**")
    fp = getattr(score_result, "job_fingerprint", "")
    if fp:
        st.caption(f"scored for job_fingerprint `{fp}`")
    for key, comp in components.items():
        label = SUB_SCORE_LABELS.get(key, key)
        value = getattr(comp, "value", 0)
        max_value = getattr(comp, "max_value", 0)
        confidence = getattr(comp, "confidence", "—")
        source = getattr(comp, "source", "—")
        ids = getattr(comp, "evidence_ids_used", []) or []
        st.write(
            f"- **{label}** {value}/{max_value} · confidence `{confidence}` · "
            f"source `{source}`"
        )
        if ids:
            st.caption("evidence: " + ", ".join(f"`{i}`" for i in ids))


# ---------------------------------------------------------------------------
# Beginner Job Check
# ---------------------------------------------------------------------------


def render_beginner_check_card(beginner_eval: dict, *, show_debug: bool = False) -> None:
    """Render the clean, user-facing Beginner Job Check card.

    Shows only the result chip and at most two short reasons. Raw extracted
    field internals and rule-engine details are never shown here — they
    appear only inside the debug breakdown (``show_debug=True``).
    """
    if not beginner_eval:
        return
    result = beginner_eval.get("result") or "—"
    reasons = (beginner_eval.get("reasons") or [])[:2]
    with st.container(border=True):
        theme.section_label("Beginner Job Check")
        st.markdown(
            theme.status_chip(result, _BEGINNER_RESULT_CHIP.get(result, "neutral")),
            unsafe_allow_html=True,
        )
        for reason in reasons:
            st.write(f"- {reason}")
        if show_debug:
            _render_debug_beginner_details(beginner_eval)


def _render_debug_beginner_details(beginner_eval: dict) -> None:
    """Render the full beginner rule breakdown (debug panel only)."""
    be = beginner_eval or {}
    fields = be.get("fields", {}) or {}
    st.markdown("**Beginner evaluator breakdown:**")

    pay = fields.get("payment_verification", {})
    st.write(
        f"- Payment verification: `{pay.get('result', '—')}` "
        f"(value: {pay.get('value', '—')})"
    )
    pc = fields.get("proposal_count", {})
    st.write(
        f"- Proposal count bucket: `{pc.get('bucket', '—')}` "
        f"(count: {pc.get('count', '—')})"
    )
    pa = fields.get("posted_age", {})
    st.write(
        f"- Posted age bucket: `{pa.get('bucket', '—')}` "
        f"(age_days: {pa.get('age_days', '—')})"
    )
    cr = fields.get("client_rating", {})
    st.write(
        f"- Client rating warning: `{'yes' if cr.get('warning') else 'no'}` "
        f"(rating: {cr.get('rating', '—')})"
    )
    ex = fields.get("experience_level", {})
    st.write(
        f"- Experience level warning: `{'yes' if ex.get('warning') else 'no'}` "
        f"(level: {ex.get('level', '—')})"
    )
    st.write(f"- Triggered rule: `{be.get('triggered_rule', '—')}`")
    if be.get("missing_fields"):
        st.caption("Not visible: " + ", ".join(be["missing_fields"]))
