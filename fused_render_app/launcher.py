"""The launcher's data: what it can open, how a query ranks it, its settings.

The launcher (``static/launcher.html`` in ``launcher_panel.py``) is a
Spotlight-like panel on a global shortcut: an empty query lists the apps
pinned in the menu-bar Dock, and typing searches every app Render App
knows — the Dock's remembered apps (``dock_store``: pinned plus the recent
ones it keeps) and the shipped showcase (``showcase``). There is no other
registry: a .fused the user has never opened and that is not shipped is not
findable, which is the same set the home page shows.

Settings live in ``<home>/launcher.json``::

    {"hotkey": "alt+space", "rowModifier": "alt"}

``hotkey`` opens the launcher (``hotkey.py`` spec syntax). ``rowModifier``
is the modifier (or ``+``-joined modifiers) that, with a digit 1–9, opens
the Nth app: from anywhere, the Nth PINNED Dock app (nine global
shortcuts); while the launcher is up, the Nth row — the same list when the
query is empty. One knob from the user's point of view. Missing or corrupt
→ the defaults.
Kept apart from ``dock.json`` because ``dock_store._save`` rewrites that
document whole.

``search`` is pure and ranks by match quality then by the Dock's own order
(pinned in user order, then recent): a name that starts with the query,
then a word inside the name that does, then the query as a substring, then
its letters in order (``"os"`` finds ``OpenSVG``). Titles from the showcase
sidecar are searched too. Case-insensitive throughout.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import urllib.parse

from fused_render_app import dock_store, hotkey, paths, showcase

logger = logging.getLogger(__name__)

MAX_RESULTS = 9  # one ⌥-digit each

_lock = threading.Lock()


# ---- settings --------------------------------------------------------------------

def _path() -> str:
    return os.path.join(paths.home(), "launcher.json")


def _load_doc() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_doc(doc: dict) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".launcher-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_hotkey() -> str:
    """The stored shortcut spec, canonical; the default when absent or invalid."""
    with _lock:
        raw = _load_doc().get("hotkey")
    try:
        return hotkey.canonical(raw) if isinstance(raw, str) else hotkey.DEFAULT_SPEC
    except hotkey.SpecError:
        return hotkey.DEFAULT_SPEC


def set_hotkey(spec) -> str:
    """Store ``spec`` (canonicalised); returns what was stored. Raises
    ``hotkey.SpecError`` for an invalid spec — nothing is written then."""
    canon = hotkey.canonical(str(spec or ""))
    with _lock:
        doc = _load_doc()
        doc["hotkey"] = canon
        _save_doc(doc)
    return canon


DEFAULT_ROW_MODIFIER = "alt"


def _canonical_modifiers(spec) -> str:
    """``"cmd+alt"`` → ``"alt+cmd"``; SpecError when empty or not all modifiers."""
    parts = [p.strip().lower() for p in str(spec or "").split("+") if p.strip()]
    if not parts:
        raise hotkey.SpecError("pick at least one modifier")
    names = set()
    for m in parts:
        m = hotkey.MODIFIER_ALIASES.get(m, m)
        if m not in hotkey.MODIFIERS:
            raise hotkey.SpecError(f"unknown modifier {m!r}")
        names.add(m)
    return "+".join(m for m in hotkey.MODIFIER_ORDER if m in names)


def get_row_modifier() -> str:
    with _lock:
        raw = _load_doc().get("rowModifier")
    try:
        return _canonical_modifiers(raw) if isinstance(raw, str) else DEFAULT_ROW_MODIFIER
    except hotkey.SpecError:
        return DEFAULT_ROW_MODIFIER


def set_row_modifier(spec) -> str:
    canon = _canonical_modifiers(spec)
    with _lock:
        doc = _load_doc()
        doc["rowModifier"] = canon
        _save_doc(doc)
    return canon


def pinned_specs(modifier: str) -> list[str]:
    """The nine specs ``<modifier>+1`` … ``+9``; empty when off."""
    return [f"{modifier}+{n}" for n in range(1, 10)] if modifier else []


def nth_pinned(n: int, running=frozenset()) -> str | None:
    """The .fused of the ``n``-th pinned Dock app (1-based, Dock order), or None."""
    pinned = [a for a in dock_store.list_apps(running) if a["pinned"]]
    return pinned[n - 1]["file"] if 1 <= n <= len(pinned) else None


def modifier_display(spec: str) -> str:
    """``"alt+cmd"`` → ``"⌥⌘"``."""
    names = set(str(spec or "").split("+"))
    return "".join(hotkey.MODIFIER_SYMBOLS[m] for m in hotkey.MODIFIER_ORDER if m in names)


def settings() -> dict:
    """What the pages read: both shortcuts, with display forms."""
    spec, row = get_hotkey(), get_row_modifier()
    return {"hotkey": spec, "display": hotkey.display(spec),
            "rowModifier": row, "rowModifierDisplay": modifier_display(row)}


# ---- registry ------------------------------------------------------------------------

def registry(running=frozenset()) -> list[dict]:
    """Every app the launcher can open, Dock order first (pinned in user
    order, then recent), then the showcase apps not already there.

    Rows: ``{file, name, title, description, pinned, running, showcase,
    hasIcon, iconVersion}``; ``title`` is the showcase sidecar's when the
    file is a shipped app, else the name.
    """
    listing = showcase.list_showcase()
    by_file = {os.path.abspath(r["file"]): r for r in listing}
    rows = []
    seen = set()
    for a in dock_store.list_apps(running):
        ex = by_file.get(a["file"])
        seen.add(a["file"])
        rows.append({
            "file": a["file"],
            "name": a["name"],
            "title": ex["title"] if ex else a["name"],
            "description": ex["description"] if ex else "",
            "pinned": bool(a["pinned"]),
            "running": bool(a["running"]),
            "showcase": ex is not None,
            "hasIcon": a["hasIcon"],
            "iconVersion": a["iconVersion"],
        })
    running_abs = {os.path.abspath(f) for f in running}
    for r in listing:
        file = os.path.abspath(r["file"])
        if file in seen:
            continue
        has_icon, icon_version, _p, _pv = dock_store._card_info(file)
        rows.append({
            "file": file,
            "name": r["name"],
            "title": r["title"],
            "description": r["description"],
            "pinned": False,
            "running": file in running_abs,
            "showcase": True,
            "hasIcon": has_icon,
            "iconVersion": icon_version,
        })
    return rows


# ---- search ----------------------------------------------------------------------------

def _subsequence(q: str, s: str) -> bool:
    it = iter(s)
    return all(ch in it for ch in q)


def _score(q: str, row: dict) -> int | None:
    """Lower is better; None when the row does not match."""
    best = None
    for field, penalty in ((row.get("name") or "", 0), (row.get("title") or "", 1)):
        s = field.lower()
        if not s:
            continue
        if s.startswith(q):
            score = 0
        elif any(w.startswith(q) for w in s.replace("-", " ").replace("_", " ").split()):
            score = 10
        elif q in s:
            score = 20
        elif _subsequence(q, s):
            score = 30
        else:
            continue
        score += penalty
        if best is None or score < best:
            best = score
    return best


def search(query: str, rows: list[dict], limit: int = MAX_RESULTS) -> list[dict]:
    """An empty query → the pinned rows, in Dock order. Otherwise every row
    that matches, best match first, ties in registry order."""
    q = str(query or "").strip().lower()
    if not q:
        return [r for r in rows if r.get("pinned")][:limit]
    scored = []
    for i, r in enumerate(rows):
        s = _score(q, r)
        if s is not None:
            scored.append((s, i, r))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [r for _s, _i, r in scored[:limit]]


def results(query: str, running=frozenset()) -> list[dict]:
    """``search`` over the live registry, each row with its ``icon`` URL
    (None when the app has none — the page paints a monogram then)."""
    out = []
    for r in search(query, registry(running)):
        icon = None
        if r["hasIcon"]:
            icon = ("/api/dock/icon?file=" + urllib.parse.quote(r["file"], safe="/")
                    + "&v=" + urllib.parse.quote(str(r["iconVersion"] or "")))
        out.append({**r, "icon": icon})
    return out
