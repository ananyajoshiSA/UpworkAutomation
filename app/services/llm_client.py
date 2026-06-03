"""Central LLM client.

This is the ONLY module that talks to Anthropic, OpenAI, Groq, or Gemini
directly (Groq for text only). All other services route through
:func:`call_text_llm` or :func:`call_vision_llm` so:

* every API call is captured in :data:`st.session_state.api_usage_log`
  with a stable ``task_name``,
* the UI can show a single "API Usage Status" panel that knows which
  stages really used the API,
* the API key is never returned, logged, or echoed.

Return value
------------
Every call returns an :class:`LLMCallResult` with::

    success: bool
    task_name: str
    provider: str | None
    model: str | None
    used_api: bool
    response_text: str | None
    response_json: dict | list | None
    error_message: str | None
    timestamp: str          # ISO 8601, UTC
    estimated_input_tokens: int | None
    estimated_output_tokens: int | None
    status: str             # "ok" | "no_api" | "invalid_key" | "quota" |
                            # "connection" | "model_missing" | "parse_error"
                            # | "unsupported_provider" | "skipped" | "exception"

``used_api`` is true only when bytes actually went over the wire (or were
attempted). Callers that fall back to local logic set it to ``False`` via
:func:`record_local_use`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.config import (
    GROQ_VISION_UNSUPPORTED_MESSAGE,
    LLM_TASK_NAMES,
    Settings,
    get_settings,
)
from app.utils.logging_utils import get_logger
from app.utils.redaction import (
    API_KEY_PATTERNS as _API_KEY_PATTERNS,
    ORG_ID_PATTERNS as _ORG_ID_PATTERNS,
    redact as _redact,
)


_logger: logging.Logger = get_logger("upwork_strategist.llm_client")


# Stable status codes — surfaced verbatim by the UI / api_gate.
STATUS_OK = "ok"
STATUS_NO_API = "no_api"
STATUS_INVALID_KEY = "invalid_key"
STATUS_QUOTA = "quota"
STATUS_CONTEXT_OVERFLOW = "context_overflow"
STATUS_CONNECTION = "connection"
STATUS_MODEL_MISSING = "model_missing"
STATUS_PARSE_ERROR = "parse_error"
STATUS_TRUNCATED = "truncated"
STATUS_UNSUPPORTED_PROVIDER = "unsupported_provider"
STATUS_SKIPPED = "skipped"
STATUS_EXCEPTION = "exception"


_SUPPORTED_PROVIDERS = ("anthropic", "openai", "groq", "gemini")

# Providers that can read images. Groq is text-only here, so it is excluded
# and handled with a dedicated, actionable message in ``call_vision_llm``.
_SUPPORTED_VISION_PROVIDERS = ("anthropic", "openai", "gemini")


# Per-request HTTP timeout (seconds) applied to every provider client so a
# hung socket cannot freeze the synchronous Streamlit call indefinitely.
# Overridable via the LLM_REQUEST_TIMEOUT env var.
def _request_timeout() -> float:
    raw = os.getenv("LLM_REQUEST_TIMEOUT", "").strip()
    try:
        value = float(raw)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return 60.0


# Bounded transient-failure retry. Only STATUS_CONNECTION and genuine
# rate-limit (STATUS_QUOTA) are retried; auth/model/parse/context errors
# are never retried (retrying cannot help and would waste calls/money).
_MAX_ATTEMPTS = 3
_RETRYABLE_STATUSES = (STATUS_CONNECTION, STATUS_QUOTA)
# Backoff base in seconds (exponential: base, base*2, …). Kept small so the
# synchronous UI is never blocked for long.
_RETRY_BACKOFF_BASE = 0.5


# Finish/stop reasons that mean the model was cut off by the token budget.
_TRUNCATION_REASONS = frozenset(
    {"length", "max_tokens", "max_output_tokens"}
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class LLMCallResult:
    success: bool
    task_name: str
    provider: Optional[str] = None
    model: Optional[str] = None
    used_api: bool = False
    response_text: Optional[str] = None
    response_json: Any = None
    error_message: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    estimated_input_tokens: Optional[int] = None
    estimated_output_tokens: Optional[int] = None
    status: str = STATUS_OK
    duration_seconds: Optional[float] = None

    def to_log_entry(self) -> dict:
        """Return a dict that is safe to display in the UI / usage log."""
        return {
            "timestamp": self.timestamp,
            "task_name": self.task_name,
            "provider": self.provider,
            "model": self.model,
            "used_api": self.used_api,
            "status": self.status,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            # response_text / response_json are intentionally NOT stored
            # in the usage log — that log is metadata-only.
        }


# ---------------------------------------------------------------------------
# Session-state usage log
# ---------------------------------------------------------------------------


def _record_to_session(entry: dict) -> None:
    """Append ``entry`` to ``st.session_state.api_usage_log`` if available."""
    try:
        import streamlit as st  # type: ignore
    except Exception:  # pragma: no cover - streamlit not always loaded
        return
    try:
        log = st.session_state.get("api_usage_log")
        if log is None:
            log = []
            st.session_state["api_usage_log"] = log
        log.append(entry)
    except Exception:  # pragma: no cover - not running inside a session
        return


def record_local_use(task_name: str, *, note: str = "") -> None:
    """Record a non-API stage so the UI can show LOCAL PLACEHOLDER clearly.

    Callers that intentionally use deterministic local logic must call
    this so the API Usage panel can flag the stage.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_name": task_name,
        "provider": None,
        "model": None,
        "used_api": False,
        "status": "local_placeholder",
        "error_message": note or None,
        "duration_seconds": None,
        "estimated_input_tokens": None,
        "estimated_output_tokens": None,
    }
    _record_to_session(entry)
    _logger.info(
        "stage=%s used_api=false status=local_placeholder note=%s",
        task_name,
        note or "-",
    )


