"""The menu-bar dock: dock_store.py, appfile.icon_bytes, the /api/dock routes."""
import itertools
import json
import os
import sys
import urllib.parse

import pytest

from fused_render_app import appfile, container, dock_store, env, icon_color, server
from tests.conftest import ENTRY_HTML, ICON_PNG, ICON_SVG


@pytest.fixture(autouse=True)
def stdlib_python(monkeypatch):
    # Same as test_server: POST /api/open would otherwise build a venv with uv.
    monkeypatch.setattr(env, "base_python", lambda: sys.executable)
    monkeypatch.setattr(env, "is_ready", lambda app_dir: True)
    monkeypatch.setattr(env, "interpreter_for", lambda app_dir: sys.executable)


@pytest.fixture(autouse=True)
def ticking_clock(monkeypatch):
    # Deterministic, strictly increasing openedAt so ordering/eviction are testable.
    counter = itertools.count(1)
    monkeypatch.setattr(dock_store, "_now", lambda: f"2026-09-15T00:00:{next(counter):02d}.000000Z")
    dock_store._icon_cache.clear()


def files_of(apps):
    return [a["file"] for a in apps]


def touch(path) -> str:
    """A .fused that exists (any bytes: only the store's bookkeeping is under
    test). list_apps drops entries whose file is gone, so store tests need
    real files."""
    path = str(path)
    with open(path, "ab"):
        pass
    return path


# ---- store -----------------------------------------------------------------


def test_record_open_upserts_and_orders_recent_first(tmp_path):
    a, b = touch(tmp_path / "a.fused"), touch(tmp_path / "b.fused")
    dock_store.record_open(a, "A")
    dock_store.record_open(b, "B")
    assert files_of(dock_store.list_apps()) == [b, a]
    # re-open a: same entry (no duplicate), moves to front, name updates
    dock_store.record_open(str(tmp_path / "." / "a.fused"), "A2")
    apps = dock_store.list_apps()
    assert files_of(apps) == [a, b]
    assert apps[0]["name"] == "A2"
    # canonical key on disk
    stored = json.load(open(os.path.join(os.environ["FUSED_RENDER_APP_HOME"], "dock.json")))
    assert [e["file"] for e in stored["apps"]] == [a, b]


def test_eviction_keeps_pinned(tmp_path):
    pinned = touch(tmp_path / "pinned.fused")
    dock_store.record_open(pinned, "P")
    dock_store.set_pinned(pinned, True)
    names = [touch(tmp_path / f"r{i}.fused") for i in range(dock_store.MAX_RECENT + 3)]
    for f in names:
        dock_store.record_open(f, os.path.basename(f))
    apps = dock_store.list_apps()
    assert apps[0]["file"] == pinned and apps[0]["pinned"] is True
    recent = files_of(apps[1:])
    assert len(recent) == dock_store.MAX_RECENT
    assert recent == list(reversed(names))[: dock_store.MAX_RECENT]  # oldest evicted


def test_set_pinned_order_unknown_and_unpin(tmp_path):
    a, b, c = (touch(tmp_path / f"{n}.fused") for n in "abc")
    dock_store.record_open(a, "A")
    dock_store.record_open(b, "B")
    dock_store.set_pinned(b, True)
    dock_store.set_pinned(a, True)
    assert files_of(dock_store.list_apps()) == [b, a]  # pinning appends to the pinned group
    # unknown + pinned -> new entry named after the file stem
    dock_store.set_pinned(c, True)
    apps = dock_store.list_apps()
    assert files_of(apps) == [b, a, c]
    assert apps[2]["name"] == "c" and apps[2]["openedAt"] is None
    # unknown + unpinned -> no-op
    dock_store.set_pinned(touch(tmp_path / "zzz.fused"), False)
    assert len(dock_store.list_apps()) == 3
    # unpin b: falls into the recent group, ordered by openedAt (c has none -> last)
    dock_store.set_pinned(b, False)
    apps = dock_store.list_apps()
    assert files_of(apps) == [a, c, b]
    assert [x["pinned"] for x in apps] == [True, True, False]


