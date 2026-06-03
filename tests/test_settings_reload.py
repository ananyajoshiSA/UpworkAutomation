"""Regression tests for live ``.env`` reload.

``get_settings`` is process-cached and ``load_dotenv`` runs once at import.
Streamlit keeps one Python process alive across reruns, so a user who launches
the app before adding their API key — then fills it in and clicks "Run API
Check" — would keep hitting the stale cached Settings (empty key) and see
"API Missing" until they restarted the server.

``config.reload_settings`` re-reads ``.env`` and rebuilds the cache so a key
added/corrected after launch is picked up live.
"""

from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _clean_settings_cache(monkeypatch):
    """Start/end with a fresh cache; isolate from the real .env on disk.

    ``reload_settings`` calls ``load_dotenv(override=True)``. In production
    that re-reads the user's ``.env``; in tests we stub it to a no-op so the
    monkeypatched environment is the single source of truth (otherwise the
    repo's real .env would clobber the values under test).
    """
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_reload_picks_up_key_added_after_launch(monkeypatch):
    # App launches with the provider set but no key — the empty value is
    # cached as the process-wide Settings.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1")

    first = config.get_settings()
    assert first.has_api_key is False

    # Re-running get_settings keeps returning the SAME stale object — this is
    # the bug the reload guards against.
    assert config.get_settings() is first

    # User adds the key to .env. We simulate that edit via the environment;
    # reload_settings(override=True) makes the new value win and rebuilds
    # the cache.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-REALKEY1234567890")
    refreshed = config.reload_settings()

    assert refreshed.has_api_key is True
    assert refreshed.openai_api_key == "sk-proj-REALKEY1234567890"
    # The cache now serves the fresh object to every other screen too.
    assert config.get_settings() is refreshed


def test_reload_reflects_provider_change(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-aaaaaaaaaaaaaaaa")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    assert config.get_settings().llm_provider == "anthropic"

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-REALKEY1234567890")
    refreshed = config.reload_settings()

    assert refreshed.llm_provider == "openai"
    assert refreshed.has_api_key is True
