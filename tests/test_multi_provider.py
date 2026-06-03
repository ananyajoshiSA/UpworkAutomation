"""Tests for multi-provider support (OpenAI, Anthropic, Groq, Gemini).

Covers the provider-extension work end to end:

- ``config`` loads each of the four providers (env -> Settings, active_*).
- The Setup form saves Groq/Gemini keys+models to the correct env vars and
  leaves the other providers' keys untouched.
- ``api_gate`` validates all four providers offline.
- ``llm_client`` routes text calls to the matching provider adapter.
- Groq is text-only: it is not offered as a vision provider and a Groq vision
  request returns a clean, actionable message without hitting the network.
- Raw API keys never appear in safe (user-facing) error messages, masks, or
  reprs, for any provider.
- ``.env.example`` documents every provider key.
- Backward compatibility: an OpenAI/Anthropic-only config still works and
  missing Groq/Gemini keys never break the other providers.

No real network calls are made — provider adapters are monkey-patched.
"""

from __future__ import annotations

import os

import pytest

from app import config
from app.config import Settings
from app.services import llm_client
from app.services.api_gate import ApiGateError, run_capability_test
from app.ui import setup_screen


# Realistic-looking, provider-shaped fakes. None are real keys.
KEYS = {
    "openai": "sk-proj-FAKE-openai-1234567890abcdef",
    "anthropic": "sk-ant-FAKE-anthropic-1234567890abcdef",
    "groq": "gsk_FAKE-groq-1234567890abcdefghij",
    "gemini": "AIzaSyFAKE-gemini-1234567890abcdefghij",
}
MODELS = {
    "openai": "gpt-4.1",
    "anthropic": "claude-sonnet-4-6",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-1.5-pro",
}
TEXT_ADAPTERS = {
    "openai": "_openai_text",
    "anthropic": "_anthropic_text",
    "groq": "_groq_text",
    "gemini": "_gemini_text",
}


def _settings_for(provider: str, **overrides) -> Settings:
    """A fully-configured Settings for ``provider`` (others left unset)."""
    base = dict(
        llm_provider=provider,
        openai_api_key=KEYS["openai"] if provider == "openai" else None,
        openai_model=MODELS["openai"],
        anthropic_api_key=KEYS["anthropic"] if provider == "anthropic" else None,
        anthropic_model=MODELS["anthropic"],
        groq_api_key=KEYS["groq"] if provider == "groq" else None,
        groq_model=MODELS["groq"],
        gemini_api_key=KEYS["gemini"] if provider == "gemini" else None,
        gemini_model=MODELS["gemini"],
    )
    base.update(overrides)
    return Settings(**base)


def _form(**overrides) -> dict:
    """A minimal valid Configure-AI-Service payload for build_env_updates."""
    payload = dict(
        llm_provider="openai",
        llm_model="gpt-4.1",
        api_key=KEYS["openai"],
        vision_provider="openai",
        vision_model="gpt-4o",
        allow_provider_fallback=False,
        allow_local_placeholders=False,
        show_debug_panel=False,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
        log_retention_days=7,
    )
    payload.update(overrides)
    return payload


# In-memory streamlit shim so the usage log can be written without a session.
class _FakeSessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _FakeStreamlit:
    def __init__(self):
        self.session_state = _FakeSessionState()


@pytest.fixture()
def fake_streamlit(monkeypatch):
    fake = _FakeStreamlit()
    monkeypatch.setitem(__import__("sys").modules, "streamlit", fake)
    yield fake


@pytest.fixture()
def env_loader(monkeypatch):
    """Yield a function that loads a fresh Settings from given env values.

    Clears every managed key first so values from the developer's real .env
    (already in ``os.environ`` from import-time ``load_dotenv``) can't bleed
    in. Restoration is handled by monkeypatch at teardown.
    """
    for key in config._MANAGED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    def _load(**env) -> Settings:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        # _load_settings reads os.environ fresh (not cached), so it reflects
        # exactly the values set above without touching the on-disk .env.
        return config._load_settings()

    return _load


