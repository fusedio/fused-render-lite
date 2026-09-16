"""`jobnotify`: the pure click-routing decision, and that `install` wires the
jobs hook to the policy and the policy to `webnotify.notify`."""
import os

import pytest

from fused_render_app import jobnotify, jobs, notify_policy, webnotify


@pytest.fixture(autouse=True)
def _clean():
    jobs.reset()
    jobs.set_transition_hook(None)
    webnotify._click_handlers.clear()
    yield
    jobs.reset()
    jobs.set_transition_hook(None)
    webnotify._click_handlers.clear()


ROOT = "/home/apps"


def test_env_install_page_maps_to_the_window_showing_that_app():
    page = os.path.join(ROOT, "demo-abc123")
    cands = [("/x/other.fused", os.path.join(ROOT, "other-999")),
             ("/x/demo.fused", page)]
    assert jobnotify.page_target(page, ROOT, cands) == ("window", "/x/demo.fused")


def test_file_inside_extracted_app_maps_to_that_app():
    page = os.path.join(ROOT, "demo-abc123", "out", "a.png")
    cands = [("/x/demo.fused", os.path.join(ROOT, "demo-abc123"))]
    assert jobnotify.page_target(page, ROOT, cands) == ("window", "/x/demo.fused")


def test_candidates_without_extract_dir_are_skipped():
    page = os.path.join(ROOT, "demo-abc123")
    assert jobnotify.page_target(page, ROOT, [("/x/broken.fused", None)]) == ("home", "")


def test_existing_file_outside_apps_is_revealed(tmp_path):
    out = tmp_path / "render.png"
    out.write_bytes(b"x")
    assert jobnotify.page_target(str(out), ROOT, []) == ("reveal", str(out))


def test_shell_route_and_empty_page_go_home():
    assert jobnotify.page_target("/ai-models/local", ROOT, []) == ("home", "")
    assert jobnotify.page_target("", ROOT, []) == ("home", "")
    assert jobnotify.page_target("relative/path", ROOT, []) == ("home", "")


def test_install_routes_transitions_to_notify(monkeypatch):
    posted = []
    monkeypatch.setattr(webnotify, "notify",
                        lambda ident, title, body, *, sound=True: posted.append(
                            (ident, title, body, sound)))
    jobnotify.install(manager=object(), apps_root=ROOT)
    assert notify_policy.IDENTIFIER_PREFIX in webnotify._click_handlers

    job = "sys:ai-model:org--whisper"
    jobs.upsert({"id": job, "title": "org/whisper", "state": "running",
                 "kind": "download", "tier": "trail", "detail": "Preparing…"}, server=True)
    jobs.upsert({"id": job, "done": 10, "total": 100}, server=True)  # tick: nothing
    jobs.upsert({"id": job, "state": "done", "detail": "Downloaded"}, server=True)
    assert [p[0] for p in posted] == ["job:" + job, "job:" + job]
    assert posted[0][3] is False and posted[0][2] == "Preparing…"
    assert posted[1][3] is True and posted[1][2] == "Downloaded"


def test_install_silent_load_posts_nothing_on_success(monkeypatch):
    posted = []
    monkeypatch.setattr(webnotify, "notify", lambda *a, **k: posted.append(a))
    jobnotify.install(manager=object(), apps_root=ROOT)
    job = "sys:ai-model:org--whisper"
    jobs.upsert({"id": job, "title": "org/whisper", "state": "running",
                 "tier": "silent"}, server=True)
    jobs.upsert({"id": job, "state": "done", "detail": "Model loaded"}, server=True)
    assert posted == []
    jobs.upsert({"id": job, "state": "running", "tier": "silent"}, server=True)
    jobs.upsert({"id": job, "state": "error",
                 "message": "Traceback:\n  x\nValueError: boom\nsee docs"}, server=True)
    assert len(posted) == 1 and posted[0][2] == "ValueError: boom"


class _Win:
    def __init__(self, app_file):
        self.app_file = app_file


class _Manager:
    def __init__(self, windows):
        self._windows = windows
        self.calls = []

    def focus_or_open(self, f):
        self.calls.append(("focus_or_open", f))

    def show_home(self):
        self.calls.append(("show_home",))


def test_click_focuses_the_app_whose_env_is_installing(monkeypatch):
    from fused_render_app import appfile

    extract = os.path.join(ROOT, "demo-abc123")
    monkeypatch.setattr(appfile, "extract_dir_for",
                        lambda f: extract if f == "/x/demo.fused" else None)
    monkeypatch.setattr(webnotify, "notify", lambda *a, **k: None)
    mgr = _Manager([_Win(None), _Win("/x/demo.fused")])
    jobnotify.install(mgr, ROOT, remembered_files=lambda: ["/x/old.fused"])
    jobs.upsert({"id": "sys:env-install:k", "title": "Preparing demo",
                 "state": "running"}, page=extract, server=True)
    handler = webnotify._click_handlers[notify_policy.IDENTIFIER_PREFIX]
    handler("job:sys:env-install:k")
    assert mgr.calls == [("focus_or_open", "/x/demo.fused")]


def test_click_after_row_dismissed_still_reaches_the_banner_page(monkeypatch):
    from fused_render_app import appfile

    extract = os.path.join(ROOT, "demo-abc123")
    monkeypatch.setattr(appfile, "extract_dir_for", lambda f: extract)
    monkeypatch.setattr(webnotify, "notify", lambda *a, **k: None)
    mgr = _Manager([_Win("/x/demo.fused")])
    jobnotify.install(mgr, ROOT)
    jobs.upsert({"id": "sys:env-install:k", "title": "Preparing demo",
                 "state": "running"}, page=extract, server=True)
    jobs.upsert({"id": "sys:env-install:k", "state": "error", "message": "x"}, server=True)
    jobs.dismiss("sys:env-install:k")
    webnotify._click_handlers[notify_policy.IDENTIFIER_PREFIX]("job:sys:env-install:k")
    assert mgr.calls == [("focus_or_open", "/x/demo.fused")]


def test_click_survives_an_unreadable_candidate(monkeypatch):
    from fused_render_app import appfile

    def boom(f):
        raise RuntimeError("corrupt")

    monkeypatch.setattr(appfile, "extract_dir_for", boom)
    monkeypatch.setattr(webnotify, "notify", lambda *a, **k: None)
    mgr = _Manager([_Win("/x/demo.fused")])
    jobnotify.install(mgr, ROOT)
    jobs.upsert({"id": "sys:env-install:k", "title": "Preparing demo",
                 "state": "running"}, page=os.path.join(ROOT, "d"), server=True)
    webnotify._click_handlers[notify_policy.IDENTIFIER_PREFIX]("job:sys:env-install:k")
    assert mgr.calls == [("show_home",)]


def test_click_on_unknown_row_shows_home(monkeypatch):
    monkeypatch.setattr(webnotify, "notify", lambda *a, **k: None)
    mgr = _Manager([])
    jobnotify.install(mgr, ROOT)
    webnotify._click_handlers[notify_policy.IDENTIFIER_PREFIX]("job:sys:ai-model:gone")
    assert mgr.calls == [("show_home",)]
