"""The launcher: hotkey.py spec handling, launcher.py search/settings, /api/launcher routes."""
import json
import os
import urllib.parse

import pytest

from fused_render_app import dock_store, hotkey, launcher, server, showcase


def touch(path) -> str:
    path = str(path)
    with open(path, "ab"):
        pass
    return path


# ---- hotkey specs ------------------------------------------------------------


def test_parse_spec_default():
    keycode, flags, names, key = hotkey.parse_spec("alt+space")
    assert keycode == 0x31 and flags == hotkey.ALT and names == {"alt"} and key == "space"


def test_parse_spec_accepts_browser_codes_and_aliases():
    keycode, flags, names, key = hotkey.parse_spec("Option+Shift+KeyA")
    assert keycode == 0x00 and flags == hotkey.ALT | hotkey.SHIFT and key == "a"
    assert hotkey.parse_spec("cmd+Digit1")[3] == "1"
    assert hotkey.parse_spec("ctrl+F5")[0] == 0x60


@pytest.mark.parametrize("bad", ["", "space", "alt", "alt+", "hyper+space", "alt+nosuchkey", "+"])
def test_parse_spec_rejects(bad):
    with pytest.raises(hotkey.SpecError):
        hotkey.parse_spec(bad)


def test_canonical_orders_modifiers():
    assert hotkey.canonical("shift+cmd+alt+ctrl+KeyK") == "ctrl+alt+shift+cmd+k"
    assert hotkey.canonical("ALT+SPACE") == "alt+space"


def test_display():
    assert hotkey.display("alt+space") == "⌥Space"
    assert hotkey.display("cmd+shift+k") == "⇧⌘K"
    assert hotkey.display("ctrl+slash") == "⌃/"
    assert hotkey.display("garbage") == "garbage"


# ---- settings ----------------------------------------------------------------


def test_hotkey_setting_default_and_roundtrip():
    assert launcher.get_hotkey() == hotkey.DEFAULT_SPEC
    assert launcher.set_hotkey("Cmd+KeyJ") == "cmd+j"
    assert launcher.get_hotkey() == "cmd+j"
    stored = json.load(open(os.path.join(os.environ["FUSED_RENDER_APP_HOME"], "launcher.json")))
    assert stored == {"hotkey": "cmd+j"}


def test_hotkey_setting_invalid_is_not_written():
    launcher.set_hotkey("alt+space")
    with pytest.raises(hotkey.SpecError):
        launcher.set_hotkey("space")
    assert launcher.get_hotkey() == "alt+space"


def test_hotkey_setting_corrupt_file_is_default(app_home):
    os.makedirs(app_home, exist_ok=True)
    with open(app_home / "launcher.json", "w") as f:
        f.write("{not json")
    assert launcher.get_hotkey() == hotkey.DEFAULT_SPEC
    with open(app_home / "launcher.json", "w") as f:
        json.dump({"hotkey": "nonsense"}, f)
    assert launcher.get_hotkey() == hotkey.DEFAULT_SPEC


# ---- search ------------------------------------------------------------------


def rows(*names, pinned=()):
    return [{"file": f"/x/{n}.fused", "name": n, "title": n, "pinned": n in pinned} for n in names]


def test_empty_query_lists_pinned_in_order():
    r = rows("A", "B", "C", "D", pinned=("C", "A"))
    assert [x["name"] for x in launcher.search("", r)] == ["A", "C"]
    assert [x["name"] for x in launcher.search("   ", r)] == ["A", "C"]


def test_search_ranks_prefix_then_word_then_substring_then_subsequence():
    r = rows("Photos", "Screen Shot", "Amphetamine", "OpenSVG", "Nothing")
    got = [x["name"] for x in launcher.search("s", r)]
    # prefix "S…" none by name except Screen Shot; "Photos" has substring; OpenSVG has S inside
    assert got[0] == "Screen Shot"
    assert set(got) == {"Screen Shot", "Photos", "OpenSVG"}
    got = [x["name"] for x in launcher.search("os", r)]
    assert got == ["Photos", "OpenSVG"]  # substring beats subsequence
    assert launcher.search("zz", r) == []


def test_search_is_case_insensitive_and_uses_title():
    r = [{"file": "/x/a.fused", "name": "05_OpenWhisper", "title": "Open Whisper", "pinned": False}]
    assert launcher.search("WHISPER", r) == r
    assert launcher.search("open w", r) == r


def test_search_ties_keep_registry_order_and_limit():
    r = rows(*[f"App{i}" for i in range(12)])
    got = launcher.search("app", r)
    assert [x["name"] for x in got] == [f"App{i}" for i in range(launcher.MAX_RESULTS)]


# ---- registry ----------------------------------------------------------------


