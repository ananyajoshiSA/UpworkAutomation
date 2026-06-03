"""Tests for per-opportunity analysis state.

Covers the lifecycle guarantees:

* uploading a new screenshot clears the previous opportunity's analysis,
* the analysis regenerates when the job fingerprint changes,
* the recommendation regenerates with it,
* a matching fingerprint reuses the cached analysis (no recompute),
* the job fingerprint / evidence IDs stay hidden on the normal UI.

A fake Streamlit records every call so the render code runs headless.
"""

from __future__ import annotations

import sys

import pytest

from app import config, main as app_main
from app.services.match_engine import job_fingerprint
from app.services.scoring import WEIGHTS, ScoreResult
from app.ui import analysis_screen, output_screen, screenshot_screen, theme
from app.services.screenshot_parser import NOT_VISIBLE, SCREENSHOT_FIELDS


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


def _install(monkeypatch, fake, modules):
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    for mod in modules:
        if hasattr(mod, "st"):
            monkeypatch.setattr(mod, "st", fake, raising=True)


def _field(value=NOT_VISIBLE):
    return {"value": value, "confidence": "high" if value != NOT_VISIBLE else "low",
            "source": "ocr extracted" if value != NOT_VISIBLE else "not visible"}


def _job(title, skills="Python") -> dict:
    job = {k: _field() for k in SCREENSHOT_FIELDS}
    job["job_title"] = _field(title)
    job["required_skills"] = _field(skills)
    job["client_need"] = _field(f"Need help with {title}")
    job["budget_or_rate"] = _field("$70/hr")
    job["proposal_count"] = _field("5")
    return job


# ---------------------------------------------------------------------------
# Compute fakes for analysis_screen (count calls, stamp fingerprints)
# ---------------------------------------------------------------------------


def _make_compute_fakes():
    calls = {"evaluate": 0, "score": 0, "recommend": 0}

    def fake_evaluate(confirmed_job, evidence_index, **kw):
        calls["evaluate"] += 1
        return {
            "job_fingerprint": job_fingerprint(confirmed_job),
            "skill_match": {"matched": ["python"], "missing": []},
            "missing_critical_fields": [],
            "beginner_evaluation": {
                "result": "Proceed With Caution",
                "reasons": ["Competition is high, so write a very strong proposal."],
                "missing_info_note": None,
            },
            "__meta__": {"used_api": True, "status": "ok"},
        }

    def fake_score(match_data, dossier_strength, missing_critical_fields, **kw):
        calls["score"] += 1
        return ScoreResult(
            total=72,
            sub_scores={k: 1 for k in WEIGHTS},
            confidence="MEDIUM",
            components={},
            job_fingerprint=(match_data or {}).get("job_fingerprint", ""),
        )

    def fake_recommend(score_result, match_data, **kw):
        calls["recommend"] += 1
        return {
            "verdict": "Proceed",
            "short_verdict": "Worth a look.",
            "why": "Skill overlap is solid.\nClient looks healthy.",
            "match_strengths": ["Skill overlap"],
            "concerns": ["Light proof"],
            "best_proposal_angle": "Lead with delivery speed",
            "connects_recommendation": "Spend connects, no boost.",
            "job_fingerprint": (match_data or {}).get("job_fingerprint", ""),
            "__meta__": {"used_api": True, "status": "ok"},
        }

    return calls, fake_evaluate, fake_score, fake_recommend


def _seed_cached_analysis(fake, job, *, fingerprint):
    """Seed session_state as if the analysis already ran for ``fingerprint``."""
    fake.session_state["fields_confirmed"] = True
    fake.session_state["confirmed_job_fields"] = job
    fake.session_state["evidence_index"] = ["proof"]
    fake.session_state["match_data"] = {
        "job_fingerprint": fingerprint,
        "skill_match": {"matched": ["python"], "missing": []},
        "missing_critical_fields": [],
        "__meta__": {"used_api": True, "status": "ok"},
    }
    fake.session_state["scoring_result"] = ScoreResult(
        total=72, sub_scores={k: 1 for k in WEIGHTS}, confidence="MEDIUM",
        components={}, job_fingerprint=fingerprint,
    )
    fake.session_state["recommendation_result"] = {
        "verdict": "Proceed", "why": "x\ny", "match_strengths": [], "concerns": [],
        "best_proposal_angle": "x", "connects_recommendation": "y",
        "job_fingerprint": fingerprint, "__meta__": {"used_api": True, "status": "ok"},
    }


# ---------------------------------------------------------------------------
# 1. clear_opportunity_state + defaults
# ---------------------------------------------------------------------------