def test_remove_and_reorder(tmp_path):
    a, b, c, d = (touch(tmp_path / f"{n}.fused") for n in "abcd")
    for f in (a, b, c, d):
        dock_store.record_open(f, f)
    for f in (a, b, c):
        dock_store.set_pinned(f, True)
    assert files_of(dock_store.list_apps()) == [a, b, c, d]
    # listed pinned first in given order; unlisted pinned (a) after; unpinned/unknown ignored
    dock_store.reorder([c, d, "/nope.fused", b])
    assert files_of(dock_store.list_apps()) == [c, b, a, d]
    dock_store.remove(b)
    dock_store.remove(d)
    assert files_of(dock_store.list_apps()) == [c, a]
    dock_store.remove("/never/there.fused")
    assert files_of(dock_store.list_apps()) == [c, a]


def test_corrupt_or_missing_json_is_empty(app_home, tmp_path):
    assert dock_store.list_apps() == []
    path = app_home / "dock.json"
    path.write_text("{not json")
    assert dock_store.list_apps() == []
    dock_store.record_open(touch(tmp_path / "a.fused"), "A")  # recovers by overwriting
    assert len(dock_store.list_apps()) == 1
    path.write_text(json.dumps({"apps": "nope"}))
    assert dock_store.list_apps() == []


def test_tilesize_default_clamp_and_survives_app_writes(app_home, tmp_path):
    assert dock_store.get_tilesize() == dock_store.DEFAULT_TILESIZE
    assert dock_store.set_tilesize(64) == 64
    assert dock_store.get_tilesize() == 64
    dock_store.record_open(touch(tmp_path / "a.fused"), "A")  # app writes keep the size
    assert dock_store.get_tilesize() == 64
    assert dock_store.set_tilesize(3) == dock_store.MIN_TILESIZE
    assert dock_store.set_tilesize(9999) == dock_store.MAX_TILESIZE
    assert dock_store.set_tilesize(71.6) == 72
    assert dock_store.set_tilesize("big") == dock_store.DEFAULT_TILESIZE
    assert dock_store.set_tilesize(True) == dock_store.DEFAULT_TILESIZE
    assert len(dock_store.list_apps()) == 1  # the apps list is intact
    path = app_home / "dock.json"
    path.write_text(json.dumps({"apps": [], "tilesize": "nope"}))
    assert dock_store.get_tilesize() == dock_store.DEFAULT_TILESIZE
    path.write_text("{not json")
    assert dock_store.get_tilesize() == dock_store.DEFAULT_TILESIZE


def test_dock_size_route(client):
    body = json.loads(client.get("/api/dock")[2])
    assert body["tilesize"] == dock_store.DEFAULT_TILESIZE
    status, _, body = client.post("/api/dock/size", {"tilesize": 96})
    assert status == 200 and json.loads(body) == {"tilesize": 96}
    assert json.loads(client.get("/api/dock")[2])["tilesize"] == 96
    status, _, body = client.post("/api/dock/size", {"tilesize": 1})
    assert json.loads(body) == {"tilesize": dock_store.MIN_TILESIZE}
    status, _, body = client.post("/api/dock/size", {})
    assert json.loads(body) == {"tilesize": dock_store.DEFAULT_TILESIZE}


def test_list_apps_shape(v2_fused_icon, v2_fused):
    dock_store.record_open(v2_fused, "demo")
    dock_store.record_open(v2_fused_icon, "iconic")
    by_file = {a["file"]: a for a in dock_store.list_apps(running={v2_fused})}
    assert set(by_file[v2_fused]) == {"file", "name", "pinned", "running", "openedAt",
                                      "hasIcon", "iconVersion", "hasPreview", "previewVersion"}
    assert by_file[v2_fused]["hasPreview"] is False and by_file[v2_fused]["previewVersion"] is None
    assert by_file[v2_fused]["running"] is True and by_file[v2_fused]["hasIcon"] is False
    assert by_file[v2_fused_icon]["running"] is False and by_file[v2_fused_icon]["hasIcon"] is True


