"""Screen 1: Setup — configure the AI service and run the API check.

This is the only onboarding step a user needs. They pick a provider, model,
and (vision) model and paste their API key directly into the **Configure AI
Service** form; clicking *Save Configuration* writes a local ``.env`` for
them (no manual file editing). They then click *Run API Check* to confirm the
service is reachable, which unlocks the rest of the flow.

Internal data-handling rules (kept in code/README, not surfaced as a
required UI step):

- Original dossier files stay on the user's machine.
- Only selected extracted text from the dossier and screenshot may be
  sent to the configured LLM API.
- Raw dossier text, screenshot bytes, generated proposals, and API
  keys are never logged.

Secret-safety: the API-key field is masked, the saved key is shown only as a
``sk-****abcd`` mask (never prefilled or echoed), and the short, actionable
error messages shown to every user never include API keys, organization IDs,
raw provider errors, stack traces, or internal task names. The exact API
status string and the configuration detail block only appear inside the
Developer Debug Panel when ``SHOW_DEBUG_PANEL=true``.
"""

from __future__ import annotations

import streamlit as st

from app import config
from app.config import (
    GROQ_VISION_UNSUPPORTED_MESSAGE,
    PROVIDER_OPTIONS,
    VISION_PROVIDER_OPTIONS,
    VISION_SAME_AS_LLM,
    build_env_updates,
    get_settings,
    reload_settings,
    update_env_file,
)
from app.models.provider_models import get_text_models, get_vision_models
from app.services.api_gate import ApiGateError, run_capability_test
from app.ui import theme


# Label for the "type the model name myself" choice appended to every model
# dropdown. Selecting it reveals a free-text box, so a model that isn't in the
# curated suggestion list (e.g. a brand-new release) stays usable — the list is
# guidance, not a hard allow-list (see app.models.provider_models).
_MODEL_CUSTOM_LABEL = "Custom — type a model name…"

# Leading choice in the Vision Model dropdown: leave the model blank so the app
# reuses the LLM model when that model can read images.
_VISION_REUSE_LABEL = "Reuse the LLM model (when it supports images)"


MINIMAL_DATA_NOTICE = (
    "Files are processed locally; selected extracted text may be sent "
    "to the configured API."
)


# Session-state keys cleared after Save / "Refresh API Config" so a changed
# configuration is re-checked cleanly without a server restart.
_API_STATE_KEYS: tuple[str, ...] = ("api_status", "api_ok")

# One-shot flag set right after a successful save; consumed on the next run to
# flash a confirmation and clear the typed key out of widget state.
_JUST_SAVED_FLAG = "_config_just_saved"

# One-shot flag set right after Clear Credentials; consumed on the next run to
# flash a confirmation that every saved key was wiped.
_CREDS_CLEARED_FLAG = "_creds_cleared"

# Setup-form widget keys. Kept distinct from the gate-state keys above so the
# form can be prefilled and cleared independently.
_FORM_PROVIDER = "cfg_llm_provider"
_FORM_MODEL = "cfg_llm_model"
_FORM_API_KEY = "cfg_api_key"
_FORM_VISION_PROVIDER = "cfg_vision_provider"
_FORM_VISION_MODEL = "cfg_vision_model"
_FORM_ALLOW_FALLBACK = "cfg_allow_provider_fallback"
_FORM_ALLOW_LOCAL = "cfg_allow_local_placeholders"
_FORM_SHOW_DEBUG = "cfg_show_debug_panel"
_FORM_MAX_CHARS = "cfg_max_proposal_context_chars"
_FORM_MAX_EVIDENCE = "cfg_max_proposal_evidence_points"
_FORM_MAX_TOKENS = "cfg_proposal_max_output_tokens"
_FORM_LOG_DAYS = "cfg_log_retention_days"


# Safe, actionable, user-facing messages keyed by gate status. These are
# shown to ALL users (debug or not): they contain no key, no org id, no raw
# provider text, no task name. The debug panel adds the raw status on top.
_SAFE_STATUS_MESSAGE: dict[str, str] = {
    ApiGateError.NO_API_ADDED: (
        "API key is missing. Enter it above and click Save Configuration."
    ),
    ApiGateError.MODEL_NOT_CONFIGURED: (
        "Model is missing. Enter a model above and click Save Configuration."
    ),
    ApiGateError.INVALID_API_KEY: "The API key or model could not be validated.",
    ApiGateError.API_QUOTA_EXCEEDED: (
        "The AI service is temporarily unavailable. Try again."
    ),
    ApiGateError.API_CONNECTION_FAILED: (
        "The AI service is temporarily unavailable. Try again."
    ),
    ApiGateError.VISION_MODEL_NOT_AVAILABLE: (
        "The configured model cannot read screenshots. Set a vision model "
        "above and click Save Configuration."
    ),
}