def get_usage_log() -> list[dict]:
    """Return the current session's usage log (or an empty list)."""
    try:
        import streamlit as st  # type: ignore
    except Exception:  # pragma: no cover
        return []
    try:
        return list(st.session_state.get("api_usage_log") or [])
    except Exception:  # pragma: no cover
        return []


def reset_usage_log() -> None:
    try:
        import streamlit as st  # type: ignore
    except Exception:  # pragma: no cover
        return
    try:
        st.session_state["api_usage_log"] = []
    except Exception:  # pragma: no cover
        return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_task_name(task_name: str) -> None:
    if task_name not in LLM_TASK_NAMES:
        _logger.warning("unknown llm task_name=%s", task_name)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Rough heuristic: ~4 characters per token. Cheap, never exact.
    return max(1, len(text) // 4)


def _resolve_key(settings: Settings, provider: Optional[str] = None) -> Optional[str]:
    target = (provider or settings.llm_provider or "").lower()
    return {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "groq": settings.groq_api_key,
        "gemini": settings.gemini_api_key,
    }.get(target)


def _resolve_model(settings: Settings, *, vision: bool = False) -> Optional[str]:
    if vision:
        # Vision model resolution (VISION_MODEL, or the text model when it is
        # itself vision-capable) lives on Settings so the rule is shared.
        return settings.active_vision_model
    return {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "groq": settings.groq_model,
        "gemini": settings.gemini_model,
    }.get((settings.llm_provider or "").lower())


def _resolve_provider(settings: Settings, *, vision: bool = False) -> str:
    if vision:
        return settings.active_vision_provider
    return (settings.llm_provider or "").lower()


def _find_json_span(text: str) -> Optional[Any]:
    """Return the first complete JSON value embedded in ``text``.

    Scans from each ``{`` or ``[`` and uses ``json.JSONDecoder.raw_decode``
    to find the first span that decodes cleanly. This replaces the old
    greedy ``(\\{.*\\}|\\[.*\\])`` regex, which spanned from the first
    brace to the LAST one — so prose containing a stray brace, or two
    objects, would defeat it and a perfectly good object would parse as
    ``None``. The scanner finds the *first* balanced, decodable value.
    """
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(text, idx)
        except ValueError:
            continue
        return value
    return None


def _parse_json(text: str) -> Optional[Any]:
    if not text:
        return None
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?", "", candidate).strip()
    candidate = re.sub(r"```$", "", candidate).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        pass
    # Fallback: locate the first balanced, decodable JSON value rather than
    # greedily spanning to the last brace in the string.
    return _find_json_span(candidate)


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def _classify_exception(exc: BaseException) -> str:
    """Map provider SDK exceptions to one of the stable status codes.

    Covers OpenAI/Anthropic/Groq (httpx-based SDKs) and Gemini
    (``google.api_core`` exceptions such as ``PermissionDenied``,
    ``ResourceExhausted``, ``InvalidArgument``, ``NotFound``).
    """
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if (
        "auth" in name
        or "permissiondenied" in name
        or "unauthenticated" in name
        or ("invalid" in name and "key" in msg)
    ):
        return STATUS_INVALID_KEY
    if (
        "authentication" in msg
        or "invalid api key" in msg
        or "api key not valid" in msg
        or "api_key_invalid" in msg
        or "permission denied" in msg
        or "unauthorized" in msg
    ):
        return STATUS_INVALID_KEY
    # Context-window / prompt-size overflow is a DISTINCT condition from a
    # billing/rate-limit quota: the fix is "send less input / raise
    # max_tokens", not "wait or buy credits". Classify it first so it is
    # never mislabeled as STATUS_QUOTA (which would tell the user they are
    # out of credits). Note the parenthesized and-clause — without parens
    # Python's `and` binds tighter than `or`, which silently changed the
    # intended grouping in the original code.
    if (
        "context length" in msg
        or "context_length" in msg
        or "context window" in msg
        or "maximum context" in msg
        or "too many tokens" in msg
        or "token limit" in msg
        or "tokens exceed" in msg
        or ("exceeds the" in msg and "token" in msg)
        or "request too large" in msg
        or "reduce the length" in msg
    ):
        return STATUS_CONTEXT_OVERFLOW
    if (
        "ratelimit" in name
        or "resourceexhausted" in name
        or "quota" in msg
        or "rate limit" in msg
        or "rate_limit" in msg
        or "insufficient" in msg
        or "429" in msg
        or "tokens per minute" in msg
        or "tokens-per-minute" in msg
        or "tpm" in msg
    ):
        return STATUS_QUOTA
    if (
        "connect" in name
        or "timeout" in name
        or "deadlineexceeded" in name
        or "serviceunavailable" in name
        or "internalservererror" in name
        or "network" in msg
        or "connection" in msg
    ):
        return STATUS_CONNECTION
    if "notfound" in name or ("model" in msg and "not" in msg):
        return STATUS_MODEL_MISSING
    return STATUS_EXCEPTION


# Key/org-id patterns now live in app.utils.redaction (single source of
# truth, imported above as _API_KEY_PATTERNS / _ORG_ID_PATTERNS) so the
# sink-level logging Filter and this sanitizer can never drift apart.


def sanitize_error_message(
    message: Optional[str], *, secrets: Iterable[Optional[str]] = ()
) -> Optional[str]:
    """Return ``message`` with API keys and provider organization IDs scrubbed.

    The UI and the API usage log only ever show the sanitized form. Provider
    SDK errors can echo the raw API key (an OpenAI-compatible endpoint emits
    "Incorrect API key provided: sk-..."; Gemini emits "API key not valid.
    Provided: AIza..."), so this redacts, in order:

    * any exact ``secrets`` value the caller supplies (the configured key) —
      catches even unanticipated formats,
    * API-key-shaped tokens for every provider family,
    * provider organization IDs and request slugs.

    Idempotent — safe to call more than once — and never returns a raw key.
    Delegates to :func:`app.utils.redaction.redact` (also used by the
    logging Filter) so there is exactly one definition of "what is a secret".
    """
    return _redact(message, secrets=secrets)


def extend_last_entry(task_name: str, extra: dict) -> None:
    """Merge ``extra`` into the most recent usage-log entry for ``task_name``.

    Used by callers that need to attach task-specific metadata
    (e.g. compact-context size) to the entry the central client wrote.
    The merge is metadata-only — callers must not pass raw prompts,
    proposal text, dossier content, or API keys.

    If no entry yet exists for ``task_name`` (this happens in tests
    that monkey-patch the provider call and bypass the central
    finalize step), a fresh metadata-only entry is appended so the
    caller's attached fields are still recorded.
    """
    try:
        import streamlit as st  # type: ignore
    except Exception:  # pragma: no cover - streamlit not always loaded
        return
    try:
        log = st.session_state.get("api_usage_log")
        if log is None:
            log = []
            st.session_state["api_usage_log"] = log
    except Exception:  # pragma: no cover
        return
    for entry in reversed(log):
        if entry.get("task_name") == task_name:
            entry.update(extra)
            return
    new_entry: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_name": task_name,
        "provider": None,
        "model": None,
        "used_api": False,
        "status": "metadata_only",
        "error_message": None,
        "duration_seconds": None,
        "estimated_input_tokens": None,
        "estimated_output_tokens": None,
    }
    new_entry.update(extra)
    log.append(new_entry)


