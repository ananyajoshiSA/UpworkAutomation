"""Tests confirming the Privacy and data flow disclosure section has been
removed from the main user flow.

Specifically:

- The setup screen no longer renders the disclosure block or checkbox.
- The dossier screen unlocks after API OK alone — no privacy gate.
- When ``SHOW_DEBUG_PANEL=false`` the privacy disclosure text, provider/
  model labels, and task names do NOT appear on the main UI.
- When ``SHOW_DEBUG_PANEL=true`` the debug surfaces (provider/model,
  task names, etc.) reappear.

These tests stub ``streamlit`` so render code can run headless.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app import config, main as app_main
from app.ui import dossier_screen, setup_screen, theme


class _ContextNoop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _SessionState(dict):
    """Dict that also supports attribute access, like Streamlit's session_state."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __setattr__(self, name, value):
        self[name] = value


class _Recorder:
    """Streamlit stand-in that records every call and returns falsy widgets.

    Buttons and checkboxes always return ``False`` so render paths take the
    "not clicked" branch — enough to inspect what would have been drawn.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.texts: list[str] = []
        self.session_state: _SessionState = _SessionState()

    def _record(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        for a in args:
            if isinstance(a, str):
                self.texts.append(a)
        for v in kwargs.values():
            if isinstance(v, str):
                self.texts.append(v)
        return False

    # falsy widget returns
    def button(self, *a, **kw):
        self._record("button", *a, **kw)
        return False

    def checkbox(self, *a, **kw):
        self._record("checkbox", *a, **kw)
        return False

    def text_input(self, *a, **kw):
        self._record("text_input", *a, **kw)
        return ""

    def file_uploader(self, *a, **kw):
        self._record("file_uploader", *a, **kw)
        return None

    # layout helpers
    def columns(self, spec):
        n = spec if isinstance(spec, int) else len(spec)
        return tuple(_Col(self) for _ in range(n))

    def spinner(self, *a, **kw):
        self._record("spinner", *a, **kw)
        return _ContextNoop()

    def expander(self, *a, **kw):
        self._record("expander", *a, **kw)
        return _ContextNoop()

    def container(self, *a, **kw):
        self._record("container", *a, **kw)
        return _ContextNoop()

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._record(name, *a, **kw)
        return _call


class _Col(_ContextNoop):
    def __init__(self, parent: _Recorder):
        self._parent = parent

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._parent._record(f"col.{name}", *a, **kw)
        return _call


@pytest.fixture()
def fake_st(monkeypatch):
    fake = _Recorder()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.setattr(setup_screen, "st", fake, raising=True)
    monkeypatch.setattr(dossier_screen, "st", fake, raising=True)
    monkeypatch.setattr(app_main, "st", fake, raising=True)
    monkeypatch.setattr(theme, "st", fake, raising=True)
    return fake


def _settings(show_debug_panel: bool):
    return config.Settings(
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-1234567890abcdef",
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
        show_debug_panel=show_debug_panel,
    )


# ---------------------------------------------------------------------------
# 1. Disclosure section + checkbox are gone
# ---------------------------------------------------------------------------


def test_setup_screen_has_no_privacy_disclosure_attributes():
    assert not hasattr(setup_screen, "PRIVACY_DISCLOSURE")
    assert not hasattr(setup_screen, "PRIVACY_CHECKBOX_LABEL")
    assert not hasattr(setup_screen, "_render_privacy_disclosure")


def test_setup_screen_does_not_render_privacy_section_or_checkbox(
    fake_st, monkeypatch
):
    monkeypatch.setattr(setup_screen, "get_settings", lambda: _settings(False))
    fake_st.session_state["api_ok"] = True
    fake_st.session_state["api_status"] = "API OK"

    setup_screen.render()

    joined = "\n".join(fake_st.texts).lower()
    # No checkbox at all on the setup screen.
    assert not any(name == "checkbox" for name, _, _ in fake_st.calls)
    # Old disclosure headings and copy must be gone.
    for forbidden in (
        "privacy and data flow disclosure",
        "i understand and accept this data flow",
        "data flow accepted",
    ):
        assert forbidden not in joined


# ---------------------------------------------------------------------------
# 2. App no longer blocks progression on privacy_accepted
# ---------------------------------------------------------------------------


def test_session_defaults_no_longer_track_privacy_accepted():
    assert "privacy_accepted" not in app_main.SESSION_DEFAULTS
    assert "data_flow_accepted" not in app_main.SESSION_DEFAULTS
    assert "disclosure_accepted" not in app_main.SESSION_DEFAULTS


def test_config_session_keys_no_longer_track_privacy_accepted():
    assert "privacy_accepted" not in config.SESSION_KEYS
    assert "data_flow_accepted" not in config.SESSION_KEYS
    assert "disclosure_accepted" not in config.SESSION_KEYS


def test_dossier_unlocks_after_api_ok_without_privacy(monkeypatch, fake_st):
    # Make _is_unlocked read our fake session_state.
    fake_st.session_state["api_ok"] = True
    # privacy_accepted is intentionally absent.
    assert app_main._is_unlocked("dossier") is True


def test_dossier_locked_when_api_not_ok(fake_st):
    fake_st.session_state["api_ok"] = False
    assert app_main._is_unlocked("dossier") is False


def test_lock_reason_for_dossier_no_longer_mentions_data_flow():
    reason = app_main._lock_reason("dossier")
    assert "data flow" not in reason.lower()
    assert "privacy" not in reason.lower()
    assert "api check" in reason.lower()


# ---------------------------------------------------------------------------
# 3. Dossier screen renders without privacy_accepted
# ---------------------------------------------------------------------------


def test_dossier_screen_renders_when_api_ok_and_no_privacy_flag(
    fake_st, monkeypatch
):
    monkeypatch.setattr(dossier_screen, "get_settings", lambda: _settings(False))
    fake_st.session_state["api_ok"] = True
    # No privacy_accepted in state — must NOT be needed.

    dossier_screen.render()

    joined = "\n".join(fake_st.texts).lower()
    # Should NOT show the old locked banner that referenced data flow.
    assert "accept the data flow" not in joined
    # Step headings must still render.
    assert "step 1" in joined or "dossier folder path" in joined


def test_dossier_extract_gate_only_needs_api_screenshots_and_index(monkeypatch):
    """``_step_extract_fields`` should compute ``can_extract`` ignoring privacy."""
    src = open(dossier_screen.__file__).read()
    # The replaced block must no longer reference privacy_accepted.
    assert "privacy_accepted" not in src


# ---------------------------------------------------------------------------
# 4. Disclosure / debug surfaces are hidden when SHOW_DEBUG_PANEL=false
# ---------------------------------------------------------------------------


def test_setup_screen_hides_provider_model_labels_when_debug_off(
    fake_st, monkeypatch
):
    monkeypatch.setattr(setup_screen, "get_settings", lambda: _settings(False))
    fake_st.session_state["api_ok"] = True
    fake_st.session_state["api_status"] = "API OK"

    setup_screen.render()

    joined = "\n".join(fake_st.texts)
    # The Setup screen now hosts the Configure AI Service form, so provider
    # and model names legitimately appear in it. What must STILL stay hidden
    # when debug is off is the developer debug block and its raw env-var
    # labels.
    for forbidden in (
        "LLM_PROVIDER",
        "Active model",
        "ALLOW_LOCAL_PLACEHOLDERS",
        "Detected project root",
    ):
        assert forbidden not in joined
    # The raw API key is never shown — only a masked status.
    assert "sk-ant-test-1234567890abcdef" not in joined
    assert config.mask_secret("sk-ant-test-1234567890abcdef") in joined


def test_setup_screen_shows_minimal_data_notice_when_debug_off(
    fake_st, monkeypatch
):
    monkeypatch.setattr(setup_screen, "get_settings", lambda: _settings(False))
    setup_screen.render()
    joined = "\n".join(fake_st.texts)
    assert setup_screen.MINIMAL_DATA_NOTICE in joined
    # It really is non-blocking — no checkbox, no warning that anything is
    # locked behind accepting it.
    for forbidden in ("accept", "tick the box", "i understand"):
        assert forbidden not in joined.lower()


# ---------------------------------------------------------------------------
# 5. Debug surfaces reappear when SHOW_DEBUG_PANEL=true
# ---------------------------------------------------------------------------


def test_setup_screen_shows_provider_model_when_debug_on(
    fake_st, monkeypatch
):
    monkeypatch.setattr(setup_screen, "get_settings", lambda: _settings(True))
    fake_st.session_state["api_ok"] = True
    fake_st.session_state["api_status"] = "API OK"

    setup_screen.render()

    joined = "\n".join(fake_st.texts)
    assert "LLM_PROVIDER" in joined
    assert "Text model" in joined
    assert "ALLOW_LOCAL_PLACEHOLDERS" in joined


# ---------------------------------------------------------------------------
# 6. Config still exposes SHOW_DEBUG_PANEL=false as default
# ---------------------------------------------------------------------------


def test_show_debug_panel_default_is_false(monkeypatch):
    monkeypatch.delenv("SHOW_DEBUG_PANEL", raising=False)
    config.get_settings.cache_clear()
    try:
        s = config.get_settings()
        assert s.show_debug_panel is False
    finally:
        config.get_settings.cache_clear()
