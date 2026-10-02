"""The menu-bar dock (bots/dock.py, bots/dock_routes.py): the pinned-apps
store, the tile size, bot pins, `entries()` ordering / limits / exclusions over
fake bot.json files and a tmp apps root, the tray's routes through the real
server (open / home with and without the native hooks, reveal's root guard,
pin, pin-bot, size), and `/dock` serving the built tray page. The tray's
panel itself (menubar_dock.py) is AppKit-only and untested, like
mainwindow.py."""
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
                 "running": False, "updated": 60.0, "pinned": False}
    a = e["recent_apps"][0]
    assert a == {"kind": "app", "dir": os.path.join(bpaths.apps_root(), "a2"),  # the listing's spelling
                 "name": "Two", "icon": False, "pinned": False, "mtime": 400}
    assert [r["pinned"] for r in e["pinned"]] == [True] * 4


def test_entries_status_live_vs_on_disk(ws, monkeypatch):
    bot("b1", "Runner", 10, status="running", face={"shape": "cloud", "color": "#f0762a"})
    bot("b2", "Asker", 20, status="waiting")
    # Not loaded: a status a dead process left behind reads as idle (Bot.__init__ does the same reset).
    rows = {r["id"]: r for r in dock.entries()["recent_bots"]}
    assert rows["b1"]["status"] == "idle" and rows["b2"]["status"] == "idle"
    assert not rows["b1"]["running"] and not rows["b2"]["running"]
    assert rows["b1"]["face"] == {"shape": "cloud", "color": "#f0762a"}
    # Loaded: the live meta wins (status, name, pin) without touching disk.
    live = types.SimpleNamespace(id="b1", meta={"name": "Runner", "status": "running", "updated": 99})
    monkeypatch.setattr(registry, "loaded", lambda: [live])
    rows = dock.entries()["recent_bots"]
    assert rows[0]["id"] == "b1" and rows[0]["status"] == "running" and rows[0]["running"] is True


def test_entries_never_builds_a_bot(ws, monkeypatch):
    bot("b1", "Scout", 10, status="running")
    before = open(store.meta_path("b1")).read()
    monkeypatch.setattr(registry, "get", lambda bid: pytest.fail("entries() must not construct a Bot"))
    dock.entries()
    assert open(store.meta_path("b1")).read() == before


