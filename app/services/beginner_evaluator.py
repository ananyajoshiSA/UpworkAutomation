"""Beginner Job Evaluator.

A small, fully deterministic rule layer that screens one uploaded Upwork
opportunity through beginner-safe rules *before* the final recommendation
is produced. It exists to protect a beginner profile from wasting
connects on jobs that are statistically not worth a proposal.

The evaluator reads five confirmed job fields:

* ``payment_verification``
* ``proposal_count``
* ``posted_date`` (or ``posted_age``)
* ``client_rating``
* ``experience_level``

and emits one of three results:

* ``"Do Not Proceed"`` — an *Instant No* rule fired (payment not verified,
  or 50+ proposals). These override every positive signal.
* ``"Apply Confidently"`` — every green-light condition is satisfied.
* ``"Proceed With Caution"`` — payment is verified and proposals are under
  50, but at least one warning is present (or a green-light condition
  cannot be confirmed).

Nothing here is guessed: a field that is not legible is recorded as
``not_visible`` and never assumed to be good or bad. Missing fields lower
confidence and add a missing-info note instead of inventing a verdict.

This layer is deterministic and runs independently of the LLM. The match
engine attaches its output to ``match_data['beginner_evaluation']``; the
scoring and recommendation layers then apply it with deterministic rules
(see :mod:`app.services.scoring` and :mod:`app.services.recommendation`).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional


NOT_VISIBLE = "Not visible"

# Result vocabulary (note the title-cased "With" — this is the beginner
# evaluator's own label, distinct from the recommendation verdict
# "Proceed with Caution").
APPLY_CONFIDENTLY = "Apply Confidently"
PROCEED_WITH_CAUTION = "Proceed With Caution"
DO_NOT_PROCEED = "Do Not Proceed"


# User-facing reason strings. These are the exact, beginner-friendly
# sentences shown on the clean UI.
REASON_PAYMENT_NOT_VERIFIED = (
    "Payment is not verified, so there is a higher risk of not getting paid."
)
REASON_PROPOSALS_50_PLUS = (
    "This job already has 50 or more proposals, so competition is too high "
    "for a beginner profile."
)
WARN_PROPOSALS_15_49 = "Competition is high, so the proposal must be very strong."
WARN_POSTED_STALE = (
    "The client may have already moved forward with other freelancers."
)
WARN_RATING_LOW = "The client may be difficult to satisfy."
WARN_EXPERT_LEVEL = "The client may screen out beginner profiles."


# Friendly labels for the five evaluated fields (used in the missing-info
# note and the debug breakdown).
_FIELD_LABELS: dict[str, str] = {
    "payment_verification": "payment verification",
    "proposal_count": "proposal count",
    "posted_date": "posted date",
    "client_rating": "client rating",
    "experience_level": "experience level",
}


# ---------------------------------------------------------------------------
# Field access helpers
# ---------------------------------------------------------------------------


def _value(confirmed_job: dict, *keys: str) -> str:
    """Return the first present, non-empty value across ``keys``.

    Accepts either ``{"value": ...}`` field dicts (the confirmed-job shape)
    or plain strings, so the evaluator works against the real app state and
    against simple test fixtures.
    """
    for key in keys:
        entry = (confirmed_job or {}).get(key)
        if isinstance(entry, dict):
            value = str(entry.get("value", "") or "").strip()
        else:
            value = str(entry or "").strip()
        if not _is_missing(value):
            return value
    return ""


def _is_missing(value: Optional[str]) -> bool:
    if value is None:
        return True
    v = value.strip()
    return not v or v.lower() == NOT_VISIBLE.lower()


def _ints(value: str) -> list[int]:
    return [int(tok) for tok in re.findall(r"\d+", value.replace(",", ""))]


def _first_float(value: str) -> Optional[float]:
    match = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Field parsers — each returns a stable bucket; never guesses on missing data
# ---------------------------------------------------------------------------


def _payment_status(value: str) -> str:
    """``"verified"`` | ``"not_verified"`` | ``"not_visible"``."""
    if _is_missing(value):
        return "not_visible"
    low = value.lower()
    # Check the negative markers first — "not verified" contains "verified".
    negative = (
        "not verified",
        "not_verified",
        "unverified",
        "no payment",
        "payment not",
        "not confirmed",
        "billing not",
    )
    if any(marker in low for marker in negative):
        return "not_verified"
    if low in {"no", "n", "false", "unverified"}:
        return "not_verified"
    if "verified" in low or "verify" in low or low in {"yes", "y", "true"}:
        return "verified"
    # Unrecognised text → do not guess.
    return "not_visible"


def _proposal_count(value: str) -> Optional[int]:
    """Representative proposal count, or ``None`` when not visible.

    Upwork shows plain numbers ("12"), ranges ("5 to 10", "20 to 50"),
    open-ended buckets ("50+", "Less than 5") and qualified phrases
    ("more than 50"). We use the lower bound of a range (the freelancer's
    best case) but honour explicit "less than" wording, so the buckets stay
    predictable and never over-block.
    """
    if _is_missing(value):
        return None
    ints = _ints(value)
    if not ints:
        return None
    low = value.lower()
    if any(p in low for p in ("less than", "fewer than", "under", "below")):
        return max(min(ints) - 1, 0)
    return ints[0]


def _proposal_bucket(count: Optional[int]) -> str:
    """``"low"`` (<15) | ``"high"`` (15-49) | ``"too_high"`` (50+) | ``"not_visible"``."""
    if count is None:
        return "not_visible"
    if count >= 50:
        return "too_high"
    if count >= 15:
        return "high"
    return "low"


def _posted_age_days(value: str, today: Optional[date] = None) -> Optional[int]:
    """Age of the post in days, or ``None`` when not visible / unparseable.

    Relative phrases ("today", "yesterday", "3 days ago", "2 weeks ago")
    are the common Upwork shape and are parsed first. An absolute date is
    a best-effort fallback measured against ``today`` (defaults to the
    system date; injectable for deterministic tests).
    """
    if _is_missing(value):
        return None
    low = value.lower()

    # Same-day markers.
    same_day = (
        "just now", "moments ago", "today", "few minutes", "minute ago",
        "minutes ago", "hour ago", "hours ago", "an hour", "less than a day",
        "<1 day", "less than 1 day", "few hours",
    )
    if any(marker in low for marker in same_day):
        return 0
    if "yesterday" in low:
        return 1

    m = re.search(r"(\d+)\s*day", low)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*week", low)
    if m:
        return int(m.group(1)) * 7
    if "last week" in low or "a week ago" in low or "1 week" in low:
        return 7
    m = re.search(r"(\d+)\s*month", low)
    if m:
        return int(m.group(1)) * 30
    if "last month" in low or "a month ago" in low or "1 month" in low:
        return 30

    return _absolute_age_days(value, today)


def _absolute_age_days(value: str, today: Optional[date]) -> Optional[int]:
    reference = today or _today()
    if reference is None:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            parsed = _strptime_date(value.strip(), fmt)
        except ValueError:
            continue
        if parsed is None:
            continue
        delta = (reference - parsed).days
        return max(delta, 0)
    return None


def _today() -> Optional[date]:
    # Wrapped so tests can stay deterministic by passing ``today=`` instead.
    try:
        return date.today()
    except Exception:  # pragma: no cover - defensive only
        return None


def _strptime_date(value: str, fmt: str) -> Optional[date]:
    from datetime import datetime

    return datetime.strptime(value, fmt).date()


def _posted_bucket(age_days: Optional[int]) -> str:
    """``"fresh"`` (today/yesterday) | ``"recent"`` (2 days) | ``"stale"`` (3+) | ``"not_visible"``."""
    if age_days is None:
        return "not_visible"
    if age_days <= 1:
        return "fresh"
    if age_days >= 3:
        return "stale"
    return "recent"


def _rating(value: str) -> Optional[float]:
    if _is_missing(value):
        return None
    low = value.lower()
    if any(p in low for p in ("no review", "no rating", "not rated", "new client", "no feedback")):
        return None
    r = _first_float(value)
    if r is None:
        return None
    # Client ratings are on a 0-5 scale. A bare number above 5 is almost
    # certainly something else (e.g. a percentage) — do not guess.
    if r > 5:
        return None
    return r


def _rating_bucket(rating: Optional[float]) -> str:
    """``"ok"`` (>=4.0) | ``"low"`` (<4.0) | ``"not_visible"``."""
    if rating is None:
        return "not_visible"
    return "low" if rating < 4.0 else "ok"


def _experience_level(value: str) -> str:
    """``"entry"`` | ``"intermediate"`` | ``"expert"`` | ``"other"`` | ``"not_visible"``."""
    if _is_missing(value):
        return "not_visible"
    low = value.lower()
    if "expert" in low:
        return "expert"
    if "intermediate" in low:
        return "intermediate"
    if "entry" in low or "beginner" in low:
        return "entry"
    return "other"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate(confirmed_job: dict, *, today: Optional[date] = None) -> dict[str, Any]:
    """Run the beginner-safety checklist over one confirmed opportunity.

    Returns a plain dict (matching the rest of the analysis bundle) with:

    * ``result`` — ``"Apply Confidently"`` | ``"Proceed With Caution"`` |
      ``"Do Not Proceed"``.
    * ``instant_no`` — ``True`` when an Instant-No rule fired.
    * ``reasons`` — up to two short, user-facing reason strings.
    * ``warnings`` — ``[{"key", "reason"}]`` for the Caution path.
    * ``instant_no_reasons`` — the Instant-No reason strings.
    * ``fields`` — per-field buckets for the debug breakdown.
    * ``missing_fields`` / ``missing_info_note`` — fields that were not
      visible and a single note summarising them.
    * ``reduce_confidence`` — ``True`` when scoring should lower confidence.
    * ``triggered_rule`` — which rule decided the final result.
    * ``score_signals`` — deterministic booleans the scoring layer reads.

    Never raises on missing or malformed fields.
    """
    confirmed_job = confirmed_job or {}

    payment_value = _value(confirmed_job, "payment_verification")
    proposal_value = _value(confirmed_job, "proposal_count")
    posted_value = _value(confirmed_job, "posted_date", "posted_age")
    rating_value = _value(confirmed_job, "client_rating")
    experience_value = _value(confirmed_job, "experience_level")

    payment_status = _payment_status(payment_value)
    count = _proposal_count(proposal_value)
    proposal_bucket = _proposal_bucket(count)
    age_days = _posted_age_days(posted_value, today=today)
    posted_bucket = _posted_bucket(age_days)
    rating = _rating(rating_value)
    rating_bucket = _rating_bucket(rating)
    experience = _experience_level(experience_value)

    # ---- Instant No (overrides every positive signal) -----------------
    instant_no_reasons: list[str] = []
    if payment_status == "not_verified":
        instant_no_reasons.append(REASON_PAYMENT_NOT_VERIFIED)
    if proposal_bucket == "too_high":
        instant_no_reasons.append(REASON_PROPOSALS_50_PLUS)
    instant_no = bool(instant_no_reasons)

    # ---- Warnings (Proceed With Caution drivers) ----------------------
    # Appended in BEGINNER-RISK ORDER (highest first) so that when the list
    # is later capped to two for the UI/LLM, the most important safety flags
    # survive. For a beginner, being screened out by an expert-level post and
    # a difficult (low-rated) client are the sharpest risks, followed by
    # heavy competition, then a stale (likely already-hiring) post.
    warnings: list[tuple[str, str]] = []
    if experience == "expert":
        warnings.append(("expert_level", WARN_EXPERT_LEVEL))
    if rating_bucket == "low":
        warnings.append(("client_rating_below_4", WARN_RATING_LOW))
    if proposal_bucket == "high":
        warnings.append(("proposals_15_49", WARN_PROPOSALS_15_49))
    if posted_bucket == "stale":
        warnings.append(("posted_3_days_plus", WARN_POSTED_STALE))

    # ---- Apply Confidently green-light conditions ---------------------
    apply_conditions_met = (
        payment_status == "verified"
        and proposal_bucket == "low"
        and posted_bucket == "fresh"
        and experience in {"entry", "intermediate"}
    )

    # ---- Missing fields (never guessed) -------------------------------
    missing_fields: list[str] = []
    if payment_status == "not_visible":
        missing_fields.append("payment_verification")
    if proposal_bucket == "not_visible":
        missing_fields.append("proposal_count")
    if posted_bucket == "not_visible":
        missing_fields.append("posted_date")
    if rating_bucket == "not_visible":
        missing_fields.append("client_rating")
    if experience == "not_visible":
        missing_fields.append("experience_level")

    # ---- Decide the final result --------------------------------------
    if instant_no:
        result = DO_NOT_PROCEED
        if len(instant_no_reasons) >= 2:
            triggered_rule = "instant_no:payment_not_verified+proposals_50_plus"
        elif payment_status == "not_verified":
            triggered_rule = "instant_no:payment_not_verified"
        else:
            triggered_rule = "instant_no:proposals_50_plus"
        reasons = instant_no_reasons[:2]
    elif apply_conditions_met and not warnings:
        result = APPLY_CONFIDENTLY
        triggered_rule = "apply_confidently:all_conditions_met"
        reasons = ["Payment is verified and competition is still low (under 15 proposals)."]
        if posted_bucket == "fresh":
            reasons.append("Posted in the last day, so you are early.")
        reasons = reasons[:2]
    else:
        result = PROCEED_WITH_CAUTION
        if warnings:
            triggered_rule = "proceed_with_caution:" + warnings[0][0]
            reasons = [reason for _, reason in warnings][:2]
        else:
            triggered_rule = "proceed_with_caution:missing_info"
            reasons = [
                "Some beginner-safety details are not visible, so proceed carefully."
            ]

    # ---- Confidence + missing-info note -------------------------------
    reduce_confidence = bool(
        missing_fields or posted_bucket == "stale" or experience == "expert"
    )
    missing_info_note: Optional[str] = None
    if missing_fields:
        labels = ", ".join(_FIELD_LABELS.get(f, f) for f in missing_fields)
        missing_info_note = (
            f"Could not confirm: {labels}. Treating this recommendation with "
            "extra caution."
        )

    score_signals = {
        "payment_not_verified": payment_status == "not_verified",
        "proposals_50_plus": proposal_bucket == "too_high",
        "proposals_under_15": proposal_bucket == "low",
        "posted_fresh": posted_bucket == "fresh",
        "posted_stale": posted_bucket == "stale",
        "expert_level": experience == "expert",
    }

    fields = {
        "payment_verification": {
            "value": payment_value or NOT_VISIBLE,
            "result": payment_status,
        },
        "proposal_count": {
            "value": proposal_value or NOT_VISIBLE,
            "count": count,
            "bucket": proposal_bucket,
        },
        "posted_age": {
            "value": posted_value or NOT_VISIBLE,
            "age_days": age_days,
            "bucket": posted_bucket,
        },
        "client_rating": {
            "value": rating_value or NOT_VISIBLE,
            "rating": rating,
            "bucket": rating_bucket,
            "warning": rating_bucket == "low",
        },
        "experience_level": {
            "value": experience_value or NOT_VISIBLE,
            "level": experience,
            "warning": experience == "expert",
        },
    }

    return {
        "result": result,
        "instant_no": instant_no,
        "reasons": reasons,
        "warnings": [{"key": key, "reason": reason} for key, reason in warnings],
        "instant_no_reasons": instant_no_reasons,
        "fields": fields,
        "missing_fields": missing_fields,
        "missing_info_note": missing_info_note,
        "reduce_confidence": reduce_confidence,
        "triggered_rule": triggered_rule,
        "score_signals": score_signals,
    }
