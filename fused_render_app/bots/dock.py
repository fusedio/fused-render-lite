"""The menu-bar dock: what the FusedBot menu-bar item lists above its fixed
items (macapp.py), and the apps' "Pin to menu bar" (apps/AppMenu.tsx).

    Pinned          pinned bots (bot.json `pinned`), then pinned apps (dock.json)
    Recent bots     the 3 most recently updated bots that are not pinned
    Recent apps     the 3 most recently changed apps that are not pinned

Bots are read straight from disk (`store.list_ids` + bot.json) — a Bot the
registry already built is read from memory instead, so its live status shows —
and never constructed here: building a Bot writes its bot.json and pulls in the
Chrome/CDP code, which a 5-second menu refresh has no business doing. A
`hidden` bot is never listed, pinned or not. Apps are the same scan that feeds
every bot's APPS section (`apptools.list_apps` over `<workspace>/app`), sorted
by their index.html's mtime; a pinned app whose folder is gone (or no longer an
app) is simply not listed.

Pinned apps live in `<home>/bots/dock.json` as `{"pinned_apps": [<real dir>]}`,
in pin order. Pinned BOTS stay where the sidebar's pin already puts them
(bot.json), so one pin means the same thing in both places.

`dock_menu_items(entries)` is the menu layout as plain data — the one piece of
the menu-bar code that is testable without AppKit.
"""
from __future__ import annotations

import json
import os

from fused_render_app.bots import paths as bpaths

DOCK_FILE = "dock.json"
RECENT_BOTS = 3
RECENT_APPS = 3
TITLE_MAX = 40
#: Bot statuses a NEW Bot resets to idle (bot.py: a status left over from a
#: process that died). A bot this process never loaded is shown that way too.
_STALE_STATUSES = ("running", "waiting", "paused")
#: Titles of the menu's fixed items and section headers: rumps keys a menu by
#: title, so a bot named "Quit" must not collide with (and vanish behind) them.
SECTION_PINNED = "Pinned"
SECTION_BOTS = "Recent bots"
SECTION_APPS = "Recent apps"
FIXED_TITLES = ("Open FusedBot", "Tasks", "Open in browser", "Open app logs", "Quit")


# ------------------------------------------------------------------ store ---
def dock_path() -> str:
    return os.path.join(bpaths.root(), DOCK_FILE)


