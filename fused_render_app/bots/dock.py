"""The menu-bar dock: the tiles of the FusedBot menu-bar tray (menubar_dock.py
hosts the page at `/dock`), and the apps' "Pin to menu bar" (apps/AppMenu.tsx).

    Pinned          pinned bots (bot.json `pinned`), then pinned apps (dock.json)
    Recent bots     the 3 most recently updated bots that are not pinned
    Recent apps     the 3 most recently changed apps that are not pinned

Bots are read straight from disk (`store.list_ids` + bot.json) — a Bot the
registry already built is read from memory instead, so its live status shows —
and never constructed by `entries()`: building a Bot writes its bot.json and
pulls in the Chrome/CDP code, which the tray's poll has no business doing. A
`hidden` bot is never listed, pinned or not. Apps are the same scan that feeds
every bot's APPS section (`apptools.list_apps` over `<workspace>/app`), sorted
by their index.html's mtime; a pinned app whose folder is gone (or no longer an
app) is simply not listed.

`<home>/bots/dock.json` holds `{"pinned_apps": [<real dir>], "tilesize": <px>}`:
the pinned app folders in pin order, and the tile size the tray's separator
drag last left. Pinned BOTS stay where the sidebar's pin already puts them
(bot.json, `set_bot_pinned` writes it the way the sidebar's flag route does),
so one pin means the same thing in both places.
"""
from __future__ import annotations

import json
import os
import subprocess

from fused_render_app.bots import paths as bpaths

DOCK_FILE = "dock.json"
RECENT_BOTS = 3
RECENT_APPS = 3
#: The tray's tile edge in px: the default, and the range a drag may set.
TILESIZE_DEFAULT = 52
TILESIZE_MIN = 16
TILESIZE_MAX = 128
#: Bot statuses a NEW Bot resets to idle (bot.py: a status left over from a
#: process that died). A bot this process never loaded is shown that way too.
_STALE_STATUSES = ("running", "waiting", "paused")
ICON_FILES = ("icon.svg", "icon.png")


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
    _write("pinned_apps", pins)
    return pinned_apps()


def set_app_order(dirs) -> list[str]:
    """Reorder the pinned apps after a drag in the tray: `dirs` is the full
    left-to-right order (any spelling of each folder). Dirs that are not
    pinned are ignored; a pinned dir the list leaves out keeps its place
    relative to the others, after the listed ones. Returns `pinned_apps()`.
    ValueError when `dirs` is not a list of strings."""
    if not isinstance(dirs, list) or not all(isinstance(d, str) for d in dirs):
        raise ValueError("dirs must be a list of app folders")
    pins = _stored_pins()
    pinned = set(pins)
    ordered: list[str] = []
    for d in dirs:
        real = os.path.realpath(d) if os.path.isabs(d) else ""
        if real in pinned and real not in ordered:
            ordered.append(real)
    ordered += [p for p in pins if p not in ordered]
    _write("pinned_apps", ordered)
    return pinned_apps()


def _write(key: str, value) -> None:
    """Set one key of dock.json, keeping the others."""
    from fused_render_app.bots import store

    data = _read()
    data[key] = value
    store.write_json_atomic(dock_path(), data)


def clamp_tilesize(value) -> int:
    """`value` as a tile size in range; ValueError when it is not a number."""
    if isinstance(value, bool):
        raise ValueError("tilesize must be a number")
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError("tilesize must be a number") from None
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError("tilesize must be a number")
    return int(min(max(round(n), TILESIZE_MIN), TILESIZE_MAX))


def tilesize() -> int:
    """The stored tile size (clamped), else the default."""
    try:
        return clamp_tilesize(_read().get("tilesize", TILESIZE_DEFAULT))
    except ValueError:
        return TILESIZE_DEFAULT


def set_tilesize(value) -> int:
    """Clamp, persist and return the tile size; ValueError for a non-number."""
    n = clamp_tilesize(value)
    _write("tilesize", n)
    return n


def set_bot_pinned(bid: str, pinned: bool) -> bool:
    """Pin or unpin a bot: `pinned` in its bot.json, through the registry's
    Bot under its lock — exactly what the sidebar's flag route does
    (routes._flag), so a loaded bot's live meta and the file agree. ValueError
    for an unknown bot. Returns the new value."""
    from fused_render_app.bots import registry

    b = registry.get(bid)
    with b.lock:
        b.meta["pinned"] = bool(pinned)
        b.save()
    return bool(pinned)


