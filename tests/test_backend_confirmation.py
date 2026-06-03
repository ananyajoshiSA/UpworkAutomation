"""Tests for the backend job-detail confirmation flow.

The "Confirm Details" page is removed from the normal user UI. Instead,
right after screenshot extraction the extracted job fields are confirmed
automatically in the backend and the analysis unlocks. The manual review
page only exists when ``SHOW_DEBUG_PANEL=true``.

These tests assert:

* with ``SHOW_DEBUG_PANEL=false`` the Confirm Details step is not in the
  sidebar navigation (and is present when ``true``);
* after screenshot extraction ``confirmed_job_fields`` is populated and
  ``fields_confirmed`` becomes ``True`` automatically;
* the analysis unlocks once extraction has happened;
* missing fields stay "Not visible" and never get guessed;
* confidence drops when important job fields are missing;
* the Confirm Details screen is gated to debug mode.

A fake Streamlit records every call so the render code runs headless.
"""

from __future__ import annotations

import sys

import pytest

from app import config, main as app_main
from app.services.screenshot_parser import (
    NOT_VISIBLE,
    SCREENSHOT_FIELDS,
    confirm_fields,
)
from app.services.match_engine import CRITICAL_FIELDS
from app.services.scoring import score
from app.ui import analysis_screen, confirmation_screen, screenshot_screen, theme


# ---------------------------------------------------------------------------
# Fake Streamlit
# ---------------------------------------------------------------------------


class _CtxNoop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _Col(_CtxNoop):
    def __init__(self, parent):
        self._parent = parent

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._parent._record(f"col.{name}", *a, **kw)

        return _call


class FakeStreamlit:
    """Records calls; buttons in ``click_keys`` return ``True``."""

    def __init__(self, *, click_keys=(), uploader_return=None):
        self.calls: list[tuple] = []
        self.texts: list[str] = []
        self.session_state = _SessionState()
        self.click_keys = set(click_keys)
        self.uploader_return = uploader_return

    def _record(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        for x in a:
            if isinstance(x, str):
                self.texts.append(x)
        for v in kw.values():
            if isinstance(v, str):
                self.texts.append(v)
        return None

    def container(self, *a, **kw):
        self._record("container", *a, **kw)
        return _CtxNoop()

    def expander(self, *a, **kw):
        self._record("expander", *a, **kw)
        return _CtxNoop()

    def spinner(self, *a, **kw):
        self._record("spinner", *a, **kw)
        return _CtxNoop()

    def columns(self, spec):
        n = spec if isinstance(spec, int) else len(spec)
        return tuple(_Col(self) for _ in range(n))

    def button(self, *a, **kw):
        self._record("button", *a, **kw)
        return kw.get("key") in self.click_keys

    def checkbox(self, *a, **kw):
        self._record("checkbox", *a, **kw)
        return False

    def text_input(self, *a, **kw):
        self._record("text_input", *a, **kw)
        return ""

    def text_area(self, *a, **kw):
        self._record("text_area", *a, **kw)
        return ""

    def file_uploader(self, *a, **kw):
        self._record("file_uploader", *a, **kw)
        return self.uploader_return

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._record(name, *a, **kw)

        return _call


class _FakeUpload:
    """Minimal stand-in for a Streamlit ``UploadedFile`` (bytes via getvalue)."""

    def __init__(self, name="job.png", mime="image/png", data=b"\x89PNG fake"):
        self.name = name
        self.type = mime
        self._data = data

    def getvalue(self):
        return self._data


def _settings(*, show_debug_panel=False, has_key=True):
    return config.Settings(
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-key" if has_key else None,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
        show_debug_panel=show_debug_panel,
    )


_PATCHED_MODULES = [screenshot_screen, confirmation_screen, analysis_screen, theme, app_main]


def _install(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    for mod in _PATCHED_MODULES:
        if hasattr(mod, "st"):
            monkeypatch.setattr(mod, "st", fake, raising=True)


def _mixed_extracted():
    """One visible field; the rest were not legible in the screenshot."""
    fields = {
        key: {"value": NOT_VISIBLE, "confidence": "low", "source": "not visible"}
        for key in SCREENSHOT_FIELDS
    }
    fields["job_title"] = {
        "value": "Senior Python Developer",
        "confidence": "high",
        "source": "ocr extracted",
    }
    fields["__meta__"] = {"used_api": True, "status": "ok", "task_name": "x"}
    return fields


# ---------------------------------------------------------------------------
# 1. Confirm Details is removed from / restored to the sidebar navigation
# ---------------------------------------------------------------------------


def test_confirm_details_hidden_from_nav_when_debug_off():
    keys = [key for key, _ in app_main._visible_steps(show_debug=False)]
    assert keys == ["setup", "dossier", "screenshot", "analysis", "proposal"]
    assert "confirmation" not in keys


def test_confirm_details_shown_in_nav_when_debug_on():
    keys = [key for key, _ in app_main._visible_steps(show_debug=True)]
    assert keys == [
        "setup",
        "dossier",
        "screenshot",
        "confirmation",
        "analysis",
        "proposal",
    ]


# ---------------------------------------------------------------------------
# 2. Extraction auto-confirms in the backend and unlocks the analysis
# ---------------------------------------------------------------------------


def test_render_auto_confirms_extracted_fields(monkeypatch):
    # The single "Analyze Opportunity" button extracts, auto-confirms in the
    # backend, and advances to Analysis — all in one click.
    fake = FakeStreamlit(
        click_keys={"analyze_opportunity_btn"}, uploader_return=[_FakeUpload()]
    )
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        screenshot_screen, "extract_fields", lambda *a, **kw: _mixed_extracted()
    )

    fake.session_state["api_ok"] = True
    fake.session_state["evidence_index"] = ["proof"]

    screenshot_screen.render()

    confirmed = fake.session_state.get("confirmed_job_fields")
    assert isinstance(confirmed, dict)
    assert set(confirmed) == set(SCREENSHOT_FIELDS)
    assert fake.session_state.get("fields_confirmed") is True
    # Analysis unlocks purely on the auto-set fields_confirmed flag.
    assert app_main._is_unlocked("analysis") is True
    # One go: the merged button lands the user on the Analysis step.
    assert fake.session_state.get("current_step") == "analysis"
    # No manual Confirm Details handoff and no dev-facing "not visible" note.
    joined = "\n".join(fake.texts)
    assert "Confirm Details" not in joined
    assert "were not visible" not in joined


def test_extract_button_click_auto_confirms(monkeypatch):
    fake = FakeStreamlit(
        click_keys={"analyze_opportunity_btn"}, uploader_return=[_FakeUpload()]
    )
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        screenshot_screen, "extract_fields", lambda *a, **kw: _mixed_extracted()
    )

    fake.session_state["api_ok"] = True
    fake.session_state["evidence_index"] = ["proof"]

    screenshot_screen.render()

    assert fake.session_state.get("extracted_job_fields") is not None
    confirmed = fake.session_state.get("confirmed_job_fields")
    assert isinstance(confirmed, dict)
    assert confirmed["job_title"]["value"] == "Senior Python Developer"
    assert fake.session_state.get("fields_confirmed") is True
    assert fake.session_state.get("current_step") == "analysis"


