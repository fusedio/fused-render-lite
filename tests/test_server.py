import json
import os
import shutil
import sys
import urllib.parse

import pytest

from fused_render_lite import env

_REAL = {name: getattr(env, name) for name in ("base_python", "is_ready", "interpreter_for")}


@pytest.fixture(autouse=True)
def stdlib_python(monkeypatch):
    # Every app would otherwise get a venv built by uv (its own, or the shared
    # legacy set). Unit tests run the .py on this interpreter instead; the
    # integration tests below restore the real functions.
    monkeypatch.setattr(env, "base_python", lambda: sys.executable)
    monkeypatch.setattr(env, "is_ready", lambda app_dir: True)
    monkeypatch.setattr(env, "interpreter_for", lambda app_dir: sys.executable)


def _real_env(monkeypatch):
    for name, fn in _REAL.items():
        monkeypatch.setattr(env, name, fn)


def q(**kw):
    return "?" + urllib.parse.urlencode(kw)


def test_placeholder_and_open_page(client):
    status, headers, body = client.get("/")
    assert status == 200 and b".fused" in body
    status, _, body = client.get("/open" + q(_file="/nope/x.fused"))
    assert status == 200 and b'"/nope/x.fused"' in body


def test_open_run_and_fs(client, v2_fused):
    status, _, body = client.post("/api/open", {"file": v2_fused})
    assert status == 200, body
    data = json.loads(body)
    entry = data["entry"]
    assert data["view"].startswith("/render?path=")
    assert data["install"]["status"] == "done"

    # render injects the runtime into <head>
    status, _, html = client.get(data["view"])
    assert status == 200
    assert html.index(b"/static/runtime.js") < html.index(b'name="fused-app"')

    # runPython: relative py resolved against html, params coerced by annotation
    status, _, body = client.post("/api/run", {"py": "calc.py", "html": entry, "params": {"n": "21"}})
    result = json.loads(body)
    assert result["ok"] is True, result
    assert result["result"] == {"double": 42, "label": "x"}
    assert "hello from calc" in result["stdout"]
    assert result["resolved_py"] == os.path.join(os.path.dirname(entry), "calc.py")

    # a failing run comes back as the envelope, not a 500
    status, _, body = client.post("/api/run", {"py": "calc.py", "html": entry, "params": {"n": "x"}})
    result = json.loads(body)
    assert status == 200 and result["ok"] is False
    assert result["error"]["type"] == "ParamError"

    # stat / raw / range / write round trip
    status, _, body = client.get("/api/fs/stat" + q(path="data/note.txt", base=entry))
    st = json.loads(body)
    assert status == 200 and st["size"] == 5 and st["writable"] is True
    status, headers, body = client.get("/api/fs/raw" + q(path="data/note.txt", base=entry),
                                       headers={"Range": "bytes=1-2"})
    assert status == 206 and body == b"el" and headers["Content-Range"] == "bytes 1-2/5"
    note = os.path.join(os.path.dirname(entry), "data", "note.txt")
    status, _, body = client.post("/api/fs/write", {"path": note, "content": "bye",
                                                    "expected_mtime": st["mtime"]})
    assert status == 200, body
    assert open(note).read() == "bye"
    status, _, body = client.post("/api/fs/write", {"path": note, "content": "x",
                                                    "expected_mtime": st["mtime"]})
    assert status == 409 and json.loads(body)["error"] == "conflict"
    status, _, body = client.post("/api/fs/write", {"path": note, "content": "x", "create": True})
    assert status == 409
    new = os.path.join(os.path.dirname(entry), "new.txt")
    status, _, body = client.post("/api/fs/write", {"path": new, "content": "n", "create": True})
    assert status == 200 and json.loads(body)["created"] is True


def test_mutations_require_header(client, v2_fused):
    status, _, _ = client.post("/api/open", {"file": v2_fused}, headers={"X-Fused": "0"})
    assert status == 403
    status, _, _ = client.post("/api/run", {"py": "/x.py"}, headers={"X-Fused": "0"})
    assert status == 403
    status, _, _ = client.post("/api/fs/write", {"path": "/x", "content": ""}, headers={"X-Fused": "0"})
    assert status == 403