def bot_exists(bid: str) -> bool:
    """Whether `bid` names a bot on disk (no Bot is built). A plain id only:
    anything with a path separator or a dot-name is not one."""
    from fused_render_app.bots import store

    if not isinstance(bid, str) or not bid or bid != os.path.basename(bid) or bid in (".", ".."):
        return False
    return os.path.isfile(store.meta_path(bid))


def app_dir(dir: str) -> str:  # noqa: A002 — the wire name
    """`dir` as a real app folder under the apps root (has index.html), else
    ValueError."""
    real = _under_apps_root(dir)
    if not os.path.isfile(os.path.join(real, "index.html")):
        raise ValueError(f"not an app folder: {dir}")
    return real


def app_has_icon(dir: str) -> bool:  # noqa: A002 — the wire name
    """Whether the app folder carries its own icon (what `/api/apps/icon`
    serves): icon.svg or icon.png."""
    return any(os.path.isfile(os.path.join(dir, i)) for i in ICON_FILES)


def _open_reveal(path: str) -> None:
    subprocess.Popen(["open", "-R", path])  # noqa: S603,S607 — fixed argv


def reveal_app(dir: str) -> str:  # noqa: A002 — the wire name
    """Show an app folder in Finder (`open -R`); apps root only (ValueError
    otherwise). Returns the real path revealed."""
    real = app_dir(dir)
    _open_reveal(real)
    return real


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
                     "face": face, "status": str(status), "running": str(status) != "idle",
                     "updated": float(meta.get("updated") or 0), "pinned": bool(meta.get("pinned"))})
    return rows


def _app_rows() -> list[dict]:
    from fused_render_app.bots import apptools

    root = bpaths.apps_root()
    if not os.path.isdir(root):
        return []
    pins = set(pinned_apps())
    rows = []
    for a in apptools.list_apps([root], skip_dir="/nonexistent"):
        d = a["dir"]
        try:
            mtime = os.path.getmtime(os.path.join(d, "index.html"))
        except OSError:
            continue
        rows.append({"kind": "app", "dir": d, "name": str(a.get("name") or a.get("folder") or os.path.basename(d)),
                     "icon": app_has_icon(d), "pinned": os.path.realpath(d) in pins, "mtime": mtime})
    return rows


def entries() -> dict:
    """{"pinned": [bot rows, then app rows], "recent_bots": [≤3], "recent_apps": [≤3]}.

    A bot row is {kind: "bot", id, name, face, status, running, updated,
    pinned} (`running`: status is not idle); an app row {kind: "app", dir,
    name, icon, pinned, mtime} (`icon`: the folder has icon.svg / icon.png,
    served by `/api/apps/icon?dir=`), `dir` spelled as the apps listing spells
    it (what `/api/apps` and the page hold)."""
    bots = _bot_rows()
    apps = _app_rows()
    by_real = {os.path.realpath(a["dir"]): a for a in apps}
    pinned_bots = sorted((b for b in bots if b["pinned"]), key=lambda b: (b["name"].casefold(), b["id"]))
    pinned_app_rows = [by_real[p] for p in pinned_apps() if p in by_real]
    pinned_dirs = {a["dir"] for a in pinned_app_rows}
    recent_bots = sorted((b for b in bots if not b["pinned"]), key=lambda b: -b["updated"])[:RECENT_BOTS]
    recent_apps = sorted((a for a in apps if a["dir"] not in pinned_dirs), key=lambda a: -a["mtime"])[:RECENT_APPS]
    return {"pinned": pinned_bots + pinned_app_rows, "recent_bots": recent_bots, "recent_apps": recent_apps}


# ------------------------------------------------------------------ views ---
def bot_view_path(bid: str) -> str:
    """The server path that selects bot `bid` on the bots page."""
    import urllib.parse

    return "/?bot=" + urllib.parse.quote(bid, safe="")


def app_render_path(app_dir: str) -> str:
    """The server path that shows an app folder in a window — spelled exactly
    like the page's own "Open in tab" (apps.ts appOpenUrl, encodeURIComponent),
    so a window it opened is found and raised rather than duplicated."""
    import urllib.parse

    return "/render?path=" + urllib.parse.quote(app_dir + "/index.html", safe="!'()*-._~")
