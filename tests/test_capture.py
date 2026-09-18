"""Native capture (fused_render_app/capture/ + routes/capture.py, SPEC §45).

The Apple half cannot run in the suite — it needs a Mac, a granted TCC
permission and a real display — so the backend is a FAKE here and what is
tested is everything that decides whether a recording behaves: the arguments
that must be refused before a microphone turns on, where the file lands, the job
row a recording appears as, and the three ways a recording can end (the page's
`stop`, the manager's ✕, and the cap).

The two endings are the point of most of this file. ✕ DISCARDS, like every other
row in the manager — and the cap STOPS AND KEEPS, because a page can be closed
mid-recording and then the ✕ is the only control left, so if the cap discarded
too there would be no ending that kept the file.

Ported from fused-render's tests/test_capture.py onto the lite stdlib server:
the `client` fixture (tests/conftest.py) answers `(status, headers, body)`.
Everything about the browser-encoder stream (Windows/Linux) is gone with the
feature; the macOS-only muxer tests stay.
"""
import builtins
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from fused_render_app import capture, jobs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_GUARD = {"X-Fused": "0"}


def J(body):
    return json.loads(body)


def row_for(client, job_id):
    _, _, body = client.get("/api/jobs")
    return next(j for j in J(body)["jobs"] if j["id"] == job_id)


class FakeHandle:
    def __init__(self, path):
        self.path = path
        self.stopped = False
        self.error = None


class FakeBackend:
    """Writes bytes where the real one writes a movie. Nothing else differs."""

    def __init__(self):
        self.handles = []

    def probe(self):
        return {"video": {"available": True, "granted": True, "reason": None},
                "audio": {"available": True, "granted": True, "reason": None},
                "systemAudio": {"available": True, "reason": None},
                "screenshot": {"available": True, "granted": True,
                               "reason": None},
                "displays": [{"id": 1, "width": 100, "height": 80,
                              "main": True}],
                "microphones": [{"id": "mic0", "name": "Fake", "default": True}]}

    def _start(self, out, spec):
        with open(out, "wb") as fh:
            fh.write(b"recording")
        handle = FakeHandle(out)
        self.handles.append(handle)
        return handle

    start_screen = _start
    start_audio = _start

    def ext(self, mode, spec):
        return ".mov" if mode == "screen" else ".m4a"

    def refuse(self, mode, spec):
        # The real macOS backend's one refusal, kept here because the neutral
        # half's plumbing for it is what these tests exercise.
        if mode == "audio" and spec.get("device"):
            return ("audio-only recording uses the system's current input "
                    "device, so 'device' cannot be chosen here — record the "
                    "screen with audio: 'mic' to pick a specific microphone")
        return None

    def stop(self, handle):
        handle.stopped = True

    def failure(self, handle):
        return handle.error

    def screenshot(self, out, spec):
        with open(out, "wb") as fh:
            fh.write(b"png")
        return {"width": 4, "height": 2}