def test_drop_saves_and_validates(client, v2_fused, lite_home):
    raw = open(v2_fused, "rb").read()
    status, _, body = client.post("/api/drop", raw, raw=True,
                                  headers={"X-Filename": "my%20app.fused",
                                           "Content-Type": "application/octet-stream"})
    assert status == 200, body
    saved = json.loads(body)["file"]
    assert saved.startswith(str(lite_home / "dropped")) and os.path.isfile(saved)
    status, _, body = client.post("/api/drop", b"garbage", raw=True,
                                  headers={"X-Filename": "bad.fused"})
    assert status == 400 and not os.path.exists(str(lite_home / "dropped" / "bad.fused"))


def test_unsupported_apis_throw():
    js = open(os.path.join(os.path.dirname(env.__file__), "static", "runtime.js")).read()
    for name in ("ai", "capture", "fileIndex", "daemon", "trackJob", "watchJob",
                 "autoReload", "uploadFile", "mkdir", "snapshot"):
        assert name in js
    assert "is not supported on Render Lite" in js
    assert "stub" not in js.lower()


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv (integration)")
def test_pyproject_builds_a_venv(client, tmp_path, lite_home, monkeypatch):
    from fused_render_lite import container

    _real_env(monkeypatch)  # uv finds/downloads 3.12 and builds the venv
    entry = b'<html><head><meta name="fused-app"></head><body></body></html>'
    py = b"def main():\n    import six\n    return six.__version__\n"
    pyproject = b'[project]\nname = "six-app"\nversion = "0.1"\nrequires-python = ">=3.12"\n' \
                b'dependencies = ["six"]\n[tool.uv]\npackage = false\n'
    out = tmp_path / "six.fused"
    container.write(str(out), {"name": "six-app", "entry": "index.html"},
                    [("index.html", entry), ("app.py", py), ("pyproject.toml", pyproject)])
    status, _, body = client.post("/api/open", {"file": str(out)})
    data = json.loads(body)
    assert status == 200, body
    status, _, body = client.post("/api/run", {"py": "app.py", "html": data["entry"], "params": {}})
    result = json.loads(body)
    assert result["ok"] is True, result
    assert result["result"].count(".") >= 1
    assert env.is_ready(data["dir"])


def test_upload_mkdir_and_jobs(client, v2_fused):
    status, _, body = client.post("/api/open", {"file": v2_fused})
    entry = json.loads(body)["entry"]
    app_dir = os.path.dirname(entry)
    base = urllib.parse.quote(entry, safe="")

    # upload: relative path resolved against base, raw body
    status, _, body = client.post(f"/api/fs/upload?path=out/blob.bin&base={base}", b"\x00\x01bin",
                                  raw=True, headers={"Content-Type": "application/octet-stream"})
    assert status == 404  # parent missing
    status, _, body = client.post("/api/fs/mkdir", {"path": "out", "base": entry})
    assert status == 200 and json.loads(body)["is_dir"] is True
    status, _, body = client.post("/api/fs/mkdir", {"path": "out", "base": entry})
    assert status == 409 and json.loads(body)["error"] == "exists"
    status, _, body = client.post(f"/api/fs/upload?path=out/blob.bin&base={base}", b"\x00\x01bin",
                                  raw=True, headers={"Content-Type": "application/octet-stream"})
    assert status == 200 and json.loads(body)["size"] == 5
    assert open(os.path.join(app_dir, "out", "blob.bin"), "rb").read() == b"\x00\x01bin"
    status, _, _ = client.post("/api/fs/upload?path=/x", b"x", raw=True, headers={"X-Fused": "0"})
    assert status == 403

    # jobs: report, list, cancel, terminal, dismiss
    status, _, body = client.post("/api/jobs", {"id": "j1", "title": "Build", "total": 10, "state": "running"})
    assert status == 200 and json.loads(body)["cancel_requested"] is False
    status, _, body = client.post("/api/jobs/j1/cancel", {})
    assert status == 200 and json.loads(body)["cancel_requested"] is True
    status, _, body = client.post("/api/jobs", {"id": "j1", "done": 4})
    row = json.loads(body)
    assert row["done"] == 4 and row["total"] == 10 and row["cancel_requested"] is True
    status, _, body = client.get("/api/jobs")
    assert [j["id"] for j in json.loads(body)["jobs"]] == ["j1"]
    status, _, body = client.post("/api/jobs", {"id": "j1", "state": "bogus"})
    assert status == 400
    status, _, body = client.post("/api/jobs", {"id": "j1", "state": "cancelled"})
    assert json.loads(body)["state"] == "cancelled"
    status, _, body = client.post("/api/jobs/j1/dismiss", {})
    assert json.loads(body)["dismissed"] is True
    assert json.loads(client.get("/api/jobs")[2])["jobs"] == []