def test_deleted_file_is_dropped_from_the_store_on_read(v2_fused, tmp_path, app_home):
    """No "missing" state: an entry whose .fused is gone — pinned or not —
    leaves the list AND dock.json on the next read; the rest survive."""
    gone = touch(tmp_path / "gone.fused")
    pinned_gone = touch(tmp_path / "pinned_gone.fused")
    dock_store.record_open(v2_fused, "demo")
    dock_store.record_open(gone, "gone")
    dock_store.record_open(pinned_gone, "pg")
    dock_store.set_pinned(pinned_gone, True)
    assert files_of(dock_store.list_apps()) == [pinned_gone, gone, v2_fused]
    os.remove(gone)
    os.remove(pinned_gone)
    assert files_of(dock_store.list_apps()) == [v2_fused]
    stored = json.load(open(os.path.join(app_home, "dock.json")))
    assert [e["file"] for e in stored["apps"]] == [v2_fused]
    # a directory is not a file either
    os.mkdir(tmp_path / "dir.fused")
    dock_store.record_open(str(tmp_path / "dir.fused"), "dir")
    assert files_of(dock_store.list_apps()) == [v2_fused]


def test_prune_keeps_an_entry_changed_while_it_stat_ed(v2_fused, tmp_path, monkeypatch):
    """The stats run unlocked; a file re-opened (or re-pinned) in that window
    has a changed entry and must survive the rewrite — otherwise a slow mount
    could wipe a fresh open, pin included."""
    back = touch(tmp_path / "back.fused")
    dock_store.record_open(v2_fused, "demo")
    dock_store.record_open(back, "back")
    os.remove(back)
    real_isfile = os.path.isfile

    def isfile(path):
        # between the stat and the rewrite the file returns and is re-opened + pinned
        if path == back and real_isfile(path) is False:
            monkeypatch.setattr(os.path, "isfile", real_isfile)
            touch(back)
            dock_store.record_open(back, "back again")
            dock_store.set_pinned(back, True)
            return False  # what the stat saw
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", isfile)
    apps = dock_store.list_apps()
    assert files_of(apps) == [back, v2_fused]
    assert apps[0]["pinned"] is True and apps[0]["name"] == "back again"


def test_prune_survives_an_unwritable_store(v2_fused, tmp_path, monkeypatch, caplog):
    gone = touch(tmp_path / "gone.fused")
    dock_store.record_open(v2_fused, "demo")
    dock_store.record_open(gone, "gone")
    os.remove(gone)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dock_store, "_save", boom)
    assert files_of(dock_store.list_apps()) == [v2_fused]  # filtered answer, no raise
    assert "pruning" in caplog.text


def test_list_apps_sees_icon_written_to_extract_later(v2_fused):
    """The .fused's own icon status is memoised on its (size, mtime); an
    icon.svg written into the EXTRACT dir must still show up on the next
    poll, with a changed iconVersion so tiles retarget their <img>."""
    dock_store.record_open(v2_fused, "demo")
    first = dock_store.list_apps()[0]
    assert first["hasIcon"] is False and first["iconVersion"] is None
    result = appfile.open_app_file(v2_fused)
    with open(os.path.join(result["dir"], appfile.ICON_NAME), "wb") as f:
        f.write(b"<svg>late</svg>")
    second = dock_store.list_apps()[0]
    assert second["hasIcon"] is True and second["iconVersion"] is not None
    # an over-cap override counts as absent again
    with open(os.path.join(result["dir"], appfile.ICON_NAME), "wb") as f:
        f.write(b"x" * (appfile.ICON_MAX_BYTES + 1))
    assert dock_store.list_apps()[0]["hasIcon"] is False


def test_list_apps_icon_version_tracks_override(v2_fused_icon):
    dock_store.record_open(v2_fused_icon, "iconic")
    shipped = dock_store.list_apps()[0]
    assert shipped["hasIcon"] is True and shipped["iconVersion"] is not None
    result = appfile.open_app_file(v2_fused_icon)
    override = os.path.join(result["dir"], appfile.ICON_NAME)
    with open(override, "wb") as f:
        f.write(b"<svg>mine</svg>")
    later = shipped["iconVersion"] + 10**9
    os.utime(override, ns=(later, later))
    assert dock_store.list_apps()[0]["iconVersion"] == later


