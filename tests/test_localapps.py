"""Folder apps from fused-render's workspace (``~/Fused/local/<app>``):
localapps.py, appfile's folder branches, and the routes that list/open them."""
import json
import os
import sys
import urllib.parse

import pytest

from fused_render_app import appfile, env, jobnotify, localapps, paths, server
from tests.conftest import CALC_PY, ENTRY_HTML, ICON_PNG, ICON_SVG

MARKED = ENTRY_HTML.replace("<title>t</title>", "<title>Calc &amp; Co</title>")
UNMARKED = "<!DOCTYPE html><html><head><title>plain</title></head><body></body></html>"


@pytest.fixture(autouse=True)
def stdlib_python(monkeypatch):
    monkeypatch.setattr(env, "base_python", lambda: sys.executable)
    monkeypatch.setattr(env, "is_ready", lambda app_dir: True)
    monkeypatch.setattr(env, "interpreter_for", lambda app_dir: sys.executable)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A fused-render workspace with three folders under ``local/``: an app
    (marked entry + nested .py + preview), a folder of untagged html, and a
    hidden one. Only the first is an app."""
    ws = tmp_path / "Fused"
    local = ws / "local"
    app = local / "calc-app"
    (app / "lib").mkdir(parents=True)
    (app / "index.html").write_text(MARKED, encoding="utf-8")
    (app / "calc.py").write_text(CALC_PY, encoding="utf-8")
    (app / "lib" / "deep.py").write_text(CALC_PY, encoding="utf-8")
    (app / "preview.png").write_bytes(ICON_PNG)
    (app / "icon.svg").write_bytes(ICON_SVG)
    (app / "metadata.json").write_text(json.dumps(
        {"name": "Calc Pro", "description": "Adds things."}), encoding="utf-8")
    plain = local / "not-an-app"
    plain.mkdir()
    (plain / "index.html").write_text(UNMARKED, encoding="utf-8")
    hidden = local / ".hidden"
    hidden.mkdir()
    (hidden / "index.html").write_text(MARKED, encoding="utf-8")
    monkeypatch.setenv("FUSED_RENDER_DIR", str(ws))
    return {"ws": ws, "local": local, "app": str(app), "plain": str(plain)}


def test_entry_rule_and_listing(workspace):
    app, plain = workspace["app"], workspace["plain"]
    assert appfile.is_app_dir(app)
    assert not appfile.is_app_dir(plain)
    assert appfile.dir_entry(app) == os.path.join(app, "index.html")
    assert appfile.dir_name(app) == "Calc & Co"  # <title>, html-unescaped
    rows = localapps.list_local()
    assert [r["file"] for r in rows] == [app]
    row = rows[0]
    assert row["name"] == "Calc & Co"
    assert row["title"] == "Calc Pro" and row["description"] == "Adds things."  # metadata.json
    assert row["has_preview"] is True and row["preview_version"]


def test_entry_rule_is_name_order_not_index_html(tmp_path):
    d = tmp_path / "two"
    d.mkdir()
    (d / "index.html").write_text(UNMARKED, encoding="utf-8")
    (d / "app.html").write_text(MARKED, encoding="utf-8")
    assert appfile.dir_entry(str(d)) == str(d / "app.html")
    (d / "index.html").write_text(MARKED, encoding="utf-8")
    assert appfile.dir_entry(str(d)) == str(d / "app.html")  # first in name order wins


def test_missing_local_dir_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_DIR", str(tmp_path / "nope"))
    assert localapps.list_local() == []
    assert not os.path.exists(tmp_path / "nope")  # never created


def test_app_dir_for_maps_nested_paths(workspace):
    app = workspace["app"]
    assert localapps.app_dir_for(os.path.join(app, "lib", "deep.py")) == os.path.realpath(app)
    assert localapps.app_dir_for(app) == os.path.realpath(app)
    assert localapps.app_dir_for(str(workspace["local"])) is None
    assert localapps.app_dir_for("/etc/hosts") is None
    assert server.app_dir_for(os.path.join(app, "lib", "deep.py")) == os.path.realpath(app)


def test_open_in_place_keeps_state_local(client, workspace, app_home):
    app = workspace["app"]
    # A stable app id in the entry page must NOT relocate the folder's .fused
    # into fused_data: that folder is fused-render's workspace.
    page = MARKED.replace('<meta name="fused-app" />',
                          '<meta name="fused-app" /><meta name="fused-app-id" content="calc-app-0badf00d" />')
    with open(os.path.join(app, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    status, _h, body = client.post("/api/open", {"file": app})
    data = json.loads(body)
    assert status == 200, data
    assert data["dir"] == app
    assert data["entry"] == os.path.join(app, "index.html")
    assert data["name"] == "Calc & Co"
    assert data["app_id"] == "calc-app-0badf00d"
    assert data["view"] == "/render?path=" + urllib.parse.quote(data["entry"], safe="/")
    assert os.listdir(paths.apps_dir()) == []  # nothing extracted
    dot = os.path.join(app, ".fused")
    assert os.path.isdir(dot) and not os.path.islink(dot)
    assert os.path.isdir(os.path.join(dot, "data"))
    assert os.listdir(paths.fused_data_dir()) == []
    # status re-resolves the same folder
    status, _h, body = client.get("/api/open/status?file=" + urllib.parse.quote(app, safe="/"))
    assert status == 200 and json.loads(body)["status"] == "done"


def test_open_rejects_a_plain_folder(client, workspace):
    status, _h, body = client.post("/api/open", {"file": workspace["plain"]})
    assert status == 400
    assert "fused-app" in json.loads(body)["error"]


def test_run_resolves_nested_py_to_the_folder(client, workspace, monkeypatch):
    app = workspace["app"]
    seen = {}

    def fake_run(path, params, app_dir, timeout=None):
        seen.update(path=path, app_dir=app_dir)
        return {"ok": True, "result": {"x": 1}, "stdout": ""}

    monkeypatch.setattr(env, "run_python", fake_run)
    html = os.path.join(app, "index.html")
    status, _h, body = client.post("/api/run", {"py": "lib/deep.py", "html": html, "params": {}})
    assert status == 200, body
    assert seen["path"] == os.path.join(app, "lib", "deep.py")
    assert seen["app_dir"] == os.path.realpath(app)


def test_folder_readers(workspace):
    app = workspace["app"]
    assert appfile.extract_dir_for(app) == app
    assert appfile.extract_dir_for(workspace["plain"]) is None
    assert appfile.icon_bytes(app) == ICON_SVG
    assert appfile.preview_bytes(app) == ICON_PNG
    assert appfile.app_id_of(app) is None
    assert appfile.icon_override_paths(app) == [os.path.join(app, "icon.svg"), os.path.join(app, "icon.png")]
    assert appfile.preview_override_path(app) == os.path.join(app, "preview.png")
    assert appfile.has_shipped_icon(app) is False and appfile.has_shipped_preview(app) is False


def test_job_banner_maps_a_folder_page_to_its_window(workspace, tmp_path):
    app = workspace["app"]
    page = os.path.join(app, "index.html")
    candidates = [(app, appfile.extract_dir_for(app))]
    assert jobnotify.page_target(page, str(tmp_path / "apps"), candidates) == ("window", app)


def test_symlinked_workspace_agrees_on_one_dir(workspace, tmp_path, monkeypatch):
    """A symlinked ~/Fused: the window keys on the link path, the open/run
    side on the real one. Every reader must land on the same folder, and a
    job page under the real path must map back to the link-path window."""
    link = tmp_path / "FusedLink"
    os.symlink(workspace["ws"], link)
    monkeypatch.setenv("FUSED_RENDER_DIR", str(link))
    via_link = str(link / "local" / "calc-app")
    real = os.path.realpath(workspace["app"])
    assert via_link != real
    assert appfile.open_app_dir(via_link)["dir"] == real
    assert appfile.extract_dir_for(via_link) == real
    assert server.app_dir_for(os.path.join(via_link, "lib", "deep.py")) == real
    page = os.path.join(real, "index.html")
    candidates = [(via_link, appfile.extract_dir_for(via_link))]
    assert jobnotify.page_target(page, str(tmp_path / "apps"), candidates) == ("window", via_link)
    # and the other way round: an abspath candidate against a real page
    assert jobnotify.page_target(page, str(tmp_path / "apps"), [(via_link, via_link)]) == ("window", via_link)