def test_worker_origin_is_exported(client):
    assert os.environ["FUSED_RENDER_ORIGIN"] == client.base


def test_runtime_no_longer_throws_for_030_members():
    js = open(os.path.join(os.path.dirname(env.__file__), "static", "runtime.js")).read()
    for name in ("uploadFile", "mkdir", "trackJob", "watchJob", "autoReload"):
        assert f'unsupportedFn("fused.{name}")' not in js
    assert 'unsupportedNamespace("fused.daemon")' not in js  # 0.7.0: fused.daemon supported
    assert 'throw unsupported("fused.autoReload(true)")' in js


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv (integration)")
def test_no_pyproject_app_gets_the_legacy_env(client, tmp_path, lite_home, monkeypatch):
    from fused_render_lite import container

    _real_env(monkeypatch)
    monkeypatch.setenv(env.LEGACY_DEPS_ENV, "six")  # stand-in for the real ~150 MB set
    entry = b'<html><head><meta name="fused-app"></head><body></body></html>'
    py = b"def main():\n    import six\n    return 'legacy ok ' + six.__version__\n"
    out = tmp_path / "plain.fused"
    container.write(str(out), {"name": "plain", "entry": "index.html"},
                    [("index.html", entry), ("app.py", py)])
    status, _, body = client.post("/api/open", {"file": str(out)})
    data = json.loads(body)
    assert status == 200, body
    assert data["install"]["status"] in ("pending", "running", "done")
    status, _, body = client.post("/api/run", {"py": "app.py", "html": data["entry"], "params": {}})
    result = json.loads(body)
    assert result["ok"] is True, result
    assert result["result"].startswith("legacy ok ")
    # shared venv lives under the legacy project, not the app
    assert env.venv_dir_for(env.legacy_project_dir()) == env.venv_dir_for(env.project_dir_for(data["dir"]))
    assert "six" in open(os.path.join(env.legacy_project_dir(), "pyproject.toml")).read()


def test_old_uv_on_path_is_skipped(tmp_path, monkeypatch):
    """A stale uv on PATH (no --managed-python / --no-default-groups) must not
    be picked over downloading the pinned one."""
    old = tmp_path / "uv"
    old.write_text("#!/bin/sh\necho 'uv 0.4.30'\n")
    old.chmod(0o755)
    new = tmp_path / "new" / "uv"
    new.parent.mkdir()
    new.write_text("#!/bin/sh\necho 'uv 0.12.13 (abc 2026-01-01)'\n")
    new.chmod(0o755)
    monkeypatch.delenv("FUSED_RENDER_LITE_UV", raising=False)
    monkeypatch.setattr(env.shutil, "which", lambda name: str(old))
    monkeypatch.setattr(env, "_download_uv", lambda log: "downloaded")
    assert env.uv_bin(download=True) == "downloaded"
    monkeypatch.setattr(env.shutil, "which", lambda name: str(new))
    assert env.uv_bin(download=True) == str(new)