# ---------------------------------------------------------------------------
# 1-4. config loads each provider from the environment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "anthropic", "groq", "gemini"])
def test_config_loads_each_provider(env_loader, provider):
    key_var, model_var = config._PROVIDER_REQUIRED_ENV[provider]
    settings = env_loader(
        LLM_PROVIDER=provider,
        **{key_var: KEYS[provider], model_var: MODELS[provider]},
    )
    assert settings.active_provider == provider
    assert settings.active_model == MODELS[provider]
    assert settings.has_api_key is True
    assert settings.provider_configured is True
    # The active key is exactly the one configured for the active provider.
    assert settings._key_for_provider(provider) == KEYS[provider]


def test_config_default_models_present():
    # A provider selected with no explicit model still resolves to a default.
    for provider in ("openai", "anthropic", "groq", "gemini"):
        s = Settings(llm_provider=provider)
        assert s.active_model, provider


# ---------------------------------------------------------------------------
# 5-6. Setup form saves Groq / Gemini key + model to the right env vars
# ---------------------------------------------------------------------------


def test_setup_form_saves_groq_key_and_model():
    updates = config.build_env_updates(
        **_form(
            llm_provider="groq",
            llm_model="llama-3.3-70b-versatile",
            api_key=KEYS["groq"],
            vision_provider="openai",
            vision_model="gpt-4o",
        )
    )
    assert updates["LLM_PROVIDER"] == "groq"
    assert updates["GROQ_API_KEY"] == KEYS["groq"]
    assert updates["GROQ_MODEL"] == "llama-3.3-70b-versatile"
    # The other providers' keys/models are never written on a Groq save.
    for absent in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        assert absent not in updates


def test_setup_form_saves_gemini_key_and_model():
    updates = config.build_env_updates(
        **_form(
            llm_provider="gemini",
            llm_model="gemini-1.5-pro",
            api_key=KEYS["gemini"],
            vision_provider="gemini",
            vision_model="gemini-1.5-pro",
        )
    )
    assert updates["LLM_PROVIDER"] == "gemini"
    assert updates["GEMINI_API_KEY"] == KEYS["gemini"]
    assert updates["GEMINI_MODEL"] == "gemini-1.5-pro"
    for absent in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"):
        assert absent not in updates


def test_switching_to_groq_leaves_other_provider_keys_untouched(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"OPENAI_API_KEY={KEYS['openai']}\n"
        f"ANTHROPIC_API_KEY={KEYS['anthropic']}\n"
    )
    updates = config.build_env_updates(
        **_form(
            llm_provider="groq",
            llm_model="llama-3.1-8b-instant",
            api_key=KEYS["groq"],
            vision_provider="openai",
            vision_model="gpt-4o",
        )
    )
    config.update_env_file(updates, env_path=env)
    text = env.read_text()
    assert f"OPENAI_API_KEY={KEYS['openai']}" in text
    assert f"ANTHROPIC_API_KEY={KEYS['anthropic']}" in text
    assert f"GROQ_API_KEY={KEYS['groq']}" in text
    assert "GROQ_MODEL=llama-3.1-8b-instant" in text


def test_blank_groq_key_preserves_existing(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"GROQ_API_KEY={KEYS['groq']}\nGROQ_MODEL=llama-3.3-70b-versatile\n")
    # Re-save Groq with the key field blank (UI never prefills it).
    updates = config.build_env_updates(
        **_form(
            llm_provider="groq",
            llm_model="llama-3.1-8b-instant",
            api_key="",
            vision_provider="openai",
            vision_model="gpt-4o",
        )
    )
    assert "GROQ_API_KEY" not in updates  # blank key never written
    config.update_env_file(updates, env_path=env)
    text = env.read_text()
    assert f"GROQ_API_KEY={KEYS['groq']}" in text  # preserved
    assert "GROQ_MODEL=llama-3.1-8b-instant" in text  # updated


def test_groq_gemini_round_trip_through_env_file(tmp_path, monkeypatch):
    """Save Groq via the form, reload from disk, and confirm the gate is OK."""
    env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env)
    # Clear managed keys so the real .env can't influence the reload.
    for key in config._MANAGED_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    updates = config.build_env_updates(
        **_form(
            llm_provider="gemini",
            llm_model="gemini-1.5-flash",
            api_key=KEYS["gemini"],
            vision_provider="gemini",
            vision_model="gemini-1.5-flash",
        )
    )
    config.update_env_file(updates)
    try:
        settings = config.reload_settings()
        assert settings.active_provider == "gemini"
        assert settings.gemini_model == "gemini-1.5-flash"
        assert settings.has_api_key is True
        assert run_capability_test(settings=settings).ok is True
    finally:
        config.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 7. api_gate validates all four providers (offline)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "anthropic", "groq", "gemini"])