# Fallback for any unmapped status — still safe and generic.
_GENERIC_SAFE_MESSAGE = "The AI service is temporarily unavailable. Try again."


def _safe_message(status: str) -> str:
    """Return a clean, leak-free message for a gate status string."""
    return _SAFE_STATUS_MESSAGE.get(status, _GENERIC_SAFE_MESSAGE)


# ---------------------------------------------------------------------------
# Configure AI Service form
# ---------------------------------------------------------------------------


def _initial_vision_provider(settings) -> str:
    """Map saved VISION_PROVIDER to a dropdown choice.

    Anything not in the vision dropdown (blank, ``same_as_llm``, or an
    unsupported value such as a hand-edited ``groq``) falls back to
    ``same_as_llm`` so the form never shows an option that isn't offered.
    """
    raw = (getattr(settings, "vision_provider", None) or "").strip().lower()
    return raw if raw in ("openai", "anthropic", "gemini") else VISION_SAME_AS_LLM


def _seed_form_defaults(settings) -> None:
    """Prefill the form widgets from current settings — never the API key.

    Uses ``setdefault`` so a user's in-progress edits survive reruns. The API
    key field is always seeded blank: the saved key is shown only as a mask,
    never prefilled or revealed.
    """
    ss = st.session_state
    provider = settings.llm_provider if settings.llm_provider in PROVIDER_OPTIONS else "openai"
    ss.setdefault(_FORM_PROVIDER, provider)
    ss.setdefault(_FORM_API_KEY, "")
    ss.setdefault(_FORM_VISION_PROVIDER, _initial_vision_provider(settings))
    # The LLM/Vision model dropdowns seed their own state from the saved model
    # on first render (see _render_model_dropdown): their options depend on the
    # live provider selection, so they can't be seeded with a flat setdefault.
    ss.setdefault(_FORM_ALLOW_FALLBACK, bool(settings.allow_provider_fallback))
    ss.setdefault(_FORM_ALLOW_LOCAL, bool(settings.allow_local_placeholders))
    ss.setdefault(_FORM_SHOW_DEBUG, bool(settings.show_debug_panel))
    ss.setdefault(_FORM_MAX_CHARS, int(settings.max_proposal_context_chars))
    ss.setdefault(_FORM_MAX_EVIDENCE, int(settings.max_proposal_evidence_points))
    ss.setdefault(_FORM_MAX_TOKENS, int(settings.proposal_max_output_tokens))
    ss.setdefault(_FORM_LOG_DAYS, int(config.LOG_RETENTION_DAYS))


def _saved_key_status(settings) -> str:
    """Masked status of the saved key for the active provider (or "").

    Uses the central, provider-aware mask so Groq/Gemini keys are shown
    correctly and the raw key is never exposed.
    """
    return settings.active_api_key_masked


def _handle_config_save(
    *,
    provider: str,
    model: str,
    api_key: str,
    vision_provider: str,
    vision_model: str,
    allow_provider_fallback: object,
    allow_local_placeholders: object,
    show_debug_panel: object,
    max_proposal_context_chars: object,
    max_proposal_evidence_points: object,
    proposal_max_output_tokens: object,
    log_retention_days: object,
) -> None:
    """Write the form values to ``.env``, reload, and re-lock the API gate."""
    updates = build_env_updates(
        llm_provider=provider,
        llm_model=model,
        api_key=api_key,
        vision_provider=vision_provider,
        vision_model=vision_model,
        allow_provider_fallback=allow_provider_fallback,
        allow_local_placeholders=allow_local_placeholders,
        show_debug_panel=show_debug_panel,
        max_proposal_context_chars=max_proposal_context_chars,
        max_proposal_evidence_points=max_proposal_evidence_points,
        proposal_max_output_tokens=proposal_max_output_tokens,
        log_retention_days=log_retention_days,
    )
    # ``updates`` holds the raw key — written to disk only, never logged.
    update_env_file(updates)
    reload_settings()

    # Clear stale gate state so the next step re-locks until the new config
    # passes the API check.
    for key in _API_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state["api_status"] = None
    st.session_state["api_ok"] = False
    st.session_state[_JUST_SAVED_FLAG] = True
    st.rerun()


