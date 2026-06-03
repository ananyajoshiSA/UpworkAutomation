"""Headless smoke tests for the polished UI.

Each screen is rendered against a fake Streamlit so we can assert that:

* the screen renders without raising, and
* with ``SHOW_DEBUG_PANEL=false`` no technical/debug jargon leaks into the
  user-facing copy (provider/model names, task names, internal field
  names, fallback markers, stack traces, etc.).

The fake records every call and every string the screen would have drawn.
"""

from __future__ import annotations

import sys

import pytest

from app import config
from app.services.scoring import WEIGHTS, ScoreResult
from app.ui import (
    analysis_screen,
    confirmation_screen,
    dossier_screen,
    output_screen,
    proposal_screen,
    screenshot_screen,
    setup_screen,
    theme,
)
from app.services.screenshot_parser import SCREENSHOT_FIELDS


# Terms that must never appear in user-facing copy when debug is off.
FORBIDDEN_WHEN_DEBUG_OFF = [
    "chunk",
    "source_priority",
    "evidence_id",
    "parser",
    "local placeholder",
    "api not used",
    "local fallback",
    "traceback",
    "opportunity_matching",
    "recommendation_generation",
    "screenshot_extraction",
    "claude",
    "anthropic",
    "gpt-4o",
    "session_state",
    "__meta__",
    "llm",
]


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
    def __init__(self):
        self.calls: list[tuple] = []
        self.texts: list[str] = []
        self.session_state = _SessionState()

    def _record(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        for x in a:
            if isinstance(x, str):
                self.texts.append(x)
        for v in kw.values():
            if isinstance(v, str):
                self.texts.append(v)
        return None

    # context managers
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

    # value-returning widgets
    def button(self, *a, **kw):
        self._record("button", *a, **kw)
        return False

    def checkbox(self, *a, **kw):
        self._record("checkbox", *a, **kw)
        return False

    def download_button(self, *a, **kw):
        self._record("download_button", *a, **kw)
        return False

    def text_input(self, *a, **kw):
        self._record("text_input", *a, **kw)
        return ""

    def text_area(self, *a, **kw):
        self._record("text_area", *a, **kw)
        return ""

    def selectbox(self, label, options=(), index=0, **kw):
        self._record("selectbox", label, **kw)
        opts = list(options)
        return opts[index] if opts else None

    def file_uploader(self, *a, **kw):
        self._record("file_uploader", *a, **kw)
        return None

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._record(name, *a, **kw)

        return _call


def _settings(*, show_debug_panel=False, has_key=True, allow_local=False):
    return config.Settings(
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-key" if has_key else None,
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=allow_local,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
        show_debug_panel=show_debug_panel,
    )


_UI_MODULES = [
    setup_screen,
    dossier_screen,
    screenshot_screen,
    confirmation_screen,
    analysis_screen,
    proposal_screen,
    output_screen,
    theme,
]


@pytest.fixture()
def fake(monkeypatch):
    fk = FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fk)
    for mod in _UI_MODULES:
        if hasattr(mod, "st"):
            monkeypatch.setattr(mod, "st", fk, raising=True)
    return fk


def _assert_clean(fake):
    joined = "\n".join(fake.texts).lower()
    for term in FORBIDDEN_WHEN_DEBUG_OFF:
        assert term not in joined, f"leaked technical term: {term!r}"


def _extracted_fields():
    return {
        key: {"value": "Not visible", "confidence": "low", "source": "not visible"}
        for key in SCREENSHOT_FIELDS
    }


def test_setup_screen_clean(fake, monkeypatch):
    # The Setup screen is now the configuration surface: it intentionally
    # shows provider/model choices and model examples (so the blanket
    # provider/model jargon check in _assert_clean does NOT apply here). It
    # must still render the config form, keep the data notice, never show the
    # raw API key, and never leak internal task names / fallback markers.
    monkeypatch.setattr(setup_screen, "get_settings", lambda: _settings())
    fake.session_state["api_ok"] = True
    fake.session_state["api_status"] = "API OK"
    setup_screen.render()
    joined = "\n".join(fake.texts)

    assert "Configure AI Service" in joined
    assert "Save Configuration" in joined
    assert setup_screen.MINIMAL_DATA_NOTICE in joined

    # Secret-safety: the raw key is never shown; a masked status is fine.
    assert "sk-ant-test-key" not in joined
    assert config.mask_secret("sk-ant-test-key") in joined

    # No acceptance-style checkbox on the setup screen.
    assert not any(name == "checkbox" for name, _, _ in fake.calls)

    # Internal task names and stack traces never leak on setup. ("Allow local
    # placeholders" is a legitimate advanced-config label, so runtime fallback
    # markers are not part of this setup-screen check.)
    lowered = joined.lower()
    for term in (
        "opportunity_matching",
        "recommendation_generation",
        "screenshot_extraction",
        "verification_pass",
        "traceback",
    ):
        assert term not in lowered