# ---- icon_bytes ------------------------------------------------------------


def test_icon_bytes_v2_and_v1(v2_fused_icon, v1_fused_icon, v2_fused, v1_fused):
    assert appfile.icon_bytes(v2_fused_icon) == ICON_SVG
    assert appfile.icon_bytes(v1_fused_icon) == ICON_SVG
    assert appfile.icon_bytes(v2_fused) is None
    assert appfile.icon_bytes(v1_fused) is None
    assert appfile.icon_bytes("/nope/missing.fused") is None


def test_icon_bytes_extracted_dir_overrides(v2_fused_icon):
    result = appfile.open_app_file(v2_fused_icon)
    with open(os.path.join(result["dir"], appfile.ICON_NAME), "wb") as f:
        f.write(b"<svg>mine</svg>")
    assert appfile.icon_bytes(v2_fused_icon) == b"<svg>mine</svg>"
    # an over-cap override is ignored; the shipped icon still shows
    with open(os.path.join(result["dir"], appfile.ICON_NAME), "wb") as f:
        f.write(b"x" * (appfile.ICON_MAX_BYTES + 1))
    assert appfile.icon_bytes(v2_fused_icon) == ICON_SVG


def test_icon_bytes_png_fallback(v2_fused_png, v1_fused_png):
    """icon.png is accepted when there is no icon.svg — v2 member and v1 zip alike."""
    assert appfile.icon_bytes(v2_fused_png) == ICON_PNG
    assert appfile.icon_bytes(v1_fused_png) == ICON_PNG
    assert appfile.has_shipped_icon(v2_fused_png) and appfile.has_shipped_icon(v1_fused_png)
    assert appfile.is_png(ICON_PNG) and not appfile.is_png(ICON_SVG)


def test_icon_bytes_svg_outranks_png(tmp_path):
    out = tmp_path / "both.fused"
    container.write(str(out), {"name": "both", "entry": "index.html"},
                    [("index.html", ENTRY_HTML.encode()), ("icon.svg", ICON_SVG), ("icon.png", ICON_PNG)])
    assert appfile.icon_bytes(str(out)) == ICON_SVG
    # an over-cap svg does not hide the png beside it
    big = b"<svg>" + b"x" * appfile.ICON_MAX_BYTES + b"</svg>"
    out2 = tmp_path / "bigsvg.fused"
    container.write(str(out2), {"name": "bigsvg", "entry": "index.html"},
                    [("index.html", ENTRY_HTML.encode()), ("icon.svg", big), ("icon.png", ICON_PNG)])
    assert appfile.icon_bytes(str(out2)) == ICON_PNG


def test_icon_bytes_png_override_in_extract(v2_fused, v2_fused_png):
    # a png written into the extract of an iconless app is its icon
    result = appfile.open_app_file(v2_fused)
    png_path = os.path.join(result["dir"], "icon.png")
    with open(png_path, "wb") as f:
        f.write(ICON_PNG)
    assert appfile.icon_bytes(v2_fused) == ICON_PNG
    # ...but an svg written beside it outranks the png
    with open(os.path.join(result["dir"], appfile.ICON_NAME), "wb") as f:
        f.write(b"<svg>mine</svg>")
    assert appfile.icon_bytes(v2_fused) == b"<svg>mine</svg>"
    # a png has its own, larger cap; over it counts as absent and the shipped png shows
    result = appfile.open_app_file(v2_fused_png)
    over = os.path.join(result["dir"], "icon.png")
    with open(over, "wb") as f:
        f.write(ICON_PNG + b"\0" * appfile.PNG_ICON_MAX_BYTES)
    assert appfile.icon_bytes(v2_fused_png) == ICON_PNG
    with open(over, "wb") as f:
        f.write(ICON_PNG + b"\0" * (appfile.ICON_MAX_BYTES * 2))  # over the svg cap, under the png cap
    assert appfile.icon_bytes(v2_fused_png) == ICON_PNG + b"\0" * (appfile.ICON_MAX_BYTES * 2)