def _normalize_image(image: Any) -> Optional[tuple[bytes, str]]:
    """Return (raw_bytes, mime) for a Streamlit UploadedFile or bytes-like."""
    if image is None:
        return None
    if isinstance(image, tuple) and len(image) == 2 and isinstance(image[0], (bytes, bytearray)):
        return bytes(image[0]), str(image[1]) or "image/png"
    if isinstance(image, (bytes, bytearray)):
        return bytes(image), "image/png"

    name = getattr(image, "name", "") or ""
    mime = getattr(image, "type", "") or ""
    suffix = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if not mime:
        mime = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
        }.get(suffix, "image/png")

    data: Optional[bytes] = None
    if hasattr(image, "getvalue"):
        try:
            data = image.getvalue()
        except Exception:  # noqa: BLE001
            data = None
    if data is None and hasattr(image, "read"):
        try:
            if hasattr(image, "seek"):
                image.seek(0)
            data = image.read()
        except Exception:  # noqa: BLE001
            data = None
    if data is None:
        return None
    return data, mime


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


def _anthropic_text(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=api_key, timeout=_request_timeout())
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=system_prompt or "",
        messages=[{"role": "user", "content": user_prompt}],
    )
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    out_text = "\n".join(parts).strip()
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "input_tokens", None) if usage else None
    out_tokens = getattr(usage, "output_tokens", None) if usage else None
    finish = getattr(response, "stop_reason", None)
    return out_text, in_tokens, out_tokens, finish


