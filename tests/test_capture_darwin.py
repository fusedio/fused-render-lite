"""The macOS capture backend's guards — none of these record, prompt or
photograph anything; every framework call that could is faked out.

`AVF` / `SCK` are replaced WHOLE on `_darwin` (a `types.SimpleNamespace`):
monkeypatching one selector on a real pyobjc class fails with
`BadPrototypeError`.
"""

from __future__ import annotations

import sys
import types

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin",
                                reason="the macOS backend")

from fused_render_app import capture  # noqa: E402


def _fake_avf(status: int, answer=None):
    """An `AVFoundation` stand-in whose authorization status is `status` and
    whose access request answers `answer` at once (never when None)."""
    calls: list = []

    class Device:
        @staticmethod
        def authorizationStatusForMediaType_(media):
            return status

        @staticmethod
        def requestAccessForMediaType_completionHandler_(media, handler):
            calls.append(media)
            if answer is not None:
                handler(answer)

    ns = types.SimpleNamespace(AVCaptureDevice=Device, AVMediaTypeAudio="soun")
    ns.calls = calls
    return ns


def test_the_prompt_bound_answers_before_the_browser_gives_up():
    """WKWebView abandons a `fetch` at 60 s. A start that waited longer behind
    the TCC prompt registered a recording the page no longer held."""
    from fused_render_app.capture import _darwin

    assert _darwin.PROMPT_S < 60
    assert _darwin.WAIT_S >= _darwin.PROMPT_S
    # The stop side keeps the generous bound, and so do the muxer's positional
    # `_Wait(...)` calls, which take the default.
    assert _darwin._Wait("x").timeout == _darwin.WAIT_S
    assert _darwin._Wait("x", _darwin.PROMPT_S).timeout == _darwin.PROMPT_S


def test_a_timed_out_prompt_says_so_and_says_to_try_again():
    from fused_render_app.capture import _darwin

    wait = _darwin._Wait("listing displays", 0.01, prompt=True)
    with pytest.raises(RuntimeError) as e:
        wait.result()
    assert "not answered in time" in str(e.value)
    assert "try again" in str(e.value)

    plain = _darwin._Wait("stopping the capture", 0.01)
    with pytest.raises(RuntimeError) as e:
        plain.result()
    assert "prompt" not in str(e.value)


def test_ensure_mic_is_a_no_op_when_authorized(monkeypatch):
    from fused_render_app.capture import _darwin

    avf = _fake_avf(3)
    monkeypatch.setattr(_darwin, "AVF", avf)
    _darwin._ensure_mic()
    assert avf.calls == []


@pytest.mark.parametrize("status", [2, 1], ids=["denied", "restricted"])
def test_ensure_mic_refuses_a_denied_or_restricted_grant(monkeypatch, status):
    from fused_render_app.capture import _darwin

    avf = _fake_avf(status)
    monkeypatch.setattr(_darwin, "AVF", avf)
    with pytest.raises(capture.Unsupported) as e:
        _darwin._ensure_mic()
    assert "System Settings" in str(e.value)
    assert "Microphone" in str(e.value)
    assert avf.calls == [], "denied is never re-asked — the OS would not show it"


def test_ensure_mic_asks_when_undetermined_and_refuses_a_no(monkeypatch):
    """The bug: `record()` on an undetermined grant posts the prompt, returns
    True, and records silence. The answer must be waited for — and a False
    answer is a refusal with the System Settings sentence, not a RuntimeError
    reading "failed: False"."""
    from fused_render_app.capture import _darwin

    avf = _fake_avf(0, answer=False)
    monkeypatch.setattr(_darwin, "AVF", avf)
    with pytest.raises(capture.Unsupported) as e:
        _darwin._ensure_mic()
    assert avf.calls == ["soun"]
    assert "System Settings" in str(e.value)
    assert "False" not in str(e.value)


def test_ensure_mic_accepts_a_yes(monkeypatch):
    from fused_render_app.capture import _darwin

    avf = _fake_avf(0, answer=True)
    monkeypatch.setattr(_darwin, "AVF", avf)
    _darwin._ensure_mic()
    assert avf.calls == ["soun"]


def test_an_unanswered_mic_prompt_is_a_bounded_refusal(monkeypatch):
    from fused_render_app.capture import _darwin

    monkeypatch.setattr(_darwin, "AVF", _fake_avf(0, answer=None))
    monkeypatch.setattr(_darwin, "PROMPT_S", 0.01)
    with pytest.raises(capture.Unsupported) as e:
        _darwin._ensure_mic()
    assert "not answered in time" in str(e.value)


def test_probe_never_prompts_for_the_microphone(monkeypatch):
    """`sources()` is called from render paths; undetermined stays
    `granted: False, available: True` and no request goes out."""
    from fused_render_app.capture import _darwin

    avf = _fake_avf(0)
    monkeypatch.setattr(_darwin, "AVF", avf)
    monkeypatch.setattr(_darwin, "_list_mics", lambda: [])
    payload = _darwin.probe()
    assert avf.calls == []
    assert payload["audio"] == {"available": True, "granted": False,
                                "reason": payload["audio"]["reason"]}
    assert "not been granted" in payload["audio"]["reason"]


