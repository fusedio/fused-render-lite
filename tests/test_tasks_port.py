"""The Claude sessions / tasks routers copied from fused-render answer on
Render App's stdlib server: the shim binds their FastAPI-shaped signatures
(Query, BaseModel bodies, PUT, HTTPException), the state lives under the
app home, and the shipped chat engine runs through /api/run on the base
interpreter."""
import json
import os
import urllib.request

import pytest


def _get(client, path):
    status, _h, body = client.get(path)
    return status, json.loads(body or b"{}")


def _post(client, path, body=None, headers=None):
    status, _h, raw = client.post(path, body if body is not None else {}, headers=headers)
    return status, json.loads(raw or b"{}")


def test_tasks_listing_and_changes_answer_empty(client):
    status, data = _get(client, "/api/tasks")
    assert status == 200
    assert data["tasks"] == []
    assert isinstance(data["generation"], int)

    status, data = _get(client, "/api/tasks/changes?since=-1&wait=0")
    assert status == 200
    assert data["generation"] >= 0

    status, data = _get(client, "/api/tasks/pulse")
    assert status == 200


def test_task_writes_need_the_fused_header(client):
    req = urllib.request.Request(client.base + "/api/tasks/read", data=b'{"key": "x"}',
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 403


def test_basemodel_body_binding_and_http_exception_status(client):
    # `ReadPatch(key: str)` — an empty key is a 400 raised as HTTPException.
    status, data = _post(client, "/api/tasks/read", {"key": ""})
    assert status == 400
    assert "task key" in data["error"]
    # An unknown key is a 404 raised the same way.
    status, data = _post(client, "/api/tasks/archive", {"key": "nope"})
    assert status == 404


def test_scope_app_needs_a_page_header(client):
    status, data = _get(client, "/api/tasks?scope=app")
    assert status == 400
    assert "X-Fused-Page" in data["error"]


def test_query_alias_binds(client):
    # `api_tasks_scheduled(window_from: str = Query("", alias="from"), ...)`
    status, data = _get(client, "/api/tasks/scheduled?from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z")
    assert status == 200, data
    assert "tasks" in data or "entries" in data or isinstance(data, dict)


def test_schedule_listing_and_defaults(client):
    status, data = _get(client, "/api/schedule")
    assert status == 200
    assert isinstance(data.get("entries", data.get("scheduled", [])), list)
    status, data = _get(client, "/api/claude-sessions/defaults")
    assert status in (200, 503)


def test_queue_event_endpoint_accepts_turn_ended(client):
    status, data = _post(client, "/api/tasks/queue/event",
                         {"kind": "turn_ended", "run_id": "20260101-000000-abcd", "session_id": "s1"})
    assert status == 200
    assert data.get("ok") is True
    status, data = _post(client, "/api/tasks/queue/event", {"kind": "bogus", "run_id": "x"})
    assert status == 400


def test_drafts_put_route_is_reachable(client, app_home):
    body = json.dumps({"text": "hello"}).encode()
    req = urllib.request.Request(client.base + "/api/drafts/chat/abc", data=body,
                                 headers={"Content-Type": "application/json", "X-Fused": "1"},
                                 method="PUT")
    with urllib.request.urlopen(req) as r:
        assert r.status == 200
        data = json.load(r)
    assert data
    assert os.path.isfile(os.path.join(str(app_home), "claude-sessions", "drafts.json"))


def test_state_lands_under_the_app_home(client, app_home):
    _post(client, "/api/tasks/settings", {"session_id": "abc", "model": "haiku"})
    assert os.path.isfile(os.path.join(str(app_home), "claude-sessions", "session_settings.json"))


@pytest.mark.parametrize("script", ["agent.py", "app.py", "artifacts.py"])
def test_shipped_templates_run_on_the_base_interpreter(client, tmp_path, script):
    """/api/run with the packaged chat engine: no app venv, no legacy env
    install — `env.run_python_trusted` on the base python."""
    from fused_render_app import server

    py = os.path.join(server.TEMPLATES_DIR, "claude", script)
    assert server.is_shipped_template(py)
    assert not server.is_shipped_template(str(tmp_path / "agent.py"))
    target = tmp_path / "proj"
    target.mkdir()
    params = {"action": "sessions", "file": str(target)} if script == "agent.py" else \
        {"dir": str(target)} if script == "app.py" else {"cwd": str(target)}
    status, data = _post(client, "/api/run", {"py": py, "params": params})
    assert status == 200, data
    assert data.get("ok") is True, data
    assert not os.path.isdir(os.path.join(str(tmp_path), "home", "legacy"))


def test_required_query_and_file_answer_422():
    """`Query(...)` / `File(...)` with no default are required: the shim
    refuses the call like FastAPI's validation, instead of binding None."""
    from fused_render_app import _web

    router = _web.APIRouter()

    @router.get("/api/need")
    def need(thing: str = _web.Query(...), opt: str = _web.Query("d")):
        return {"thing": thing, "opt": opt}

    @router.post("/api/shot")
    def shot(file: _web.UploadFile | None = _web.File(...)):
        return {"name": file.filename}

    fn, params = router.match("GET", "/api/need")
    req = _web.Request("GET", "/api/need", {}, {})
    with pytest.raises(_web.HTTPException) as exc:
        _web.call_route(fn, body=None, headers={}, query={}, path_params=params, request=req)
    assert exc.value.status_code == 422 and "thing" in exc.value.detail
    assert _web.call_route(fn, body=None, headers={}, query={"thing": "x"}, path_params=params,
                           request=req) == {"thing": "x", "opt": "d"}
    fn, params = router.match("POST", "/api/shot")
    with pytest.raises(_web.HTTPException) as exc:
        _web.call_route(fn, body={}, headers={}, query={}, path_params=params, request=req, files={})
    assert exc.value.status_code == 422
    assert _web.call_route(fn, body={}, headers={}, query={}, path_params=params, request=req,
                           files={"file": _web.UploadFile("a.png", "image/png", b"x")}) == {"name": "a.png"}
