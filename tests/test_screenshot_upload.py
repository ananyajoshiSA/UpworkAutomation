"""Tests for reliable screenshot upload handling.

These cover the upload contract the Job Screenshot screen must honour:

* the native ``st.file_uploader`` result is read into session state as
  ``{name, mime, bytes}`` records under ``uploaded_screenshots`` — the
  image is held in memory, never written to disk;
* "Extract Job Details" is enabled exactly when the API is ready and at
  least one screenshot is present;
* uploading a new/swapped screenshot clears the previous opportunity's
  analysis;
* the vision parser receives raw image bytes, not file paths;
* screenshot bytes are never written to the usage log or the app log.

A fake Streamlit records every call so the screen renders headless.
"""

from __future__ import annotations

import logging
import sys

import pytest

from app import config
from app.services import llm_client
from app.services.screenshot_parser import (
    NOT_VISIBLE,
    SCREENSHOT_FIELDS,
    extract_fields,
)
from app.ui import screenshot_screen, theme


# ---------------------------------------------------------------------------
# Fakes
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

    def file_uploader(self, *a, **kw):
        self._record("file_uploader", *a, **kw)
        return self.uploader_return

    def __getattr__(self, name):
        def _call(*a, **kw):
            return self._record(name, *a, **kw)

        return _call

    def button_kwargs(self, key):
        for name, _a, kw in self.calls:
            if name == "button" and kw.get("key") == key:
                return kw
        return None


class _FakeUpload:
    """Minimal stand-in for a Streamlit ``UploadedFile``."""

    def __init__(self, name="job.png", mime="image/png", data=b"\x89PNG-bytes"):
        self.name = name
        self.type = mime
        self._data = data

    def getvalue(self):
        return self._data


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