def test_registry_dock_first_then_unopened_showcase(tmp_path):
    a = touch(tmp_path / "a.fused")
    dock_store.record_open(a, "Alpha")
    dock_store.set_pinned(a, True)
    shipped = showcase.showcase_files()
    if shipped:
        dock_store.record_open(shipped[0], "First")
    reg = launcher.registry(running={a})
    assert reg[0]["file"] == a and reg[0]["pinned"] and reg[0]["running"] and not reg[0]["showcase"]
    files = [r["file"] for r in reg]
    assert len(files) == len(set(files))
    if shipped:
        assert files[1] == os.path.abspath(shipped[0]) and reg[1]["showcase"]
        assert set(files) >= {os.path.abspath(f) for f in shipped}
        assert reg[1]["title"] == showcase.list_showcase()[0]["title"]


# ---- routes ------------------------------------------------------------------


def test_launcher_page_and_search_route(client, tmp_path):
    status, headers, body = client.get("/launcher")
    assert status == 200 and b"Search apps" in body
    status, _, body = client.get("/settings")
    assert status == 200 and b"/api/launcher/settings" in body
    assert b'href="/settings"' in client.get("/")[2]
    a = touch(tmp_path / "zeta.fused")
    dock_store.record_open(a, "Zeta")
    dock_store.set_pinned(a, True)
    body = json.loads(client.get("/api/launcher")[2])
    assert body["query"] == "" and [x["file"] for x in body["apps"]] == [a]
    assert body["apps"][0]["icon"] is None
    body = json.loads(client.get("/api/launcher?q=" + urllib.parse.quote("zet"))[2])
    assert [x["name"] for x in body["apps"]] == ["Zeta"]
    body = json.loads(client.get("/api/launcher?q=qqqq")[2])
    assert body["apps"] == []


def test_launcher_search_route_icon_url(client, v2_fused_icon):
    dock_store.record_open(v2_fused_icon, "iconic")
    body = json.loads(client.get("/api/launcher?q=icon")[2])
    icon = body["apps"][0]["icon"]
    assert icon.startswith("/api/dock/icon?file=")
    status, _, data = client.get(icon)
    assert status == 200 and b"<svg" in data


def test_launcher_settings_route(client, monkeypatch):
    calls = []
    monkeypatch.setitem(server.native_hooks, "launcher_rebind", calls.append)
    monkeypatch.setitem(server.native_hooks, "launcher_hotkey_bound", lambda: True)
    body = json.loads(client.get("/api/launcher/settings")[2])
    assert body == {"hotkey": "alt+space", "display": "⌥Space", "bound": True,
                    "rowModifier": "alt", "rowModifierDisplay": "⌥", "pinnedBound": None}
    assert json.loads(client.get("/api/launcher/hotkey")[2]) == body  # alias
    status, _, raw = client.post("/api/launcher/settings", {"hotkey": "cmd+shift+KeyL"})
    assert status == 200
    assert json.loads(raw)["hotkey"] == "shift+cmd+l" and calls == ["shift+cmd+l"]
    status, _, raw = client.post("/api/launcher/settings", {"hotkey": "KeyL"})
    assert status == 400 and "modifier" in json.loads(raw)["error"]
    assert launcher.get_hotkey() == "shift+cmd+l"
    # row modifier: stored canonically, no rebind (hook told with None)
    status, _, raw = client.post("/api/launcher/settings", {"rowModifier": "cmd+alt"})
    assert status == 200
    assert json.loads(raw)["rowModifier"] == "alt+cmd" and json.loads(raw)["rowModifierDisplay"] == "⌥⌘"
    assert calls == ["shift+cmd+l", None]
    status, _, raw = client.post("/api/launcher/settings", {"rowModifier": "space"})
    assert status == 400 and launcher.get_row_modifier() == "alt+cmd"
    status, _, raw = client.post("/api/launcher/settings", {"rowModifier": ""})
    assert status == 400
    # unguarded (no X-Fused header) is refused
    status, _, _ = client.post("/api/launcher/settings", {"hotkey": "alt+space"}, headers={"X-Fused": ""})
    assert status in (400, 403)


def test_pinned_specs_and_nth_pinned(tmp_path):
    assert launcher.pinned_specs("") == []
    assert launcher.pinned_specs("alt") == [f"alt+{n}" for n in range(1, 10)]
    assert all(hotkey.parse_spec(s) for s in launcher.pinned_specs("ctrl+alt"))
    a, b, c = (touch(tmp_path / f"{n}.fused") for n in "abc")
    for f in (a, b, c):
        dock_store.record_open(f, f)
    dock_store.set_pinned(c, True)
    dock_store.set_pinned(a, True)
    assert launcher.nth_pinned(1) == c and launcher.nth_pinned(2) == a
    assert launcher.nth_pinned(3) is None and launcher.nth_pinned(0) is None


def test_row_modifier_setting_default_and_corrupt(app_home):
    assert launcher.get_row_modifier() == "alt"
    assert launcher.set_row_modifier("Command") == "cmd"
    assert launcher.get_row_modifier() == "cmd"
    with open(app_home / "launcher.json", "w") as f:
        json.dump({"rowModifier": "space", "hotkey": "alt+space"}, f)
    assert launcher.get_row_modifier() == "alt"
    assert launcher.settings()["rowModifierDisplay"] == "⌥"
