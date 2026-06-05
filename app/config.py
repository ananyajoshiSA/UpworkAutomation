"""Application configuration.

Loads non-secret defaults from environment variables via python-dotenv.
API keys are held only as opaque strings on the Settings object and are
never printed, logged, or included in `repr()`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from app.paths import resource_base, state_dir


# Project root: the repo root in dev, or the read-only bundle dir
# (``sys._MEIPASS``) when frozen. Safe for code/resources, never for writes.
# Resolved by the shared helper so .env loading works regardless of the current
# working directory and survives the project folder being moved/renamed.
PROJECT_ROOT = resource_base()

# Where user-writable state lives. In a normal dev / ``streamlit run`` checkout
# (and under pytest) this is the repo root, so behavior and tests are
# unchanged. In the packaged desktop app it moves to a per-user location
# (``%APPDATA%\\UpworkProposalStrategist``) so no write ever targets the
# read-only / relocatable install folder. See :mod:`app.paths`.
STATE_DIR = state_dir()
if STATE_DIR != PROJECT_ROOT:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover - first-run dir creation best effort
        pass

ENV_PATH = STATE_DIR / ".env"

# Whether a real ``.env`` file was found at the resolved path on the last
# load. Surfaced (never the contents) in the developer debug panel.
_env_file_found: bool = False


def _load_env(*, override: bool = False) -> bool:
    """Load ``.env`` from the project root. Returns True if the file exists.

    Uses an absolute path anchored to :data:`PROJECT_ROOT` rather than the
    current working directory, so the same file is read whether the app is
    launched from the repo root, from ``app/``, or by the test runner.
    """
    global _env_file_found
    if ENV_PATH.is_file():
        load_dotenv(dotenv_path=ENV_PATH, override=override)
        _env_file_found = True
    else:
        # Fall back to dotenv's default search so the app still works if the
        # operator keeps their .env somewhere unusual; record that the
        # canonical project-root file was not present.
        load_dotenv(override=override)
        _env_file_found = False
    return _env_file_found


_load_env()


def env_file_loaded() -> bool:
    """Return True when a ``.env`` was found at the project root."""
    return _env_file_found


APP_TITLE = "Upwork Proposal Strategist"
APP_TAGLINE = (
    "Analyze fit before spending connects. "
    "Generate grounded proposals from real profile evidence."
)

# Writable state dirs. Anchored to STATE_DIR (the repo root in dev, the
# per-user location in a frozen build) so logs never target the read-only
# bundle when packaged.
LOG_DIR = STATE_DIR / "logs"
DATA_DIR = STATE_DIR / "data"

LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "7"))

SUPPORTED_DOSSIER_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
}
SUPPORTED_SCREENSHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


# Canonical session-state key names. Imported by the UI and main module so
# the flow stays consistent across screens.
SESSION_KEYS: dict[str, str] = {
    "api_ok": "api_ok",
    "api_status": "api_status",
    "current_step": "current_step",
    "dossier_folder_path": "dossier_folder_path",
    "dossier_validation": "dossier_validation",
    "dossier_chunks": "dossier_chunks",
    "dossier_read": "dossier_read",
    "evidence_index": "evidence_index",
    "canonical_profile": "canonical_profile",
    "screenshots_uploaded": "screenshots_uploaded",
    "screenshot_uploader": "screenshot_uploader",
    "uploaded_screenshots": "uploaded_screenshots",
    "screenshot_upload_sig": "screenshot_upload_sig",
    "extracted_job_fields": "extracted_job_fields",
    "confirmed_job_fields": "confirmed_job_fields",
    "fields_confirmed": "fields_confirmed",
    "current_job_fingerprint": "current_job_fingerprint",
    "scoring_result": "scoring_result",
    "recommendation_result": "recommendation_result",
    "match_data": "match_data",
    "generated_proposal": "generated_proposal",
    "verified_proposal": "verified_proposal",
    "proposal_context": "proposal_context",
    "selected_evidence_for_proposal": "selected_evidence_for_proposal",
}


# Session keys that hold analysis for ONE uploaded opportunity. They are
# cleared whenever a new screenshot is uploaded or the confirmed job
# details change, so a previous opportunity's match/score/recommendation
# can never leak into the next one. ``fields_confirmed`` resets to False;
# every other key resets to None.
OPPORTUNITY_ANALYSIS_KEYS: tuple[str, ...] = (
    "confirmed_job_fields",
    "fields_confirmed",
    "current_job_fingerprint",
    "match_data",
    "scoring_result",
    "recommendation_result",
    "generated_proposal",
    "verified_proposal",
    "proposal_context",
    "selected_evidence_for_proposal",
)


def clear_opportunity_state(session_state) -> None:
    """Reset every per-opportunity analysis key on ``session_state``.

    Accepts any mapping that supports item assignment (Streamlit's
    ``session_state`` or a plain dict in tests). ``fields_confirmed`` is
    set to ``False``; all other keys are set to ``None``.
    """
    for key in OPPORTUNITY_ANALYSIS_KEYS:
        session_state[key] = False if key == "fields_confirmed" else None


_SUPPORTED_PROVIDERS = ("anthropic", "openai", "groq", "gemini")
_DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
_DEFAULT_OPENAI_MODEL = "gpt-4o"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
_DEFAULT_GEMINI_MODEL = "gemini-1.5-pro"

# Vision-capable providers. Groq is text-only in this app — the Groq API has
# no image input — so it is intentionally excluded. Selecting it for vision
# surfaces a clean, actionable message instead of a failed request.
_SUPPORTED_VISION_PROVIDERS = ("anthropic", "openai", "gemini")

# Shown verbatim whenever the resolved vision provider is Groq. Imported by the
# LLM client (runtime guard) and the UI (setup / screenshot screens).
GROQ_VISION_UNSUPPORTED_MESSAGE = (
    "Vision extraction is not supported for Groq. Please choose OpenAI, "
    "Anthropic, Gemini, or same_as_llm if it supports vision."
)

# Required .env variable names per provider, used for actionable
# "what is missing" messages. Never holds the values themselves.
_PROVIDER_REQUIRED_ENV: dict[str, tuple[str, str]] = {
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL"),
}

# Substrings that mark a model as vision-capable. Used only to decide
# whether the text model can stand in for a blank VISION_MODEL. When
# uncertain we treat the model as text-only and ask the operator to set
# VISION_MODEL explicitly.
_VISION_MODEL_MARKERS: tuple[str, ...] = (
    "4o",          # gpt-4o, gpt-4o-mini
    "4.1",         # gpt-4.1 family
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-5",       # gpt-5 family (multimodal)
    "o3",          # o3 reasoning (vision) — text-only o3-mini excluded below
    "o4",          # o4 family
    "claude-3",    # claude-3 / claude-3-5 families
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-4",
    "claude-4",    # forward-compat claude-4.x naming
    "gemini-1.5",        # gemini-1.5-pro / -flash (multimodal)
    "gemini-2",          # gemini-2.x family (multimodal)
    "gemini-pro-vision", # legacy explicit vision model
)

# Substrings that mark an OpenAI variant as NON-vision even though it would
# otherwise match a marker above (e.g. "gpt-4o-audio" matches "4o"). These
# are checked first so audio/realtime/transcribe/embedding and the
# text-only o1-mini / o3-mini reasoning models are never treated as
# image-capable.
_VISION_MODEL_EXCLUSIONS: tuple[str, ...] = (
    "audio",
    "realtime",
    "transcribe",
    "tts",
    "embedding",
    "moderation",
    "o1-mini",
    "o3-mini",
    "o4-mini",
)


def _model_supports_vision(model: str | None) -> bool:
    """Best-effort allow-list check: can ``model`` accept image inputs?

    A model is treated as vision-capable when its name matches a known
    vision marker AND does not match a known non-vision exclusion. This
    fixes the prior naive substring match, which both admitted non-vision
    variants (gpt-4o-audio/realtime/transcribe, o3-mini) and rejected newer
    vision families (gpt-5).
    """
    if not model:
        return False
    name = model.lower()
    if any(bad in name for bad in _VISION_MODEL_EXCLUSIONS):
        return False
    return any(marker in name for marker in _VISION_MODEL_MARKERS)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (_env(name) or "").lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


_DEFAULT_MAX_PROPOSAL_CONTEXT_CHARS = 15000
_DEFAULT_MAX_PROPOSAL_EVIDENCE_POINTS = 20
_DEFAULT_PROPOSAL_MAX_OUTPUT_TOKENS = 700


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings.

    API key fields are excluded from `repr` so the object is safe to log.
    """

    llm_provider: str = "anthropic"
    anthropic_api_key: str | None = field(default=None, repr=False)
    anthropic_model: str = _DEFAULT_ANTHROPIC_MODEL
    openai_api_key: str | None = field(default=None, repr=False)
    openai_model: str = _DEFAULT_OPENAI_MODEL
    groq_api_key: str | None = field(default=None, repr=False)
    groq_model: str = _DEFAULT_GROQ_MODEL
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = _DEFAULT_GEMINI_MODEL
    # Vision provider/model. ``vision_provider`` defaults to the text
    # provider when blank; ``vision_model`` defaults to the active text
    # model only when that model is vision-capable (see active_vision_model).
    vision_provider: str | None = None
    vision_model: str | None = None
    allow_provider_fallback: bool = False
    allow_local_placeholders: bool = False
    max_proposal_context_chars: int = _DEFAULT_MAX_PROPOSAL_CONTEXT_CHARS
    max_proposal_evidence_points: int = _DEFAULT_MAX_PROPOSAL_EVIDENCE_POINTS
    proposal_max_output_tokens: int = _DEFAULT_PROPOSAL_MAX_OUTPUT_TOKENS
    # Developer-only debug toggle. When false (the default), the main UI
    # hides API usage status, provider/model labels, task names, prompt
    # size, evidence count sent, and internal fallback messages. Internal
    # API logging still runs — only the on-screen surface is gated.
    show_debug_panel: bool = False
    # Last-used dossier folder path. Persisted to .env so the user doesn't
    # retype it every launch (not a secret).
    dossier_folder_path: str | None = None

    @property
    def active_provider(self) -> str:
        """The configured text LLM provider, normalized to lowercase."""
        return (self.llm_provider or "").strip().lower()

    def _key_for_provider(self, provider: str) -> str | None:
        """Return the configured API key for ``provider`` (never logged).

        A plain method (not a property/field) so the raw keys are never part
        of the dataclass ``repr`` or attribute surface used for display.
        """
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "groq": self.groq_api_key,
            "gemini": self.gemini_api_key,
        }.get((provider or "").strip().lower())

    @property
    def has_api_key(self) -> bool:
        return bool(self._key_for_provider(self.active_provider))

    @property
    def has_any_api_key(self) -> bool:
        """True if *any* provider has a stored key (used by Clear Credentials)."""
        return any(
            self._key_for_provider(p) for p in _SUPPORTED_PROVIDERS
        )

    @property
    def active_model(self) -> str:
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
            "groq": self.groq_model,
            "gemini": self.gemini_model,
        }.get(self.active_provider, self.anthropic_model)

    @property
    def active_api_key_masked(self) -> str:
        """Display-safe mask of the active provider's key — never the raw key."""
        return mask_secret(self._key_for_provider(self.active_provider))

    @property
    def provider_configured(self) -> bool:
        return self.active_provider in _SUPPORTED_PROVIDERS and self.has_api_key

    # -- Vision ---------------------------------------------------------

    @property
    def active_vision_provider(self) -> str:
        """Vision provider, defaulting to the text provider when blank."""
        return (self.vision_provider or self.llm_provider or "").strip().lower()

    @property
    def active_vision_model(self) -> str | None:
        """Resolved vision model.

        Uses ``VISION_MODEL`` when set. When blank, falls back to the active
        text model *only* if that model is vision-capable; otherwise returns
        ``None`` so the screenshot stage can show a clean, deferred warning
        instead of failing setup.
        """
        if self.vision_model:
            return self.vision_model
        text_model = self.active_model
        return text_model if _model_supports_vision(text_model) else None

    @property
    def vision_configured(self) -> bool:
        return bool(self.active_vision_model)

    # -- Required-config introspection (no secret values) ---------------

    @property
    def required_env_vars(self) -> tuple[str, str]:
        """(key_var, model_var) names required for the active provider."""
        return _PROVIDER_REQUIRED_ENV.get(
            self.llm_provider, ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL")
        )

    def missing_config(self) -> list[str]:
        """Return the names of required .env values that are absent.

        Never returns or logs any value — only variable names. Used by the
        debug panel and to drive actionable setup messages.
        """
        if self.llm_provider not in _SUPPORTED_PROVIDERS:
            return ["LLM_PROVIDER"]
        key_var, model_var = self.required_env_vars
        missing: list[str] = []
        if not self.has_api_key:
            missing.append(key_var)
        if not self.active_model:
            missing.append(model_var)
        return missing


