"""API Gate.

Validates configuration before downstream screens are enabled. Surfaces the
precise status strings from the build plan. Resolves and validates the
configured text provider — one of OpenAI, Anthropic, Groq, or Gemini — by
checking provider support, API-key presence, and model presence.
Configuration-level checks only: no expensive or live API calls are made here
unless ``live=True`` is requested. A lightweight format-only placeholder check
is applied to the Anthropic key specifically (``sk-ant-`` prefix); the other
providers rely on the optional live reachability check.

The API key value is never logged, displayed, or included in error output.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings


_SUPPORTED_PROVIDERS = ("anthropic", "openai", "groq", "gemini")


class ApiGateError(str):
    """Status strings returned by the gate. Mirrors the build plan exactly."""

    NO_API_ADDED = "NO API ADDED"
    INVALID_API_KEY = "INVALID API KEY"
    API_QUOTA_EXCEEDED = "API QUOTA EXCEEDED"
    API_CONNECTION_FAILED = "API CONNECTION FAILED"
    VISION_MODEL_NOT_AVAILABLE = "VISION MODEL NOT AVAILABLE"
    MODEL_NOT_CONFIGURED = "MODEL NOT CONFIGURED"
    API_OK = "API OK"


@dataclass(frozen=True)
class CapabilityTestResult:
    status: str
    model: str | None = None
    provider: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == ApiGateError.API_OK

    @property
    def error(self) -> str | None:
        return None if self.ok else self.status


def _resolve_provider(provider: str | None, settings: Settings) -> str:
    raw = provider if provider is not None else settings.llm_provider
    return (raw or "").strip().lower()


def _resolve_api_key(api_key: str | None, provider: str, settings: Settings) -> str | None:
    if api_key is not None:
        key = api_key.strip()
        return key or None
    if provider == "anthropic":
        return settings.anthropic_api_key
    if provider == "openai":
        return settings.openai_api_key
    if provider == "groq":
        return settings.groq_api_key
    if provider == "gemini":
        return settings.gemini_api_key
    return None


def _resolve_model(model: str | None, provider: str, settings: Settings) -> str | None:
    if model is not None:
        m = model.strip()
        return m or None
    if provider == "anthropic":
        return settings.anthropic_model
    if provider == "openai":
        return settings.openai_model
    if provider == "groq":
        return settings.groq_model
    if provider == "gemini":
        return settings.gemini_model
    return None


def _anthropic_key_format_ok(api_key: str) -> bool:
    """Cheap structural check — no network call.

    The Anthropic console issues keys prefixed with `sk-ant-`. This is a
    format sanity check only; it does not prove the key is live.
    """
    key = api_key.strip()
    return key.startswith("sk-ant-") and len(key) >= 20


def run_capability_test(
    api_key: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    settings: Settings | None = None,
    *,
    live: bool = False,
) -> CapabilityTestResult:
    """Run configuration-level checks against the resolved provider.

    Returns a CapabilityTestResult whose `status` is one of the build-plan
    status strings. Never raises on bad input; never echoes the API key.

    When ``live=True``, after the offline checks succeed, route through
    :func:`app.services.llm_client.call_text_llm` with task ``api_check``
    and the prompt ``"Reply with exactly: API OK"``. The live response
    only has to be received — content matching is tolerant — so this
    works as a lightweight reachability check.
    """
    settings = settings if settings is not None else get_settings()
    resolved_provider = _resolve_provider(provider, settings)

    if not resolved_provider:
        return CapabilityTestResult(status=ApiGateError.MODEL_NOT_CONFIGURED)

    if resolved_provider not in _SUPPORTED_PROVIDERS:
        return CapabilityTestResult(
            status=ApiGateError.MODEL_NOT_CONFIGURED,
            provider=resolved_provider,
        )

    resolved_key = _resolve_api_key(api_key, resolved_provider, settings)
    if not resolved_key:
        return CapabilityTestResult(
            status=ApiGateError.NO_API_ADDED,
            provider=resolved_provider,
        )

    resolved_model = _resolve_model(model, resolved_provider, settings)
    if not resolved_model:
        return CapabilityTestResult(
            status=ApiGateError.MODEL_NOT_CONFIGURED,
            provider=resolved_provider,
        )

    if resolved_provider == "anthropic" and not _anthropic_key_format_ok(resolved_key):
        return CapabilityTestResult(
            status=ApiGateError.INVALID_API_KEY,
            provider=resolved_provider,
            model=resolved_model,
        )

    if live:
        return _run_live_check(
            api_key=resolved_key,
            model=resolved_model,
            provider=resolved_provider,
            settings=settings,
        )

    return CapabilityTestResult(
        status=ApiGateError.API_OK,
        provider=resolved_provider,
        model=resolved_model,
    )


# ---------------------------------------------------------------------------
# Live reachability check
# ---------------------------------------------------------------------------


_LIVE_STATUS_MAP: dict[str, str] = {
    # llm_client.STATUS_* → ApiGateError.*
    "ok": ApiGateError.API_OK,
    "no_api": ApiGateError.NO_API_ADDED,
    "invalid_key": ApiGateError.INVALID_API_KEY,
    "quota": ApiGateError.API_QUOTA_EXCEEDED,
    # A context-window overflow is a prompt-size problem, NOT a billing
    # quota; it must never be reported as "quota exceeded". For the tiny
    # api_check ping it also proves the key/endpoint work, so the gate
    # treats it as reachable.
    "context_overflow": ApiGateError.API_OK,
    "connection": ApiGateError.API_CONNECTION_FAILED,
    "model_missing": ApiGateError.MODEL_NOT_CONFIGURED,
    "unsupported_provider": ApiGateError.MODEL_NOT_CONFIGURED,
    # parse_error and generic exception both indicate the call reached
    # the provider but didn't come back clean — treat as connection issue.
    "parse_error": ApiGateError.API_CONNECTION_FAILED,
    # A truncated ping reply still proves the provider is reachable and the
    # key works (the api_check call only needs *a* response).
    "truncated": ApiGateError.API_OK,
    "exception": ApiGateError.API_CONNECTION_FAILED,
    "skipped": ApiGateError.API_CONNECTION_FAILED,
}


def _run_live_check(
    *,
    api_key: str,
    model: str,
    provider: str,
    settings: Settings,
) -> CapabilityTestResult:
    """Lightweight real-API check via the central llm_client."""
    # Import lazily to avoid a circular import at module load.
    from app.services import llm_client

    result = llm_client.call_text_llm(
        task_name="api_check",
        system_prompt="",
        user_prompt="Reply with exactly: API OK",
        expected_json=False,
        max_tokens=16,
        settings=settings,
    )
    if result.success:
        return CapabilityTestResult(
            status=ApiGateError.API_OK,
            provider=provider,
            model=model,
        )
    mapped = _LIVE_STATUS_MAP.get(result.status, ApiGateError.API_CONNECTION_FAILED)
    return CapabilityTestResult(
        status=mapped,
        provider=provider,
        model=model,
    )