def _handle_clear_credentials() -> None:
    """Wipe every saved API key from ``.env`` and re-lock the API gate."""
    config.clear_api_keys()

    # Drop any typed key from widget state so nothing is re-rendered, then
    # re-lock the gate: with no key saved, the rest of the flow must stay
    # locked until a new key is saved and the check passes again.
    st.session_state.pop(_FORM_API_KEY, None)
    for key in _API_STATE_KEYS:
        st.session_state.pop(key, None)
    st.session_state["api_status"] = None
    st.session_state["api_ok"] = False
    st.session_state[_CREDS_CLEARED_FLAG] = True
    st.rerun()


def _render_model_dropdown(
    *,
    label: str,
    options: list[str],
    state_prefix: str,
    saved_model: str,
    help_text: str,
    reuse_label: str | None = None,
) -> str:
    """Render a provider-aware model dropdown and return the chosen model.

    ``options`` is the curated model list for the *currently selected* provider
    (from :mod:`app.models.provider_models`). A *Custom* choice is always
    appended; picking it reveals a free-text box so a model that isn't in the
    list is still usable. ``reuse_label`` — passed for the Vision Model only —
    adds a leading "leave blank" choice that returns ``""`` so the app reuses
    the LLM model.

    State is namespaced under ``state_prefix`` (a selectbox key, a custom-text
    key, and a one-shot init flag). The saved model survives reruns, and a
    provider switch can never leave the widget pointing at an option the new
    provider lacks — the stored choice is reconciled against the live option
    list *before* the widget is built, so Streamlit never sees a stale value.
    """
    ss = st.session_state
    sel_key = f"{state_prefix}__select"
    custom_key = f"{state_prefix}__custom"
    init_key = f"{state_prefix}__init"

    choices: list[str] = []
    if reuse_label is not None:
        choices.append(reuse_label)
    choices.extend(options)
    choices.append(_MODEL_CUSTOM_LABEL)

    # One-time seed from the saved model: match it to a curated option, fall
    # back to the (prefilled) Custom box for a hand-entered model, else pick a
    # sensible first choice.
    if init_key not in ss:
        ss[init_key] = True
        saved = (saved_model or "").strip()
        if saved and saved in options:
            ss[sel_key] = saved
        elif saved:
            ss[sel_key] = _MODEL_CUSTOM_LABEL
            ss.setdefault(custom_key, saved)
        else:
            ss[sel_key] = choices[0]

    # A provider switch can leave the stored choice pointing at a model the new
    # provider doesn't offer — reset to the first choice (the new provider's
    # default, or the reuse choice for vision) before the widget is built.
    if ss.get(sel_key) not in choices:
        ss[sel_key] = choices[0]

    choice = st.selectbox(label, choices, key=sel_key, help=help_text)

    if choice == _MODEL_CUSTOM_LABEL:
        return st.text_input(
            f"{label} — custom name",
            key=custom_key,
            placeholder="e.g. gpt-4.1",
            help="Enter the exact model id from your provider account.",
        ).strip()
    if reuse_label is not None and choice == reuse_label:
        return ""
    return choice


