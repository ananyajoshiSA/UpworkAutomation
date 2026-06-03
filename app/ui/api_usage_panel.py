"""Reusable "API Usage Status" panel.

Renders an expandable block that summarises how the configured LLM was
used during the current session. The panel reads
:data:`st.session_state.api_usage_log`, which is appended to by
:mod:`app.services.llm_client`.

The panel must NEVER show the API key, raw dossier text, screenshots,
or full generated proposals. It only shows metadata.
"""

from __future__ import annotations

from typing import Iterable, Optional

import streamlit as st

from app.config import LLM_TASK_NAMES, get_settings


TRACKED_TASKS: tuple[str, ...] = LLM_TASK_NAMES


_TASK_LABELS: dict[str, str] = {
    "api_check": "API capability check",
    "screenshot_extraction": "Screenshot extraction",
    "evidence_index_generation": "Evidence index generation",
    "opportunity_matching": "Opportunity matching",
    "missing_info_labeling": "Missing-info labeling",
    "recommendation_generation": "Recommendation reasoning",
    "proposal_generation": "Proposal generation",
    "verification_pass": "Proposal verification pass",
}


def _last_entry(log: Iterable[dict], *, task_name: Optional[str] = None) -> Optional[dict]:
    last: Optional[dict] = None
    for entry in log:
        if task_name and entry.get("task_name") != task_name:
            continue
        last = entry
    return last


def render(*, expanded: bool = False, key_suffix: str = "") -> None:
    """Render the API Usage Status panel.

    Pass a unique ``key_suffix`` if you call this from more than one
    screen so the Streamlit widget keys stay unique.

    Hidden entirely from the main UI when ``SHOW_DEBUG_PANEL`` is false
    (the production default). Internal API logging still runs — this
    only controls the on-screen surface.
    """
    settings = get_settings()
    if not getattr(settings, "show_debug_panel", False):
        return
    log: list[dict] = list(st.session_state.get("api_usage_log") or [])

    with st.expander("API Usage Status", expanded=expanded):
        # Environment / configuration line
        col1, col2, col3 = st.columns(3)
        col1.metric("Provider", settings.llm_provider or "—")
        col2.metric("Active model", settings.active_model or "—")
        col3.metric(
            "Local placeholders allowed",
            "yes" if settings.allow_local_placeholders else "no",
        )

        real_calls_enabled = settings.has_api_key
        st.caption(
            "Real API calls enabled: "
            + ("✅ yes" if real_calls_enabled else "❌ no (missing API key)")
            + " — API key is never displayed or logged."
        )

        total_api_calls = sum(1 for e in log if e.get("used_api"))
        total_local = sum(1 for e in log if not e.get("used_api"))
        m1, m2, m3 = st.columns(3)
        m1.metric("API calls this session", total_api_calls)
        m2.metric("Local placeholder calls", total_local)
        last_any = _last_entry(log)
        m3.metric(
            "Last API call task",
            (last_any or {}).get("task_name") or "—",
        )

        if last_any:
            st.caption(
                f"Last call: `{last_any.get('task_name')}` • "
                f"status `{last_any.get('status')}` • "
                f"used_api `{str(bool(last_any.get('used_api'))).lower()}` • "
                f"{last_any.get('timestamp')}"
            )

        st.markdown("**Per-stage status**")
        for task in TRACKED_TASKS:
            label = _TASK_LABELS.get(task, task)
            entries = [e for e in log if e.get("task_name") == task]
            api_hits = sum(1 for e in entries if e.get("used_api"))
            local_hits = sum(1 for e in entries if not e.get("used_api"))
            last = entries[-1] if entries else None

            if not entries:
                badge = "⚪ not run"
            elif api_hits and not local_hits:
                badge = "🟢 used API"
            elif api_hits and local_hits:
                badge = "🟡 mixed (API + local)"
            else:
                badge = "🔴 LOCAL PLACEHOLDER — API NOT USED"

            line = f"- **{label}** (`{task}`) — {badge}"
            if last:
                line += (
                    f"  \n  status `{last.get('status')}` • "
                    f"provider `{last.get('provider') or '—'}` • "
                    f"model `{last.get('model') or '—'}`"
                )
                if last.get("error_message"):
                    line += f"  \n  reason: {last['error_message']}"
            st.markdown(line)

        if log:
            with st.expander("Raw call log (metadata only)", expanded=False):
                # Streamlit's dataframe renders dict lists cleanly.
                st.dataframe(log, use_container_width=True, hide_index=True)
        else:
            st.caption(
                "No API calls or local placeholder calls recorded yet."
            )