def _read() -> dict:
    try:
        with open(dock_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stored_pins() -> list[str]:
    pins = _read().get("pinned_apps")
    if not isinstance(pins, list):
        return []
    return [p for p in pins if isinstance(p, str) and os.path.isabs(p)]


def _under_apps_root(d: str) -> str:
    """`d` as a real path inside the apps root, else ValueError (the same rule
    as routes._under_apps_root, which this module must not import)."""
    root = os.path.realpath(bpaths.apps_root())
    if not d or not isinstance(d, str) or not os.path.isabs(d):
        raise ValueError("dir must be an absolute path")
    real = os.path.realpath(d)
    if not real.startswith(root + os.sep):
        raise ValueError(f"dir must be an app folder under {root}")
    return real


def pinned_apps() -> list[str]:
    """The pinned app folders (real paths, pin order) that still exist."""
    return [p for p in _stored_pins() if os.path.isdir(p)]


def set_app_pinned(dir: str, pinned: bool) -> list[str]:  # noqa: A002 — the wire name
    """Pin (append) or unpin an app folder; returns `pinned_apps()`. Pinning
    needs an existing app folder under the apps root; unpinning a folder that
    is already gone is allowed (it only tidies dock.json)."""
    real = _under_apps_root(dir)
    if pinned and not os.path.isfile(os.path.join(real, "index.html")):
        raise ValueError(f"not an app folder: {dir}")
    pins = [p for p in _stored_pins() if p != real]
    if pinned:
        pins.append(real)
    from fused_render_app.bots import store

    data = _read()
    data["pinned_apps"] = pins
    store.write_json_atomic(dock_path(), data)
    return pinned_apps()


# ---------------------------------------------------------------- entries ---
def _bot_rows() -> list[dict]:
    from fused_render_app.bots import registry, store

    live = {}
    for b in registry.loaded():
        bid = getattr(b, "id", None)
        if bid:
            live[bid] = b
    rows = []
    for bid in store.list_ids():
        b = live.get(bid)
        if b is not None:
            meta, status = dict(b.meta), b.meta.get("status") or "idle"
        else:
            try:
                meta = store.read_meta(bid)
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            status = meta.get("status") or "idle"
            if status in _STALE_STATUSES:
                status = "idle"
        if meta.get("hidden"):
            continue
        face = meta.get("face") if isinstance(meta.get("face"), dict) else {}
        rows.append({"kind": "bot", "id": bid, "name": str(meta.get("name") or "Bot"),
                     "face": face, "status": str(status),
                     "updated": float(meta.get("updated") or 0), "pinned": bool(meta.get("pinned"))})
    return rows


def _app_rows() -> list[dict]:
    from fused_render_app.bots import apptools

    root = bpaths.apps_root()
    if not os.path.isdir(root):
        return []
    rows = []
    for a in apptools.list_apps([root], skip_dir="/nonexistent"):
        d = a["dir"]
        try:
            mtime = os.path.getmtime(os.path.join(d, "index.html"))
        except OSError:
            continue
        icon = next((i for i in ("icon.svg", "icon.png") if os.path.isfile(os.path.join(d, i))), "")
        rows.append({"kind": "app", "dir": d, "name": str(a.get("name") or a.get("folder") or os.path.basename(d)),
                     "icon": icon, "mtime": mtime})
    return rows


def entries() -> dict:
    """{"pinned": [bot rows, then app rows], "recent_bots": [≤3], "recent_apps": [≤3]}.

    A bot row is {kind: "bot", id, name, face, status, updated, pinned}; an app
    row {kind: "app", dir, name, icon, mtime}, `dir` spelled as the apps
    listing spells it (what `/api/apps` and the page hold)."""
    bots = _bot_rows()
    apps = _app_rows()
    by_real = {os.path.realpath(a["dir"]): a for a in apps}
    pinned_bots = sorted((b for b in bots if b["pinned"]), key=lambda b: (b["name"].casefold(), b["id"]))
    pinned_app_rows = [by_real[p] for p in pinned_apps() if p in by_real]
    pinned_dirs = {a["dir"] for a in pinned_app_rows}
    recent_bots = sorted((b for b in bots if not b["pinned"]), key=lambda b: -b["updated"])[:RECENT_BOTS]
    recent_apps = sorted((a for a in apps if a["dir"] not in pinned_dirs), key=lambda a: -a["mtime"])[:RECENT_APPS]
    return {"pinned": pinned_bots + pinned_app_rows, "recent_bots": recent_bots, "recent_apps": recent_apps}


# ------------------------------------------------------------------- menu ---
def _cap(s: str) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= TITLE_MAX else s[:TITLE_MAX - 1].rstrip() + "…"


def bot_title(row: dict) -> str:
    """`<name>`, plus ` · <status>` when the bot is not idle."""
    st = row.get("status") or "idle"
    return _cap(row.get("name") or "Bot") + ("" if st == "idle" else f" · {st}")


def dock_menu_items(ents: dict) -> list[tuple]:
    """The dock part of the menu, top to bottom, as (title, kind, payload):

        ("Pinned", "header", None)          a disabled section header
        ("Scout · running", "bot", <id>)    shows the bot (`/?bot=<id>`)
        ("Map", "app", <dir>)               opens `/render?path=<dir>/index.html`
        (None, "separator", None)

    Sections are Pinned, Recent bots, Recent apps; an empty one is left out,
    and every section is followed by a separator (the fixed items come after
    the last). Titles are unique across the whole menu — rumps keys items by
    title, so a repeat (two bots with one name, a bot named "Tasks") gets
    " (2)", " (3)"…"""
    used = {SECTION_PINNED, SECTION_BOTS, SECTION_APPS, *FIXED_TITLES}

    def unique(title: str) -> str:
        if title not in used:
            used.add(title)
            return title
        n = 2
        while f"{title} ({n})" in used:
            n += 1
        used.add(f"{title} ({n})")
        return f"{title} ({n})"

    def row_item(row: dict) -> tuple:
        if row.get("kind") == "bot":
            return unique(bot_title(row)), "bot", row["id"]
        return unique(_cap(row.get("name") or os.path.basename(row.get("dir") or "") or "App")), "app", row["dir"]

    out: list[tuple] = []
    for header, key in ((SECTION_PINNED, "pinned"), (SECTION_BOTS, "recent_bots"), (SECTION_APPS, "recent_apps")):
        rows = [r for r in (ents.get(key) or []) if isinstance(r, dict) and r.get("kind") in ("bot", "app")]
        if not rows:
            continue
        out.append((header, "header", None))
        out.extend(row_item(r) for r in rows)
        out.append((None, "separator", None))
    return out


def app_render_path(app_dir: str) -> str:
    """The server path that shows an app folder in a window — spelled exactly
    like the page's own "Open in tab" (apps.ts appOpenUrl, encodeURIComponent),
    so a window it opened is found and raised rather than duplicated."""
    import urllib.parse

    return "/render?path=" + urllib.parse.quote(app_dir + "/index.html", safe="!'()*-._~")