def _env_positive_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _load_settings() -> Settings:
    provider = (_env("LLM_PROVIDER") or "anthropic").lower()
    return Settings(
        llm_provider=provider,
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL") or _DEFAULT_ANTHROPIC_MODEL,
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_model=_env("OPENAI_MODEL") or _DEFAULT_OPENAI_MODEL,
        groq_api_key=_env("GROQ_API_KEY"),
        groq_model=_env("GROQ_MODEL") or _DEFAULT_GROQ_MODEL,
        gemini_api_key=_env("GEMINI_API_KEY"),
        gemini_model=_env("GEMINI_MODEL") or _DEFAULT_GEMINI_MODEL,
        vision_provider=_env("VISION_PROVIDER"),
        vision_model=_env("VISION_MODEL"),
        allow_provider_fallback=_env_bool("ALLOW_PROVIDER_FALLBACK", default=False),
        allow_local_placeholders=_env_bool("ALLOW_LOCAL_PLACEHOLDERS", default=False),
        max_proposal_context_chars=_env_positive_int(
            "MAX_PROPOSAL_CONTEXT_CHARS", _DEFAULT_MAX_PROPOSAL_CONTEXT_CHARS
        ),
        max_proposal_evidence_points=_env_positive_int(
            "MAX_PROPOSAL_EVIDENCE_POINTS", _DEFAULT_MAX_PROPOSAL_EVIDENCE_POINTS
        ),
        proposal_max_output_tokens=_env_positive_int(
            "PROPOSAL_MAX_OUTPUT_TOKENS", _DEFAULT_PROPOSAL_MAX_OUTPUT_TOKENS
        ),
        show_debug_panel=_env_bool("SHOW_DEBUG_PANEL", default=False),
        dossier_folder_path=_env("DOSSIER_FOLDER_PATH"),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings singleton. Call `get_settings.cache_clear()` in tests."""
    return _load_settings()


def reload_settings() -> Settings:
    """Re-read ``.env`` from disk and return a fresh :class:`Settings`.

    ``get_settings`` is process-cached and ``load_dotenv`` only runs once at
    import. Streamlit keeps a single Python process alive across reruns, so a
    user who launches the app before filling in their API key — then adds it
    to ``.env`` and clicks "Run API Check" — would otherwise keep hitting the
    stale cached Settings (empty key) and see "API Missing" until they kill
    and restart the server. Calling this on the API-check button re-reads the
    file (``override=True`` so edited values win over the values already in
    ``os.environ``) and rebuilds the cache so a corrected ``.env`` is picked
    up live.
    """
    _load_env(override=True)
    get_settings.cache_clear()
    return get_settings()


# ---------------------------------------------------------------------------
# Configuration writing — Setup form → .env
# ---------------------------------------------------------------------------
#
# The Setup screen lets operators configure the provider, model, and API key
# from the UI instead of hand-editing ``.env``. These helpers translate the
# form values into the canonical environment-variable names and write them
# back to the project-root ``.env``, preserving unrelated keys and comments.
#
# Secret-safety: ``build_env_updates`` returns a plain dict that *does* hold
# the API key (so it can be written to disk). Callers must never log, print,
# or display that dict. ``update_env_file`` writes the file with ``0o600``
# permissions and never logs any value. Use :func:`mask_secret` for any
# on-screen confirmation that a key is present.

# Provider order shown in the Setup dropdown (openai first, matching
# ``.env.example``). Validation accepts any of these via ``_SUPPORTED_PROVIDERS``.
PROVIDER_OPTIONS: tuple[str, ...] = ("openai", "anthropic", "groq", "gemini")

# Sentinel used by the Vision Provider dropdown: "use whatever the text
# provider is". Resolved to a concrete provider name at save time. Groq is
# intentionally absent — it is text-only here (see _SUPPORTED_VISION_PROVIDERS).
VISION_SAME_AS_LLM = "same_as_llm"
VISION_PROVIDER_OPTIONS: tuple[str, ...] = (
    "openai",
    "anthropic",
    "gemini",
    VISION_SAME_AS_LLM,
)

# API-key env var names, one per supported provider. These hold the only
# secrets the app stores, so :func:`clear_api_keys` blanks exactly these to
# wipe every stored credential while leaving models/provider selection intact.
_PROVIDER_API_KEY_ENV: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
)


# Environment keys this app manages through the Setup form. Unrelated keys
# already present in ``.env`` are always preserved on write. Imported by the
# tests that assert ``.env.example`` stays in sync.
_MANAGED_ENV_KEYS: tuple[str, ...] = (
    "LLM_PROVIDER",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "VISION_PROVIDER",
    "VISION_MODEL",
    "ALLOW_PROVIDER_FALLBACK",
    "ALLOW_LOCAL_PLACEHOLDERS",
    "SHOW_DEBUG_PANEL",
    "MAX_PROPOSAL_CONTEXT_CHARS",
    "MAX_PROPOSAL_EVIDENCE_POINTS",
    "PROPOSAL_MAX_OUTPUT_TOKENS",
    "LOG_RETENTION_DAYS",
)


def mask_secret(secret: str | None) -> str:
    """Return a display-safe mask of ``secret`` — never the full value.

    Shows a short non-secret prefix and the last four characters, e.g.
    ``sk-****wxyz``. Returns ``""`` for an empty/blank secret and a fully
    opaque ``"****"`` for values too short to mask safely. The full secret is
    never returned, logged, or echoed.
    """
    if not secret:
        return ""
    s = str(secret).strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "****"
    return f"{s[:3]}****{s[-4:]}"


def _bool_to_env(value: object) -> str:
    """Coerce a toggle/checkbox/string value to the env literal 'true'/'false'."""
    if isinstance(value, str):
        truthy = value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return "true" if truthy else "false"
    return "true" if value else "false"


def _coerce_int(value: object, default: int) -> int:
    """Best-effort positive-int coercion, falling back to ``default``."""
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_env_updates(
    *,
    llm_provider: str,
    llm_model: str,
    api_key: str,
    vision_provider: str,
    vision_model: str,
    allow_provider_fallback: object = False,
    allow_local_placeholders: object = False,
    show_debug_panel: object = False,
    max_proposal_context_chars: object = _DEFAULT_MAX_PROPOSAL_CONTEXT_CHARS,
    max_proposal_evidence_points: object = _DEFAULT_MAX_PROPOSAL_EVIDENCE_POINTS,
    proposal_max_output_tokens: object = _DEFAULT_PROPOSAL_MAX_OUTPUT_TOKENS,
    log_retention_days: object = LOG_RETENTION_DAYS,
) -> dict[str, str]:
    """Translate Setup-form values into the ``.env`` keys to write.

    Provider-specific rules (mirrors the build plan):

    * ``LLM_PROVIDER`` is always written.
    * The model and key are written to the *selected* provider's pair only
      (``OPENAI_*``, ``ANTHROPIC_*``, ``GROQ_*``, or ``GEMINI_*``); the other
      providers' keys are left untouched so switching provider never blanks a
      key the operator already saved.
    * A blank ``api_key`` is **not** written, so re-saving without retyping the
      key preserves the existing one (the UI never prefills the key field).
    * ``vision_provider == "same_as_llm"`` (or blank) resolves
      ``VISION_PROVIDER`` to the text provider and, when no separate vision
      model is given, ``VISION_MODEL`` to the text model.

    The returned dict holds the raw API key when one was supplied — callers
    must write it via :func:`update_env_file` and never log or display it.
    """
    provider = (llm_provider or "").strip().lower()
    model = (llm_model or "").strip()
    key = (api_key or "").strip()

    updates: dict[str, str] = {"LLM_PROVIDER": provider}

    if provider == "openai":
        if model:
            updates["OPENAI_MODEL"] = model
        if key:
            updates["OPENAI_API_KEY"] = key
    elif provider == "anthropic":
        if model:
            updates["ANTHROPIC_MODEL"] = model
        if key:
            updates["ANTHROPIC_API_KEY"] = key
    elif provider == "groq":
        if model:
            updates["GROQ_MODEL"] = model
        if key:
            updates["GROQ_API_KEY"] = key
    elif provider == "gemini":
        if model:
            updates["GEMINI_MODEL"] = model
        if key:
            updates["GEMINI_API_KEY"] = key

    raw_vision = (vision_provider or "").strip().lower()
    inherit = raw_vision in ("", VISION_SAME_AS_LLM)
    updates["VISION_PROVIDER"] = provider if inherit else raw_vision
    resolved_vision_model = (vision_model or "").strip()
    if not resolved_vision_model and inherit:
        resolved_vision_model = model
    updates["VISION_MODEL"] = resolved_vision_model

    updates["ALLOW_PROVIDER_FALLBACK"] = _bool_to_env(allow_provider_fallback)
    updates["ALLOW_LOCAL_PLACEHOLDERS"] = _bool_to_env(allow_local_placeholders)
    updates["SHOW_DEBUG_PANEL"] = _bool_to_env(show_debug_panel)
    updates["MAX_PROPOSAL_CONTEXT_CHARS"] = str(
        _coerce_int(max_proposal_context_chars, _DEFAULT_MAX_PROPOSAL_CONTEXT_CHARS)
    )
    updates["MAX_PROPOSAL_EVIDENCE_POINTS"] = str(
        _coerce_int(max_proposal_evidence_points, _DEFAULT_MAX_PROPOSAL_EVIDENCE_POINTS)
    )
    updates["PROPOSAL_MAX_OUTPUT_TOKENS"] = str(
        _coerce_int(proposal_max_output_tokens, _DEFAULT_PROPOSAL_MAX_OUTPUT_TOKENS)
    )
    updates["LOG_RETENTION_DAYS"] = str(_coerce_int(log_retention_days, LOG_RETENTION_DAYS))
    return updates


def _format_env_value(value: object) -> str:
    """Render a value for the right-hand side of a ``KEY=VALUE`` line.

    Bare for simple tokens (keys, model names, booleans, ints); double-quoted
    and escaped only when the value contains whitespace or characters that
    would otherwise break parsing.
    """
    s = "" if value is None else str(value)
    if s == "":
        return ""
    needs_quote = s != s.strip() or any(
        ch in s for ch in (" ", "\t", "#", '"', "'", "\n", "\r")
    )
    if needs_quote:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _match_managed_key(line: str, keys: dict[str, str]) -> tuple[str | None, str]:
    """If ``line`` assigns one of ``keys``, return ``(key, prefix)``; else ``(None, "")``.

    Handles an optional ``export `` prefix and surrounding whitespace; ignores
    comments and blank lines.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None, ""
    prefix = ""
    body = stripped
    if body.startswith("export ") or body.startswith("export\t"):
        prefix = "export "
        body = body[len("export "):].lstrip()
    if "=" not in body:
        return None, ""
    candidate = body.split("=", 1)[0].strip()
    return (candidate, prefix) if candidate in keys else (None, "")


def update_env_file(updates: dict[str, str], *, env_path: Path | None = None) -> Path:
    """Write ``updates`` into the project-root ``.env``, preserving everything else.

    * Creates the file (and parent dirs) when missing.
    * Rewrites the value of any managed key already present, in place — every
      occurrence, so a duplicated key can't leave a stale value behind.
    * Appends managed keys that were not present.
    * Leaves unrelated keys, comments, and blank lines untouched.

    The file is written atomically (temp file + ``os.replace``) with ``0o600``
    permissions because it holds the API key. No value is ever logged.
    """
    path = Path(env_path) if env_path is not None else ENV_PATH
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    seen: set[str] = set()
    out_lines: list[str] = []
    for line in existing:
        key, prefix = _match_managed_key(line, updates)
        if key is not None:
            out_lines.append(f"{prefix}{key}={_format_env_value(updates[key])}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            out_lines.append(f"{key}={_format_env_value(value)}")

    text = "\n".join(out_lines)
    if text and not text.endswith("\n"):
        text += "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover - platform without POSIX chmod semantics
        pass
    os.replace(tmp, path)
    return path


def clear_api_keys(*, env_path: Path | None = None) -> Settings:
    """Blank every stored provider API key in ``.env`` and reload settings.

    Writes an empty value for each provider's ``*_API_KEY`` so no credential
    is left on disk, then re-reads ``.env`` so the cleared state takes effect
    live (no server restart). Models, provider selection, and every unrelated
    key/comment are preserved. Returns the freshly reloaded :class:`Settings`.

    ``env_path`` overrides the write target for tests; the reload always reads
    the canonical :data:`ENV_PATH`, matching the Setup save flow.
    """
    updates: dict[str, str] = {name: "" for name in _PROVIDER_API_KEY_ENV}
    update_env_file(updates, env_path=env_path)
    return reload_settings()


def save_dossier_path(path: str, *, env_path: Path | None = None) -> None:
    """Persist the last-used dossier folder path to ``.env`` for next launch.

    Non-secret convenience state: the user points at their dossier folder once
    and the app pre-fills it on every later launch until they change it. Written
    via the same managed-``.env`` writer as the API config, so unrelated keys and
    comments are preserved.
    """
    update_env_file(
        {"DOSSIER_FOLDER_PATH": (path or "").strip()}, env_path=env_path
    )


# Canonical LLM task names. Used by llm_client and the API usage panel so
# the UI can spot which stages talked to the real API.
LLM_TASK_NAMES: tuple[str, ...] = (
    "api_check",
    "screenshot_extraction",
    "evidence_index_generation",
    "opportunity_matching",
    "missing_info_labeling",
    "recommendation_generation",
    "proposal_generation",
    "verification_pass",
)


# Back-compat constants for existing imports. Resolved once at import time.
_settings = get_settings()
PROVIDER = _settings.llm_provider
DEFAULT_MODEL = _settings.active_model
DEFAULT_VISION_MODEL = _settings.active_vision_model or _settings.active_model


# ---------------------------------------------------------------------------
# Provider/model reference list (suggestions only — not enforced)
# ---------------------------------------------------------------------------
# Re-exported for convenience so code that already imports ``app.config`` can
# reach the curated provider/model suggestion list without a second import.
# This is reference data only: it does NOT gate provider routing, model
# selection, or any API behavior. The source of truth lives in
# ``app/models/provider_models.py``. The import is placed at the end of the
# module and the source module imports nothing from here, so there is no
# import cycle.
from app.models.provider_models import (  # noqa: E402  (intentional late import)
    SUPPORTED_PROVIDER_MODELS,
)
