"""Tests for the "Read Dossier" button behaviour in the dossier screen.

These render ``dossier_screen`` against a fake Streamlit so we can assert:

* Clicking "Read Dossier" stores chunks in ``st.session_state`` and sets
  ``dossier_read = True``.
* A success summary (files processed / chunks extracted / failed files)
  is rendered after a read.
* Raw extracted dossier text is NEVER shown in the normal UI — with the
  debug panel off *or* on.
* "Create Evidence Index" is disabled until the dossier has been read.
"""

from __future__ import annotations

import sys

import pytest

from app import config
from app.ui import dossier_screen, theme
from app.services.folder_validator import validate


SENTINEL = "SENTINEL_RAW_DOSSIER_TEXT_77321"


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
    def __init__(self, parent: "FakeStreamlit"):
        self._parent = parent

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._parent._record(f"col.{name}", *a, **kw)

        return _call


class FakeStreamlit:
    """Records calls/text. ``click_label`` makes one button return True."""

    def __init__(self, click_label: str | None = None):
        self.calls: list[tuple] = []
        self.texts: list[str] = []
        self.session_state = _SessionState()
        self.click_label = click_label

    def _record(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        for x in a:
            if isinstance(x, str):
                self.texts.append(x)
        for v in kw.values():
            if isinstance(v, str):
                self.texts.append(v)
        return None

    def button(self, label, *a, **kw):
        self._record("button", label, *a, **kw)
        return label == self.click_label

    def text_input(self, *a, **kw):
        self._record("text_input", *a, **kw)
        return ""

    def columns(self, spec):
        n = spec if isinstance(spec, int) else len(spec)
        return tuple(_Col(self) for _ in range(n))

    def container(self, *a, **kw):
        self._record("container", *a, **kw)
        return _CtxNoop()

    def expander(self, *a, **kw):
        self._record("expander", *a, **kw)
        return _CtxNoop()

    def spinner(self, *a, **kw):
        self._record("spinner", *a, **kw)
        return _CtxNoop()

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._record(name, *a, **kw)

        return _call


def _settings(*, show_debug_panel=False):
    return config.Settings(
        llm_provider="anthropic",
        anthropic_api_key="sk-ant-test-key",
        anthropic_model="claude-sonnet-4-6",
        openai_api_key=None,
        openai_model="gpt-4o",
        allow_local_placeholders=False,
        max_proposal_context_chars=15000,
        max_proposal_evidence_points=20,
        proposal_max_output_tokens=700,
        show_debug_panel=show_debug_panel,
    )


def _make_dossier(tmp_path):
    (tmp_path / "resume.txt").write_text(f"{SENTINEL} — eight years of experience.")
    (tmp_path / "profile.json").write_text('{"name": "Alex", "skills": ["python"]}')
    return validate(tmp_path)


def _install(monkeypatch, fake, *, show_debug_panel=False):
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    monkeypatch.setattr(dossier_screen, "st", fake, raising=True)
    monkeypatch.setattr(theme, "st", fake, raising=True)
    monkeypatch.setattr(
        dossier_screen, "get_settings", lambda: _settings(show_debug_panel=show_debug_panel)
    )


def _button_call(fake, label):
    for name, args, kwargs in fake.calls:
        if name == "button" and args and args[0] == label:
            return kwargs
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_read_dossier_button_stores_chunks_in_session_state(tmp_path, monkeypatch):
    validation = _make_dossier(tmp_path)
    fake = FakeStreamlit(click_label="Read Dossier")
    _install(monkeypatch, fake, show_debug_panel=True)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    chunks = fake.session_state.get("dossier_chunks")
    assert chunks, "chunks should be stored after clicking Read Dossier"
    assert fake.session_state.get("dossier_read") is True
    file_names = {c.file_name for c in chunks}
    assert {"resume.txt", "profile.json"} <= file_names


def test_read_dossier_shows_success_summary(tmp_path, monkeypatch):
    validation = _make_dossier(tmp_path)
    fake = FakeStreamlit(click_label="Read Dossier")
    _install(monkeypatch, fake, show_debug_panel=True)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    joined = "\n".join(fake.texts)
    assert "Dossier read successfully" in joined
    # Summary metric labels are present.
    assert "Files processed" in joined
    assert "Chunks extracted" in joined
    assert "Failed files" in joined


def test_raw_dossier_text_not_shown_when_debug_off(tmp_path, monkeypatch):
    # Normal mode: the single "Continue to Job Screenshot" button runs the
    # read + index chain silently. Raw dossier text must never reach the UI.
    validation = _make_dossier(tmp_path)
    fake = FakeStreamlit(click_label="Continue to Job Screenshot")
    _install(monkeypatch, fake, show_debug_panel=False)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    joined = "\n".join(fake.texts)
    assert SENTINEL not in joined, "raw dossier text must never reach the UI"
    # The silent chain ran end-to-end and advanced to the screenshot step.
    assert fake.session_state.get("evidence_index")
    assert fake.session_state.get("current_step") == "screenshot"


def test_raw_dossier_text_not_shown_even_when_debug_on(tmp_path, monkeypatch):
    # The debug panel may reveal file names / status / counts, but never the
    # extracted text itself.
    validation = _make_dossier(tmp_path)
    fake = FakeStreamlit(click_label="Read Dossier")
    _install(monkeypatch, fake, show_debug_panel=True)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    joined = "\n".join(fake.texts)
    assert SENTINEL not in joined


def test_create_evidence_index_disabled_until_dossier_read(tmp_path, monkeypatch):
    validation = _make_dossier(tmp_path)
    # No click this time — just render the validated-but-not-read state.
    fake = FakeStreamlit(click_label=None)
    _install(monkeypatch, fake, show_debug_panel=True)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    kwargs = _button_call(fake, "Create Evidence Index")
    assert kwargs is not None
    assert kwargs.get("disabled") is True
    assert fake.session_state.get("dossier_read") in (None, False)


def test_create_evidence_index_enabled_after_dossier_read(tmp_path, monkeypatch):
    validation = _make_dossier(tmp_path)
    fake = FakeStreamlit(click_label="Read Dossier")
    _install(monkeypatch, fake, show_debug_panel=True)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    kwargs = _button_call(fake, "Create Evidence Index")
    assert kwargs is not None
    assert kwargs.get("disabled") is False


def test_dossier_normal_mode_single_button_no_file_count(tmp_path, monkeypatch):
    # Normal (debug-off) mode: exactly ONE button, NO file count, and the
    # misleading dossier-strength score fully hidden. The single button runs
    # validate + read + index behind the scenes.
    validation = _make_dossier(tmp_path)
    fake = FakeStreamlit(click_label=None)
    _install(monkeypatch, fake, show_debug_panel=False)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    labels = [a[0] for name, a, _kw in fake.calls if name == "button" and a]
    assert labels == ["Continue to Job Screenshot"]

    joined = "\n".join(fake.texts)
    assert "Dossier Files" not in joined
    assert "Dossier strength" not in joined
    assert "/100" not in joined
    assert "consider adding" not in joined


def test_normal_mode_bad_folder_shows_clean_error_and_stays(tmp_path, monkeypatch):
    # Normal mode: the single button validates first. A bad path must show one
    # clean inline error, NOT advance, and NOT raise a traceback.
    bad_path = str(tmp_path / "does-not-exist")
    fake = FakeStreamlit(click_label="Continue to Job Screenshot")
    _install(monkeypatch, fake, show_debug_panel=False)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_folder_path"] = bad_path

    dossier_screen.render()

    joined = "\n".join(fake.texts)
    assert "Couldn't read that folder. Check the path and try again." in joined
    # Did not advance, and no evidence was built.
    assert fake.session_state.get("current_step") != "screenshot"
    assert not fake.session_state.get("evidence_index")
    # No raw paths / tracebacks leaked as an error string.
    assert "Traceback" not in joined


def test_failed_files_warning_is_clean(tmp_path, monkeypatch):
    (tmp_path / "good.txt").write_text("readable content")
    (tmp_path / "broken.json").write_text("{not valid json")
    validation = validate(tmp_path)
    fake = FakeStreamlit(click_label="Read Dossier")
    _install(monkeypatch, fake, show_debug_panel=True)
    fake.session_state["api_ok"] = True
    fake.session_state["dossier_validation"] = validation
    fake.session_state["dossier_folder_path"] = str(tmp_path)

    dossier_screen.render()

    joined = "\n".join(fake.texts)
    assert "Some files could not be read, but the app continued." in joined
