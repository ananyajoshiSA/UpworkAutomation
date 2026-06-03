"""Tests for .env loading, provider/model validation, vision defaulting,
and the safe, leak-free setup-screen messaging.

Covers the app-launch / API-setup fix:

- ``.env`` is loaded from the project root regardless of CWD.
- OpenAI config validates when key + model are present.
- A missing OpenAI key returns ``NO API ADDED``.
- A missing model returns ``MODEL NOT CONFIGURED``.
- The API check routes through ``llm_client`` with the configured
  provider/model and the lightweight ``api_check`` prompt.
- The API key never appears in any UI-safe error message.
- ``SHOW_DEBUG_PANEL=false`` hides all config debug details.

Streamlit is stubbed so the render code runs headless.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from app import config
from app.config import Settings
from app.services import api_gate, llm_client
from app.services.api_gate import ApiGateError, run_capability_test
from app.ui import setup_screen, theme


# A realistic-looking secret used to prove it never leaks into UI copy.
SECRET_KEY = "sk-proj-SUPERSECRET-should-never-be-shown-1234567890"


def _settings(**overrides) -> Settings:
    base: dict[str, Any] = dict(
        llm_provider="openai",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4.1",
        vision_provider="openai",
        vision_model="gpt-4o",
        show_debug_panel=False,
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 1. .env loads from the project root
# ---------------------------------------------------------------------------


def test_env_path_anchored_to_project_root():
    assert config.ENV_PATH == config.PROJECT_ROOT / ".env"
    # PROJECT_ROOT is the repo root (parent of the app package), not app/.
    assert (config.PROJECT_ROOT / "app" / "config.py").is_file()


def test_env_file_loaded_reports_presence(tmp_path, monkeypatch):
    # Point ENV_PATH at a temp file and confirm presence is tracked.
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=openai\n")
    monkeypatch.setattr(config, "ENV_PATH", env_file)
    assert config._load_env() is True
    assert config.env_file_loaded() is True

    missing = tmp_path / "nope" / ".env"
    monkeypatch.setattr(config, "ENV_PATH", missing)
    assert config._load_env() is False
    assert config.env_file_loaded() is False


# ---------------------------------------------------------------------------
# 2. Provider/model validation (offline gate)
# ---------------------------------------------------------------------------


def test_openai_config_validates_when_key_and_model_present():
    result = run_capability_test(settings=_settings(openai_api_key=SECRET_KEY))
    assert result.status == ApiGateError.API_OK
    assert result.ok is True
    assert result.provider == "openai"
    assert result.model == "gpt-4.1"


def test_missing_openai_key_returns_no_api_added():
    result = run_capability_test(settings=_settings(openai_api_key=None))
    assert result.status == ApiGateError.NO_API_ADDED
    assert result.ok is False


def test_missing_model_returns_model_not_configured():
    settings = _settings(openai_api_key=SECRET_KEY, openai_model="")
    result = run_capability_test(settings=settings, model="")
    assert result.status == ApiGateError.MODEL_NOT_CONFIGURED
    assert result.ok is False


# ---------------------------------------------------------------------------
# 3. Vision provider/model defaulting
# ---------------------------------------------------------------------------


def test_vision_provider_defaults_to_text_provider_when_blank():
    s = _settings(vision_provider=None)
    assert s.active_vision_provider == "openai"


def test_vision_model_uses_text_model_when_blank_and_capable():
    # gpt-4.1 is vision-capable, so a blank VISION_MODEL falls back to it.
    s = _settings(vision_model=None, openai_model="gpt-4.1")
    assert s.active_vision_model == "gpt-4.1"
    assert s.vision_configured is True


def test_vision_model_none_when_text_model_not_vision_capable():
    s = _settings(vision_model=None, openai_model="text-only-model")
    assert s.active_vision_model is None
    assert s.vision_configured is False


# ---------------------------------------------------------------------------
# 4. API check routes through llm_client with the configured provider/model
# ---------------------------------------------------------------------------


def test_api_check_calls_llm_client_with_configured_provider(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_call(**kwargs):
        captured.update(kwargs)
        return llm_client.LLMCallResult(
            success=True,
            task_name=kwargs["task_name"],
            provider="openai",
            model="gpt-4.1",
            used_api=True,
            response_text="API OK",
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(llm_client, "call_text_llm", _fake_call)
    settings = _settings(openai_api_key=SECRET_KEY)
    result = run_capability_test(settings=settings, live=True)

    assert captured["task_name"] == "api_check"
    assert "API OK" in captured["user_prompt"]
    # The configured settings object is threaded through to the client.
    assert captured["settings"] is settings
    assert result.status == ApiGateError.API_OK


def test_api_check_does_not_call_vision(monkeypatch):
    """Initial setup must not exercise the vision model."""
    called = {"vision": False}
    monkeypatch.setattr(
        llm_client,
        "call_vision_llm",
        lambda *a, **k: called.__setitem__("vision", True),
    )
    monkeypatch.setattr(
        llm_client,
        "call_text_llm",
        lambda **k: llm_client.LLMCallResult(
            success=True, task_name=k["task_name"], status=llm_client.STATUS_OK
        ),
    )
    run_capability_test(settings=_settings(openai_api_key=SECRET_KEY), live=True)
    assert called["vision"] is False


# ---------------------------------------------------------------------------
# 5. The API key never appears in UI-safe error messages
# ---------------------------------------------------------------------------


def test_safe_messages_never_leak_key_or_internals():
    statuses = [
        ApiGateError.NO_API_ADDED,
        ApiGateError.MODEL_NOT_CONFIGURED,
        ApiGateError.INVALID_API_KEY,
        ApiGateError.API_QUOTA_EXCEEDED,
        ApiGateError.API_CONNECTION_FAILED,
        ApiGateError.VISION_MODEL_NOT_AVAILABLE,
        "SOME UNMAPPED STATUS",
    ]
    for status in statuses:
        msg = setup_screen._safe_message(status)
        assert SECRET_KEY not in msg
        assert "sk-" not in msg
        for forbidden in ("Traceback", "org-", "organization", "api_check"):
            assert forbidden.lower() not in msg.lower()


def test_specific_safe_messages_match_requirements():
    # Messages now point users at the in-app Configure AI Service form rather
    # than at hand-editing .env, but they remain short, safe, and actionable.
    assert (
        setup_screen._safe_message(ApiGateError.NO_API_ADDED)
        == "API key is missing. Enter it above and click Save Configuration."
    )
    assert setup_screen._safe_message(ApiGateError.MODEL_NOT_CONFIGURED) == (
        "Model is missing. Enter a model above and click Save Configuration."
    )
    assert (
        setup_screen._safe_message(ApiGateError.INVALID_API_KEY)
        == "The API key or model could not be validated."
    )
    assert (
        setup_screen._safe_message(ApiGateError.API_CONNECTION_FAILED)
        == "The AI service is temporarily unavailable. Try again."
    )


# ---------------------------------------------------------------------------
# 6. Headless render: debug gating + no key leak on a failed check
# ---------------------------------------------------------------------------


class _SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _ContextNoop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Col(_ContextNoop):
    def __init__(self, parent):
        self._p = parent

    def __getattr__(self, name):
        return lambda *a, **k: self._p._record(f"col.{name}", *a, **k)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.texts: list[str] = []
        self.session_state = _SessionState()

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        for a in args:
            if isinstance(a, str):
                self.texts.append(a)
        for v in kwargs.values():
            if isinstance(v, str):
                self.texts.append(v)
        return _ContextNoop()

    def __getattr__(self, name):
        return lambda *a, **k: self._record(name, *a, **k)

    def button(self, *a, **k):
        self._record("button", *a, **k)
        return False

    def columns(self, spec):
        n = spec if isinstance(spec, int) else len(spec)
        return tuple(_Col(self) for _ in range(n))


@pytest.fixture()
def fake_st(monkeypatch):
    rec = _Recorder()
    monkeypatch.setitem(sys.modules, "streamlit", rec)
    monkeypatch.setattr(setup_screen, "st", rec, raising=True)
    monkeypatch.setattr(theme, "st", rec, raising=True)
    return rec


def test_debug_off_hides_config_details(fake_st, monkeypatch):
    monkeypatch.setattr(
        setup_screen, "get_settings", lambda: _settings(show_debug_panel=False)
    )
    fake_st.session_state["api_status"] = ApiGateError.NO_API_ADDED
    fake_st.session_state["api_ok"] = False

    setup_screen.render()
    joined = "\n".join(fake_st.texts)

    # The actionable safe message is shown…
    assert "API key is missing" in joined
    # …but no provider/model/root/raw-status detail leaks.
    for forbidden in (
        "Detected project root",
        "LLM_PROVIDER",
        "Vision provider",
        "Text model",
        "Status:",
        SECRET_KEY,
        "sk-",
    ):
        assert forbidden not in joined


def test_debug_on_shows_config_details(fake_st, monkeypatch):
    monkeypatch.setattr(
        setup_screen,
        "get_settings",
        lambda: _settings(show_debug_panel=True, openai_api_key=SECRET_KEY),
    )
    fake_st.session_state["api_status"] = "API OK"
    fake_st.session_state["api_ok"] = True

    setup_screen.render()
    joined = "\n".join(fake_st.texts)

    assert "Detected project root" in joined
    assert "LLM_PROVIDER" in joined
    assert "Vision provider" in joined
    # Even with the debug panel open, the key itself is never printed.
    assert SECRET_KEY not in joined


def test_refresh_button_is_rendered(fake_st, monkeypatch):
    monkeypatch.setattr(
        setup_screen, "get_settings", lambda: _settings(show_debug_panel=False)
    )
    setup_screen.render()
    labels = [
        a
        for name, args, _ in fake_st.calls
        if name == "button"
        for a in args
        if isinstance(a, str)
    ]
    assert "Refresh API Config" in labels