@pytest.fixture()
def backend(monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(capture, "_backend", lambda: fake)
    jobs.reset()
    capture._sessions.clear()
    capture._finished.clear()
    yield fake
    for cid in list(capture._sessions):
        capture._sessions.pop(cid, None)
    capture._finished.clear()
    jobs.reset()


# ------------------------------------------------------- the honest refusals


def test_a_machine_that_cannot_capture_says_so_rather_than_raising(monkeypatch):
    """`sources()` answers on EVERY platform — a page must be able to ask.

    The Render App ships for macOS, so any other `sys.platform` is a machine
    with no backend at all. What is tested is the promise CP-8 makes: an
    answer, shaped like every other answer, with a reason in it.
    """
    monkeypatch.setattr(capture.sys, "platform", "sunos5")
    payload = capture.sources()
    assert payload["video"]["available"] is False
    assert payload["audio"]["available"] is False
    assert payload["video"]["reason"]
    # Shape-identical to the real probe, EVERY key included — a page reading
    # `sources().screenshot.available` must not throw where the answer is "no".
    assert payload["displays"] == [] and payload["microphones"] == []
    assert set(payload) == set(FakeBackend().probe())
    for key in ("video", "audio", "systemAudio", "screenshot"):
        assert payload[key]["available"] is False
        assert payload[key]["reason"]


def test_starting_on_an_unsupported_platform_is_a_409_not_a_500(monkeypatch,
                                                               client):
    monkeypatch.setattr(capture.sys, "platform", "aix7")
    status, _, body = client.post("/api/capture/start", {"mode": "screen"})
    assert status == 409
    assert "macOS" in J(body)["error"]


def test_a_backend_that_will_not_import_is_a_reason_not_a_500(monkeypatch,
                                                              client):
    """The backend imports its Apple frameworks at module top, and
    `ScreenCaptureKit.framework` does not exist before macOS 12.3 — under an
    `LSMinimumSystemVersion` of 11.0. So this is a machine inside the supported
    range, and CP-8 promises it an answer: `available: false` with a reason on
    the read, a 409 on a start. An ImportError reaching either is a 500."""
    monkeypatch.setattr(capture.sys, "platform", "darwin")
    real_import = builtins.__import__

    def no_framework(name, globs=None, locs=None, fromlist=(), level=0):
        # Scoped to the backend import alone — `_backend()` reaches it as
        # `from fused_render_app.capture import _darwin`, a fromlist entry.
        if "_darwin" in (fromlist or ()):
            raise ImportError("No module named 'ScreenCaptureKit'")
        return real_import(name, globs, locs, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", no_framework)
    payload = capture.sources()
    assert payload["video"]["available"] is False
    assert "ScreenCaptureKit" in payload["video"]["reason"]
    assert set(payload) == set(FakeBackend().probe())
    status, _, body = client.post("/api/capture/start", {"mode": "screen"})
    assert status == 409, body


def test_a_probe_that_raises_is_still_an_answer(monkeypatch, client):
    """A PROBE MAY NOT RAISE: it is read while a page is drawing a record
    button, and `available: false` is something that page can render where a
    500 is not. The promise is about the SHAPE, so it cannot hold only for the
    failures this module predicted."""
    class Exploding:
        def probe(self):
            raise OSError("CoreGraphics said no")

    monkeypatch.setattr(capture, "_backend", lambda: Exploding())
    payload = capture.sources()
    assert set(payload) == set(FakeBackend().probe())
    for key in ("video", "audio", "systemAudio", "screenshot"):
        assert payload[key]["available"] is False
        assert "CoreGraphics said no" in payload[key]["reason"]
    # And through the route the page actually calls.
    status, _, body = client.get("/api/capture")
    assert status == 200
    assert J(body)["sources"]["video"]["available"] is False


@pytest.mark.parametrize("body,expected", [
    ({"mode": "sideways"}, "mode must be"),
    ({"mode": "screen", "audio": "microphone"}, "'audio' must be false"),
    ({"mode": "screen", "rect": [0, 0, 0, 10]}, "must be positive"),
    ({"mode": "screen", "rect": [0, 0, 10]}, "'rect' must be"),
    ({"mode": "screen", "maxSeconds": 0}, "must be between"),
    ({"mode": "screen", "maxSeconds": "soon"}, "whole number"),
    ({"mode": "audio", "source": "loud"}, "can only be 'mic'"),
    ({"mode": "audio", "source": "system"}, "record the screen"),
    ({"mode": "audio", "device": "mic0"}, "cannot be chosen here"),
    ({"mode": "screen", "path": "clip.mov"}, "must be absolute"),
])
def test_a_request_a_typo_deserves_an_answer_about_is_refused_before_recording(
        backend, client, body, expected):
    status, _, reply = client.post("/api/capture/start", body)
    assert status == 400, reply
    assert expected in J(reply)["error"]
    # Nothing was started and no row appeared: the refusal is BEFORE the device.
    assert backend.handles == []
    assert jobs.list_jobs() == []


def test_the_mutating_routes_carry_the_x_fused_guard(client):
    for path in ("/api/capture/start", "/api/capture/screenshot",
                 "/api/capture/shot-region",
                 "/api/capture/abc/stop", "/api/capture/abc/cancel"):
        status, _, _ = client.post(path, {}, headers=NO_GUARD)
        assert status == 403, path
    # …and the read is open, like the other read-only routes.
    assert client.get("/api/capture")[0] == 200


def test_audio_only_refuses_a_device_naming_where_it_does_work(backend, client):
    """Refused, not ignored: a silently-wrong mic is a recording made twice."""
    _, _, body = client.post("/api/capture/start",
                             {"mode": "audio", "device": "mic0"})
    assert "audio: 'mic'" in J(body)["error"]
    # The same option IS accepted on a screen recording, which is the point.
    status, _, _ = client.post("/api/capture/start",
                               {"mode": "screen", "audio": "mic",
                                "device": "mic0"})
    assert status == 200


# ------------------------------------------------------------ where it lands


def test_an_unnamed_recording_lands_in_the_app_home(backend, client, app_home):
    """`<home>/recordings` (paths.recordings_dir) is where an unnamed
    recording lands — the autouse `app_home` fixture points the home there."""
    _, _, body = client.post("/api/capture/start", {"mode": "audio"})
    started = J(body)
    assert started["path"].startswith(str(app_home / "recordings"))
    assert started["path"].endswith(".m4a")
    assert os.path.exists(started["path"])


def test_a_relative_path_resolves_beside_the_calling_page(backend, client,
                                                          tmp_path):
    page = tmp_path / "views" / "recorder.html"
    page.parent.mkdir(parents=True)
    page.write_text("<p>", encoding="utf-8")
    _, _, body = client.post("/api/capture/start",
                             {"mode": "screen", "path": "demo.mov",
                              "base": str(page)})
    assert J(body)["path"] == str(page.parent / "demo.mov")


def test_the_path_is_known_before_the_recording_ends(backend, client):
    """The whole reason this is native: `path` is on the START reply."""
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    started = J(body)
    assert started["state"] == "recording"
    _, _, body = client.post(f"/api/capture/{started['id']}/stop", {})
    stopped = J(body)
    assert stopped["path"] == started["path"]
    assert stopped["url"].startswith("/api/fs/raw?path=")
    assert stopped["mime"] == "video/quicktime"
    assert stopped["bytes"] == len(b"recording")


def test_a_missing_directory_is_refused_rather_than_half_recorded(backend,
                                                                  client,
                                                                  tmp_path):
    status, _, body = client.post(
        "/api/capture/start",
        {"mode": "audio", "path": str(tmp_path / "nope" / "a.m4a")})
    assert status == 400
    assert "no such directory" in J(body)["error"]


# ---------------------------------------------------------------- the endings


def test_a_recording_is_a_server_owned_job_row_the_manager_can_stop(backend,
                                                                    client):
    _, _, body = client.post("/api/capture/start",
                             {"mode": "screen", "title": "Walkthrough"})
    started = J(body)
    row = row_for(client, started["jobId"])
    assert row["title"] == "Walkthrough"
    assert row["owner"] == "server"      # so the ✕ really stops it
    assert row["cancellable"] is True
    assert row["unit"] == "s" and row["total"] == started["maxSeconds"]
    assert "discards" in row["detail"]   # the ✕'s meaning, in words
    _, _, body = client.get("/api/capture")
    assert J(body)["active"][0]["id"] == started["id"]
    # `fused.capture.*` is callable from any page's own script, so no single
    # hosting page names this row's source honestly — "Capture" names the
    # FEATURE that raised it instead.
    assert row["origin"] == "Capture"


def test_the_row_opens_the_page_that_started_the_capture(backend, client):
    """The capture job's destination is threaded from `X-Fused-Page`, the same
    header/unquote channel the jobs route uses for a page-owned row's `page`."""
    _, _, body = client.post(
        "/api/capture/start", {"mode": "screen"},
        headers={"X-Fused-Page": "/tmp/my%20app/index.html"})
    row = row_for(client, J(body)["jobId"])
    assert row["page"] == "/tmp/my app/index.html"


def test_no_page_header_leaves_the_row_with_no_destination(backend, client):
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    row = row_for(client, J(body)["jobId"])
    assert row["page"] == ""


def test_stop_keeps_the_file_and_finishes_the_row(backend, client):
    _, _, body = client.post("/api/capture/start", {"mode": "audio"})
    started = J(body)
    client.post(f"/api/capture/{started['id']}/stop", {})
    assert os.path.exists(started["path"])
    row = row_for(client, started["jobId"])
    assert row["state"] == "done"
    assert backend.handles[0].stopped is True
    # Live-only: a finished recording is a file, not a registry entry.
    _, _, body = client.get("/api/capture")
    assert J(body)["active"] == []


def test_cancel_deletes_the_file(backend, client):
    _, _, body = client.post("/api/capture/start", {"mode": "audio"})
    started = J(body)
    _, _, body = client.post(f"/api/capture/{started['id']}/cancel", {})
    gone = J(body)
    assert not os.path.exists(started["path"])
    # No path handed back for a file that no longer exists.
    assert gone["path"] is None and gone["url"] is None
    row = row_for(client, started["jobId"])
    assert row["state"] == "cancelled"


def test_a_second_stop_is_the_same_answer_and_not_a_second_stop(backend,
                                                               client):
    """The registry entry is taken under the lock before the device is touched,
    so a ✕ landing at the same moment as the page's own stop cannot finalise
    (or delete) the file twice.

    The LOSER of that race is answered from the finished-record cache rather
    than told its own recording never existed. Two real cases: a
    double-clicked stop button, and the watchdog's cap ending the recording a
    moment before the page's stop request lands — that cap is itself a valid
    ending, so a 404 there would report a failure for a recording that worked.
    """
    _, _, body = client.post("/api/capture/start", {"mode": "audio"})
    started = J(body)
    first_status, _, first = client.post(f"/api/capture/{started['id']}/stop", {})
    again_status, _, again = client.post(f"/api/capture/{started['id']}/stop", {})
    assert first_status == again_status == 200
    assert J(again)["path"] == J(first)["path"]
    # The file was finalised ONCE: the second stop never reached the backend.
    assert len([h for h in backend.handles if h.stopped]) == 1


def test_a_cancel_after_a_stop_still_deletes_the_file(backend, client):
    """The one thing the cache must not do is let a `cancel` become a no-op.

    The watchdog's cap can end a recording (an ending that KEEPS the file) a
    moment before the page's cancel request lands. Answering that with the
    cached "stopped" record would leave the file the caller asked to destroy
    on disk, reported as deleted.
    """
    _, _, body = client.post("/api/capture/start", {"mode": "audio"})
    started = J(body)
    client.post(f"/api/capture/{started['id']}/stop", {})
    assert os.path.exists(started["path"])
    _, _, body = client.post(f"/api/capture/{started['id']}/cancel", {})
    gone = J(body)
    assert gone["path"] is None and gone["state"] == "cancelled"
    assert not os.path.exists(started["path"])


def test_an_id_that_never_existed_is_still_a_404(backend, client):
    assert client.post("/api/capture/nope/stop", {})[0] == 404
    assert client.post("/api/capture/nope/cancel", {})[0] == 404


def test_the_managers_cross_discards_the_recording(backend, client,
                                                   monkeypatch):
    """The ✕ sets `cancel_requested`; the watchdog is what acts on it."""
    monkeypatch.setattr(capture, "TICK_S", 0.05)
    _, _, body = client.post("/api/capture/start", {"mode": "audio"})
    started = J(body)
    status, _, _ = client.post(f"/api/jobs/{started['jobId']}/cancel", {})
    assert status == 200
    deadline = time.monotonic() + 5
    while os.path.exists(started["path"]) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not os.path.exists(started["path"])
    assert capture.active() == []


def test_hitting_max_seconds_stops_and_keeps_the_file(backend, client,
                                                      monkeypatch):
    """The cap is the one automatic ending, and it is a STOP: for a recording
    whose page was closed it is the only ending that does not destroy it."""
    monkeypatch.setattr(capture, "TICK_S", 0.05)
    monkeypatch.setattr(capture, "_max_seconds", lambda value: 0.2)
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    started = J(body)
    deadline = time.monotonic() + 5
    while capture.active() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert capture.active() == []
    assert os.path.exists(started["path"])
    assert row_for(client, started["jobId"])["state"] == "done"


def test_a_recording_that_dies_mid_flight_ends_its_row_then(backend, client,
                                                            monkeypatch):
    """Otherwise the row ticks "Recording" to the cap while nothing is written,
    and the user narrates half an hour into a dead file."""
    monkeypatch.setattr(capture, "TICK_S", 0.05)
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    started = J(body)
    backend.handles[0].error = "the display went away"
    deadline = time.monotonic() + 5
    while capture.active() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert capture.active() == []
    row = row_for(client, started["jobId"])
    assert row["state"] == "error"
    assert "display went away" in row["message"]
    assert backend.handles[0].stopped is True


def test_a_tick_that_raises_does_not_strand_the_recording(backend, client,
                                                          monkeypatch):
    """The watchdog is the ONLY control left once the page that started a
    recording is gone — it carries both the cap and the ✕. So a tick that
    raises must not take the thread with it: a dead watchdog is a microphone
    nothing can turn off, behind a row that ticks "Recording" forever.

    This probe fails on EVERY tick, not one, which is the case a try/except
    around the loop does not answer on its own — it is why `_tick` checks the
    cap before anything that can fail.
    """
    monkeypatch.setattr(capture, "TICK_S", 0.05)
    monkeypatch.setattr(capture, "_max_seconds", lambda value: 0.3)

    calls = []

    def exploding_failure(session):
        calls.append(session.id)
        raise RuntimeError("the backend probe fell over")

    # `_failure` guards itself, so REPLACING it is what gets an exception past
    # the tick's own handling and onto the loop.
    monkeypatch.setattr(capture, "_failure", exploding_failure)
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    started = J(body)
    deadline = time.monotonic() + 5
    while capture.active() and time.monotonic() < deadline:
        time.sleep(0.05)
    # The cap still landed, the file was KEPT, and the backend was finalised —
    # `elapsed` only grows, so a failing tick costs one tick, not the ending.
    assert capture.active() == []
    assert os.path.exists(started["path"])
    assert backend.handles[0].stopped is True
    assert len(calls) > 1                        # it really did keep ticking


def test_a_job_store_that_cannot_be_written_still_records(backend, client,
                                                          monkeypatch):
    """`_report` is a ROW, not the work — and the `start` call site is
    load-bearing: raising there registers a session whose watchdog thread was
    never spawned, i.e. a recording with no cap and no ✕."""
    monkeypatch.setattr(capture, "TICK_S", 0.05)
    monkeypatch.setattr(capture, "_max_seconds", lambda value: 0.3)

    def exploding_upsert(body, **kwargs):
        raise RuntimeError("the job store fell over")

    monkeypatch.setattr(jobs, "upsert", exploding_upsert)
    status, _, body = client.post("/api/capture/start", {"mode": "screen"})
    assert status == 200                         # the row failed, not the start
    started = J(body)
    deadline = time.monotonic() + 5
    while capture.active() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert capture.active() == []                # the cap still ran
    assert os.path.exists(started["path"])
    assert backend.handles[0].stopped is True


def test_an_unreadable_job_store_is_not_a_cancel(backend, client, monkeypatch):
    """`_cancel_requested` is a probe: a registry it cannot read answers "no".
    Reading it as a cancel would DELETE a recording over a transient failure —
    the one ending that destroys the file."""
    monkeypatch.setattr(capture, "TICK_S", 0.05)

    def exploding_list(**kwargs):
        raise RuntimeError("the job store fell over")

    monkeypatch.setattr(jobs, "list_jobs", exploding_list)
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    started = J(body)
    time.sleep(0.3)
    assert [r["id"] for r in capture.active()] == [started["id"]]
    assert os.path.exists(started["path"])


def test_stop_all_runs_in_the_quit_path_before_the_server_goes():
    """`atexit` is the BACKSTOP, not the mechanism: the packaged app quits via
    `os._exit`, which runs no atexit handler at all. So the menu-bar quit has to
    name `capture.stop_all()` itself, and BEFORE the server shuts down — each
    recording has a file open and a native stream running, and the muxer
    finalises on stop. Otherwise a quit mid-recording leaves an unplayable .mov
    behind a row that said "recording"."""
    source = open(os.path.join(ROOT, "fused_render_app", "macapp.py"),
                  encoding="utf-8").read()
    assert "capture.stop_all()" in source
    stop = source.index("capture.stop_all()")
    assert stop < source.index("srv.shutdown")
    assert stop < source.index("os._exit(0)")


def test_stop_all_finalises_everything_on_the_way_out(backend, client):
    """A truncated .mov has no moov atom — an exiting server must not leave one."""
    _, _, body = client.post("/api/capture/start", {"mode": "screen"})
    started = J(body)
    capture.stop_all()
    assert backend.handles[0].stopped is True
    assert os.path.exists(started["path"])
    assert capture.active() == []


# --------------------------------------------------------------- screenshots


def test_a_screenshot_is_a_file_and_no_job_row(backend, client, app_home):
    _, _, body = client.post("/api/capture/screenshot", {})
    shot = J(body)
    assert shot["path"].endswith(".png") and shot["mime"] == "image/png"
    assert shot["path"].startswith(str(app_home / "recordings"))
    assert shot["width"] == 4 and shot["height"] == 2
    assert jobs.list_jobs() == []            # milliseconds need no row


def test_the_screenshots_container_follows_its_filename(backend, client,
                                                        tmp_path):
    """There is no `format` option: a `path` and a `format` could disagree, and
    "shot.jpg" holding PNG bytes is a file every other tool misreads."""
    _, _, body = client.post("/api/capture/screenshot",
                             {"path": str(tmp_path / "shot.jpg")})
    assert J(body)["mime"] == "image/jpeg"
    status, _, body = client.post("/api/capture/screenshot",
                                  {"path": str(tmp_path / "shot.gif")})
    assert status == 400
    assert ".png, .jpg or .jpeg" in J(body)["error"]


def test_shot_region_answers_png_bytes_and_leaves_no_file(backend, client,
                                                          app_home):
    """The shell's export capture: bytes back, nothing in recordings."""
    status, headers, body = client.post("/api/capture/shot-region",
                                        {"rect": [0, 0, 10, 10], "dpr": 2})
    assert status == 200, body
    assert headers.get("Content-Type", "").startswith("image/png")
    assert headers.get("Cache-Control") == "no-store"
    assert body == b"png"
    recordings = app_home / "recordings"
    assert not recordings.exists() or os.listdir(recordings) == []
    status, _, body = client.post("/api/capture/shot-region", {"dpr": 1})
    assert status == 400 and "'rect' is required" in J(body)["error"]


# ------------------------------------------------------------- the bridge doc


def test_the_runtime_bridge_exposes_capture():
    """`window.fused.capture` is the contract; this is its spelling guard."""
    source = open(os.path.join(ROOT, "fused_render_app", "static", "runtime.js"),
                  encoding="utf-8").read()
    assert "\n    capture,\n" in source          # on the window.fused literal
    for method in ("screen:", "audio:", "screenshot:", "sources:", "list:",
                   "attach:"):
        assert method in source, method
    for route in ('"/api/capture/start"', '"/api/capture/screenshot"',
                  '"/api/capture"', '"/api/capture/"'):
        assert route in source, route
    assert 'end("stop")' in source and 'end("cancel")' in source
    block = source[source.index("// ---- fused.capture"):]
    block = block[:block.index("window.fused = {")]
    # No private `onTick` (the job row + `fused.watchJob` already say elapsed)
    # and no `format` (the output filename decides png vs jpeg).
    assert "opts.onTick" not in block
    assert '"format"' not in block
    # The header block is the documented public bridge.
    assert "fused.capture.screen(opts) / audio(opts)" in source
    # No wire-only field reaches a page: the handle has no `transport` or
    # `streamToken`, and the read side strips `client` before resolving.
    handle = block[block.index("const handle = {"):]
    handle = handle[:handle.index("return handle;")]
    assert "transport" not in handle and "streamToken" not in handle
    assert "delete sources.client;" in block


def test_the_runtime_bridge_still_parses():
    """`node --check` on runtime.js — the cheapest guard that exists here.

    Nothing in this suite EXECUTES the bridge (it needs a browser), so every
    other test reads it as text and a stray brace would ship a file that breaks
    every page in the app rather than one feature. Skipped where node is absent.
    """
    node = shutil.which("node")
    if node is None:                                     # pragma: no cover
        pytest.skip("node is not installed")
    done = subprocess.run(
        [node, "--check",
         os.path.join(ROOT, "fused_render_app", "static", "runtime.js")],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_the_macos_backend_imports_and_probes_without_prompting():
    """Import + probe only: it must not need a display, a grant, or a click."""
    from fused_render_app.capture import _darwin

    payload = _darwin.probe()
    for key in ("video", "audio", "systemAudio", "screenshot"):
        assert set(payload[key]) >= {"available", "reason"}
    assert isinstance(payload["displays"], list)
    assert isinstance(payload["microphones"], list)


# --------------------------------------------------- the macOS 13-14 recorder


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_the_floors_are_per_verb_and_not_one_number():
    """`SCRecordingOutput` is 15 and `SCScreenshotManager` is 14, but neither
    is the floor of the FEATURE: `_darwin_mux` writes the movie below 15 and
    `CGDisplayCreateImage` takes the still below 14, so what is left is the
    floor of the thing native capture is FOR — system audio, macOS 13."""
    from fused_render_app.capture import _darwin

    assert _darwin.RECORD_MIN == (13, 0)
    assert _darwin.SHOT_MIN == (13, 0)
    assert _darwin.SCRO_MIN == (15, 0)
    assert _darwin.SCSHOT_MIN == (14, 0)


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_below_the_floor_is_a_reason_naming_thirteen(monkeypatch):
    """A 12.x Mac must read `available: false` with a sentence, not a raise —
    the probe is read while a page draws a record button."""
    from fused_render_app.capture import _darwin

    monkeypatch.setattr(_darwin, "_os_version", lambda: (12, 4))
    monkeypatch.setattr(_darwin.platform, "mac_ver", lambda: ("12.4", "", ""))
    payload = _darwin.probe()
    assert payload["video"]["available"] is False
    assert "needs macOS 13" in payload["video"]["reason"]
    assert payload["systemAudio"]["available"] is False
    assert payload["screenshot"]["available"] is False
    # Audio-only never needed ScreenCaptureKit at all, so it does not move.
    assert payload["audio"]["available"] is True


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_a_thirteen_or_fourteen_mac_routes_to_the_muxer(monkeypatch):
    from fused_render_app.capture import _darwin

    for version in ((13, 0), (13, 6), (14, 5)):
        monkeypatch.setattr(_darwin, "_os_version", lambda v=version: v)
        assert _darwin._use_mux() is True
    for version in ((15, 0), (26, 6)):
        monkeypatch.setattr(_darwin, "_os_version", lambda v=version: v)
        assert _darwin._use_mux() is False


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_the_muxer_can_be_forced_on_a_new_mac(monkeypatch):
    """Every API `_darwin_mux` uses exists on 15 too. Without this env var the
    only code path nobody developing it can run is the one just written."""
    from fused_render_app.capture import _darwin

    monkeypatch.setattr(_darwin, "_os_version", lambda: (26, 6))
    monkeypatch.setenv(_darwin.FORCE_MUX, "1")
    assert _darwin._use_mux() is True
    monkeypatch.setenv(_darwin.FORCE_MUX, "0")
    assert _darwin._use_mux() is False


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_the_stream_is_not_asked_for_a_microphone_on_the_mux_path(monkeypatch):
    """`captureMicrophone` is macOS 15, and on the mux path the microphone
    comes from an `AVCaptureSession` at EVERY version — asking the stream too
    would mix the same voice into the movie twice.

    Asserted against a REAL `SCStreamConfiguration` rather than a fake: the
    guard is a `respondsToSelector_` check, so a fake that answers every
    selector would pass while the thing being tested does not exist."""
    SCK = pytest.importorskip("ScreenCaptureKit")

    from fused_render_app.capture import _darwin

    if not SCK.SCStreamConfiguration.alloc().init().respondsToSelector_(
            b"setCaptureMicrophone:"):
        pytest.skip("this Mac is below macOS 15, where the guard is moot")

    monkeypatch.setattr(_darwin, "_display_scale", lambda display: 2)

    class FakeDisplay:
        @staticmethod
        def width():
            return 100

        @staticmethod
        def height():
            return 100

        @staticmethod
        def displayID():
            return 1

    muxed = _darwin._configure(FakeDisplay(), {"audio": "both"},
                               stream_mic=False)
    assert bool(muxed.capturesAudio()) is True
    assert bool(muxed.captureMicrophone()) is False

    native = _darwin._configure(FakeDisplay(), {"audio": "both"},
                                stream_mic=True)
    assert bool(native.capturesAudio()) is True
    assert bool(native.captureMicrophone()) is True


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_a_cursor_still_is_refused_on_thirteen_rather_than_drawn_without_one():
    """`CGDisplayCreateImage` never draws the pointer. Handing back a still
    that silently lacks it is the D319 mistake; the sentence names the version
    where `cursor` works."""
    from fused_render_app.capture import _darwin

    with pytest.raises(capture.CaptureError) as caught:
        _darwin._cg_shot("/tmp/never-written.png", object(), {"cursor": True})
    assert "macOS 14" in str(caught.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_the_mux_backend_imports_and_exposes_the_seam():
    """Import alone: the delegates declare real ObjC protocols and the module
    creates its dispatch queues at import time, so a typo in either is an
    ImportError here rather than a recording that never delivers a frame."""
    from fused_render_app.capture import _darwin_mux

    for hook in ("start", "stop", "failure"):
        assert callable(getattr(_darwin_mux, hook))
    assert _darwin_mux._SCREEN_Q.className() == "OS_dispatch_queue_serial"
    assert _darwin_mux._MIC_Q.className() == "OS_dispatch_queue_serial"


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_a_mux_recording_that_saw_no_frames_is_an_error_not_a_dead_file():
    """`finishWriting` over a session that never started produces a file that
    does not play. A row saying "done" over one of those is the worst outcome
    available (D409), so this raises instead."""
    from fused_render_app.capture import _darwin_mux

    handle = _darwin_mux.MuxHandle("/tmp/never-written.mov", None)
    cancelled = []
    handle.writer = type("W", (), {
        "cancelWriting": lambda self: cancelled.append(True),
        "error": lambda self: None,
    })()
    with pytest.raises(RuntimeError) as caught:
        handle.finish()
    assert "no frames" in str(caught.value)
    assert cancelled == [True]


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS backend")
def test_the_mux_handle_reports_an_error_it_has_already_died_of():
    """The read side of the per-tick `failure(handle)` hook. Without it a
    stream that dies at minute two ticks "Recording" to the cap over a file
    nothing is writing."""
    from fused_render_app.capture import _darwin_mux

    handle = _darwin_mux.MuxHandle("/tmp/x.mov", "system")
    assert _darwin_mux.failure(handle) is None
    handle.note_error("the display went away")
    assert _darwin_mux.failure(handle) == "the display went away"
    handle.note_error("something later")
    assert _darwin_mux.failure(handle) == "the display went away"