def test_entries_with_no_bots_or_apps_root(tmp_path, monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_DIR", str(tmp_path / "nowhere"))
    assert dock.entries() == {"pinned": [], "recent_bots": [], "recent_apps": []}


# ------------------------------------------------------- tiles and pins ---
def test_tilesize_default_clamp_and_persist(ws):
    assert dock.tilesize() == dock.TILESIZE_DEFAULT == 52
    assert dock.set_tilesize(64) == 64 and dock.tilesize() == 64
    assert dock.set_tilesize(3) == 16 and dock.set_tilesize(900) == 128
    assert dock.set_tilesize("40.6") == 41
    for bad in ("big", None, True, float("nan"), float("inf"), [1]):
        with pytest.raises(ValueError):
            dock.set_tilesize(bad)
    assert dock.tilesize() == 41
    # Tile size and pins share dock.json without clobbering each other.
    a = app("alpha", "Alpha", 100)
    dock.set_app_pinned(a, True)
    dock.set_tilesize(70)
    with open(dock.dock_path()) as f:
        assert json.load(f) == {"pinned_apps": [os.path.realpath(a)], "tilesize": 70}
    # A hand-edited value out of range or of the wrong type reads clamped / as the default.
    store.write_json_atomic(dock.dock_path(), {"tilesize": 4000})
    assert dock.tilesize() == 128
    store.write_json_atomic(dock.dock_path(), {"tilesize": "huge"})
    assert dock.tilesize() == 52


def test_app_icon_flag(ws):
    plain = app("plain", "Plain", 100)
    svg = app("svg", "Svg", 200, icon="icon.svg")
    png = app("png", "Png", 300, icon="icon.png")
    assert not dock.app_has_icon(plain) and dock.app_has_icon(svg) and dock.app_has_icon(png)
    icons = {os.path.basename(r["dir"]): r["icon"] for r in dock.entries()["recent_apps"]}
    assert icons == {"plain": False, "svg": True, "png": True}


def test_set_bot_pinned_writes_bot_json_and_live_meta(ws):
    bot("b1", "Scout", 10)
    assert dock.set_bot_pinned("b1", True) is True
    assert store.read_meta("b1")["pinned"] is True
    assert registry.get("b1").meta["pinned"] is True  # the loaded Bot agrees
    assert ids(dock.entries()["pinned"]) == ["b1"]
    assert dock.set_bot_pinned("b1", False) is False
    assert store.read_meta("b1")["pinned"] is False
    assert dock.entries()["pinned"] == []
    with pytest.raises(ValueError):
        dock.set_bot_pinned("nope", True)


def test_bot_exists_takes_plain_ids_only(ws):
    bot("b1", "Scout", 10)
    assert dock.bot_exists("b1")
    for bad in ("", "nope", "../b1", "x/b1", ".", "..", None, 3):
        assert not dock.bot_exists(bad), bad


def test_view_paths_match_the_pages_own_links():
    # apps.ts appOpenUrl: `/render?path=${encodeURIComponent(dir + "/index.html")}`
    assert dock.app_render_path("/Users/me/Fused/app/my map (2)") == \
        "/render?path=%2FUsers%2Fme%2FFused%2Fapp%2Fmy%20map%20(2)%2Findex.html"
    assert dock.bot_view_path("b 1/x") == "/?bot=b%201%2Fx"


# ----------------------------------------------------------------- routes ---
def j(resp):
    status, headers, body = resp
    return status, json.loads(body or b"{}")


def test_routes(client, ws):
    bot("b1", "Scout", 10)
    a = app("alpha", "Alpha", 100)
    st, out = j(client.get("/api/dock"))
    assert st == 200 and ids(out["recent_bots"]) == ["b1"] and ids(out["recent_apps"]) == ["alpha"]
    assert set(out) == {"pinned", "recent_bots", "recent_apps", "tilesize"} and out["tilesize"] == 52
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


@pytest.fixture
def no_hooks(monkeypatch):
    """`server.native_hooks` without the macOS app's dock / home hooks (a
    browser or CLI run), restored afterwards."""
    from fused_render_app import server

    for k in ("dock_open", "show_home"):
        monkeypatch.delitem(server.native_hooks, k, raising=False)
    return server.native_hooks


def test_open_route_without_the_app_returns_the_view(client, ws, no_hooks):
    bot("b1", "Scout", 10)
    a = app("my map", "Map", 100)
    st, out = j(client.post("/api/dock/open", {"kind": "bot", "id": "b1"}))
    assert st == 200 and out == {"ok": True, "native": False, "view": "/?bot=b1"}
    st, out = j(client.post("/api/dock/open", {"kind": "app", "dir": a}))
    assert st == 200 and out == {"ok": True, "native": False, "view": dock.app_render_path(a)}
    assert out["view"].startswith("/render?path=") and out["view"].endswith("%2Findex.html")


def test_open_route_calls_the_native_hook(client, ws, no_hooks, monkeypatch):
    calls = []
    monkeypatch.setitem(no_hooks, "dock_open", lambda kind, key: calls.append((kind, key)))
    bot("b1", "Scout", 10)
    a = app("alpha", "Alpha", 100)
    st, out = j(client.post("/api/dock/open", {"kind": "bot", "id": "b1"}))
    assert st == 200 and out == {"ok": True, "native": True}
    st, out = j(client.post("/api/dock/open", {"kind": "app", "dir": a}))
    assert st == 200 and out == {"ok": True, "native": True}
    # The app's key is the listing's spelling of dir (what show_url keys windows on).
    assert calls == [("bot", "b1"), ("app", a)]


def test_open_route_refusals(client, ws, no_hooks, monkeypatch):
    calls = []
    monkeypatch.setitem(no_hooks, "dock_open", lambda kind, key: calls.append((kind, key)))
    bot("b1", "Scout", 10)
    os.makedirs(os.path.join(bpaths.apps_root(), "empty"))
    bad = [
        {"kind": "window", "id": "b1"},
        {},
        {"kind": "bot"},
        {"kind": "bot", "id": "nope"},
        {"kind": "bot", "id": "../b1"},
        {"kind": "app", "dir": "/etc"},
        {"kind": "app", "dir": os.path.join(bpaths.apps_root(), "..", "x")},
        {"kind": "app", "dir": os.path.join(bpaths.apps_root(), "empty")},
        {"kind": "app"},
    ]
    for body in bad:
        st, out = j(client.post("/api/dock/open", body))
        assert st == 400 and out["error"], body
    assert calls == []
    st, _ = j(client.post("/api/dock/open", {"kind": "bot", "id": "b1"}, headers={"X-Fused": "0"}))
    assert st == 403 and calls == []


def test_home_route(client, no_hooks, monkeypatch):
    st, out = j(client.post("/api/dock/home", {}))
    assert st == 200 and out == {"ok": True, "native": False, "view": "/"}
    calls = []
    monkeypatch.setitem(no_hooks, "show_home", lambda: calls.append("home"))
    st, out = j(client.post("/api/dock/home", {}))
    assert st == 200 and out == {"ok": True, "native": True} and calls == ["home"]
    st, _ = j(client.post("/api/dock/home", {}, headers={"X-Fused": "0"}))
    assert st == 403 and calls == ["home"]


def test_reveal_route_stays_in_the_apps_root(client, ws, monkeypatch):
    shown = []
    monkeypatch.setattr(dock, "_open_reveal", shown.append)
    a = app("alpha", "Alpha", 100)
    st, out = j(client.post("/api/dock/reveal", {"dir": a}))
    assert st == 200 and out == {"ok": True} and shown == [os.path.realpath(a)]
    for d in ("/etc", "", "alpha", bpaths.apps_root(), os.path.join(bpaths.apps_root(), "..", "x")):
        st, out = j(client.post("/api/dock/reveal", {"dir": d}))
        assert st == 400 and out["error"], d
    st, _ = j(client.post("/api/dock/reveal", {"dir": a}, headers={"X-Fused": "0"}))
    assert st == 403
    assert shown == [os.path.realpath(a)]


def test_pin_bot_route(client, ws):
    bot("b1", "Scout", 10)
    st, out = j(client.post("/api/dock/pin-bot", {"id": "b1", "pinned": True}))
    assert st == 200 and out == {"ok": True, "id": "b1", "pinned": True}
    assert store.read_meta("b1")["pinned"] is True
    st, out = j(client.get("/api/dock"))
    assert ids(out["pinned"]) == ["b1"] and out["pinned"][0]["pinned"] is True and out["recent_bots"] == []
    st, out = j(client.post("/api/dock/pin-bot", {"id": "b1", "pinned": False}))
    assert st == 200 and out["pinned"] is False and store.read_meta("b1")["pinned"] is False
    for body in ({"id": "nope", "pinned": True}, {"pinned": True}, {"id": "../b1", "pinned": True}):
        st, out = j(client.post("/api/dock/pin-bot", body))
        assert st == 400 and out["error"], body
    st, _ = j(client.post("/api/dock/pin-bot", {"id": "b1", "pinned": True}, headers={"X-Fused": "0"}))
    assert st == 403 and store.read_meta("b1")["pinned"] is False


def test_order_route_reorders_pinned_apps(client, ws):
    a, b, c = (app(n, n.title(), 100) for n in ("alpha", "beta", "gamma"))
    loose = app("loose", "Loose", 100)
    for d in (a, b, c):
        dock.set_app_pinned(d, True)
    ra, rb, rc = (os.path.realpath(d) for d in (a, b, c))
    st, out = j(client.post("/api/dock/order", {"dirs": [c, a, b]}))
    assert st == 200 and out == {"ok": True, "pinned_apps": [rc, ra, rb]}
    assert ids(j(client.get("/api/dock"))[1]["pinned"]) == ["gamma", "alpha", "beta"]
    # Unpinned, unknown, relative and repeated dirs are ignored; a pinned dir
    # the list omits keeps its relative place after the listed ones.
    st, out = j(client.post("/api/dock/order", {"dirs": [loose, b, "/etc", "rel", b]}))
    assert st == 200 and out["pinned_apps"] == [rb, rc, ra]
    assert loose not in out["pinned_apps"] and os.path.realpath(loose) not in out["pinned_apps"]
    for body in ({}, {"dirs": "alpha"}, {"dirs": [a, 3]}):
        st, out = j(client.post("/api/dock/order", body))
        assert st == 400 and out["error"], body
    st, _ = j(client.post("/api/dock/order", {"dirs": [a, b, c]}, headers={"X-Fused": "0"}))
    assert st == 403
    assert dock.pinned_apps() == [rb, rc, ra]


def test_size_route_clamps_and_persists(client, ws):
    st, out = j(client.post("/api/dock/size", {"tilesize": 80}))
    assert st == 200 and out == {"ok": True, "tilesize": 80}
    assert j(client.get("/api/dock"))[1]["tilesize"] == 80
    st, out = j(client.post("/api/dock/size", {"tilesize": 1}))
    assert st == 200 and out["tilesize"] == 16
    st, out = j(client.post("/api/dock/size", {"tilesize": 10_000}))
    assert st == 200 and out["tilesize"] == 128
    for body in ({"tilesize": "big"}, {}, {"tilesize": None}):
        st, out = j(client.post("/api/dock/size", body))
        assert st == 400 and out["error"], body
    st, _ = j(client.post("/api/dock/size", {"tilesize": 40}, headers={"X-Fused": "0"}))
    assert st == 403
    assert j(client.get("/api/dock"))[1]["tilesize"] == 128


def test_dock_page_serves_the_built_tray_or_503(client, tmp_path, monkeypatch):
    from fused_render_app import server

    static = tmp_path / "static"
    monkeypatch.setattr(server, "STATIC_DIR", str(static))
    status, headers, body = client.get("/dock")
    assert status == 503 and b"dock.html missing" in body and b"Run scripts/build_shell.sh" in body
    assert headers["Content-Type"].startswith("text/html")
    (static / "shell-dist").mkdir(parents=True)
    (static / "shell-dist" / "dock.html").write_text("<!doctype html><title>tray</title>")
    status, headers, body = client.get("/dock")
    assert status == 200 and body == b"<!doctype html><title>tray</title>"
    assert headers["Content-Type"].startswith("text/html")