def test_api_gate_supports_all_four_providers(provider):
    result = run_capability_test(settings=_settings_for(provider))
    assert result.ok is True, (provider, result.status)
    assert result.provider == provider
    assert result.model == MODELS[provider]


def test_api_gate_missing_key_for_selected_provider(provider="groq"):
    # Groq selected but no Groq key -> NO API ADDED (not a crash).
    s = Settings(llm_provider="groq", groq_model=MODELS["groq"])
    result = run_capability_test(settings=s)
    assert result.status == ApiGateError.NO_API_ADDED
    assert result.ok is False


# ---------------------------------------------------------------------------
# 8. llm_client routes text calls to the correct provider adapter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "anthropic", "groq", "gemini"])
def test_llm_client_routes_text_to_correct_provider(provider, fake_streamlit, monkeypatch):
    calls: list[str] = []

    def make(name):
        def _adapter(**kwargs):
            calls.append(name)
            # The adapter receives the active provider's key, never another's.
            assert kwargs["api_key"] == KEYS[provider]
            return ("API OK", 3, 1)

        return _adapter

    for name in TEXT_ADAPTERS.values():
        monkeypatch.setattr(llm_client, name, make(name))

    result = llm_client.call_text_llm(
        task_name="api_check",
        system_prompt="",
        user_prompt="Reply with exactly: API OK",
        settings=_settings_for(provider),
    )
    assert result.success is True
    assert result.provider == provider
    assert result.model == MODELS[provider]
    # Exactly one adapter ran — the one for this provider.
    assert calls == [TEXT_ADAPTERS[provider]]


def test_llm_client_routes_vision_to_gemini(fake_streamlit, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        llm_client,
        "_gemini_vision",
        lambda **kw: (seen.append("gemini") or ("{}", 1, 1)),
    )
    settings = _settings_for("gemini", vision_provider="gemini", vision_model="gemini-1.5-pro")
    result = llm_client.call_vision_llm(
        task_name="screenshot_extraction",
        system_prompt="",
        image_inputs=[(b"img-bytes", "image/png")],
        settings=settings,
    )
    assert result.success is True
    assert seen == ["gemini"]
    assert result.provider == "gemini"


# ---------------------------------------------------------------------------
# 9. Groq is text-only — not a vision provider
# ---------------------------------------------------------------------------


def test_groq_not_offered_as_vision_provider():
    assert "groq" not in config.VISION_PROVIDER_OPTIONS
    # The supported vision providers are exactly these three.
    assert set(config._SUPPORTED_VISION_PROVIDERS) == {"openai", "anthropic", "gemini"}
    assert "groq" in config.PROVIDER_OPTIONS  # but it IS a valid text provider


def test_groq_vision_returns_clean_unsupported_message(fake_streamlit):
    # llm_provider=groq with no separate vision provider -> vision resolves to groq.
    settings = _settings_for("groq")
    assert settings.active_vision_provider == "groq"
    result = llm_client.call_vision_llm(
        task_name="screenshot_extraction",
        system_prompt="",
        image_inputs=[(b"img-bytes", "image/png")],
        settings=settings,
    )
    assert result.success is False
    assert result.used_api is False  # no network call attempted
    assert result.status == llm_client.STATUS_UNSUPPORTED_PROVIDER
    assert result.error_message == config.GROQ_VISION_UNSUPPORTED_MESSAGE
    assert "Groq" in result.error_message


def test_groq_text_provider_with_gemini_vision_is_allowed(fake_streamlit, monkeypatch):
    # A Groq text provider paired with an explicit Gemini vision provider works.
    monkeypatch.setattr(llm_client, "_gemini_vision", lambda **kw: ("{}", 1, 1))
    settings = _settings_for(
        "groq",
        gemini_api_key=KEYS["gemini"],
        vision_provider="gemini",
        vision_model="gemini-1.5-pro",
    )
    result = llm_client.call_vision_llm(
        task_name="screenshot_extraction",
        system_prompt="",
        image_inputs=[(b"img-bytes", "image/png")],
        settings=settings,
    )
    assert result.success is True
    assert result.provider == "gemini"