def test_clear_opportunity_state_resets_all_keys():
    state = {k: "stale" for k in config.OPPORTUNITY_ANALYSIS_KEYS}
    state["fields_confirmed"] = True
    config.clear_opportunity_state(state)
    for key in config.OPPORTUNITY_ANALYSIS_KEYS:
        if key == "fields_confirmed":
            assert state[key] is False
        else:
            assert state[key] is None


def test_session_defaults_include_new_opportunity_keys():
    for key in (
        "current_job_fingerprint", "proposal_context", "selected_evidence_for_proposal",
    ):
        assert key in app_main.SESSION_DEFAULTS


# ---------------------------------------------------------------------------
# 2. A new screenshot clears the previous opportunity's scoring_result
# ---------------------------------------------------------------------------


def test_new_screenshot_clears_old_scoring_result(monkeypatch):
    fake = FakeStreamlit(
        click_keys={"analyze_opportunity_btn"}, uploader_return=[_FakeUpload()]
    )
    _install(monkeypatch, fake, [screenshot_screen, theme, app_main])
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())

    extracted = {k: _field() for k in SCREENSHOT_FIELDS}
    extracted["job_title"] = _field("Brand new opportunity")
    extracted["__meta__"] = {"used_api": True, "status": "ok"}
    monkeypatch.setattr(screenshot_screen, "extract_fields", lambda *a, **kw: extracted)

    # Stale analysis from a PREVIOUS opportunity sitting in session.
    fake.session_state["api_ok"] = True
    fake.session_state["evidence_index"] = ["proof"]
    fake.session_state["match_data"] = {"job_fingerprint": "old"}
    fake.session_state["scoring_result"] = ScoreResult(
        total=99, sub_scores={k: 9 for k in WEIGHTS}, confidence="HIGH",
        job_fingerprint="old",
    )
    fake.session_state["recommendation_result"] = {"verdict": "Strongly Proceed"}
    fake.session_state["verified_proposal"] = {"proposal": "old proposal"}
    fake.session_state["current_job_fingerprint"] = "old"

    screenshot_screen.render()

    # The previous opportunity's derived analysis is gone…
    assert fake.session_state["scoring_result"] is None
    assert fake.session_state["match_data"] is None
    assert fake.session_state["recommendation_result"] is None
    assert fake.session_state["verified_proposal"] is None
    assert fake.session_state["current_job_fingerprint"] is None
    # …and the new screenshot's fields were auto-confirmed.
    assert fake.session_state["fields_confirmed"] is True
    assert fake.session_state["confirmed_job_fields"]["job_title"]["value"] == (
        "Brand new opportunity"
    )


# ---------------------------------------------------------------------------
# 3. Analysis regenerates when the fingerprint changes
# ---------------------------------------------------------------------------


def test_analysis_regenerates_when_fingerprint_changes(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake, [analysis_screen, output_screen, theme])
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings())
    calls, fe, fs, fr = _make_compute_fakes()
    monkeypatch.setattr(analysis_screen, "evaluate", fe)
    monkeypatch.setattr(analysis_screen, "score", fs)
    monkeypatch.setattr(analysis_screen, "recommend", fr)
    # Heads-up labeling is exercised in test_analysis_heads_up; stub it here so
    # the per-opportunity state tests stay deterministic and offline.
    monkeypatch.setattr(analysis_screen, "_missing_field_labels", lambda *a, **k: [])

    job_a = _job("Python API work", skills="Python, React")
    job_b = _job("Logo design", skills="Illustrator, Branding")

    # Cached analysis belongs to job A; the screen now shows job B.
    _seed_cached_analysis(fake, job_a, fingerprint=job_fingerprint(job_a))
    fake.session_state["confirmed_job_fields"] = job_b

    analysis_screen.render()

    # It recomputed for the new opportunity…
    assert calls["evaluate"] == 1
    assert calls["score"] == 1
    assert calls["recommend"] == 1
    # …and the stored results now carry job B's fingerprint.
    fp_b = job_fingerprint(job_b)
    assert fake.session_state["current_job_fingerprint"] == fp_b
    assert fake.session_state["scoring_result"].job_fingerprint == fp_b
    assert fake.session_state["match_data"]["job_fingerprint"] == fp_b
    assert fake.session_state["recommendation_result"]["job_fingerprint"] == fp_b