def _render_config_form(settings) -> None:
    """The 'Configure AI Service' card: provider/model/key inputs + Save."""
    # One-time post-save housekeeping: clear the typed key from widget state
    # (so it is never re-rendered) and remember to flash a confirmation.
    just_saved = bool(st.session_state.pop(_JUST_SAVED_FLAG, False))
    if just_saved:
        st.session_state.pop(_FORM_API_KEY, None)
    # One-shot confirmation after Clear Credentials wiped every saved key.
    creds_cleared = bool(st.session_state.pop(_CREDS_CLEARED_FLAG, False))

    _seed_form_defaults(settings)

    with st.container(border=True):
        theme.section_label("Configure AI Service")
        st.write(
            "Enter your AI service details and click Save Configuration. "
            "The app stores them in a local .env file for you — no manual "
            "editing required."
        )
        if just_saved:
            st.success("Configuration saved. Run API Check to continue.")
        if creds_cleared:
            st.success("Saved credentials cleared. Enter a new API key to continue.")
        masked = _saved_key_status(settings)
        if masked:
            st.caption(f"Saved key for the current provider: {masked}")

        # --- Text / LLM settings ---------------------------------------
        provider = st.selectbox(
            "LLM Provider",
            PROVIDER_OPTIONS,
            key=_FORM_PROVIDER,
            help="Which AI service to use for text reasoning.",
        )
        model = _render_model_dropdown(
            label="LLM Model",
            options=get_text_models(provider),
            state_prefix=_FORM_MODEL,
            saved_model=settings.active_model or "",
            help_text=(
                "Models available for the selected provider. Choose "
                f"“{_MODEL_CUSTOM_LABEL}” to enter one yourself."
            ),
        )
        api_key = st.text_input(
            "API Key",
            key=_FORM_API_KEY,
            type="password",
            help="Saved to your local .env. Leave blank to keep the saved key.",
        )

        # --- Vision settings -------------------------------------------
        vision_provider = st.selectbox(
            "Vision Provider",
            VISION_PROVIDER_OPTIONS,
            key=_FORM_VISION_PROVIDER,
            help="Reads job screenshots. 'same_as_llm' reuses the provider above.",
        )
        # 'same_as_llm' has no models of its own — show the LLM provider's
        # vision models so the dropdown reflects what will actually be used.
        resolved_vision_provider = (
            provider if vision_provider == VISION_SAME_AS_LLM else vision_provider
        )
        vision_model = _render_model_dropdown(
            label="Vision Model",
            options=get_vision_models(resolved_vision_provider),
            state_prefix=_FORM_VISION_MODEL,
            saved_model=getattr(settings, "vision_model", None) or "",
            help_text=(
                "Reads job screenshots. Keep the reuse choice to fall back to "
                "the LLM model when it supports images."
            ),
            reuse_label=_VISION_REUSE_LABEL,
        )

        # Groq is text-only: 'same_as_llm' vision with a Groq text provider
        # can't read screenshots. Steer the user to a separate vision provider.
        if provider == "groq" and vision_provider == VISION_SAME_AS_LLM:
            st.warning(GROQ_VISION_UNSUPPORTED_MESSAGE)

        # --- Advanced options (collapsed) ------------------------------
        # Booleans use toggles (never checkboxes) so the Setup screen stays
        # free of any acceptance-style checkbox.
        with st.expander("Advanced options", expanded=False):
            allow_fallback = st.toggle(
                "Allow provider fallback", key=_FORM_ALLOW_FALLBACK
            )
            allow_local = st.toggle(
                "Allow local placeholders", key=_FORM_ALLOW_LOCAL
            )
            # The developer debug panel exposes provider/model/task names,
            # prompt sizes, evidence IDs, and sanitized errors. Enabling it
            # is a developer/operator action, not an end-user one, so the
            # in-UI toggle is only offered when debug is ALREADY enabled via
            # the environment (allowing a developer to turn it back off). A
            # normal user running with debug off cannot flip it on from the
            # UI — they must set SHOW_DEBUG_PANEL=true in .env deliberately.
            if settings.show_debug_panel:
                show_debug = st.toggle(
                    "Show developer debug panel", key=_FORM_SHOW_DEBUG
                )
            else:
                show_debug = False
                st.caption(
                    "Developer debug panel is off. To enable it, set "
                    "`SHOW_DEBUG_PANEL=true` in your `.env`."
                )
            max_chars = st.number_input(
                "Max proposal context characters",
                min_value=1000,
                step=500,
                key=_FORM_MAX_CHARS,
            )
            max_evidence = st.number_input(
                "Max proposal evidence points",
                min_value=1,
                step=1,
                key=_FORM_MAX_EVIDENCE,
            )
            max_tokens = st.number_input(
                "Proposal max output tokens",
                min_value=50,
                step=50,
                key=_FORM_MAX_TOKENS,
            )
            log_days = st.number_input(
                "Log retention days",
                min_value=1,
                step=1,
                key=_FORM_LOG_DAYS,
            )

        col_save, col_clear = st.columns([1, 1])
        with col_save:
            save_clicked = st.button(
                "Save Configuration", type="primary", key="save_config_btn"
            )
        with col_clear:
            # Security control: wipe every stored API key so no credential is
            # left on disk. Disabled when there's nothing saved to clear.
            clear_clicked = st.button(
                "Clear Credentials",
                key="clear_credentials_btn",
                disabled=not settings.has_any_api_key,
                help=(
                    "Remove all saved API keys from your local .env. "
                    "You'll need to re-enter a key before running the app."
                ),
            )

        if save_clicked:
            _handle_config_save(
                provider=provider,
                model=model,
                api_key=api_key,
                vision_provider=vision_provider,
                vision_model=vision_model,
                allow_provider_fallback=allow_fallback,
                allow_local_placeholders=allow_local,
                show_debug_panel=show_debug,
                max_proposal_context_chars=max_chars,
                max_proposal_evidence_points=max_evidence,
                proposal_max_output_tokens=max_tokens,
                log_retention_days=log_days,
            )
        if clear_clicked:
            _handle_clear_credentials()


# ---------------------------------------------------------------------------
# API check card
# ---------------------------------------------------------------------------


