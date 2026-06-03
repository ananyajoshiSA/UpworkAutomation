"""Tests for the in-app API configuration flow.

Covers the Setup-page "Configure AI Service" form, which writes the user's
provider/model/key into a local ``.env`` so they never have to edit it by
hand:

- ``update_env_file`` creates ``.env`` when missing and updates it safely.
- Unrelated keys, comments, and blank lines are preserved.
- OpenAI config writes ``OPENAI_API_KEY`` / ``OPENAI_MODEL``; Anthropic writes
  the ``ANTHROPIC_*`` pair; the inactive provider's keys are left untouched.
- A blank API key is never written (re-save keeps the saved key).
- ``"same_as_llm"`` vision resolves to the text provider/model.
- ``mask_secret`` never returns the full key.
- ``reload_settings`` picks up the freshly written file, and the API check
  then validates against the saved config.
- ``.env.example`` documents every managed key.

These tests exercise the pure config/IO layer directly — no Streamlit needed.
"""

from __future__ import annotations

import os

import pytest

from app import config
from app.services.api_gate import ApiGateError, run_capability_test


REAL_OPENAI_KEY = "sk-proj-REAL-OPENAI-KEY-1234567890abcdef"
REAL_ANTHROPIC_KEY = "sk-ant-REAL-ANTHROPIC-KEY-1234567890abcdef"