# ---------------------------------------------------------------------------
# 3. Missing fields stay "Not visible" — nothing is guessed
# ---------------------------------------------------------------------------


def test_confirm_fields_keeps_missing_not_visible():
    confirmed = confirm_fields(_mixed_extracted())
    assert "__meta__" not in confirmed
    assert confirmed["job_title"]["value"] == "Senior Python Developer"
    missing = [k for k in SCREENSHOT_FIELDS if k != "job_title"]
    for key in missing:
        assert confirmed[key]["value"] == NOT_VISIBLE
        assert confirmed[key]["source"] == "not visible"


def test_render_auto_confirm_keeps_missing_not_visible(monkeypatch):
    fake = FakeStreamlit(
        click_keys={"analyze_opportunity_btn"}, uploader_return=[_FakeUpload()]
    )
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        screenshot_screen, "extract_fields", lambda *a, **kw: _mixed_extracted()
    )

    fake.session_state["api_ok"] = True
    fake.session_state["evidence_index"] = ["proof"]

    screenshot_screen.render()

    confirmed = fake.session_state["confirmed_job_fields"]
    assert confirmed["budget_or_rate"]["value"] == NOT_VISIBLE
    assert confirmed["required_skills"]["value"] == NOT_VISIBLE


# ---------------------------------------------------------------------------
# 4. Confidence drops when important fields are missing
# ---------------------------------------------------------------------------


def test_confidence_reduces_when_critical_fields_missing():
    match_data = {"missing_critical_fields": list(CRITICAL_FIELDS)}
    strong = score(match_data, dossier_strength=80, missing_critical_fields=0)
    weak = score(match_data, dossier_strength=80, missing_critical_fields=len(CRITICAL_FIELDS))
    assert strong.confidence == "HIGH"
    assert weak.confidence == "LOW"


# ---------------------------------------------------------------------------
# 5. Confirm Details screen is gated to debug mode
# ---------------------------------------------------------------------------


def test_confirmation_screen_redirects_when_debug_off(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake)
    monkeypatch.setattr(
        confirmation_screen, "get_settings", lambda: _settings(show_debug_panel=False)
    )
    fake.session_state["extracted_job_fields"] = _mixed_extracted()

    # Already confirmed → straight to analysis.
    fake.session_state["fields_confirmed"] = True
    confirmation_screen.render()
    assert fake.session_state["current_step"] == "analysis"

    # Not yet confirmed → back to the screenshot step.
    fake.session_state["fields_confirmed"] = False
    confirmation_screen.render()
    assert fake.session_state["current_step"] == "screenshot"

    # The editable field table never rendered.
    joined = "\n".join(fake.texts)
    for group_title in ("Job basics", "Client details", "Budget & competition"):
        assert group_title not in joined


def test_confirmation_screen_available_when_debug_on(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake)
    monkeypatch.setattr(
        confirmation_screen, "get_settings", lambda: _settings(show_debug_panel=True)
    )
    fake.session_state["extracted_job_fields"] = _mixed_extracted()

    confirmation_screen.render()

    joined = "\n".join(fake.texts)
    for group_title in ("Job basics", "Client details", "Budget & competition"):
        assert group_title in joined
    assert "Confirm Details & Analyze" in joined


# ---------------------------------------------------------------------------
# 6. Analysis no longer routes normal users to a Confirm Details page
# ---------------------------------------------------------------------------


def test_analysis_lock_routes_to_screenshot_when_debug_off(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake)
    monkeypatch.setattr(
        analysis_screen, "get_settings", lambda: _settings(show_debug_panel=False)
    )
    fake.session_state["fields_confirmed"] = False

    analysis_screen.render()

    joined = "\n".join(fake.texts)
    assert "Confirm Details" not in joined
    assert "Back to Job Screenshot" in joined
