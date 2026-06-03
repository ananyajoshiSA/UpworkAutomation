"""Static reference list of supported LLM providers and common model names.

This module is a *reference / helper* layer, **not** an enforcement layer. It
maps each provider the app supports (see :data:`app.config.PROVIDER_OPTIONS`)
to a curated set of plausible, commonly used model names — split into text and
vision-capable models — plus a short human-readable note per provider.

Why this exists
---------------
The Setup screen already lets operators pick any provider and type any model
name, and the runtime routes calls based on the resolved ``Settings`` object
(see ``app.config`` and ``app.services.llm_client``). **Nothing here changes
that flow.** This list is intended for *future* use by:

* the Setup dropdowns (offer suggested model names instead of a free-text box),
* documentation (the README "Supported Provider Model Reference" section), and
* optional, non-blocking validation / hints.

Suggestions, not rules
----------------------
These model names are *suggestions*. A model that is not listed here is **not**
rejected — if the operator's provider account supports it, the app will still
use it. Strict validation (hard-blocking unknown models) is intentionally not
implemented; a caller that wants it can layer it on top of the
``is_supported_*`` helpers below.

Vision support
--------------
``vision_models`` lists models that can accept image input (used to read job
screenshots). Groq is treated as text-only in this app — its ``vision_models``
list is empty — mirroring ``app.config._SUPPORTED_VISION_PROVIDERS``.
"""

from __future__ import annotations


# Curated, commonly-used model names per provider. Reference data only — see
# the module docstring. Keys are the lowercase provider identifiers used
# everywhere else in the app (matching ``app.config.PROVIDER_OPTIONS``).
SUPPORTED_PROVIDER_MODELS: dict[str, dict[str, object]] = {
    "openai": {
        "text_models": [
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o",
            "gpt-4o-mini",
        ],
        "vision_models": [
            "gpt-4o",
            "gpt-4.1",
            "gpt-4o-mini",
        ],
        "notes": (
            "OpenAI offers both text and vision-capable models. The 4o and "
            "4.1 families accept image input and can read job screenshots."
        ),
    },
    "anthropic": {
        "text_models": [
            "claude-sonnet-4-6",
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
        ],
        "vision_models": [
            "claude-sonnet-4-6",
            "claude-3-5-sonnet-latest",
        ],
        "notes": (
            "Anthropic Claude models support both text and vision. "
            "Sonnet-class models are recommended for screenshot extraction."
        ),
    },
    "groq": {
        "text_models": [
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ],
        "vision_models": [],
        "notes": (
            "Groq is treated as text-only in this app unless vision support "
            "is added later."
        ),
    },
    "gemini": {
        "text_models": [
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
        ],
        "vision_models": [
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.0-flash",
        ],
        "notes": (
            "Gemini models are multimodal and support both text and vision "
            "(image) input."
        ),
    },
}


def _normalize_provider(provider: str | None) -> str:
    """Lowercase, whitespace-trimmed provider key (``""`` when not a string)."""
    if not isinstance(provider, str):
        return ""
    return provider.strip().lower()


def _normalize_model(model: str | None) -> str:
    """Whitespace-trimmed model name (``""`` when not a string)."""
    if not isinstance(model, str):
        return ""
    return model.strip()


def get_supported_providers() -> list[str]:
    """Return the provider identifiers in the reference list, in display order."""
    return list(SUPPORTED_PROVIDER_MODELS.keys())


def is_supported_provider(provider: str) -> bool:
    """True when ``provider`` (case-insensitive) is in the reference list."""
    return _normalize_provider(provider) in SUPPORTED_PROVIDER_MODELS


def get_text_models(provider: str) -> list[str]:
    """Suggested text model names for ``provider`` (``[]`` when unknown).

    Returns a fresh copy so callers cannot mutate the reference data.
    """
    entry = SUPPORTED_PROVIDER_MODELS.get(_normalize_provider(provider))
    if not entry:
        return []
    return list(entry.get("text_models", []))


def get_vision_models(provider: str) -> list[str]:
    """Suggested vision model names for ``provider``.

    Returns ``[]`` when the provider is unknown *or* text-only (e.g. Groq).
    Returns a fresh copy so callers cannot mutate the reference data.
    """
    entry = SUPPORTED_PROVIDER_MODELS.get(_normalize_provider(provider))
    if not entry:
        return []
    return list(entry.get("vision_models", []))


def provider_supports_vision(provider: str) -> bool:
    """True when ``provider`` has at least one suggested vision model."""
    return bool(get_vision_models(provider))


def is_supported_text_model(provider: str, model: str) -> bool:
    """True when ``model`` is a suggested *text* model for ``provider``.

    Matching is case-insensitive and whitespace-tolerant. This is a reference
    check only — a ``False`` result does **not** mean the model is unusable,
    only that it is not in the curated suggestion list.
    """
    target = _normalize_model(model).lower()
    if not target:
        return False
    return any(target == m.lower() for m in get_text_models(provider))


def is_supported_vision_model(provider: str, model: str) -> bool:
    """True when ``model`` is a suggested *vision* model for ``provider``.

    Same matching and "reference only" semantics as
    :func:`is_supported_text_model`.
    """
    target = _normalize_model(model).lower()
    if not target:
        return False
    return any(target == m.lower() for m in get_vision_models(provider))
