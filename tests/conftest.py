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
    return home


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
def v1_fused_icon(tmp_path):
    out = tmp_path / "legacy-icon.fused"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"fused_app_file": 1, "name": "legacy-icon", "entry": "index.html"}))
        zf.writestr("files/index.html", ENTRY_HTML)
        zf.writestr("files/icon.svg", ICON_SVG)
    return str(out)
