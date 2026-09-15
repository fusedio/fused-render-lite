"""The showcase .fused apps shipped in fused_render_lite/showcase/."""
import json
import os
import sys

import pytest

from fused_render_lite import appfile, container, env, showcase

# Every member a showcase app must NOT call: lite throws on these. An app that
# probes one inside try/catch and falls back (OpenWhisper: fused.capture ->
# getUserMedia) lists it under "guarded" in showcase.json.
UNSUPPORTED = ("fused.capture", "fused.fileIndex", "fused.snapshot", "autoReload(true")


@pytest.fixture(autouse=True)
def stdlib_python(monkeypatch):
    monkeypatch.setattr(env, "base_python", lambda: sys.executable)
    monkeypatch.setattr(env, "is_ready", lambda app_dir: True)
    monkeypatch.setattr(env, "interpreter_for", lambda app_dir: sys.executable)


def test_showcase_are_shipped_and_listed():
    files = showcase.showcase_files()
    assert len(files) >= 2
    assert all(os.path.isfile(f) and f.startswith(showcase.SHOWCASE_DIR) for f in files)
    rows = showcase.list_showcase()
    assert [r["file"] for r in rows] == files  # nothing skipped: every file parses
    for row in rows:
        assert row["id"] == os.path.basename(row["file"])
        assert row["title"] and row["size"] > 0
        assert row["has_preview"] is True  # cards need a thumbnail


def test_sidecar_matches_files():
    with open(showcase.SIDECAR, encoding="utf-8") as f:
        meta = json.load(f)
    assert set(meta) == {os.path.basename(p) for p in showcase.showcase_files()}
    for row in showcase.list_showcase():
        assert row["title"] == meta[row["id"]]["title"]
        assert row["description"] == meta[row["id"]]["description"]


def test_showcase_call_nothing_lite_rejects():
    with open(showcase.SIDECAR, encoding="utf-8") as f:
        meta = json.load(f)
    for path in showcase.showcase_files():
        guarded = set(meta.get(os.path.basename(path), {}).get("guarded", ()))
        assert guarded <= set(UNSUPPORTED), path
        index = appfile.read_manifest(path)
        assert index.get("fused_app_file") == container.VERSION, path
        for entry in index["files"]:
            if entry["path"].lower().endswith((".html", ".htm", ".js")):
                text = container.read_member(path, index, entry["path"], appfile.MAX_ENTRY_BYTES)
                text = text.decode("utf-8", "replace")
                for name in UNSUPPORTED:
                    if name in guarded:
                        continue
                    assert name not in text, f"{os.path.basename(path)}:{entry['path']} uses {name}"
        # each declares its own environment: no legacy numpy/pandas install on first click
        assert container.find(index, "pyproject.toml") is not None, path


def test_showcase_open(lite_home):
    for path in showcase.showcase_files():
        result = appfile.open_app_file(path)
        assert os.path.isfile(result["entry"])
        assert result["dir"].startswith(str(lite_home / "apps"))
        assert os.path.getsize(path) > 0  # the packaged file is only read, never moved


def test_showcase_api(client):
    status, _, body = client.get("/api/showcase")
    assert status == 200
    rows = json.loads(body)["showcase"]
    assert len(rows) == len(showcase.showcase_files())
    ex = rows[0]
    status, headers, body = client.get("/api/showcase/preview?id=" + ex["id"])
    assert status == 200 and headers["Content-Type"] == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    for bad in ("nope.fused", "../showcase.json", "%2e%2e%2fshowcase.json", ""):
        status, _, _ = client.get("/api/showcase/preview?id=" + bad)
        assert status == 404, bad
    # the card's click is the ordinary open path
    status, _, body = client.post("/api/open", {"file": ex["file"]})
    assert status == 200, body
    assert json.loads(body)["view"].startswith("/render?path=")


def test_placeholder_lists_showcase(client):
    status, _, body = client.get("/")
    assert status == 200
    assert b'id="showcase"' in body and b"/api/showcase" in body