def test_recommendation_regenerates_when_fingerprint_changes(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake, [analysis_screen, output_screen, theme])
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings())
    calls, fe, fs, fr = _make_compute_fakes()
    monkeypatch.setattr(analysis_screen, "evaluate", fe)
    monkeypatch.setattr(analysis_screen, "score", fs)
    monkeypatch.setattr(analysis_screen, "recommend", fr)
    # Heads-up labeling is exercised in test_analysis_heads_up; stub it here so
    # the per-opportunity state tests stay deterministic and offline.
    monkeypatch.setattr(analysis_screen, "_missing_field_labels", lambda *a, **k: [])

    job_a = _job("Python API work")
    job_b = _job("Data pipeline build", skills="Python, Airflow")
    _seed_cached_analysis(fake, job_a, fingerprint=job_fingerprint(job_a))
    fake.session_state["confirmed_job_fields"] = job_b

    analysis_screen.render()

    assert calls["recommend"] == 1
    assert fake.session_state["recommendation_result"]["job_fingerprint"] == (
        job_fingerprint(job_b)
    )


def test_analysis_is_cached_when_fingerprint_matches(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake, [analysis_screen, output_screen, theme])
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings())
    calls, fe, fs, fr = _make_compute_fakes()
    monkeypatch.setattr(analysis_screen, "evaluate", fe)
    monkeypatch.setattr(analysis_screen, "score", fs)
    monkeypatch.setattr(analysis_screen, "recommend", fr)
    # Heads-up labeling is exercised in test_analysis_heads_up; stub it here so
    # the per-opportunity state tests stay deterministic and offline.
    monkeypatch.setattr(analysis_screen, "_missing_field_labels", lambda *a, **k: [])

    job = _job("Python API work")
    _seed_cached_analysis(fake, job, fingerprint=job_fingerprint(job))

    analysis_screen.render()

    # Fingerprint matches the cached analysis → no recompute, no extra API.
    assert calls == {"evaluate": 0, "score": 0, "recommend": 0}


def test_rerun_analysis_button_forces_recompute(monkeypatch):
    fake = FakeStreamlit(click_keys={"rerun_analysis_btn"})
    _install(monkeypatch, fake, [analysis_screen, output_screen, theme])
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings())
    calls, fe, fs, fr = _make_compute_fakes()
    monkeypatch.setattr(analysis_screen, "evaluate", fe)
    monkeypatch.setattr(analysis_screen, "score", fs)
    monkeypatch.setattr(analysis_screen, "recommend", fr)
    # Heads-up labeling is exercised in test_analysis_heads_up; stub it here so
    # the per-opportunity state tests stay deterministic and offline.
    monkeypatch.setattr(analysis_screen, "_missing_field_labels", lambda *a, **k: [])

    job = _job("Python API work")
    _seed_cached_analysis(fake, job, fingerprint=job_fingerprint(job))

    analysis_screen.render()

    # Even though the fingerprint matched, the explicit re-run recomputed.
    assert calls["evaluate"] == 1
    assert "Analysis updated for this opportunity." in "\n".join(fake.texts)


# ---------------------------------------------------------------------------
# 4. The fingerprint / evidence IDs stay off the normal UI
# ---------------------------------------------------------------------------


def test_analysis_hides_fingerprint_when_debug_off(monkeypatch):
    fake = FakeStreamlit()
    _install(monkeypatch, fake, [analysis_screen, output_screen, theme])
    monkeypatch.setattr(analysis_screen, "get_settings", lambda: _settings(show_debug_panel=False))
    calls, fe, fs, fr = _make_compute_fakes()
    monkeypatch.setattr(analysis_screen, "evaluate", fe)
    monkeypatch.setattr(analysis_screen, "score", fs)
    monkeypatch.setattr(analysis_screen, "recommend", fr)
    # Heads-up labeling is exercised in test_analysis_heads_up; stub it here so
    # the per-opportunity state tests stay deterministic and offline.
    monkeypatch.setattr(analysis_screen, "_missing_field_labels", lambda *a, **k: [])

    job = _job("Python API work")
    fake.session_state["fields_confirmed"] = True
    fake.session_state["confirmed_job_fields"] = job
    fake.session_state["evidence_index"] = ["proof"]

    analysis_screen.render()

    joined = "\n".join(fake.texts)
    fp = job_fingerprint(job)
    assert fp not in joined
    assert "job_fingerprint" not in joined
    assert "evidence_id" not in joined.lower()
    # Clean verdict still renders; the score card, LLM recommendation card,
    # and fingerprint are all debug-only now.
    assert "Verdict" in joined
    assert "Proceed With Caution" in joined
    assert "Fit score" not in joined
    assert "Recommendation" not in joined
