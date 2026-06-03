"""Tests for the Analysis "Heads up" missing-details card.

Covers the polished behaviour:

* the specific missing fields for THIS job are detected,
* the API is used for short plain-English bullet labels, with a
  deterministic flag→label fallback when the call fails / returns junk,
* a raw flag token (e.g. ``client_need``) is NEVER rendered,
* the card shows a header + short bullets when something is missing, and
* the card is hidden entirely when every tracked field is visible.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app import config
from app.ui import analysis_screen, output_screen, theme


# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


NOT_VISIBLE = "Not visible"


def _field(value=NOT_VISIBLE):
    return {
        "value": value,
        "confidence": "high" if value != NOT_VISIBLE else "low",
        "source": "ocr extracted" if value != NOT_VISIBLE else "not visible",
    }


def _settings(*, has_key=True):
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
        show_debug_panel=False,
    )


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
    def __init__(self, *, click_keys=()):
        self.calls: list[tuple] = []
        self.texts: list[str] = []
        self.session_state = _SessionState()
        self.click_keys = set(click_keys)

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

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._record(name, *a, **kw)

        return _call


def _install_render(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    for mod in (analysis_screen, output_screen, theme):
        monkeypatch.setattr(mod, "st", fake, raising=True)
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        analysis_screen,
        "evaluate",
        lambda *a, **kw: {
            "skill_match": {"matched": [], "missing": []},
            "missing_critical_fields": [],
            "beginner_evaluation": {
                "result": "Proceed With Caution",
                "reasons": ["Competition is high; write a strong proposal."],
                "missing_info_note": None,
            },
            "__meta__": {"used_api": True, "status": "ok"},
        },
    )
    monkeypatch.setattr(
        analysis_screen,
        "score",
        lambda *a, **kw: SimpleNamespace(
            total=70, sub_scores={}, confidence="MEDIUM", job_fingerprint="",
            components={},
        ),
    )
    monkeypatch.setattr(
        analysis_screen,
        "recommend",
        lambda *a, **kw: {
            "verdict": "Proceed",
            "why": "Skill overlap is solid.",
            "match_strengths": [],
            "concerns": [],
            "best_proposal_angle": "Lead with speed",
            "connects_recommendation": "Spend connects.",
            "__meta__": {"used_api": True, "status": "ok"},
        },
    )


# ---------------------------------------------------------------------------
# Unit: which fields are missing
# ---------------------------------------------------------------------------


def test_missing_heads_up_fields_detects_only_actually_missing():
    job = {f: _field("present") for f in analysis_screen._HEADS_UP_FIELDS}
    job["job_title"] = _field("A title")
    assert analysis_screen._missing_heads_up_fields(job) == []

    job["budget_or_rate"] = _field()  # Not visible
    job["experience_level"] = _field()
    assert analysis_screen._missing_heads_up_fields(job) == [
        "budget_or_rate",
        "experience_level",
    ]


# ---------------------------------------------------------------------------
# Unit: label generation (API + fallback) — item 5
# ---------------------------------------------------------------------------


def test_missing_field_labels_uses_api_and_coerces(monkeypatch):
    captured = {}

    def fake_call(*a, **kw):
        captured["task"] = kw.get("task_name")
        return SimpleNamespace(
            success=True,
            response_json={
                "labels": [
                    "Budget / rate",
                    "client_need",  # raw flag — must be dropped
                    "Experience level",
                    "way too long a label to ever be a short bullet for sure",
                    "When it was posted",
                ]
            },
        )

    monkeypatch.setattr(analysis_screen.llm_client, "call_text_llm", fake_call)

    labels = analysis_screen._missing_field_labels(
        ["budget_or_rate", "client_need", "experience_level"],
        {"job_title": _field("Build an API")},
        _settings(),
    )
    # Underscore-bearing and over-long labels are dropped; the rest survive.
    assert labels == ["Budget / rate", "Experience level", "When it was posted"]
    assert captured["task"] == "missing_info_labeling"
    assert not any("_" in label for label in labels)


def test_missing_field_labels_falls_back_when_api_fails(monkeypatch):
    monkeypatch.setattr(
        analysis_screen.llm_client,
        "call_text_llm",
        lambda *a, **k: SimpleNamespace(success=False, response_json=None),
    )
    labels = analysis_screen._missing_field_labels(
        ["budget_or_rate", "experience_level"], {}, _settings()
    )
    assert labels == ["Budget / rate", "Experience level"]


def test_missing_field_labels_falls_back_on_malformed_json(monkeypatch):
    # A successful call that returns junk (no usable labels) → fallback map.
    monkeypatch.setattr(
        analysis_screen.llm_client,
        "call_text_llm",
        lambda *a, **k: SimpleNamespace(success=True, response_json={"nope": 1}),
    )
    labels = analysis_screen._missing_field_labels(
        ["proposal_count"], {}, _settings()
    )
    assert labels == ["Number of proposals"]


def test_missing_field_labels_skips_api_without_key(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the API must not be called without a key")

    monkeypatch.setattr(analysis_screen.llm_client, "call_text_llm", boom)
    labels = analysis_screen._missing_field_labels(
        ["budget_or_rate"], {}, _settings(has_key=False)
    )
    assert labels == ["Budget / rate"]


def test_missing_field_labels_empty_when_nothing_missing():
    assert analysis_screen._missing_field_labels([], {}, _settings()) == []


# ---------------------------------------------------------------------------
# Render: bullets shown for missing fields, no raw flags — item 3
# ---------------------------------------------------------------------------


def test_heads_up_renders_short_bullets_and_no_raw_flags(monkeypatch):
    fake = FakeStreamlit()
    _install_render(monkeypatch, fake)
    # Drive the bullets deterministically (network-free).
    monkeypatch.setattr(
        analysis_screen,
        "_missing_field_labels",
        lambda *a, **k: ["Budget / rate", "Experience level", "When it was posted"],
    )

    job = {f: _field() for f in analysis_screen._HEADS_UP_FIELDS}  # all Not visible
    job["job_title"] = _field("Some job")
    fake.session_state["fields_confirmed"] = True
    fake.session_state["confirmed_job_fields"] = job
    fake.session_state["evidence_index"] = ["proof"]
    # Strong dossier so the "light dossier" line doesn't muddy the assertion.
    fake.session_state["dossier_validation"] = SimpleNamespace(strength_score=80)

    analysis_screen.render()

    joined = "\n".join(fake.texts)
    assert "Heads up" in joined
    assert "Some details weren't visible, so this is less certain:" in joined
    for bullet in ("Budget / rate", "Experience level", "When it was posted"):
        assert f"- {bullet}" in joined
    # No raw flag tokens anywhere.
    for flag in analysis_screen._HEADS_UP_FIELDS:
        assert flag not in joined


# ---------------------------------------------------------------------------
# Render: card hidden when nothing is missing — item 4
# ---------------------------------------------------------------------------


def test_heads_up_hidden_when_all_fields_visible(monkeypatch):
    fake = FakeStreamlit()
    _install_render(monkeypatch, fake)

    job = {f: _field("clearly visible") for f in analysis_screen._HEADS_UP_FIELDS}
    job["job_title"] = _field("Some job")
    fake.session_state["fields_confirmed"] = True
    fake.session_state["confirmed_job_fields"] = job
    fake.session_state["evidence_index"] = ["proof"]
    fake.session_state["dossier_validation"] = SimpleNamespace(strength_score=80)

    analysis_screen.render()

    joined = "\n".join(fake.texts)
    assert "Heads up" not in joined
    assert "less certain" not in joined
