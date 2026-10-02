import json
import os
import threading
import urllib.request
import zipfile

import pytest

ENTRY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="fused-app" />
<meta name="fused-api-version" content="1" /><title>t</title></head>
<body><script>fused.runPython("calc.py", {n: "3"});</script></body></html>
"""

CALC_PY = """import json
def main(n: int = 1, label: str = "x") -> dict:
    print("hello from calc")
    return {"double": n * 2, "label": label}
"""


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("FUSED_RENDER_APP_HOME", str(home))
    # The copied Claude sessions / tasks modules (tasks_store, drafts,
    # agent.py) key their state dir off FUSED_RENDER_HOME; keep it in the same
    # tmp home so no test touches ~/.fused-render or ~/.fused-render-app.
    monkeypatch.setenv("FUSED_RENDER_HOME", str(home))
    # Those modules compute their state dir at import, so the env alone does
    # not redirect an already-imported module: pin the attribute too.
    from fused_render_app import drafts, tasks_store
    from fused_render_app.routes import claude_sessions

    state = str(home / "claude-sessions")
    for mod in (tasks_store, drafts, claude_sessions):
        monkeypatch.setattr(mod, "STATE_DIR", state)
    return home


@pytest.fixture(autouse=True)
def _isolated_claude_home(tmp_path_factory, monkeypatch):
    """Nothing under test reads the developer's real ~/.claude: the tasks
    listing, the change-watcher and session liveness all walk
    `~/.claude/projects` and `~/.claude/sessions`, so HOME and
    CLAUDE_CONFIG_DIR point at an empty tree per test (fused-render's
    conftest does the same). Its own tmp dir, not `tmp_path`: tests that
    list `tmp_path` must not see it."""
    fake_home = tmp_path_factory.mktemp("claude-home")
    claude_dir = fake_home / ".claude"
    claude_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    # Six copied modules bind CLAUDE_DIR (and PROJECTS_DIR / SESSIONS_DIR
    # derived from it) at import; re-root every such constant to the fake tree.
    from fused_render_app import (claude_artifacts, claude_session_move, session_liveness,
                                  tasks_store, tasks_watch)
    from fused_render_app.routes import claude_sessions

    for mod in (claude_artifacts, claude_session_move, session_liveness, tasks_store,
                tasks_watch, claude_sessions):
        real = getattr(mod, "CLAUDE_DIR", None)
        if not real:
            continue
        for name, value in list(vars(mod).items()):
            if name.isupper() and isinstance(value, str) and (value == real or value.startswith(real + os.sep)):
                monkeypatch.setattr(mod, name, str(claude_dir) + value[len(real):])
    return fake_home


@pytest.fixture(autouse=True)
def _no_user_plugin_sync(monkeypatch):
    """`start_ai` (reached through `serve_in_thread`) starts the published
    plugin sync, which would spawn `claude plugin marketplace add …` against
    the config dir and write a stamp under the home. Marked already-started
    for every test; tests/test_user_plugin.py resets the flag itself."""
    from fused_render_app import user_plugin

    monkeypatch.setattr(user_plugin, "_started", True)


@pytest.fixture(autouse=True)
def _no_real_claude_cli(monkeypatch, tmp_path):
    """No test spawns the developer's real `claude`: `make_server` exports the
    resolved binary as FUSED_RENDER_CLAUDE_BIN for the chat engine, and a
    `client` test that hits /api/tasks/create would otherwise detach a real,
    billed session. Both variables point at a path that does not exist; a
    test that wants a CLI installs its own stub (test_claude_session_host)."""
    missing = str(tmp_path / "no-such-claude")
    monkeypatch.setenv("FUSED_RENDER_CLAUDE_BIN", missing)
    monkeypatch.setenv("FUSED_RENDER_APP_CLAUDE_BIN", missing)


@pytest.fixture(autouse=True)
def _no_task_threads(monkeypatch):
    """No test starts the scheduled-messages loop, the Tasks change-watcher
    or a queue manager that outlives it (fused-render's `_no_schedule_loop_
    thread` / `_no_tasks_watch_thread` / `_no_queue_manager_across_tests`).
    Tests about them call `tick()` themselves."""
    from fused_render_app import queue_manager, schedule, tasks_watch
    from fused_render_app.bots import registry as bots_registry

    monkeypatch.setattr(schedule, "start", lambda: None)
    monkeypatch.setattr(tasks_watch, "start", lambda: None)
    # The bots' scheduler + iMessage bridge (`start_ai` -> bots.registry.start);
    # tests drive `tick_routines()` themselves. Bots built by one test are
    # forgotten before the next.
    monkeypatch.setattr(bots_registry, "start", lambda: None)
    bots_registry.reset_for_tests()
    queue_manager.reset_for_tests(None)
    yield
    queue_manager.reset_for_tests(None)
    bots_registry.reset_for_tests()


@pytest.fixture
def v2_fused(tmp_path):
    from fused_render_app import container

    out = tmp_path / "demo.fused"
    container.write(
        str(out),
        {"name": "demo", "entry": "index.html"},
        [("index.html", ENTRY_HTML.encode()), ("calc.py", CALC_PY.encode()),
         ("data/note.txt", b"hello")],
    )
    return str(out)


@pytest.fixture
def v1_fused(tmp_path):
    out = tmp_path / "legacy.fused"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"fused_app_file": 1, "name": "legacy", "entry": "index.html"}))
        zf.writestr("files/index.html", ENTRY_HTML)
        zf.writestr("files/calc.py", CALC_PY)
    return str(out)


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        return self._do(req)

    def post(self, path, body, headers=None, raw=False):
        h = {"X-Fused": "1"}
        h.update(headers or {})
        data = body if raw else json.dumps(body).encode()
        if not raw:
            h["Content-Type"] = "application/json"
        return self._do(urllib.request.Request(self.base + path, data=data, headers=h, method="POST"))

    @staticmethod
    def _do(req):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()


@pytest.fixture
def client():
    from fused_render_app import jobs, server

    jobs.reset()  # the job registry is process-wide; each server starts clean
    srv, thread = server.serve_in_thread(0)
    yield Client(srv.server_address[1])
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


ICON_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle r="8" cx="8" cy="8"/></svg>'
# A real 1x1 red PNG (signature + IHDR + IDAT + IEND, 69 bytes): the icon.png
# fallback the dock accepts when an app ships no icon.svg.
ICON_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def v2_fused_icon(tmp_path):
    """Like ``v2_fused`` plus a shipped ``icon.svg`` (the menu-bar dock's card icon)."""
    from fused_render_app import container

    out = tmp_path / "iconic.fused"
    container.write(
        str(out),
        {"name": "iconic", "entry": "index.html"},
        [("index.html", ENTRY_HTML.encode()), ("calc.py", CALC_PY.encode()),
         ("icon.svg", ICON_SVG)],
    )
    return str(out)


@pytest.fixture
def v2_fused_png(tmp_path):
    """A v2 .fused that ships only an ``icon.png`` (no svg)."""
    from fused_render_app import container

    out = tmp_path / "raster.fused"
    container.write(
        str(out),
        {"name": "raster", "entry": "index.html"},
        [("index.html", ENTRY_HTML.encode()), ("icon.png", ICON_PNG)],
    )
    return str(out)


@pytest.fixture
def v1_fused_png(tmp_path):
    out = tmp_path / "legacy-png.fused"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"fused_app_file": 1, "name": "legacy-png", "entry": "index.html"}))
        zf.writestr("files/index.html", ENTRY_HTML)
        zf.writestr("files/icon.png", ICON_PNG)
    return str(out)


@pytest.fixture
def v2_fused_preview(tmp_path):
    """Like ``v2_fused`` plus a shipped ``preview.png`` (the dock bubble's picture)."""
    from fused_render_app import container

    out = tmp_path / "pictured.fused"
    container.write(
        str(out),
        {"name": "pictured", "entry": "index.html"},
        [("index.html", ENTRY_HTML.encode()), ("preview.png", ICON_PNG)],
    )
    return str(out)


@pytest.fixture
def v1_fused_preview(tmp_path):
    out = tmp_path / "legacy-pictured.fused"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"fused_app_file": 1, "name": "legacy-pictured", "entry": "index.html"}))
        zf.writestr("files/index.html", ENTRY_HTML)
        zf.writestr("files/preview.png", ICON_PNG)
    return str(out)


@pytest.fixture
def v1_fused_icon(tmp_path):
    out = tmp_path / "legacy-icon.fused"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"fused_app_file": 1, "name": "legacy-icon", "entry": "index.html"}))
        zf.writestr("files/index.html", ENTRY_HTML)
        zf.writestr("files/icon.svg", ICON_SVG)
    return str(out)