def test_list_apps_tracks_png_override(v2_fused):
    dock_store.record_open(v2_fused, "demo")
    assert dock_store.list_apps()[0]["hasIcon"] is False
    result = appfile.open_app_file(v2_fused)
    png_path = os.path.join(result["dir"], "icon.png")
    with open(png_path, "wb") as f:
        f.write(ICON_PNG)
    row = dock_store.list_apps()[0]
    assert row["hasIcon"] is True and row["iconVersion"] == os.stat(png_path).st_mtime_ns
    # the svg beside it takes over the version too
    svg_path = os.path.join(result["dir"], appfile.ICON_NAME)
    with open(svg_path, "wb") as f:
        f.write(ICON_SVG)
    later = row["iconVersion"] + 10**9
    os.utime(svg_path, ns=(later, later))
    assert dock_store.list_apps()[0]["iconVersion"] == later


def test_icon_bytes_oversized_is_none(tmp_path):
    big = b"<svg>" + b"x" * appfile.ICON_MAX_BYTES + b"</svg>"
    out = tmp_path / "big.fused"
    container.write(str(out), {"name": "big", "entry": "index.html"},
                    [("index.html", ENTRY_HTML.encode()), ("icon.svg", big)])
    assert appfile.icon_bytes(str(out)) is None
    # garbage file: never raises
    junk = tmp_path / "junk.fused"
    junk.write_bytes(b"not a fused file at all")
    assert appfile.icon_bytes(str(junk)) is None


# ---- routes ----------------------------------------------------------------


def test_dock_api_flow(client, v2_fused_icon, v2_fused):
    status, _, body = client.get("/api/dock")
    assert status == 200 and json.loads(body) == {"apps": [], "tilesize": dock_store.DEFAULT_TILESIZE}

    status, _, body = client.post("/api/open", {"file": v2_fused_icon})
    assert status == 200, body
    status, _, body = client.post("/api/open", {"file": v2_fused})
    assert status == 200, body
    apps = json.loads(client.get("/api/dock")[2])["apps"]
    assert files_of(apps) == [v2_fused, v2_fused_icon]
    assert apps[1]["name"] == "iconic" and apps[1]["hasIcon"] is True
    assert apps[0]["running"] is False

    status, _, body = client.post("/api/dock/pin", {"file": v2_fused_icon, "pinned": True})
    apps = json.loads(body)["apps"]
    assert status == 200 and files_of(apps) == [v2_fused_icon, v2_fused] and apps[0]["pinned"] is True
    status, _, body = client.post("/api/dock/pin", {"file": v2_fused, "pinned": True})
    assert files_of(json.loads(body)["apps"]) == [v2_fused_icon, v2_fused]
    status, _, body = client.post("/api/dock/order", {"files": [v2_fused, v2_fused_icon]})
    assert status == 200 and files_of(json.loads(body)["apps"]) == [v2_fused, v2_fused_icon]
    status, _, body = client.post("/api/dock/order", {"files": "nope"})
    assert status == 400
    status, _, body = client.post("/api/dock/remove", {"file": v2_fused})
    assert status == 200 and files_of(json.loads(body)["apps"]) == [v2_fused_icon]

    # icon
    status, headers, body = client.get("/api/dock/icon?" + urllib.parse.urlencode({"file": v2_fused_icon}))
    assert status == 200 and body == ICON_SVG
    assert headers["Content-Type"] == "image/svg+xml"
    assert headers["Cache-Control"] == "no-cache"
    status, _, _ = client.get("/api/dock/icon?" + urllib.parse.urlencode({"file": v2_fused}))
    assert status == 404
    status, _, _ = client.get("/api/dock/icon?file=relative.fused")
    assert status == 404


def test_dock_post_requires_guard(client, v2_fused):
    for action, body in (("open", {"file": v2_fused}), ("pin", {"file": v2_fused, "pinned": True}),
                         ("remove", {"file": v2_fused}), ("order", {"files": []}),
                         ("reveal", {"file": v2_fused}), ("choose", {}), ("home", {}),
                         ("size", {"tilesize": 64})):
        status, _, _ = client.post(f"/api/dock/{action}", body, headers={"X-Fused": ""})
        assert status == 403, action
    status, _, _ = client.post("/api/dock/bogus", {})
    assert status == 404


