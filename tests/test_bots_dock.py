"""The menu-bar dock (bots/dock.py, bots/dock_routes.py): the pinned-apps
store, `entries()` ordering / limits / exclusions over fake bot.json files and
a tmp apps root, `dock_menu_items()` (the menu layout macapp.py builds), and
the two routes through the real server."""
import json
import os
import types

import pytest

from fused_render_app.bots import apptools, dock, registry, store
from fused_render_app.bots import paths as bpaths

MARKED = '<!doctype html><html><head><meta name="fused-app" /><title>{}</title></head><body></body></html>'


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A tmp fused workspace: apps root = <ws>/app (the home is conftest's)."""
    w = tmp_path / "ws"
    monkeypatch.setenv("FUSED_RENDER_DIR", str(w))
    os.makedirs(bpaths.apps_root())
    monkeypatch.setattr(apptools, "REGISTRY_FILES", [])
    return w


def bot(bid, name, updated, **meta):
    store.write_meta(bid, {"name": name, "status": "idle", "updated": updated, **meta})


def app(folder, title, mtime, icon=None):
    d = os.path.join(bpaths.apps_root(), folder)
    os.makedirs(d, exist_ok=True)
    idx = os.path.join(d, "index.html")
    with open(idx, "w") as f:
        f.write(MARKED.format(title))
    if icon:
        with open(os.path.join(d, icon), "w") as f:
            f.write("<svg/>")
    os.utime(idx, (mtime, mtime))
    return d


def ids(rows):
    return [r.get("id") or os.path.basename(r["dir"]) for r in rows]


# ------------------------------------------------------------------ store ---
def test_pin_store_roundtrip_and_guards(ws):
    a = app("alpha", "Alpha", 100)
    b = app("beta", "Beta", 200)
    assert dock.pinned_apps() == []
    assert dock.set_app_pinned(a, True) == [os.path.realpath(a)]
    assert dock.set_app_pinned(b, True) == [os.path.realpath(a), os.path.realpath(b)]
    # Re-pinning moves nothing twice; pin order is kept.
    assert dock.set_app_pinned(a, True) == [os.path.realpath(b), os.path.realpath(a)]
    assert dock.set_app_pinned(b, False) == [os.path.realpath(a)]
    with open(dock.dock_path()) as f:
        assert json.load(f) == {"pinned_apps": [os.path.realpath(a)]}
    assert os.path.dirname(dock.dock_path()) == bpaths.root()
    # Outside the apps root, relative, the root itself, or not an app: refused.
    for bad in ("/etc", "alpha", bpaths.apps_root(), os.path.join(bpaths.apps_root(), "..", "x")):
        with pytest.raises(ValueError):
            dock.set_app_pinned(bad, True)
    os.makedirs(os.path.join(bpaths.apps_root(), "empty"))
    with pytest.raises(ValueError, match="not an app folder"):
        dock.set_app_pinned(os.path.join(bpaths.apps_root(), "empty"), True)


def test_vanished_pinned_app_is_dropped_and_can_be_unpinned(ws):
    import shutil

    a = app("alpha", "Alpha", 100)
    dock.set_app_pinned(a, True)
    shutil.rmtree(a)
    assert dock.pinned_apps() == []
    assert dock.entries()["pinned"] == []
    # Unpinning a folder that is gone still tidies dock.json.
    assert dock.set_app_pinned(a, False) == []
    with open(dock.dock_path()) as f:
        assert json.load(f)["pinned_apps"] == []


def test_torn_dock_json_reads_as_empty(ws):
    os.makedirs(bpaths.root(), exist_ok=True)
    with open(dock.dock_path(), "w") as f:
        f.write("{not json")
    assert dock.pinned_apps() == []
    with open(dock.dock_path(), "w") as f:
        f.write(json.dumps({"pinned_apps": ["relative", 3, None]}))
    assert dock.pinned_apps() == []


# ---------------------------------------------------------------- entries ---
def test_entries_order_limits_and_exclusions(ws):
    bot("b1", "Old", 10)
    bot("b2", "Newest", 60)
    bot("b3", "Mid", 30)
    bot("b4", "Newer", 50)
    bot("b5", "Hidden", 99, hidden=True)
    bot("b6", "Zed pin", 1, pinned=True)
    bot("b7", "Ann pin", 2, pinned=True)
    bot("b8", "Hidden pin", 99, pinned=True, hidden=True)
    a1 = app("a1", "One", 100)
    app("a2", "Two", 400)
    app("a3", "Three", 300)
    app("a4", "Four", 200)
    app("a5", "Five", 500)
    os.makedirs(os.path.join(bpaths.apps_root(), "not-an-app"))
    dock.set_app_pinned(app("a5", "Five", 500), True)
    dock.set_app_pinned(a1, True)

    e = dock.entries()
    assert set(e) == {"pinned", "recent_bots", "recent_apps"}
    # Pinned: bots by name, then apps in pin order. Hidden never shows.
    assert ids(e["pinned"]) == ["b7", "b6", "a5", "a1"]
    # Recent: newest first, at most 3, nothing pinned or hidden.
    assert ids(e["recent_bots"]) == ["b2", "b4", "b3"]
    assert ids(e["recent_apps"]) == ["a2", "a3", "a4"]
    b = e["recent_bots"][0]
    assert b == {"kind": "bot", "id": "b2", "name": "Newest", "face": {}, "status": "idle",
                 "updated": 60.0, "pinned": False}
    a = e["recent_apps"][0]
    assert a["kind"] == "app" and a["name"] == "Two" and a["mtime"] == 400
    assert a["dir"] == os.path.join(bpaths.apps_root(), "a2")  # the listing's spelling


def test_entries_status_live_vs_on_disk(ws, monkeypatch):
    bot("b1", "Runner", 10, status="running", face={"shape": "cloud", "color": "#f0762a"})
    bot("b2", "Asker", 20, status="waiting")
    # Not loaded: a status a dead process left behind reads as idle (Bot.__init__ does the same reset).
    rows = {r["id"]: r for r in dock.entries()["recent_bots"]}
    assert rows["b1"]["status"] == "idle" and rows["b2"]["status"] == "idle"
    assert rows["b1"]["face"] == {"shape": "cloud", "color": "#f0762a"}
    # Loaded: the live meta wins (status, name, pin) without touching disk.
    live = types.SimpleNamespace(id="b1", meta={"name": "Runner", "status": "running", "updated": 99})
    monkeypatch.setattr(registry, "loaded", lambda: [live])
    rows = dock.entries()["recent_bots"]
    assert rows[0]["id"] == "b1" and rows[0]["status"] == "running"


def test_entries_never_builds_a_bot(ws, monkeypatch):
    bot("b1", "Scout", 10, status="running")
    before = open(store.meta_path("b1")).read()
    monkeypatch.setattr(registry, "get", lambda bid: pytest.fail("entries() must not construct a Bot"))
    dock.entries()
    assert open(store.meta_path("b1")).read() == before


def test_entries_with_no_bots_or_apps_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_DIR", str(tmp_path / "nowhere"))
    assert dock.entries() == {"pinned": [], "recent_bots": [], "recent_apps": []}


# ------------------------------------------------------------------- menu ---
def test_dock_menu_items_layout():
    ents = {
        "pinned": [{"kind": "bot", "id": "b1", "name": "Scout", "status": "running"},
                   {"kind": "app", "dir": "/w/app/map", "name": "Map"}],
        "recent_bots": [{"kind": "bot", "id": "b2", "name": "Clerk", "status": "idle"}],
        "recent_apps": [],
    }
    assert dock.dock_menu_items(ents) == [
        ("Pinned", "header", None),
        ("Scout · running", "bot", "b1"),
        ("Map", "app", "/w/app/map"),
        (None, "separator", None),
        ("Recent bots", "header", None),
        ("Clerk", "bot", "b2"),
        (None, "separator", None),
    ]
    assert dock.dock_menu_items({"pinned": [], "recent_bots": [], "recent_apps": []}) == []
    assert dock.dock_menu_items({}) == []


def test_dock_menu_titles_are_unique_and_capped():
    ents = {
        "pinned": [{"kind": "bot", "id": "b1", "name": "Tasks", "status": "idle"},
                   {"kind": "bot", "id": "b2", "name": "Scout", "status": "idle"}],
        "recent_bots": [{"kind": "bot", "id": "b3", "name": "Scout", "status": "idle"},
                        {"kind": "bot", "id": "b4", "name": "Scout", "status": "idle"},
                        {"kind": "bot", "id": "b5", "name": "Pinned", "status": "idle"}],
        "recent_apps": [{"kind": "app", "dir": "/w/app/q", "name": "Quit"},
                        {"kind": "app", "dir": "/w/app/long", "name": "x" * 80},
                        {"kind": "app", "dir": "/w/app/folder", "name": ""}],
    }
    titles = [t for t, kind, _ in dock.dock_menu_items(ents) if kind in ("bot", "app")]
    assert titles[:5] == ["Tasks (2)", "Scout", "Scout (2)", "Scout (3)", "Pinned (2)"]
    assert titles[5] == "Quit (2)"
    assert len(titles[6]) == dock.TITLE_MAX and titles[6].endswith("…")
    assert titles[7] == "folder"
    assert len(set(titles)) == len(titles)
    assert not set(titles) & set(dock.FIXED_TITLES)


def test_app_render_path_matches_the_pages_open_in_tab():
    # apps.ts appOpenUrl: `/render?path=${encodeURIComponent(dir + "/index.html")}`
    assert dock.app_render_path("/Users/me/Fused/app/my map (2)") == \
        "/render?path=%2FUsers%2Fme%2FFused%2Fapp%2Fmy%20map%20(2)%2Findex.html"


# ----------------------------------------------------------------- routes ---
def j(resp):
    status, headers, body = resp
    return status, json.loads(body or b"{}")


def test_routes(client, ws):
    bot("b1", "Scout", 10)
    a = app("alpha", "Alpha", 100)
    st, out = j(client.get("/api/dock"))
    assert st == 200 and ids(out["recent_bots"]) == ["b1"] and ids(out["recent_apps"]) == ["alpha"]
    # POST needs X-Fused.
    st, out = j(client.post("/api/dock/pin", {"dir": a, "pinned": True}, headers={"X-Fused": "0"}))
    assert st == 403
    st, out = j(client.post("/api/dock/pin", {"dir": a, "pinned": True}))
    assert st == 200 and out == {"ok": True, "pinned_apps": [os.path.realpath(a)]}
    st, out = j(client.get("/api/dock"))
    assert ids(out["pinned"]) == ["alpha"] and out["recent_apps"] == []
    st, out = j(client.post("/api/dock/pin", {"dir": "/etc", "pinned": True}))
    assert st == 400 and "under" in out["error"]
    st, out = j(client.post("/api/dock/pin", {"dir": a, "pinned": False}))
    assert st == 200 and out["pinned_apps"] == []
