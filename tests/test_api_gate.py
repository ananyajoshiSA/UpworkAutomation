"""Tests for the API gate.

Configuration-level checks only — no live API calls. The gate must never
echo the API key, and must return the exact status strings from the
build plan.
"""

from __future__ import annotations

from app.config import Settings
from app.services.api_gate import (
    ApiGateError,
    CapabilityTestResult,
    run_capability_test,
)


VALID_ANTHROPIC_KEY = "sk-ant-test-1234567890abcdef"


def _settings(**overrides) -> Settings:
    base = dict(
        llm_provider="anthropic",
        anthropic_api_key=None,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
    )
    base.update(overrides)
    return Settings(**base)


def test_error_state_strings_match_build_plan():
    assert ApiGateError.NO_API_ADDED == "NO API ADDED"
    assert ApiGateError.INVALID_API_KEY == "INVALID API KEY"
    assert ApiGateError.API_QUOTA_EXCEEDED == "API QUOTA EXCEEDED"
    assert ApiGateError.API_CONNECTION_FAILED == "API CONNECTION FAILED"
    assert ApiGateError.VISION_MODEL_NOT_AVAILABLE == "VISION MODEL NOT AVAILABLE"
    assert ApiGateError.MODEL_NOT_CONFIGURED == "MODEL NOT CONFIGURED"
    assert ApiGateError.API_OK == "API OK"


def test_missing_api_key_returns_no_api_added():
    result = run_capability_test(settings=_settings(anthropic_api_key=None))
    assert isinstance(result, CapabilityTestResult)
    assert result.status == "NO API ADDED"
    assert result.ok is False
    assert result.error == "NO API ADDED"


def test_missing_model_returns_model_not_configured():
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY, anthropic_model="")
    result = run_capability_test(settings=settings, model="")
    assert result.status == "MODEL NOT CONFIGURED"
    assert result.ok is False


def test_unsupported_provider_returns_model_not_configured():
    # "cohere" is not one of the four supported providers (openai, anthropic,
    # groq, gemini), so the gate reports it as unconfigured.
    settings = _settings(llm_provider="cohere", anthropic_api_key=VALID_ANTHROPIC_KEY)
    result = run_capability_test(settings=settings)
    assert result.status == "MODEL NOT CONFIGURED"
    assert result.ok is False


def test_valid_config_returns_api_ok():
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)
    result = run_capability_test(settings=settings)
    assert result.status == "API OK"
    assert result.ok is True
    assert result.error is None
    assert result.provider == "anthropic"
    assert result.model == "claude-sonnet-4-6"


def test_invalid_anthropic_key_format_returns_invalid_api_key():
    settings = _settings(anthropic_api_key="not-a-real-key")
    result = run_capability_test(settings=settings)
    assert result.status == "INVALID API KEY"
    assert result.ok is False


def test_api_key_is_never_in_result_repr():
    settings = _settings(anthropic_api_key=VALID_ANTHROPIC_KEY)
    result = run_capability_test(settings=settings)
    assert VALID_ANTHROPIC_KEY not in repr(result)