def test_dock_open_without_and_with_hook(client, v2_fused, monkeypatch, tmp_path):
    status, _, body = client.post("/api/dock/open", {"file": v2_fused})
    reply = json.loads(body)
    assert status == 200 and reply == {
        "ok": True, "native": False,
        "view": "/open?_file=" + urllib.parse.quote(v2_fused, safe="/")}
    status, _, _ = client.post("/api/dock/open", {"file": str(tmp_path / "missing.fused")})
    assert status == 400
    status, _, _ = client.post("/api/dock/open", {"file": "relative.fused"})
    assert status == 400

    called = []
    monkeypatch.setitem(server.native_hooks, "focus_or_open", called.append)
    unnormalised = os.path.join(os.path.dirname(v2_fused), ".", os.path.basename(v2_fused))
    status, _, body = client.post("/api/dock/open", {"file": unnormalised})
    assert status == 200 and json.loads(body) == {"ok": True, "native": True}
    assert called == [v2_fused]


def test_dock_hooks_running_choose_home(client, v2_fused, monkeypatch):
    dock_store.record_open(v2_fused, "demo")
    assert json.loads(client.get("/api/dock")[2])["apps"][0]["running"] is False
    monkeypatch.setitem(server.native_hooks, "open_files", lambda: {v2_fused})
    assert json.loads(client.get("/api/dock")[2])["apps"][0]["running"] is True

    assert json.loads(client.post("/api/dock/choose", {})[2]) == {"ok": False}
    assert json.loads(client.post("/api/dock/home", {})[2]) == {"ok": False, "view": "/"}
    calls = []
    monkeypatch.setitem(server.native_hooks, "choose_file", lambda: calls.append("choose"))
    monkeypatch.setitem(server.native_hooks, "show_home", lambda: calls.append("home"))
    assert json.loads(client.post("/api/dock/choose", {})[2]) == {"ok": True}
    assert json.loads(client.post("/api/dock/home", {})[2]) == {"ok": True}
    assert calls == ["choose", "home"]


def test_dock_reveal(client, v2_fused, monkeypatch):
    spawned = []
    monkeypatch.setattr(server.subprocess, "Popen", lambda argv, **kw: spawned.append(argv))
    status, _, body = client.post("/api/dock/reveal", {"file": v2_fused})
    assert json.loads(body) == {"ok": True} and spawned == [["open", "-R", v2_fused]]


def test_dock_page_route(client):
    if not os.path.isfile(os.path.join(server.STATIC_DIR, "dock.html")):
        pytest.skip("static/dock.html not present yet")
    status, headers, _ = client.get("/dock")
    assert status == 200 and headers["Content-Type"].startswith("text/html")


def test_list_apps_opens_the_fused_once_per_mtime(v2_fused_icon, monkeypatch):
    """Per poll only stats happen; the manifest parse is memoised on the
    .fused's (size, mtime)."""
    dock_store.record_open(v2_fused_icon, "iconic")
    calls = {"shipped": 0, "override": 0}
    real_shipped, real_override = appfile.has_shipped_icon, appfile.icon_override_paths

    def shipped(f):
        calls["shipped"] += 1
        return real_shipped(f)

    def override(f):
        calls["override"] += 1
        return real_override(f)

    monkeypatch.setattr(appfile, "has_shipped_icon", shipped)
    monkeypatch.setattr(appfile, "icon_override_paths", override)
    for _ in range(3):
        assert dock_store.list_apps()[0]["hasIcon"] is True
    assert calls == {"shipped": 1, "override": 1}


# ---- icon_color (theme recolouring of a picked glyph) ----------------------

# What fused-render's IconPicker writes (IconPicker.glyphIconSvg, #1159): the
# colour's NAME on the root, a prefers-color-scheme fallback, a rounded plate
# on var(--fused-bg), currentColor strokes.
GLYPH_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" data-fused-color="red">'
    b"<style>svg{color:#d44c47;--fused-bg:#ffffff}"
    b"@media(prefers-color-scheme:dark){svg{color:#df5452;--fused-bg:#000000}}</style>"
    b'<rect width="24" height="24" rx="5.28" style="fill:var(--fused-bg)"/>'
    b'<g transform="translate(3 3) scale(0.75)" fill="none" stroke="currentColor" stroke-width="3">'
    b'<path d="M4 4h16"/><circle cx="12" cy="12" r="3" fill="currentColor"/></g></svg>'
)