def test_dossier_screen_clean(fake, monkeypatch):
    monkeypatch.setattr(dossier_screen, "get_settings", lambda: _settings())
    fake.session_state["api_ok"] = True
    dossier_screen.render()
    _assert_clean(fake)
    joined = "\n".join(fake.texts)
    # Normal mode collapses to a SINGLE button that runs validate + read +
    # index behind the scenes; "Validate Folder" is debug-only now.
    assert "Continue to Job Screenshot" in joined
    assert "Validate Folder" not in joined


def test_screenshot_screen_clean(fake, monkeypatch):
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    fake.session_state["api_ok"] = True
    fake.session_state["evidence_index"] = ["proof"]
    fake.session_state["extracted_job_fields"] = _extracted_fields()
    screenshot_screen.render()
    _assert_clean(fake)


def test_confirmation_screen_hidden_when_debug_off(fake, monkeypatch):
    # Confirm Details is debug-only: with the panel off it never renders the
    # editable field table — it bounces the user back into the normal flow.
    monkeypatch.setattr(
        confirmation_screen, "get_settings", lambda: _settings(show_debug_panel=False)
    )
    fake.session_state["extracted_job_fields"] = _extracted_fields()
    confirmation_screen.render()
    _assert_clean(fake)
    joined = "\n".join(fake.texts)
    for group_title in ("Job basics", "Client details", "Budget & competition"):
        assert group_title not in joined
    # Redirected away from the confirmation step.
    assert fake.session_state["current_step"] != "confirmation"


def test_confirmation_screen_available_when_debug_on(fake, monkeypatch):
    # With the debug panel on, the developer/admin review page renders the
    # editable, grouped field table.
    monkeypatch.setattr(
        confirmation_screen, "get_settings", lambda: _settings(show_debug_panel=True)
    )
    fake.session_state["extracted_job_fields"] = _extracted_fields()
    confirmation_screen.render()
    joined = "\n".join(fake.texts)
    for group_title in ("Job basics", "Client details", "Budget & competition"):
        assert group_title in joined
    assert "Confirm Details & Analyze" in joined


def test_analysis_screen_clean(fake, monkeypatch):
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(output_screen, "st", fake, raising=True)
    # Stub the Heads-up labeling so the smoke test never touches the network;
    # the labels themselves are clean plain-English bullets.
    monkeypatch.setattr(
        analysis_screen,
        "_missing_field_labels",
        lambda *a, **k: ["Budget / rate", "Experience level"],
    )

    monkeypatch.setattr(
        analysis_screen,
        "evaluate",
        lambda *a, **kw: {
            "skill_match": {"matched": ["python"], "missing": []},
            "missing_critical_fields": [],
            "beginner_evaluation": {
                "result": "Proceed With Caution",
                "reasons": [
                    "Competition is high, so the proposal must be very strong."
                ],
                "missing_info_note": None,
            },
            "__meta__": {"used_api": True, "status": "ok"},
        },
    )
    monkeypatch.setattr(
        analysis_screen,
        "score",
        lambda *a, **kw: ScoreResult(
            total=72,
            sub_scores={k: 1 for k in WEIGHTS},
            confidence="MEDIUM",
        ),
    )
    monkeypatch.setattr(
        analysis_screen,
        "recommend",
        lambda *a, **kw: {
            "verdict": "Proceed",
            "short_verdict": "Solid fit worth pursuing.",
            "why": "Strong skill overlap.\nClient signals look healthy.",
            "match_strengths": ["Skill overlap on python"],
            "concerns": ["Budget is below your target"],
            "best_proposal_angle": "Lead with delivery speed",
            "connects_recommendation": "Spend connects, no boost needed.",
            "__meta__": {"used_api": True, "status": "ok"},
        },
    )

    fake.session_state["fields_confirmed"] = True
    fake.session_state["confirmed_job_fields"] = _extracted_fields()
    fake.session_state["evidence_index"] = ["proof"]
    analysis_screen.render()

    _assert_clean(fake)
    joined = "\n".join(fake.texts)
    # The verdict shown is the deterministic beginner-checklist result, not a
    # numeric score or the LLM verdict (which is debug-only now).
    assert "Verdict" in joined
    assert "Proceed With Caution" in joined
    # No numeric fit score / scoring UI in normal mode.
    assert "Fit score" not in joined
    assert "/ 100" not in joined
    # The Heads up card names the missing details as short bullets, never as
    # raw flag tokens.
    assert "Some details weren't visible" in joined
    assert "Budget / rate" in joined
    assert "client_need" not in joined
    assert "budget_or_rate" not in joined


def test_proposal_screen_clean(fake, monkeypatch):
    monkeypatch.setattr(proposal_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(output_screen, "st", fake, raising=True)
    fake.session_state["fields_confirmed"] = True
    fake.session_state["recommendation_result"] = {
        "verdict": "Proceed",
        "__meta__": {"used_api": True, "status": "ok"},
    }
    fake.session_state["confirmed_job_fields"] = _extracted_fields()
    fake.session_state["evidence_index"] = ["proof"]
    proposal_screen.render()
    _assert_clean(fake)
    joined = "\n".join(fake.texts)
    assert "Generate Proposal" in joined
