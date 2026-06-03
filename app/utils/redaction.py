"""Secret/payload redaction patterns shared across the app.

This module has no internal dependencies so it can be imported by both
:mod:`app.utils.logging_utils` (sink-level redaction Filter) and
:mod:`app.services.llm_client` (error-message sanitizer) without creating
an import cycle. It is the single source of truth for what an API key or
provider organization ID looks like.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


# Patterns that match API-key-shaped tokens for every supported provider.
# Provider SDKs (and OpenAI-compatible endpoints such as Groq's, plus
# google-api-core for Gemini) routinely echo the raw key in auth errors.
API_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}"),     # Anthropic
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{6,}"),    # OpenAI project keys
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),        # OpenAI / generic sk- keys
    re.compile(r"gsk_[A-Za-z0-9_\-]{8,}"),        # Groq
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),       # Google / Gemini
)

# Provider organization IDs / request slugs we never want to surface.
ORG_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\borg[-_][A-Za-z0-9]{4,}\b", re.IGNORECASE),
    re.compile(r"\borganization[-_]?id[\"'=:\s]+[A-Za-z0-9_-]{4,}", re.IGNORECASE),
    re.compile(r"\bopenai[- _]?organization[\"'=:\s]+[A-Za-z0-9_-]{4,}", re.IGNORECASE),
    re.compile(r"\banthropic[- _]?organization[\"'=:\s]+[A-Za-z0-9_-]{4,}", re.IGNORECASE),
)


def redact(text: Optional[str], *, secrets: Iterable[Optional[str]] = ()) -> Optional[str]:
    """Return ``text`` with API keys and organization IDs scrubbed.

    Redacts, in order: any exact ``secrets`` value the caller supplies
    (catches even unanticipated key formats), then key-shaped tokens for
    every provider family, then provider organization IDs. Idempotent and
    never returns a raw key.
    """
    if not text:
        return text
    cleaned = str(text)
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(str(secret), "[api key redacted]")
    for pattern in API_KEY_PATTERNS:
        cleaned = pattern.sub("[api key redacted]", cleaned)
    for pattern in ORG_ID_PATTERNS:
        cleaned = pattern.sub("[organization id redacted]", cleaned)
    return cleaned