def _openai_text(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key, timeout=_request_timeout())
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            *([{"role": "system", "content": system_prompt}] if system_prompt else []),
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
    }
    if expected_json:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    out_text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    out_tokens = getattr(usage, "completion_tokens", None) if usage else None
    finish = getattr(response.choices[0], "finish_reason", None)
    return out_text, in_tokens, out_tokens, finish


def _groq_text(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """Groq text completion (text-only — Groq has no image input).

    Groq exposes an OpenAI-compatible Chat Completions API. We prefer the
    official ``groq`` SDK when installed and otherwise fall back to the
    already-bundled ``openai`` SDK pointed at Groq's compatibility endpoint,
    so the provider works even when ``groq`` was not separately installed.
    The API key is passed to the client only and never logged.
    """
    messages = [
        *([{"role": "system", "content": system_prompt}] if system_prompt else []),
        {"role": "user", "content": user_prompt},
    ]
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0}
    if expected_json:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        from groq import Groq  # type: ignore

        client = Groq(api_key=api_key, timeout=_request_timeout())
    except ModuleNotFoundError:
        # OpenAI-compatible fallback (the openai SDK ships with this app).
        from openai import OpenAI  # type: ignore

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=_request_timeout(),
        )

    response = client.chat.completions.create(**kwargs)
    out_text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    out_tokens = getattr(usage, "completion_tokens", None) if usage else None
    finish = getattr(response.choices[0], "finish_reason", None)
    return out_text, in_tokens, out_tokens, finish


