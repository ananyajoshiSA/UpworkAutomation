"""Tests for the static provider/model reference list.

Covers ``app.models.provider_models``:

- All four supported providers are present with the documented shape.
- OpenAI / Anthropic / Gemini expose both text and vision models.
- Groq exposes text models and is text-only (no vision models).
- The helper functions return the expected values.
- Unknown / malformed input is handled safely (empty lists / False, no raises).
- The list is re-exported via ``app.config`` for convenience.

These are reference-list tests only — the module deliberately does not enforce
model choices, so nothing here asserts the app rejects unlisted models.
"""

from __future__ import annotations

import pytest

from app.models import provider_models as pm


PROVIDERS = ("openai", "anthropic", "groq", "gemini")


# ---------------------------------------------------------------------------
# Structure: all providers present, with the documented shape
# ---------------------------------------------------------------------------


def test_all_four_providers_exist():
    for provider in PROVIDERS:
        assert provider in pm.SUPPORTED_PROVIDER_MODELS
    assert set(pm.SUPPORTED_PROVIDER_MODELS) == set(PROVIDERS)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_each_provider_has_expected_keys(provider):
    entry = pm.SUPPORTED_PROVIDER_MODELS[provider]
    assert set(entry) == {"text_models", "vision_models", "notes"}
    assert isinstance(entry["text_models"], list)
    assert isinstance(entry["vision_models"], list)
    assert isinstance(entry["notes"], str) and entry["notes"].strip()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_provider_has_at_least_one_text_model(provider):
    assert pm.get_text_models(provider), f"{provider} should list text models"


# ---------------------------------------------------------------------------
# Per-provider text / vision expectations
# ---------------------------------------------------------------------------


def test_openai_has_text_and_vision_models():
    assert pm.get_text_models("openai")
    assert pm.get_vision_models("openai")
    assert pm.provider_supports_vision("openai") is True


def test_anthropic_has_text_and_vision_models():
    assert pm.get_text_models("anthropic")
    assert pm.get_vision_models("anthropic")
    assert pm.provider_supports_vision("anthropic") is True


def test_groq_has_text_models_and_no_vision_models():
    assert pm.get_text_models("groq")
    assert pm.get_vision_models("groq") == []
    assert pm.provider_supports_vision("groq") is False
    # The note documents the text-only treatment.
    assert "text-only" in pm.SUPPORTED_PROVIDER_MODELS["groq"]["notes"].lower()


def test_gemini_has_text_and_vision_models():
    assert pm.get_text_models("gemini")
    assert pm.get_vision_models("gemini")
    assert pm.provider_supports_vision("gemini") is True


def test_expected_model_names_present():
    # Spot-check a representative model in each provider's lists.
    assert "gpt-4o" in pm.get_text_models("openai")
    assert "gpt-4o" in pm.get_vision_models("openai")
    assert "claude-sonnet-4-6" in pm.get_text_models("anthropic")
    assert "claude-sonnet-4-6" in pm.get_vision_models("anthropic")
    assert "llama-3.3-70b-versatile" in pm.get_text_models("groq")
    assert "gemini-1.5-pro" in pm.get_text_models("gemini")
    assert "gemini-2.0-flash" in pm.get_vision_models("gemini")


# ---------------------------------------------------------------------------
# Helper functions: expected values
# ---------------------------------------------------------------------------


def test_get_supported_providers_returns_all_in_order():
    assert pm.get_supported_providers() == list(PROVIDERS)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_is_supported_provider_true_for_known(provider):
    assert pm.is_supported_provider(provider) is True


def test_is_supported_provider_case_insensitive():
    assert pm.is_supported_provider("OpenAI") is True
    assert pm.is_supported_provider("  GROQ  ") is True


def test_is_supported_text_model():
    assert pm.is_supported_text_model("openai", "gpt-4.1") is True
    assert pm.is_supported_text_model("groq", "llama-3.1-8b-instant") is True
    # Case-insensitive / whitespace-tolerant.
    assert pm.is_supported_text_model("anthropic", "  Claude-Sonnet-4-6 ") is True
    # Not in the curated list.
    assert pm.is_supported_text_model("openai", "gpt-9-ultra") is False


def test_is_supported_vision_model():
    assert pm.is_supported_vision_model("openai", "gpt-4o") is True
    assert pm.is_supported_vision_model("gemini", "gemini-1.5-flash") is True
    # Groq is text-only — nothing counts as a vision model.
    assert pm.is_supported_vision_model("groq", "llama-3.3-70b-versatile") is False
    # A text model that is not vision-capable for that provider.
    assert pm.is_supported_vision_model("anthropic", "claude-3-5-haiku-latest") is False


def test_returned_lists_are_copies():
    # Mutating a returned list must not corrupt the reference data.
    models = pm.get_text_models("openai")
    models.append("tampered")
    assert "tampered" not in pm.get_text_models("openai")


# ---------------------------------------------------------------------------
# Safety: unknown / malformed input never raises
# ---------------------------------------------------------------------------


def test_unknown_provider_returns_empty_and_false():
    assert pm.get_text_models("does-not-exist") == []
    assert pm.get_vision_models("does-not-exist") == []
    assert pm.is_supported_provider("does-not-exist") is False
    assert pm.provider_supports_vision("does-not-exist") is False
    assert pm.is_supported_text_model("does-not-exist", "gpt-4o") is False
    assert pm.is_supported_vision_model("does-not-exist", "gpt-4o") is False


@pytest.mark.parametrize("bad", [None, "", "   ", 123, [], {}])
def test_malformed_input_is_safe(bad):
    assert pm.get_text_models(bad) == []
    assert pm.get_vision_models(bad) == []
    assert pm.is_supported_provider(bad) is False
    assert pm.provider_supports_vision(bad) is False
    assert pm.is_supported_text_model(bad, bad) is False
    assert pm.is_supported_vision_model("openai", bad) is False


# ---------------------------------------------------------------------------
# Convenience re-export through app.config
# ---------------------------------------------------------------------------


def test_reexported_via_config():
    from app import config

    assert config.SUPPORTED_PROVIDER_MODELS is pm.SUPPORTED_PROVIDER_MODELS
