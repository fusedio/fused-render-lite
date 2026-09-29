"""The page-side task API end to end on Render App's server: a page under
an extracted app creates a task through /api/tasks/create (X-Fused-Page
scope), the scheduler spawns the shipped chat engine against a stub `claude`,
the session host runs the turn, and the listing rekeys the pending row onto
the session with the reply. Same process tree a real task takes; only the
CLI is a stub."""
import json
import os
import sys
import time
import urllib.parse

import pytest

from _claude_stub_cli import write_stub_cli

_STUB = '''#!{python}
import json
import sys

def send(row):
    sys.stdout.write(json.dumps(row) + "\\n")
    sys.stdout.flush()

args = sys.argv[1:]
sid = args[args.index("--session-id") + 1] if "--session-id" in args else "sess-stub"
send({{"type": "system", "subtype": "init", "session_id": sid}})
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    row = json.loads(line)
    if row.get("type") != "user":
        continue
    text = row["message"]["content"][0]["text"]
    send({{"type": "assistant", "session_id": sid,
          "message": {{"role": "assistant", "content": [{{"type": "text", "text": "stub says: " + text}}]}}}})
    send({{"type": "result", "subtype": "success", "session_id": sid, "result": "stub says: " + text}})
'''


@pytest.fixture
def stub_cli(tmp_path, monkeypatch):
    path = write_stub_cli(tmp_path / "bin", _STUB.format(python=sys.executable))
    monkeypatch.setenv("FUSED_RENDER_CLAUDE_BIN", path)
    monkeypatch.setenv("FUSED_RENDER_APP_CLAUDE_BIN", path)
    return path


def _wait(pred, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.25)
    return pred()


def _tasks(client, **q):
    status, _h, body = client.get("/api/tasks" + ("?" + urllib.parse.urlencode(q) if q else ""))
    assert status == 200, body
    return json.loads(body)["tasks"]


@pytest.mark.skipif(os.name == "nt", reason="session host is POSIX-only")
def test_page_creates_a_task_and_sees_it_finish(client, v2_fused, stub_cli, app_home, tmp_path):
    from fused_render_app import appfile

    started = time.time() - 1
    opened = appfile.open_app_file(v2_fused)
    page = opened["entry"]
    headers = {"X-Fused-Page": urllib.parse.quote(page)}

    # Nothing yet, scoped to this app.
    status, _h, body = client.get("/api/tasks?scope=app", headers=headers)
    assert status == 200 and json.loads(body)["tasks"] == []

    # The page's own Tasks UI URL is scoped to the extract dir.
    status, _h, body = client.get("/api/tasks/ui?view=list&scope=app", headers=headers)
    assert status == 200, body
    url = json.loads(body)["url"]
    assert url.startswith("/tasks?embed=1") and urllib.parse.quote(opened["dir"], safe="") in url

    status, _h, body = client.post("/api/tasks/create", {"prompt": "hello from a page"}, headers=headers)
    assert status == 200, body
    created = json.loads(body)
    assert created["key"].startswith("pending:") and created["target"] == page
    assert created["under"] == opened["dir"]

    def finished():
        rows = _tasks(client, under=opened["dir"])
        return next((r for r in rows if r.get("session_id") and r["status"] == "done"), None)

    try:
        row = _wait(finished)
        assert row, _tasks(client, under=opened["dir"])
        assert row["task_id"] == "TASK-001"
        assert row["entry_id"] == created["entry_id"]
        # The reply itself lives in the transcript, which only the real CLI
        # writes; the row carries the message the page sent.
        assert row["last_message"]["text"] == "hello from a page"
    finally:
        # The session host is detached on purpose (a real chat outlives the
        # server); a test must reap the one it started.
        from _claude_stub_cli import reap_host
        from fused_render_app import claude_spawn

        runs = claude_spawn.load_agent().RUNS
        for name in os.listdir(runs) if os.path.isdir(runs) else []:
            run_dir = os.path.join(runs, name)
            if os.path.getmtime(run_dir) >= started:
                reap_host(run_dir)

    # The scoped listing sees it; another folder's scope does not.
    keys = [r["key"] for r in _tasks(client, under=opened["dir"])]
    assert row["key"] in keys
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _tasks(client, under=str(elsewhere)) == []

    # Task state landed under the app home, not ~/.fused-render.
    assert os.path.isfile(os.path.join(str(app_home), "claude-sessions", "task_ids.json"))
    assert os.path.isfile(os.path.join(str(app_home), "scheduled_messages.json"))