def _gemini_response_text(response: Any) -> str:
    """Best-effort text extraction from a Gemini response.

    ``response.text`` raises when the only candidate was blocked or empty, so
    we fall back to manually concatenating any text parts on the candidates.
    """
    try:
        text = getattr(response, "text", None)
        if text:
            return text.strip()
    except Exception:  # noqa: BLE001 - blocked/empty candidate; fall back below
        pass
    parts: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            piece = getattr(part, "text", None)
            if piece:
                parts.append(piece)
    return "\n".join(parts).strip()


def _gemini_usage(response: Any) -> tuple[Optional[int], Optional[int]]:
    usage = getattr(response, "usage_metadata", None)
    in_tokens = getattr(usage, "prompt_token_count", None) if usage else None
    out_tokens = getattr(usage, "candidates_token_count", None) if usage else None
    return in_tokens, out_tokens


def _gemini_finish_reason(response: Any) -> Optional[str]:
    """Return the first candidate's finish reason as a lowercase string.

    Gemini exposes ``finish_reason`` as an enum (e.g. ``MAX_TOKENS``); we
    normalize it to a string so the truncation check can recognize it.
    """
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is None:
            continue
        name = getattr(reason, "name", None)
        return str(name if name is not None else reason).lower()
    return None


def _gemini_model_obj(
    *, api_key: str, model: str, system_prompt: str, expected_json: bool, max_tokens: int
):
    """Build a configured ``genai.GenerativeModel``. Key is never logged."""
    import google.generativeai as genai  # type: ignore

    genai.configure(api_key=api_key)
    generation_config: dict[str, Any] = {
        "temperature": 0,
        "max_output_tokens": max_tokens,
    }
    if expected_json:
        generation_config["response_mime_type"] = "application/json"
    return genai.GenerativeModel(
        model_name=model,
        system_instruction=system_prompt or None,
        generation_config=generation_config,
    )


def _gemini_text(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """Gemini text completion via the google-generativeai SDK.

    When ``expected_json`` is set we both request a JSON response MIME type and
    reinforce strict-JSON output in the prompt.
    """
    gen_model = _gemini_model_obj(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        expected_json=expected_json,
        max_tokens=max_tokens,
    )
    prompt = user_prompt
    if expected_json:
        prompt = (
            f"{user_prompt}\n\nReturn ONLY a single valid JSON object. "
            "Do not include any prose or Markdown code fences."
        )
    # A per-request timeout is only honored when passed to generate_content
    # via request_options — a configure-/model-level timeout is ignored.
    response = gen_model.generate_content(
        prompt, request_options={"timeout": _request_timeout()}
    )
    out_text = _gemini_response_text(response)
    in_tokens, out_tokens = _gemini_usage(response)
    finish = _gemini_finish_reason(response)
    return out_text, in_tokens, out_tokens, finish


def _anthropic_vision(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[bytes, str]],
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    import anthropic  # type: ignore

    client = anthropic.Anthropic(api_key=api_key, timeout=_request_timeout())
    content: list[dict[str, Any]] = []
    for data, mime in images:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
        )
    if user_prompt:
        content.append({"type": "text", "text": user_prompt})
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        system=system_prompt or "",
        messages=[{"role": "user", "content": content}],
    )
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    out_text = "\n".join(parts).strip()
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "input_tokens", None) if usage else None
    out_tokens = getattr(usage, "output_tokens", None) if usage else None
    finish = getattr(response, "stop_reason", None)
    return out_text, in_tokens, out_tokens, finish


def _openai_vision(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[bytes, str]],
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=api_key, timeout=_request_timeout())
    content: list[dict[str, Any]] = []
    if user_prompt:
        content.append({"type": "text", "text": user_prompt})
    for data, mime in images:
        content.append({"type": "image_url", "image_url": {"url": _data_url(data, mime)}})

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            *([{"role": "system", "content": system_prompt}] if system_prompt else []),
            {"role": "user", "content": content},
        ],
        "temperature": 0,
    }
    if expected_json:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    out_text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    in_tokens = getattr(usage, "prompt_tokens", None) if usage else None
    out_tokens = getattr(usage, "completion_tokens", None) if usage else None
    finish = getattr(response.choices[0], "finish_reason", None)
    return out_text, in_tokens, out_tokens, finish