def test_theme_icon_svg_swaps_current_color_and_plate_for_the_theme_hex():
    dark = icon_color.theme_icon_svg(GLYPH_SVG, "dark")
    light = icon_color.theme_icon_svg(GLYPH_SVG, "light")
    assert b"currentColor" not in dark and b"currentColor" not in light
    assert b"var(--fused-bg)" not in dark and b"var(--fused-bg)" not in light
    assert dark.count(b'stroke="#df5452"') == 1 and dark.count(b'fill="#df5452"') == 1
    assert light.count(b'stroke="#d44c47"') == 1 and light.count(b'fill="#d44c47"') == 1
    assert b'style="fill:#000000"' in dark and b'style="fill:#ffffff"' in light
    # everything else — the marker, the fallback <style> — is left as it was
    assert b'data-fused-color="red"' in dark and b"<style>" in dark
    # a pre-plate file (no var) still gets its strokes swapped
    old = GLYPH_SVG.replace(b'<rect width="24" height="24" rx="5.28" style="fill:var(--fused-bg)"/>', b"")
    assert b'stroke="#df5452"' in icon_color.theme_icon_svg(old, "dark")


def test_theme_icon_svg_passes_through_when_it_should_not_touch_the_file():
    # no marker (an emoji glyph, a hand-authored icon): drawn as is
    assert icon_color.theme_icon_svg(ICON_SVG, "dark") == ICON_SVG
    plain = b'<svg xmlns="http://www.w3.org/2000/svg"><path stroke="currentColor" d="M0 0"/></svg>'
    assert icon_color.theme_icon_svg(plain, "dark") == plain
    # unknown name: "no marker", not an error
    unknown = GLYPH_SVG.replace(b'data-fused-color="red"', b'data-fused-color="teal"')
    assert icon_color.theme_icon_svg(unknown, "dark") == unknown
    # marker on a nested element does not count — only the root's
    nested = b'<svg xmlns="http://www.w3.org/2000/svg"><g data-fused-color="red" stroke="currentColor"/></svg>'
    assert icon_color.theme_icon_svg(nested, "dark") == nested
    # no / bogus theme: raw bytes (the pre-theme URL keeps working)
    assert icon_color.theme_icon_svg(GLYPH_SVG, "") == GLYPH_SVG
    assert icon_color.theme_icon_svg(GLYPH_SVG, "sepia") == GLYPH_SVG
    # not UTF-8: never raises
    assert icon_color.theme_icon_svg(b"\xff\xfe<svg>", "dark") == b"\xff\xfe<svg>"


def test_read_icon_color_legacy_names_still_follow_the_theme():
    for name in ("gray", "brown", "orange", "purple", "pink", "default", "yellow", "blue", "green"):
        svg = GLYPH_SVG.replace(b'data-fused-color="red"', b'data-fused-color="%s"' % name.encode()).decode()
        assert icon_color.read_icon_color(svg) == name
    assert icon_color.read_icon_color("<p>not svg</p>") is None


def test_dock_icon_route_serves_png_as_is(client, v2_fused_png):
    base = "/api/dock/icon?" + urllib.parse.urlencode({"file": v2_fused_png})
    status, headers, body = client.get(base + "&theme=dark")
    assert status == 200 and headers["Content-Type"] == "image/png" and body == ICON_PNG