def _render_status(*, debug: bool) -> None:
    status = st.session_state.get("api_status")
    if status is None:
        st.info("Run the check below to confirm your AI service is ready.")
        return
    if status == ApiGateError.API_OK:
        st.success("AI service is ready.")
        return
    st.error(_safe_message(status))
    if debug:
        st.caption(f"Status: {status}")


def _render_refresh_button() -> None:
    """Reload .env and clear stale API state, then rerun.

    Mostly redundant now that Save Configuration reloads automatically, but
    kept for operators who prefer to edit ``.env`` by hand (advanced path).
    """
    if st.button("Refresh API Config", key="refresh_api_config_btn"):
        # Re-read .env from disk and rebuild the cached Settings so edited
        # provider/model/key values take effect without a server restart.
        reload_settings()
        for key in _API_STATE_KEYS:
            st.session_state.pop(key, None)
        # Reset the gate flags to their unconfigured defaults so the rest of
        # the flow re-locks until the check passes against the new config.
        st.session_state["api_status"] = None
        st.session_state["api_ok"] = False
        st.rerun()


def _render_api_check_card(settings, *, debug: bool) -> None:
    with st.container(border=True):
        theme.section_label("AI service")
        col_status, col_action = st.columns([2, 1])
        with col_status:
            _render_status(debug=debug)
        with col_action:
            rerun_label = (
                "Re-run API Check"
                if st.session_state.get("api_ok")
                else "Run API Check"
            )
            test_clicked = st.button(
                rerun_label, type="primary", key="run_api_check_btn"
            )
            _render_refresh_button()

        if test_clicked:
            # Re-read .env from disk so the just-saved (or hand-edited) config
            # is used — get_settings is process-cached and load_dotenv only
            # runs once at import.
            settings = reload_settings()
            debug = bool(getattr(settings, "show_debug_panel", False))
            with st.spinner("Checking your AI service…"):
                # Live text-provider reachability only. The vision model is
                # NOT exercised during setup.
                result = run_capability_test(settings=settings, live=True)
            if result.ok:
                st.session_state.api_status = ApiGateError.API_OK
                st.session_state.api_ok = True
                st.success("AI service is ready.")
            else:
                st.session_state.api_status = result.status
                st.session_state.api_ok = False
                st.error(_safe_message(result.status))
                if debug:
                    st.caption(f"Status: {result.status}")


def _render_debug_config_block(settings) -> None:
    """Developer-only configuration details (gated behind the debug panel).

    Shows where config came from and what is missing — never any secret
    value. The API key is never read into this block.
    """
    st.markdown(f"**Detected project root:** `{config.PROJECT_ROOT}`")
    st.markdown(
        f"**`.env` loaded from project root:** "
        f"`{str(config.env_file_loaded()).lower()}`"
    )
    st.markdown(f"**`LLM_PROVIDER`:** `{settings.llm_provider}`")
    st.markdown(f"**Text model:** `{settings.active_model}`")
    st.markdown(f"**Vision provider:** `{settings.active_vision_provider}`")
    st.markdown(
        f"**Vision model:** `{settings.active_vision_model or 'not set'}`"
    )
    missing = settings.missing_config()
    if missing:
        st.markdown(f"**Missing required values:** `{', '.join(missing)}`")
    else:
        st.markdown("**Missing required values:** `none`")
    st.markdown(
        f"**`ALLOW_PROVIDER_FALLBACK`:** "
        f"`{str(settings.allow_provider_fallback).lower()}`"
    )
    st.markdown(
        f"**`ALLOW_LOCAL_PLACEHOLDERS`:** "
        f"`{str(settings.allow_local_placeholders).lower()}`"
    )
    st.caption(
        "Values are loaded from .env. The API key itself is held in "
        "memory only and is never shown, logged, or echoed back."
    )


def render() -> None:
    settings = get_settings()
    debug = bool(getattr(settings, "show_debug_panel", False))

    st.subheader("Setup")
    st.write(
        "Connect your AI service to get started. This stays on your machine — "
        "you only need to do it once per session."
    )

    _render_config_form(settings)
    _render_api_check_card(settings, debug=debug)

    if st.session_state.get("api_ok"):
        with st.container(border=True):
            theme.section_label("Next step")
            st.write("Your AI service is connected. You're ready to add your dossier.")
            if st.button(
                "Continue to Dossier",
                type="primary",
                key="continue_to_dossier_btn",
            ):
                st.session_state.current_step = "dossier"
                st.rerun()
    else:
        st.caption("Run the API check above to unlock the next step.")

    st.caption(MINIMAL_DATA_NOTICE)

    if debug:
        with st.expander("Developer Debug Panel", expanded=False):
            _render_debug_config_block(settings)