def _gemini_vision(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    images: list[tuple[bytes, str]],
    expected_json: bool,
    max_tokens: int,
) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """Gemini multimodal completion: prompt text plus inline image blobs."""
    gen_model = _gemini_model_obj(
        api_key=api_key,
        model=model,
        system_prompt=system_prompt,
        expected_json=expected_json,
        max_tokens=max_tokens,
    )
    content: list[Any] = []
    if user_prompt:
        content.append(user_prompt)
    for data, mime in images:
        # google-generativeai accepts inline blobs as {"mime_type", "data"}.
        content.append({"mime_type": mime, "data": data})
    response = gen_model.generate_content(
        content, request_options={"timeout": _request_timeout()}
    )
    out_text = _gemini_response_text(response)
    in_tokens, out_tokens = _gemini_usage(response)
    finish = _gemini_finish_reason(response)
    return out_text, in_tokens, out_tokens, finish


# ---------------------------------------------------------------------------
# Adapter invocation helpers (retry + tolerant unpack)
# ---------------------------------------------------------------------------


def _normalize_adapter_return(ret: Any) -> tuple[str, Optional[int], Optional[int], Optional[str]]:
    """Accept a 3- or 4-tuple from a provider adapter.

    Real adapters return ``(text, in_tokens, out_tokens, finish_reason)``;
    older/monkeypatched fakes return the legacy 3-tuple. This keeps both
    working without forcing every test to add a finish reason.
    """
    if isinstance(ret, tuple) and len(ret) == 4:
        return ret
    if isinstance(ret, tuple) and len(ret) == 3:
        text, in_toks, out_toks = ret
        return text, in_toks, out_toks, None
    # Unexpected shape — treat as an empty text response.
    return str(ret or ""), None, None, None