def test_dock_icon_route_recolours_for_theme(client, v2_fused_icon):
    # a picked glyph written into the extract: that override wins over the shipped icon
    result = appfile.open_app_file(v2_fused_icon)
    with open(os.path.join(result["dir"], appfile.ICON_NAME), "wb") as f:
        f.write(GLYPH_SVG)
    base = "/api/dock/icon?" + urllib.parse.urlencode({"file": v2_fused_icon})
    status, headers, body = client.get(base + "&theme=dark")
    assert status == 200 and headers["Content-Type"] == "image/svg+xml"
    assert body == icon_color.theme_icon_svg(GLYPH_SVG, "dark") and b'stroke="#df5452"' in body
    status, _, body = client.get(base + "&theme=light")
    assert status == 200 and b'stroke="#d44c47"' in body
    # no theme: the raw file, as before
    status, _, body = client.get(base)
    assert status == 200 and body == GLYPH_SVG


# ---- preview.png (the hover bubble's picture) ------------------------------


def test_preview_bytes_v2_v1_and_absent(v2_fused_preview, v1_fused_preview, v2_fused, v1_fused):
    assert appfile.preview_bytes(v2_fused_preview) == ICON_PNG
    assert appfile.preview_bytes(v1_fused_preview) == ICON_PNG
    assert appfile.preview_bytes(v2_fused) is None
    assert appfile.preview_bytes(v1_fused) is None
    assert appfile.preview_bytes("/nope/missing.fused") is None
    assert appfile.has_shipped_preview(v2_fused_preview) and appfile.has_shipped_preview(v1_fused_preview)
    assert not appfile.has_shipped_preview(v2_fused) and not appfile.has_shipped_preview("/nope.fused")


def test_preview_bytes_extract_override_and_cap(v2_fused_preview, v2_fused):
    # a preview written into the extract wins over the shipped one
    result = appfile.open_app_file(v2_fused_preview)
    with open(os.path.join(result["dir"], appfile.PREVIEW_NAME), "wb") as f:
        f.write(b"mine")
    assert appfile.preview_bytes(v2_fused_preview) == b"mine"
    # an over-cap override is ignored; the shipped preview still shows
    with open(os.path.join(result["dir"], appfile.PREVIEW_NAME), "wb") as f:
        f.write(b"x" * (appfile.PREVIEW_MAX_BYTES + 1))
    assert appfile.preview_bytes(v2_fused_preview) == ICON_PNG
    # a preview written into the extract of an app that ships none is its preview
    result = appfile.open_app_file(v2_fused)
    with open(os.path.join(result["dir"], appfile.PREVIEW_NAME), "wb") as f:
        f.write(b"late")
    assert appfile.preview_bytes(v2_fused) == b"late"


def test_list_apps_preview_flags_and_version(v2_fused_preview, v2_fused):
    dock_store.record_open(v2_fused_preview, "pictured")
    dock_store.record_open(v2_fused, "demo")
    by_file = {a["file"]: a for a in dock_store.list_apps()}
    assert by_file[v2_fused_preview]["hasPreview"] is True
    assert by_file[v2_fused_preview]["previewVersion"] == os.stat(v2_fused_preview).st_mtime_ns
    assert by_file[v2_fused]["hasPreview"] is False and by_file[v2_fused]["previewVersion"] is None
    # a preview written into the extract later shows up on the next poll (the
    # .fused's own status is memoised) with the override's mtime as version
    result = appfile.open_app_file(v2_fused)
    p = os.path.join(result["dir"], appfile.PREVIEW_NAME)
    with open(p, "wb") as f:
        f.write(ICON_PNG)
    later = os.stat(v2_fused).st_mtime_ns + 10**9
    os.utime(p, ns=(later, later))
    row = {a["file"]: a for a in dock_store.list_apps()}[v2_fused]
    assert row["hasPreview"] is True and row["previewVersion"] == later
    # icon status is untouched by the preview
    assert row["hasIcon"] is False and row["iconVersion"] is None


def test_dock_preview_route(client, v2_fused_preview, v2_fused):
    status, headers, body = client.get("/api/dock/preview?" + urllib.parse.urlencode({"file": v2_fused_preview, "v": "1"}))
    assert status == 200 and body == ICON_PNG
    assert headers["Content-Type"] == "image/png"
    assert "immutable" in headers["Cache-Control"]
    status, _, _ = client.get("/api/dock/preview?" + urllib.parse.urlencode({"file": v2_fused}))
    assert status == 404
    status, _, _ = client.get("/api/dock/preview?file=relative.fused")
    assert status == 404