# ---------------------------------------------------------------------------
# 10. Raw API keys never appear in safe error messages / masks / reprs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["openai", "anthropic", "groq", "gemini"])
def test_raw_key_never_in_safe_message_mask_or_repr(provider, fake_streamlit, monkeypatch):
    secret = KEYS[provider]

    # Worst case: the provider SDK raises an error that echoes the key.
    def boom(**kwargs):
        raise RuntimeError(f"401 Unauthorized: key {kwargs['api_key']} is invalid")

    monkeypatch.setattr(llm_client, TEXT_ADAPTERS[provider], boom)
    settings = _settings_for(provider)

    gate = run_capability_test(settings=settings, live=True)
    assert gate.ok is False
    # The user-facing safe message is a fixed, leak-free string.
    safe = setup_screen._safe_message(gate.status)
    assert secret not in safe
    assert "sk-" not in safe and "gsk_" not in safe and "AIza" not in safe

    # The raw key must NOT survive into the carrier of the leak: the
    # LLMCallResult.error_message or the always-stored (not debug-gated)
    # api_usage_log. This is the real regression guard for key redaction —
    # a provider SDK that echoes the key in its error must be scrubbed.
    result = llm_client.call_text_llm(
        task_name="api_check", system_prompt="", user_prompt="ping", settings=settings
    )
    assert result.success is False
    assert secret not in (result.error_message or "")
    assert "[api key redacted]" in (result.error_message or "")
    usage_log = fake_streamlit.session_state.get("api_usage_log", [])
    assert usage_log, "the failed call should have been recorded"
    for entry in usage_log:
        for value in entry.values():
            assert secret not in str(value or "")

    # The masked display and the Settings repr never reveal the raw key.
    masked = settings.active_api_key_masked
    assert secret not in masked
    assert "****" in masked
    assert secret not in repr(settings)


def test_all_safe_messages_are_leak_free():
    for status in (
        ApiGateError.NO_API_ADDED,
        ApiGateError.MODEL_NOT_CONFIGURED,
        ApiGateError.INVALID_API_KEY,
        ApiGateError.API_QUOTA_EXCEEDED,
        ApiGateError.API_CONNECTION_FAILED,
        "SOME UNMAPPED STATUS",
    ):
        msg = setup_screen._safe_message(status)
        for key in KEYS.values():
            assert key not in msg
        for forbidden in ("sk-", "gsk_", "AIza", "Traceback", "organization"):
            assert forbidden.lower() not in msg.lower()


# ---------------------------------------------------------------------------
# 11. .env.example documents every provider key
# ---------------------------------------------------------------------------


def test_env_example_contains_all_provider_keys():
    example = (config.PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "VISION_PROVIDER",
        "VISION_MODEL",
    ):
        assert f"{key}=" in example, f"{key} missing from .env.example"


def test_env_example_has_no_real_secret_values():
    example = (config.PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    # Key lines must be empty (no committed secrets).
    for line in example.splitlines():
        for key_var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
            if line.strip().startswith(f"{key_var}="):
                assert line.strip() == f"{key_var}=", line


# ---------------------------------------------------------------------------
# 13. Backward compatibility
# ---------------------------------------------------------------------------


def test_openai_only_env_still_works(env_loader):
    settings = env_loader(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=KEYS["openai"],
        OPENAI_MODEL="gpt-4o",
    )
    assert settings.has_api_key is True
    assert settings.provider_configured is True
    # Groq/Gemini keys absent -> default to None, never raising.
    assert settings.groq_api_key is None
    assert settings.gemini_api_key is None
    assert run_capability_test(settings=settings).ok is True


def test_anthropic_only_env_unaffected_by_missing_new_keys(env_loader):
    settings = env_loader(
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY=KEYS["anthropic"],
        ANTHROPIC_MODEL="claude-sonnet-4-6",
    )
    assert settings.has_api_key is True
    assert run_capability_test(settings=settings).ok is True


def test_selecting_groq_without_key_does_not_crash(env_loader):
    # Backward-compat guarantee: missing GROQ_API_KEY only matters when groq
    # is the selected provider, and even then it degrades to NO API ADDED.
    settings = env_loader(LLM_PROVIDER="groq", GROQ_MODEL=MODELS["groq"])
    assert settings.has_api_key is False
    result = run_capability_test(settings=settings)
    assert result.status == ApiGateError.NO_API_ADDED