def _install(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    for mod in (screenshot_screen, theme):
        if hasattr(mod, "st"):
            monkeypatch.setattr(mod, "st", fake, raising=True)


def _ready_session(fake):
    """Unlock the screen: API ok + evidence index present."""
    fake.session_state["api_ok"] = True
    fake.session_state["evidence_index"] = ["proof"]


# ---------------------------------------------------------------------------
# 1. The uploader stores file metadata + bytes in session_state
# ---------------------------------------------------------------------------


def test_uploader_stores_metadata_and_bytes_in_session_state(monkeypatch):
    upload = _FakeUpload(name="post.webp", mime="image/webp", data=b"webp-bytes")
    fake = FakeStreamlit(uploader_return=[upload])
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    screenshot_screen.render()

    stored = fake.session_state["uploaded_screenshots"]
    assert isinstance(stored, list) and len(stored) == 1
    assert stored[0] == {
        "name": "post.webp",
        "mime": "image/webp",
        "bytes": b"webp-bytes",
    }
    assert fake.session_state["screenshots_uploaded"] is True


def test_uploader_accepts_the_expected_image_types(monkeypatch):
    fake = FakeStreamlit(uploader_return=None)
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    screenshot_screen.render()

    for name, _a, kw in fake.calls:
        if name == "file_uploader":
            assert kw.get("type") == ["png", "jpg", "jpeg", "webp"]
            assert kw.get("accept_multiple_files") is True
            assert kw.get("key") == "screenshot_uploader"
            break
    else:  # pragma: no cover - the uploader must be rendered
        pytest.fail("file_uploader was not rendered")


# ---------------------------------------------------------------------------
# 1b. Clipboard paste feeds the same downstream handler as the uploader
# ---------------------------------------------------------------------------


def test_clipboard_paste_feeds_the_same_handler(monkeypatch):
    from PIL import Image

    img = Image.new("RGB", (4, 4), (255, 0, 0))

    class _PasteResult:
        image_data = img

    def _fake_paste(*_a, **_kw):
        return _PasteResult()

    # No file uploaded — the image arrives purely via the paste component.
    fake = FakeStreamlit(uploader_return=None)
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    monkeypatch.setattr(screenshot_screen, "_paste_image_button", _fake_paste)
    _ready_session(fake)

    screenshot_screen.render()

    stored = fake.session_state["uploaded_screenshots"]
    assert isinstance(stored, list) and len(stored) == 1
    assert stored[0]["name"] == "pasted-screenshot.png"
    assert stored[0]["mime"] == "image/png"
    assert isinstance(stored[0]["bytes"], (bytes, bytearray)) and stored[0]["bytes"]
    assert fake.session_state["screenshots_uploaded"] is True
    # A pasted screenshot alone enables the single Analyze button.
    kw = fake.button_kwargs("analyze_opportunity_btn")
    assert kw is not None and kw["disabled"] is False


# ---------------------------------------------------------------------------
# 2. Extract Job Details enablement
# ---------------------------------------------------------------------------


def test_extract_button_enabled_when_screenshot_present(monkeypatch):
    fake = FakeStreamlit(uploader_return=[_FakeUpload()])
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    screenshot_screen.render()

    # Normal mode merges extract + analysis into one "Analyze Opportunity" btn.
    kw = fake.button_kwargs("analyze_opportunity_btn")
    assert kw is not None
    assert kw["disabled"] is False


def test_extract_button_disabled_without_upload(monkeypatch):
    fake = FakeStreamlit(uploader_return=None)
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    screenshot_screen.render()

    kw = fake.button_kwargs("analyze_opportunity_btn")
    assert kw is not None
    assert kw["disabled"] is True
    assert fake.session_state["uploaded_screenshots"] == []


def test_extract_button_disabled_when_api_not_ready(monkeypatch):
    fake = FakeStreamlit(uploader_return=[_FakeUpload()])
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    fake.session_state["api_ok"] = False
    fake.session_state["evidence_index"] = ["proof"]

    screenshot_screen.render()

    kw = fake.button_kwargs("analyze_opportunity_btn")
    assert kw is not None
    assert kw["disabled"] is True


# ---------------------------------------------------------------------------
# 3. A new screenshot clears stale analysis (without re-extracting)
# ---------------------------------------------------------------------------


def test_new_screenshot_clears_stale_analysis(monkeypatch):
    fake = FakeStreamlit(uploader_return=[_FakeUpload(name="new.png", data=b"new")])
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    # Stale results + an OLD upload signature already in session.
    fake.session_state["screenshot_upload_sig"] = (("old.png", 3),)
    fake.session_state["extracted_job_fields"] = {"job_title": {"value": "Old"}}
    fake.session_state["confirmed_job_fields"] = {"job_title": {"value": "Old"}}
    fake.session_state["fields_confirmed"] = True
    fake.session_state["match_data"] = {"job_fingerprint": "old"}
    fake.session_state["scoring_result"] = "stale-score"
    fake.session_state["recommendation_result"] = {"verdict": "old"}
    fake.session_state["generated_proposal"] = {"proposal": "old"}
    fake.session_state["verified_proposal"] = {"proposal": "old"}

    screenshot_screen.render()

    assert fake.session_state["extracted_job_fields"] is None
    assert fake.session_state["confirmed_job_fields"] is None
    assert fake.session_state["fields_confirmed"] is False
    assert fake.session_state["match_data"] is None
    assert fake.session_state["scoring_result"] is None
    assert fake.session_state["recommendation_result"] is None
    assert fake.session_state["generated_proposal"] is None
    assert fake.session_state["verified_proposal"] is None
    # The new upload's signature is now recorded.
    assert fake.session_state["screenshot_upload_sig"] == (("new.png", 3),)


def test_same_screenshot_does_not_clear_extracted_fields(monkeypatch):
    upload = _FakeUpload(name="same.png", data=b"abc")
    fake = FakeStreamlit(uploader_return=[upload])
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    # Signature already matches the uploaded file → no new upload.
    fake.session_state["screenshot_upload_sig"] = (("same.png", 3),)
    extracted = {k: {"value": NOT_VISIBLE} for k in SCREENSHOT_FIELDS}
    extracted["job_title"] = {"value": "Kept"}
    fake.session_state["extracted_job_fields"] = extracted
    fake.session_state["fields_confirmed"] = True

    screenshot_screen.render()

    # Unchanged upload must not wipe an in-progress extraction.
    assert fake.session_state["extracted_job_fields"]["job_title"]["value"] == "Kept"


def test_failed_upload_shows_clean_message(monkeypatch):
    # A selected file whose bytes never arrived (no readable getvalue).
    fake = FakeStreamlit(uploader_return=["not-a-real-uploaded-file"])
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    screenshot_screen.render()

    joined = "\n".join(fake.texts)
    assert screenshot_screen.UPLOAD_FAILED_MESSAGE in joined
    # No technical transport error leaks to the user.
    assert "403" not in joined
    assert "axios" not in joined.lower()
    assert fake.session_state["uploaded_screenshots"] == []


# ---------------------------------------------------------------------------
# 4. The parser receives image bytes, not file paths
# ---------------------------------------------------------------------------


def test_extract_fields_receives_image_bytes_not_paths(monkeypatch):
    captured = {}

    def _fake_call(**kwargs):
        captured.update(kwargs)
        return llm_client.LLMCallResult(
            success=True,
            task_name=kwargs["task_name"],
            provider="anthropic",
            model="claude-sonnet-4-6",
            used_api=True,
            response_json={k: NOT_VISIBLE for k in SCREENSHOT_FIELDS},
            status=llm_client.STATUS_OK,
        )

    monkeypatch.setattr(llm_client, "call_vision_llm", _fake_call)

    extract_fields([(b"\x89PNG-real-bytes", "image/png")])

    inputs = list(captured["image_inputs"])
    assert inputs, "no image inputs were forwarded to the vision client"
    for item in inputs:
        # A (bytes, mime) tuple — never a filesystem path string.
        assert isinstance(item, tuple)
        assert isinstance(item[0], (bytes, bytearray))
        assert not isinstance(item, str)


def test_screen_forwards_uploaded_bytes_to_parser(monkeypatch):
    fake = FakeStreamlit(
        click_keys={"analyze_opportunity_btn"},
        uploader_return=[_FakeUpload(data=b"PIXELS-1")],
    )
    _install(monkeypatch, fake)
    monkeypatch.setattr(screenshot_screen, "get_settings", lambda: _settings())
    _ready_session(fake)

    seen = {}

    def _fake_extract(images=()):
        seen["images"] = list(images)
        return {k: {"value": NOT_VISIBLE, "confidence": "low"} for k in SCREENSHOT_FIELDS}

    monkeypatch.setattr(screenshot_screen, "extract_fields", _fake_extract)

    screenshot_screen.render()

    assert seen["images"] == [(b"PIXELS-1", "image/png")]


# ---------------------------------------------------------------------------
# 5. No screenshot bytes are logged
# ---------------------------------------------------------------------------


def test_screenshot_bytes_are_never_logged(monkeypatch, caplog):
    # Real client path, but forced down the no-API branch so nothing goes
    # over the wire — we only care that the bytes are not written anywhere.
    keyless = _settings(has_key=False)
    monkeypatch.setattr(llm_client, "get_settings", lambda: keyless)

    fake = FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    llm_client.reset_usage_log()

    sentinel = b"SENTINELPIXELDATA0123456789"
    caplog.set_level(logging.INFO, logger="app.services.llm_client")

    extract_fields([(sentinel, "image/png")])

    assert "SENTINELPIXELDATA" not in caplog.text
    assert "SENTINELPIXELDATA" not in repr(llm_client.get_usage_log())