def _invoke_with_retry(adapter, *, api_key: str, **kwargs):
    """Call ``adapter`` with a bounded transient-failure retry.

    Retries ONLY transient conditions (STATUS_CONNECTION and genuine
    rate-limit STATUS_QUOTA) with exponential backoff; never retries
    auth / model-missing / context-overflow / parse errors (a retry
    cannot help and would waste calls and money). Re-raises the final
    exception so the caller's existing except-block classifies it.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return _normalize_adapter_return(
                adapter(api_key=api_key, **kwargs)
            )
        except ModuleNotFoundError:
            raise  # missing SDK — surfaced verbatim, never retried
        except Exception as exc:  # noqa: BLE001
            status = _classify_exception(exc)
            if status in _RETRYABLE_STATUSES and attempt < _MAX_ATTEMPTS:
                _logger.info(
                    "transient llm failure status=%s attempt=%s/%s — retrying",
                    status,
                    attempt,
                    _MAX_ATTEMPTS,
                )
                time.sleep(_RETRY_BACKOFF_BASE * (2 ** (attempt - 1)))
                continue
            raise


def _is_truncated(finish_reason: Optional[str]) -> bool:
    if not finish_reason:
        return False
    return str(finish_reason).strip().lower() in _TRUNCATION_REASONS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def call_text_llm(
    task_name: str,
    system_prompt: str,
    user_prompt: str,
    *,
    expected_json: bool = False,
    max_tokens: int = 1024,
    settings: Optional[Settings] = None,
) -> LLMCallResult:
    """Send a single text completion to the configured provider."""
    _validate_task_name(task_name)
    settings = settings or get_settings()
    provider = _resolve_provider(settings)
    model = _resolve_model(settings)
    api_key = _resolve_key(settings, provider)

    started = time.monotonic()

    if provider not in _SUPPORTED_PROVIDERS:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider or None,
            model=model,
            used_api=False,
            error_message="Provider not configured",
            status=STATUS_UNSUPPORTED_PROVIDER,
        )
        _finalize(result, started)
        return result

    if not api_key:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=False,
            error_message="No API key configured for provider",
            status=STATUS_NO_API,
        )
        _finalize(result, started)
        return result

    if not model:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=None,
            used_api=False,
            error_message="Model not configured",
            status=STATUS_MODEL_MISSING,
        )
        _finalize(result, started)
        return result

    try:
        _text_adapter = {
            "anthropic": _anthropic_text,
            "openai": _openai_text,
            "groq": _groq_text,
            "gemini": _gemini_text,
        }[provider]
        text, in_toks, out_toks, finish_reason = _invoke_with_retry(
            _text_adapter,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expected_json=expected_json,
            max_tokens=max_tokens,
        )
    except ModuleNotFoundError as exc:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=True,
            error_message=f"Provider SDK missing: {exc.name}",
            status=STATUS_CONNECTION,
        )
        _finalize(result, started)
        return result
    except Exception as exc:  # noqa: BLE001
        status = _classify_exception(exc)
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=True,
            error_message=sanitize_error_message(
                f"{exc.__class__.__name__}: {exc}", secrets=(api_key,)
            )[:200],
            status=status,
        )
        _finalize(result, started)
        return result

    response_json: Any = None
    if expected_json:
        response_json = _parse_json(text)
        if response_json is None:
            # Distinguish an output cut off by the token budget (recoverable
            # by raising max_tokens) from genuinely malformed JSON.
            truncated = _is_truncated(finish_reason)
            result = LLMCallResult(
                success=False,
                task_name=task_name,
                provider=provider,
                model=model,
                used_api=True,
                response_text=text,
                error_message=(
                    "Provider response was truncated by the output token "
                    "limit; raise max_tokens and retry."
                    if truncated
                    else "Provider response was not valid JSON"
                ),
                estimated_input_tokens=in_toks if in_toks is not None
                else _estimate_tokens(system_prompt + user_prompt),
                estimated_output_tokens=out_toks if out_toks is not None
                else _estimate_tokens(text),
                status=STATUS_TRUNCATED if truncated else STATUS_PARSE_ERROR,
            )
            _finalize(result, started)
            return result

    result = LLMCallResult(
        success=True,
        task_name=task_name,
        provider=provider,
        model=model,
        used_api=True,
        response_text=text,
        response_json=response_json,
        estimated_input_tokens=in_toks if in_toks is not None
        else _estimate_tokens(system_prompt + user_prompt),
        estimated_output_tokens=out_toks if out_toks is not None
        else _estimate_tokens(text),
        status=STATUS_OK,
    )
    _finalize(result, started)
    return result


def call_vision_llm(
    task_name: str,
    system_prompt: str,
    image_inputs: Iterable,
    user_prompt: str = "",
    *,
    expected_json: bool = False,
    max_tokens: int = 2048,
    settings: Optional[Settings] = None,
) -> LLMCallResult:
    """Send a vision request to the configured provider."""
    _validate_task_name(task_name)
    settings = settings or get_settings()
    provider = _resolve_provider(settings, vision=True)
    model = _resolve_model(settings, vision=True)
    api_key = _resolve_key(settings, provider)

    started = time.monotonic()

    images: list[tuple[bytes, str]] = []
    for raw in image_inputs or []:
        norm = _normalize_image(raw)
        if norm is not None:
            images.append(norm)

    if not images:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider or None,
            model=model,
            used_api=False,
            error_message="No image inputs supplied",
            status=STATUS_SKIPPED,
        )
        _finalize(result, started)
        return result

    # Groq is text-only here — surface the dedicated, actionable message
    # rather than a generic "provider not configured".
    if provider == "groq":
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=False,
            error_message=GROQ_VISION_UNSUPPORTED_MESSAGE,
            status=STATUS_UNSUPPORTED_PROVIDER,
        )
        _finalize(result, started)
        return result

    if provider not in _SUPPORTED_VISION_PROVIDERS:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider or None,
            model=model,
            used_api=False,
            error_message="Provider not configured",
            status=STATUS_UNSUPPORTED_PROVIDER,
        )
        _finalize(result, started)
        return result

    if not api_key:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=False,
            error_message="No API key configured for provider",
            status=STATUS_NO_API,
        )
        _finalize(result, started)
        return result

    if not model:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=None,
            used_api=False,
            error_message="Model not configured",
            status=STATUS_MODEL_MISSING,
        )
        _finalize(result, started)
        return result

    try:
        _vision_adapter = {
            "anthropic": _anthropic_vision,
            "openai": _openai_vision,
            "gemini": _gemini_vision,
        }[provider]
        text, in_toks, out_toks, finish_reason = _invoke_with_retry(
            _vision_adapter,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            images=images,
            expected_json=expected_json,
            max_tokens=max_tokens,
        )
    except ModuleNotFoundError as exc:
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=True,
            error_message=f"Provider SDK missing: {exc.name}",
            status=STATUS_CONNECTION,
        )
        _finalize(result, started)
        return result
    except Exception as exc:  # noqa: BLE001
        status = _classify_exception(exc)
        result = LLMCallResult(
            success=False,
            task_name=task_name,
            provider=provider,
            model=model,
            used_api=True,
            error_message=sanitize_error_message(
                f"{exc.__class__.__name__}: {exc}", secrets=(api_key,)
            )[:200],
            status=status,
        )
        _finalize(result, started)
        return result

    response_json: Any = None
    if expected_json:
        response_json = _parse_json(text)
        if response_json is None:
            truncated = _is_truncated(finish_reason)
            result = LLMCallResult(
                success=False,
                task_name=task_name,
                provider=provider,
                model=model,
                used_api=True,
                response_text=text,
                error_message=(
                    "Provider response was truncated by the output token "
                    "limit; raise max_tokens and retry."
                    if truncated
                    else "Provider response was not valid JSON"
                ),
                estimated_input_tokens=in_toks if in_toks is not None
                else _estimate_tokens(system_prompt + user_prompt),
                estimated_output_tokens=out_toks if out_toks is not None
                else _estimate_tokens(text),
                status=STATUS_TRUNCATED if truncated else STATUS_PARSE_ERROR,
            )
            _finalize(result, started)
            return result

    result = LLMCallResult(
        success=True,
        task_name=task_name,
        provider=provider,
        model=model,
        used_api=True,
        response_text=text,
        response_json=response_json,
        estimated_input_tokens=in_toks if in_toks is not None
        else _estimate_tokens(system_prompt + user_prompt),
        estimated_output_tokens=out_toks if out_toks is not None
        else _estimate_tokens(text),
        status=STATUS_OK,
    )
    _finalize(result, started)
    return result


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------


def _finalize(result: LLMCallResult, started_monotonic: float) -> None:
    result.duration_seconds = round(time.monotonic() - started_monotonic, 3)
    # Strip provider organization IDs before the message touches the
    # session log so the UI never displays raw provider metadata.
    if result.error_message:
        result.error_message = sanitize_error_message(result.error_message)
    entry = result.to_log_entry()
    _record_to_session(entry)
    _logger.info(
        "stage=%s used_api=%s status=%s provider=%s model=%s in_tokens=%s out_tokens=%s",
        result.task_name,
        str(result.used_api).lower(),
        result.status,
        result.provider or "-",
        result.model or "-",
        result.estimated_input_tokens if result.estimated_input_tokens is not None else "-",
        result.estimated_output_tokens if result.estimated_output_tokens is not None else "-",
    )


__all__ = [
    "LLMCallResult",
    "call_text_llm",
    "call_vision_llm",
    "record_local_use",
    "extend_last_entry",
    "sanitize_error_message",
    "get_usage_log",
    "reset_usage_log",
    "STATUS_OK",
    "STATUS_NO_API",
    "STATUS_INVALID_KEY",
    "STATUS_QUOTA",
    "STATUS_CONTEXT_OVERFLOW",
    "STATUS_CONNECTION",
    "STATUS_MODEL_MISSING",
    "STATUS_PARSE_ERROR",
    "STATUS_TRUNCATED",
    "STATUS_UNSUPPORTED_PROVIDER",
    "STATUS_SKIPPED",
    "STATUS_EXCEPTION",
]