def _base_form(**overrides):
    """Minimal valid Configure-AI-Service payload for ``build_env_updates``."""
    payload = dict(
        llm_provider="openai",
        llm_model="gpt-4.1",
        api_key=REAL_OPENAI_KEY,
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


@pytest.fixture()
def isolated_env():
    """Snapshot, clear, and restore the managed env vars + settings cache.

    ``reload_settings()`` calls ``load_dotenv(override=True)``, which mutates
    ``os.environ`` directly (outside monkeypatch's tracking), so we restore the
    managed keys by hand to keep tests independent.
    """
    keys = list(config._MANAGED_ENV_KEYS)
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    config.get_settings.cache_clear()
    try:
        yield
    finally:
        for k in keys:
            v = saved[k]
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# update_env_file: create / update / preserve
# ---------------------------------------------------------------------------


def test_env_created_when_missing(tmp_path):
    env = tmp_path / ".env"
    assert not env.exists()
    config.update_env_file(
        {"LLM_PROVIDER": "openai", "OPENAI_MODEL": "gpt-4.1"}, env_path=env
    )
    assert env.is_file()
    text = env.read_text()
    assert "LLM_PROVIDER=openai" in text
    assert "OPENAI_MODEL=gpt-4.1" in text


def test_existing_value_updated_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=anthropic\nOPENAI_MODEL=gpt-4o\n")
    config.update_env_file({"OPENAI_MODEL": "gpt-4.1"}, env_path=env)
    text = env.read_text()
    assert "OPENAI_MODEL=gpt-4.1" in text
    assert "gpt-4o" not in text
    # Untouched key remains.
    assert "LLM_PROVIDER=anthropic" in text


def test_unrelated_values_and_comments_preserved(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# my notes\n"
        "CUSTOM_THING=keepme\n"
        "\n"
        "LLM_PROVIDER=anthropic\n"
        "# trailing comment\n"
    )
    config.update_env_file({"LLM_PROVIDER": "openai"}, env_path=env)
    text = env.read_text()
    assert "# my notes" in text
    assert "CUSTOM_THING=keepme" in text
    assert "# trailing comment" in text
    assert "LLM_PROVIDER=openai" in text
    assert "LLM_PROVIDER=anthropic" not in text


def test_duplicate_keys_all_updated(tmp_path):
    # A malformed .env with a duplicated key must not leave a stale value that
    # would win on the next dotenv load.
    env = tmp_path / ".env"
    env.write_text("OPENAI_MODEL=old1\nOPENAI_MODEL=old2\n")
    config.update_env_file({"OPENAI_MODEL": "gpt-4.1"}, env_path=env)
    text = env.read_text()
    assert "old1" not in text and "old2" not in text
    assert text.count("OPENAI_MODEL=gpt-4.1") == 2


def test_export_prefixed_key_is_updated_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text("export LLM_PROVIDER=anthropic\n")
    config.update_env_file({"LLM_PROVIDER": "openai"}, env_path=env)
    text = env.read_text()
    assert "export LLM_PROVIDER=openai" in text


# ---------------------------------------------------------------------------
# build_env_updates: provider-specific behavior
# ---------------------------------------------------------------------------


def test_openai_config_writes_openai_key_and_model():
    updates = config.build_env_updates(**_base_form())
    assert updates["LLM_PROVIDER"] == "openai"
    assert updates["OPENAI_API_KEY"] == REAL_OPENAI_KEY
    assert updates["OPENAI_MODEL"] == "gpt-4.1"
    # The inactive provider's keys are not written.
    assert "ANTHROPIC_API_KEY" not in updates
    assert "ANTHROPIC_MODEL" not in updates


def test_anthropic_config_writes_anthropic_key_and_model():
    updates = config.build_env_updates(
        **_base_form(
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            api_key=REAL_ANTHROPIC_KEY,
            vision_provider="same_as_llm",
            vision_model="",
        )
    )
    assert updates["LLM_PROVIDER"] == "anthropic"
    assert updates["ANTHROPIC_API_KEY"] == REAL_ANTHROPIC_KEY
    assert updates["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert "OPENAI_API_KEY" not in updates
    assert "OPENAI_MODEL" not in updates


def test_blank_api_key_is_not_written():
    updates = config.build_env_updates(**_base_form(api_key=""))
    assert "OPENAI_API_KEY" not in updates
    # Model is still written.
    assert updates["OPENAI_MODEL"] == "gpt-4.1"


def test_blank_api_key_preserves_existing_env_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={REAL_OPENAI_KEY}\nOPENAI_MODEL=gpt-4o\n")
    # User re-saves with the key field left blank (the UI never prefills it).
    updates = config.build_env_updates(**_base_form(api_key="", llm_model="gpt-4.1"))
    config.update_env_file(updates, env_path=env)
    text = env.read_text()
    assert f"OPENAI_API_KEY={REAL_OPENAI_KEY}" in text  # preserved
    assert "OPENAI_MODEL=gpt-4.1" in text  # updated


def test_switching_to_openai_leaves_anthropic_key_untouched(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        f"ANTHROPIC_API_KEY={REAL_ANTHROPIC_KEY}\nANTHROPIC_MODEL=claude-sonnet-4-6\n"
    )
    updates = config.build_env_updates(**_base_form())  # openai
    config.update_env_file(updates, env_path=env)
    text = env.read_text()
    # The Anthropic key the user already had is preserved, not blanked.
    assert f"ANTHROPIC_API_KEY={REAL_ANTHROPIC_KEY}" in text
    assert f"OPENAI_API_KEY={REAL_OPENAI_KEY}" in text


def test_same_as_llm_vision_resolves_to_text_provider_and_model():
    updates = config.build_env_updates(
        **_base_form(vision_provider="same_as_llm", vision_model="")
    )
    assert updates["VISION_PROVIDER"] == "openai"
    assert updates["VISION_MODEL"] == "gpt-4.1"


def test_explicit_vision_model_kept_with_same_as_llm():
    updates = config.build_env_updates(
        **_base_form(vision_provider="same_as_llm", vision_model="gpt-4o")
    )
    assert updates["VISION_PROVIDER"] == "openai"
    assert updates["VISION_MODEL"] == "gpt-4o"


def test_booleans_and_ints_serialized_as_env_strings():
    updates = config.build_env_updates(
        **_base_form(
            allow_provider_fallback=True,
            allow_local_placeholders=False,
            show_debug_panel=True,
            max_proposal_context_chars=12000,
            log_retention_days=14,
        )
    )
    assert updates["ALLOW_PROVIDER_FALLBACK"] == "true"
    assert updates["ALLOW_LOCAL_PLACEHOLDERS"] == "false"
    assert updates["SHOW_DEBUG_PANEL"] == "true"
    assert updates["MAX_PROPOSAL_CONTEXT_CHARS"] == "12000"
    assert updates["LOG_RETENTION_DAYS"] == "14"


# ---------------------------------------------------------------------------
# mask_secret: never reveal the full key
# ---------------------------------------------------------------------------


def test_mask_secret_never_reveals_full_key():
    masked = config.mask_secret(REAL_OPENAI_KEY)
    assert masked
    assert REAL_OPENAI_KEY not in masked
    assert masked != REAL_OPENAI_KEY
    assert "****" in masked
    # Only a short prefix and the last 4 characters are revealed.
    assert masked.endswith(REAL_OPENAI_KEY[-4:])
    assert len(masked) < len(REAL_OPENAI_KEY)


def test_mask_secret_handles_empty_and_short():
    assert config.mask_secret("") == ""
    assert config.mask_secret(None) == ""
    assert config.mask_secret("short") == "****"


# ---------------------------------------------------------------------------
# reload after save + API check uses the saved config
# ---------------------------------------------------------------------------


def test_config_reloads_after_save(tmp_path, isolated_env, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env)

    updates = config.build_env_updates(
        **_base_form(llm_provider="openai", llm_model="gpt-4.1", api_key=REAL_OPENAI_KEY)
    )
    config.update_env_file(updates)  # default ENV_PATH (monkeypatched)
    settings = config.reload_settings()

    assert settings.llm_provider == "openai"
    assert settings.openai_model == "gpt-4.1"
    assert settings.has_api_key is True
    assert settings.openai_api_key == REAL_OPENAI_KEY


def test_run_api_check_uses_saved_config(tmp_path, isolated_env, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env)

    updates = config.build_env_updates(
        **_base_form(
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            api_key=REAL_ANTHROPIC_KEY,
            vision_provider="same_as_llm",
            vision_model="",
        )
    )
    config.update_env_file(updates)
    settings = config.reload_settings()

    # The offline gate validates against the freshly saved Anthropic config.
    result = run_capability_test(settings=settings)
    assert result.status == ApiGateError.API_OK
    assert result.provider == "anthropic"
    assert result.model == "claude-sonnet-4-6"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file-mode semantics")
def test_saved_env_file_has_no_group_or_other_access(tmp_path):
    env = tmp_path / ".env"
    config.update_env_file({"LLM_PROVIDER": "openai"}, env_path=env)
    mode = env.stat().st_mode & 0o777
    # The file holds an API key — owner-only access.
    assert mode & 0o077 == 0


# ---------------------------------------------------------------------------
# .env.example stays in sync
# ---------------------------------------------------------------------------


def test_env_example_contains_all_required_keys():
    example = (config.PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in config._MANAGED_ENV_KEYS:
        assert f"{key}=" in example, f"{key} missing from .env.example"
