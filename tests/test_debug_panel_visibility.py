"""Tests for SHOW_DEBUG_PANEL visibility behaviour.

When ``SHOW_DEBUG_PANEL=false`` (the production default) the main UI
must never surface provider/model labels, task names, prompt size,
evidence count sent, raw API error reasons, or internal fallback
messages. When ``SHOW_DEBUG_PANEL=true`` those details show up behind a
clearly labelled debug surface.

These tests stub ``streamlit`` so render code can be exercised without a
running Streamlit server.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app import config
from app.ui import api_usage_panel
from app.ui import output_screen


# ---------------------------------------------------------------------------
# Streamlit shim — captures every call made by the UI code under test.
# ---------------------------------------------------------------------------


class _RecorderStreamlit:
    """Minimal Streamlit stand-in that records all calls.

    Every captured call lands in ``self.calls`` as ``(method, args, kwargs)``
    and any rendered text lands in ``self.texts`` so tests can grep it.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.texts: list[str] = []
        self.session_state: dict[str, Any] = {}

    # generic recorder ------------------------------------------------
    def _record(self, name: str, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        for a in args:
            if isinstance(a, str):
                self.texts.append(a)
        return _ContextNoop()

    def __getattr__(self, name):
        return lambda *a, **kw: self._record(name, *a, **kw)

    # widgets that need to return values ------------------------------
    def columns(self, spec):
        # Return enough _ColRecorder objects to unpack into n columns.
        n = spec if isinstance(spec, int) else len(spec)
        return tuple(_ColRecorder(self) for _ in range(n))


class _ContextNoop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _ColRecorder(_ContextNoop):
    def __init__(self, parent: _RecorderStreamlit):
        self._parent = parent

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._parent._record(f"col.{name}", *a, **kw)
        return _call


@pytest.fixture()
def fake_streamlit(monkeypatch):
    fake = _RecorderStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    # Both modules already imported streamlit; rebind their local refs.
    monkeypatch.setattr(api_usage_panel, "st", fake, raising=True)
    monkeypatch.setattr(output_screen, "st", fake, raising=True)
    return fake


def _settings_with_debug(show_debug_panel: bool, **overrides):
    base = dict(
        llm_provider="openai",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key="sk-test-key",
        openai_model="gpt-4o",
        allow_local_placeholders=False,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
        show_debug_panel=show_debug_panel,
    )
    base.update(overrides)
    return config.Settings(**base)


# ---------------------------------------------------------------------------
# api_usage_panel.render is gated on SHOW_DEBUG_PANEL
# ---------------------------------------------------------------------------


def test_api_usage_panel_is_hidden_when_show_debug_panel_false(
    fake_streamlit, monkeypatch
):
    monkeypatch.setattr(
        api_usage_panel, "get_settings", lambda: _settings_with_debug(False)
    )
    fake_streamlit.session_state["api_usage_log"] = [
        {
            "task_name": "opportunity_matching",
            "provider": "openai",
            "model": "gpt-4o",
            "used_api": True,
            "status": "ok",
        }
    ]

    api_usage_panel.render()

    # Nothing should have been rendered — not even an empty expander.
    assert fake_streamlit.calls == []
    # And of course no provider/model/task label leaked into the page.
    for text in fake_streamlit.texts:
        for forbidden in (
            "API Usage Status",
            "opportunity_matching",
            "Active model",
            "Provider",
            "gpt-4o",
        ):
            assert forbidden not in text


def test_api_usage_panel_renders_when_show_debug_panel_true(
    fake_streamlit, monkeypatch
):
    monkeypatch.setattr(
        api_usage_panel, "get_settings", lambda: _settings_with_debug(True)
    )
    fake_streamlit.session_state["api_usage_log"] = [
        {
            "task_name": "opportunity_matching",
            "provider": "openai",
            "model": "gpt-4o",
            "used_api": True,
            "status": "ok",
            "timestamp": "2026-01-01T00:00:00",
        }
    ]

    api_usage_panel.render()

    method_names = [name for name, _, _ in fake_streamlit.calls]
    # The expander must open and the per-stage block must render.
    assert "expander" in method_names
    joined = "\n".join(fake_streamlit.texts)
    assert "API Usage Status" in joined
    assert "opportunity_matching" in joined


# ---------------------------------------------------------------------------
# User-facing stage-banner helpers never leak internals
# ---------------------------------------------------------------------------


def test_user_facing_error_message_is_clean():
    msg = output_screen.USER_FACING_ERROR
    assert "AI service" in msg
    for forbidden in (
        "task_name",
        "opportunity_matching",
        "recommendation_generation",
        "provider",
        "openai",
        "anthropic",
        "Traceback",
        "stack",
    ):
        assert forbidden.lower() not in msg.lower()


def test_user_facing_basic_mode_message_is_clean():
    msg = output_screen.USER_FACING_BASIC_MODE
    assert msg == "Analysis completed in basic mode."
    for forbidden in (
        "LOCAL FALLBACK",
        "API NOT USED",
        "local_placeholder",
        "task_name",
        "provider",
        "model",
    ):
        assert forbidden.lower() not in msg.lower()


def test_stage_user_state_maps_meta_to_clean_states():
    assert output_screen._stage_user_state({"used_api": True, "status": "ok"}) == "ok"
    assert (
        output_screen._stage_user_state(
            {"used_api": False, "status": "local_placeholder"}
        )
        == "basic"
    )
    assert (
        output_screen._stage_user_state({"used_api": False, "status": "quota"})
        == "error"
    )
    assert output_screen._stage_user_state({}) == "error"


def test_stage_banner_clean_uses_user_facing_strings_only(fake_streamlit):
    output_screen._render_stage_banner_clean("error")
    output_screen._render_stage_banner_clean("basic")
    output_screen._render_stage_banner_clean("ok")

    # ok state renders nothing.
    methods = [m for m, _, _ in fake_streamlit.calls]
    assert methods.count("error") == 1
    assert methods.count("info") == 1
    # The text the user sees is exactly the clean copy — no provider/task names.
    joined = "\n".join(fake_streamlit.texts)
    assert output_screen.USER_FACING_ERROR in joined
    assert output_screen.USER_FACING_BASIC_MODE in joined
    for forbidden in (
        "opportunity_matching",
        "recommendation_generation",
        "provider",
        "gpt-4o",
        "claude",
        "LOCAL FALLBACK",
        "API NOT USED",
        "Traceback",
    ):
        assert forbidden.lower() not in joined.lower()


# ---------------------------------------------------------------------------
# Settings wiring
# ---------------------------------------------------------------------------


def test_show_debug_panel_defaults_to_false(monkeypatch):
    monkeypatch.delenv("SHOW_DEBUG_PANEL", raising=False)
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.show_debug_panel is False


def test_show_debug_panel_reads_env(monkeypatch):
    monkeypatch.setenv("SHOW_DEBUG_PANEL", "true")
    config.get_settings.cache_clear()
    try:
        settings = config.get_settings()
        assert settings.show_debug_panel is True
    finally:
        monkeypatch.delenv("SHOW_DEBUG_PANEL", raising=False)
        config.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Debug-panel detail rendering only fires when the flag is on
# ---------------------------------------------------------------------------


def test_debug_stage_details_only_run_when_flag_on(fake_streamlit, monkeypatch):
    # With the flag off, calling the public-facing banner emits clean copy
    # but the technical detail helper is never invoked. We simulate the
    # gating output_screen.render() does.
    settings_off = _settings_with_debug(False)
    settings_on = _settings_with_debug(True)

    match_meta = {
        "task_name": "opportunity_matching",
        "used_api": False,
        "status": "quota",
        "provider": "openai",
        "model": "gpt-4o",
        "error_message": "Rate limit reached: org-1234",
    }
    rec_meta = {
        "task_name": "recommendation_generation",
        "used_api": True,
        "status": "ok",
        "provider": "openai",
        "model": "gpt-4o",
    }

    def _surface(settings, *, match_meta, rec_meta):
        match_state = output_screen._stage_user_state(match_meta)
        rec_state = output_screen._stage_user_state(rec_meta)
        if "error" in (match_state, rec_state):
            fake_streamlit.error(output_screen.USER_FACING_ERROR)
        elif "basic" in (match_state, rec_state):
            fake_streamlit.info(output_screen.USER_FACING_BASIC_MODE)
        if settings.show_debug_panel:
            output_screen._render_debug_stage_details(
                match_meta=match_meta, rec_meta=rec_meta
            )

    # ---- flag OFF ----
    fake_streamlit.calls.clear()
    fake_streamlit.texts.clear()
    _surface(settings_off, match_meta=match_meta, rec_meta=rec_meta)
    joined = "\n".join(fake_streamlit.texts)
    assert output_screen.USER_FACING_ERROR in joined
    for forbidden in (
        "opportunity_matching",
        "recommendation_generation",
        "openai",
        "gpt-4o",
        "Rate limit",
        "quota",
        "org-1234",
    ):
        assert forbidden.lower() not in joined.lower()

    # ---- flag ON ----
    fake_streamlit.calls.clear()
    fake_streamlit.texts.clear()
    _surface(settings_on, match_meta=match_meta, rec_meta=rec_meta)
    joined = "\n".join(fake_streamlit.texts)
    # User-facing copy still shows…
    assert output_screen.USER_FACING_ERROR in joined
    # …plus the technical detail is now visible.
    assert "opportunity_matching" in joined
    assert "recommendation_generation" in joined
    assert "openai" in joined.lower() or "gpt-4o" in joined.lower()