def test_probe_survives_a_microphone_enumeration_that_raises(monkeypatch):
    """`devicesWithMediaType:` is deprecated; when it goes, only the
    microphone list may go with it — not video, system audio or stills."""
    from fused_render_app.capture import _darwin

    def boom():
        raise AttributeError("devicesWithMediaType_ is gone")

    monkeypatch.setattr(_darwin, "_list_mics", boom)
    monkeypatch.setattr(_darwin, "_too_old", lambda minimum: "")
    monkeypatch.setattr(_darwin, "_screen_granted", lambda: True)
    payload = _darwin.probe()
    assert payload["microphones"] == []
    assert "devicesWithMediaType_ is gone" in payload["audio"]["reason"]
    assert payload["video"] == {"available": True, "granted": True,
                                "reason": None}
    assert payload["systemAudio"]["available"] is True
    assert payload["screenshot"]["available"] is True
    assert set(payload) == {"video", "audio", "systemAudio", "screenshot",
                            "displays", "microphones"}


def test_probe_survives_a_display_enumeration_that_raises(monkeypatch):
    from fused_render_app.capture import _darwin

    def boom():
        raise RuntimeError("CGGetActiveDisplayList failed (1000)")

    monkeypatch.setattr(_darwin, "_list_displays", boom)
    monkeypatch.setattr(_darwin, "_list_mics", lambda: [{"id": "m", "name":
                                                         "Mic", "default": True}])
    monkeypatch.setattr(_darwin, "_too_old", lambda minimum: "")
    monkeypatch.setattr(_darwin, "_screen_granted", lambda: True)
    payload = _darwin.probe()
    assert payload["displays"] == []
    assert "CGGetActiveDisplayList" in payload["video"]["reason"]
    assert payload["microphones"][0]["name"] == "Mic"


def test_need_refuses_a_missing_api_as_unsupported():
    """A `getattr` on a future macOS that dropped the class must be a 409
    naming the API, not an `AttributeError` that becomes a 500."""
    from fused_render_app.capture import _darwin

    ns = types.SimpleNamespace(Present=object())
    _darwin._need(ns, "Present", "x")
    with pytest.raises(capture.Unsupported) as e:
        _darwin._need(ns, "SCRecordingOutput", "screen recording")
    assert "SCRecordingOutput" in str(e.value)
    assert "does not provide" in str(e.value)

    class Config:
        """Carries the Python attribute (pyobjc metadata) but the runtime class
        does not respond — the shape of a selector newer than this macOS."""

        def setCaptureMicrophone_(self, value):
            pass

        def respondsToSelector_(self, sel):
            return sel != b"setCaptureMicrophone:"

    with pytest.raises(capture.Unsupported) as e:
        _darwin._need(Config(), "setCaptureMicrophone_",
                      "recording the microphone with the screen")
    assert "setCaptureMicrophone:" in str(e.value)


def test_a_screen_recording_asking_for_the_mic_is_refused_not_muted(monkeypatch):
    """On the stream path, `spec.audio: "mic"` with no `setCaptureMicrophone:`
    used to record silently without the microphone."""
    from fused_render_app.capture import _darwin

    class Config:
        def __init__(self):
            self.mic = None

        def setSourceRect_(self, r): pass
        def setWidth_(self, w): pass
        def setHeight_(self, h): pass
        def setShowsCursor_(self, c): pass
        def setCapturesAudio_(self, a): pass
        def setCaptureMicrophone_(self, m): self.mic = m
        def setMicrophoneCaptureDeviceID_(self, d): pass
        def respondsToSelector_(self, sel): return False

    class Display:
        def displayID(self): return 1
        def width(self): return 10
        def height(self): return 10

    made = []

    class SCStreamConfiguration:
        @staticmethod
        def alloc():
            return types.SimpleNamespace(init=lambda: made.append(Config()) or made[-1])

    monkeypatch.setattr(_darwin, "SCK", types.SimpleNamespace(
        SCStreamConfiguration=SCStreamConfiguration))
    monkeypatch.setattr(_darwin, "_display_scale", lambda display: 1)
    monkeypatch.setattr(_darwin, "AVF", _fake_avf(3))

    with pytest.raises(capture.Unsupported) as e:
        _darwin._configure(Display(), {"audio": "mic"})
    assert "setCaptureMicrophone:" in str(e.value)
    # The muxer path owns the microphone itself: no selector needed, no refusal.
    config = _darwin._configure(Display(), {"audio": "mic"}, stream_mic=False)
    assert config.mic is None


def test_configure_settles_the_mic_grant_before_either_path(monkeypatch):
    from fused_render_app.capture import _darwin

    class SCStreamConfiguration:
        @staticmethod
        def alloc():
            return types.SimpleNamespace(init=lambda: types.SimpleNamespace(
                setWidth_=lambda w: None, setHeight_=lambda h: None,
                setShowsCursor_=lambda c: None,
                setCapturesAudio_=lambda a: None))

    class Display:
        def displayID(self): return 1
        def width(self): return 10
        def height(self): return 10

    monkeypatch.setattr(_darwin, "SCK", types.SimpleNamespace(
        SCStreamConfiguration=SCStreamConfiguration))
    monkeypatch.setattr(_darwin, "_display_scale", lambda display: 1)
    monkeypatch.setattr(_darwin, "AVF", _fake_avf(2))
    with pytest.raises(capture.Unsupported):
        _darwin._configure(Display(), {"audio": "both"}, stream_mic=False)
    # No microphone asked for: the grant is not consulted at all.
    _darwin._configure(Display(), {"audio": "system"}, stream_mic=False)
    _darwin._configure(Display(), {}, stream_mic=False)
