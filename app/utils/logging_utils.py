"""Logging utilities.

Logs API call metadata only — timestamps, model, token counts, and error
codes. Never raw dossier text, screenshot content, or generated
proposals. Logs rotate after 7 days.

Redaction is *enforced at the sink*, not left to caller discipline: every
log record passes through :class:`_RedactionFilter`, which scrubs
API-key- and organization-ID-shaped tokens from the fully-rendered
message regardless of which call site produced it. So even a careless
``logger.info("...%s", api_key)`` cannot write a secret to disk.
"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from app.config import LOG_DIR, LOG_RETENTION_DAYS
from app.utils.redaction import redact


class _RedactionFilter(logging.Filter):
    """Scrub secrets from every record before it is formatted/written.

    The filter rewrites ``record.msg`` to the fully-interpolated, redacted
    message and clears ``record.args`` so the handler's formatter cannot
    re-introduce an unscrubbed value from the original args tuple.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging raise
            return True
        cleaned = redact(rendered)
        if cleaned != rendered:
            record.msg = cleaned
            record.args = ()
        return True


def get_logger(name: str = "upwork_strategist") -> logging.Logger:
    """Return a logger configured for redacted, metadata-only logging."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        LOG_DIR / "app.log",
        when="D",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    # Enforce redaction at the sink so no caller can leak a secret, even
    # by mistake. Attached to both the handler and the logger so records
    # are scrubbed regardless of propagation path.
    redaction_filter = _RedactionFilter()
    handler.addFilter(redaction_filter)
    logger.addFilter(redaction_filter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
