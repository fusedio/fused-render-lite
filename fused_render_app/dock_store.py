"""The menu-bar dock's app list: pinned apps plus recently opened ones.

Persisted as ``<home>/dock.json``::

    {"apps": [{"file": "/abs/x.fused", "name": "X",
               "openedAt": "2026-09-15T09:00:00.000000Z", "pinned": false}],
     "tilesize": 52}

``tilesize`` is the tray's icon size in CSS px (drag the separator, like the
Dock's; ``defaults write com.apple.dock tilesize`` is the same knob), clamped
to the Dock's own range and defaulting to ``DEFAULT_TILESIZE`` when absent
or unreadable.

The stored order of PINNED entries is the user's order (drag to reorder);
unpinned entries are ordered by ``openedAt`` on read, and only the most
recent ``MAX_RECENT`` survive. ``file`` is ``os.path.abspath`` and is the
identity of an entry — the same .fused opened through two spellings of its
path must be one card, not two.

A corrupt or missing file is an empty list, never an error: the dock is a
convenience over files the user still has on disk, and losing the list is
strictly better than a menu-bar shell that will not open.

An entry whose .fused is gone from disk is dropped on the next read
(``list_apps``), pinned or not: there is no "missing" state. Deleting (or
moving, or unmounting) a file removes it from the dock and from the home
page's Recent row at once; opening it again from its new place adds it back.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone

from fused_render_app import appfile, paths

logger = logging.getLogger(__name__)

MAX_RECENT = 10
DEFAULT_TILESIZE = 52
MIN_TILESIZE, MAX_TILESIZE = 16, 128  # the Dock's Size slider range

_lock = threading.Lock()


def _now() -> str:
    # Microseconds: two record_open calls inside one second must still
    # order, or "evict the oldest" evicts an arbitrary one.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _path() -> str:
    return os.path.join(paths.home(), "dock.json")


def _load_doc() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> list[dict]:
    apps = _load_doc().get("apps")
    if not isinstance(apps, list):
        return []
    return [a for a in apps if isinstance(a, dict) and isinstance(a.get("file"), str)]


def _clamp_tilesize(value) -> int | None:
    """``value`` as a tile size inside the Dock's range, or None if it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return int(min(max(round(value), MIN_TILESIZE), MAX_TILESIZE))


def _save(apps: list[dict], tilesize: int | None = None) -> None:
    """Write ``apps`` (and ``tilesize`` when given; otherwise the stored one is kept)."""
    if tilesize is None:
        tilesize = _clamp_tilesize(_load_doc().get("tilesize"))
    doc: dict = {"apps": apps}
    if tilesize is not None:
        doc["tilesize"] = tilesize
    path = _path()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".dock-")
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


def _find(apps: list[dict], file: str) -> dict | None:
    for a in apps:
        if a["file"] == file:
            return a
    return None


def _evict(apps: list[dict]) -> list[dict]:
    """Drop unpinned entries beyond MAX_RECENT, oldest ``openedAt`` first."""
    recent = sorted((a for a in apps if not a.get("pinned")),
                    key=lambda a: a.get("openedAt") or "", reverse=True)
    keep = {id(a) for a in recent[:MAX_RECENT]}
    return [a for a in apps if a.get("pinned") or id(a) in keep]


def record_open(file: str, name: str) -> None:
    file = os.path.abspath(file)
    with _lock:
        apps = _load()
        entry = _find(apps, file)
        if entry is None:
            entry = {"file": file, "pinned": False}
            apps.append(entry)
        entry["name"] = name or entry.get("name") or _stem(file)
        entry["openedAt"] = _now()
        _save(_evict(apps))


def set_pinned(file: str, pinned: bool) -> None:
    file = os.path.abspath(file)
    with _lock:
        apps = _load()
        entry = _find(apps, file)
        if entry is None:
            if not pinned:
                return
            entry = {"file": file, "name": _stem(file), "openedAt": None}
        else:
            apps.remove(entry)
        entry["pinned"] = bool(pinned)
        if pinned:
            # end of the pinned group: stored order of pinned = user order
            last = max((i for i, a in enumerate(apps) if a.get("pinned")), default=-1)
            apps.insert(last + 1, entry)
        else:
            apps.append(entry)
        _save(_evict(apps))


def remove(file: str) -> None:
    file = os.path.abspath(file)
    with _lock:
        apps = [a for a in _load() if a["file"] != file]
        _save(apps)


def reorder(files: list[str]) -> None:
    """New order for the pinned entries. Unknown/unpinned files are ignored;
    pinned entries missing from ``files`` keep their relative order after
    the listed ones. Unpinned entries stay where they were."""
    wanted = [os.path.abspath(f) for f in files if isinstance(f, str)]
    with _lock:
        apps = _load()
        pinned = [a for a in apps if a.get("pinned")]
        by_file = {a["file"]: a for a in pinned}
        ordered: list[dict] = []
        for f in wanted:
            a = by_file.pop(f, None)
            if a is not None:
                ordered.append(a)
        ordered.extend(a for a in pinned if a["file"] in by_file)
        result: list[dict] = []
        it = iter(ordered)
        for a in apps:
            result.append(next(it) if a.get("pinned") else a)
        _save(result)


_icon_cache: dict[tuple, tuple[bool, list[str] | None, bool, str | None]] = {}


def _card_info(file: str) -> tuple[bool, int | None, bool, int | None]:
    """``(hasIcon, iconVersion, hasPreview, previewVersion)`` for a dock card.

    Everything that needs the .fused opened — whether it PACKS an icon / a
    preview, and where its extract would hold an override — is memoised on
    (file, size, mtime): the dock polls this list every ~1.5 s and parsing a
    container index per card per poll adds up. Per poll only a few stats
    happen: the .fused (cache key) and the override paths
    (``appfile.ICON_NAMES``, svg before png; ``appfile.PREVIEW_NAME``), so
    an app that writes an icon or a preview after the first poll still shows
    up, and the version changes with it so tiles retarget their ``<img>``.
    The walk mirrors ``appfile.icon_bytes`` / ``appfile.preview_bytes`` so
    ``hasIcon`` / ``hasPreview`` and the routes never disagree.
    """
    try:
        st = os.stat(file)
    except OSError:
        return False, None, False, None
    key = (file, st.st_size, st.st_mtime_ns)
    hit = _icon_cache.get(key)
    if hit is None:
        hit = (appfile.has_shipped_icon(file), appfile.icon_override_paths(file),
               appfile.has_shipped_preview(file), appfile.preview_override_path(file))
        if len(_icon_cache) > 256:
            _icon_cache.clear()
        _icon_cache[key] = hit
    shipped, overrides, shipped_preview, preview_override = hit
    has_icon, icon_version = shipped, (st.st_mtime_ns if shipped else None)
    for override in overrides or ():
        try:
            ost = os.stat(override)
        except OSError:
            continue
        if ost.st_size <= appfile.icon_cap(override):
            has_icon, icon_version = True, ost.st_mtime_ns
            break
    has_preview, preview_version = shipped_preview, (st.st_mtime_ns if shipped_preview else None)
    if preview_override:
        try:
            pst = os.stat(preview_override)
            if pst.st_size <= appfile.PREVIEW_MAX_BYTES:
                has_preview, preview_version = True, pst.st_mtime_ns
        except OSError:
            pass
    return has_icon, icon_version, has_preview, preview_version


def _pruned() -> list[dict]:
    """The stored entries minus those whose file is no longer on disk. When
    anything was dropped the store is rewritten so the file is gone for good;
    a failed write is logged, not raised — the filtered list is still the
    answer, and the next read prunes again.

    The stats happen OUTSIDE the lock: a .fused on a hung network mount must
    stall this poll, not every ``record_open`` queued behind it. The store is
    re-read before the rewrite so an entry added meanwhile is not lost."""
    with _lock:
        apps = _load()
    gone = {a["file"] for a in apps if not os.path.isfile(a["file"])}
    if not gone:
        return apps
    with _lock:
        kept = [a for a in _load() if a["file"] not in gone]
        try:
            _save(kept)
        except OSError:
            logger.warning("could not rewrite dock.json after pruning", exc_info=True)
    return kept


def list_apps(running: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """Pinned first in stored order, then unpinned by openedAt desc. Entries
    whose .fused no longer exists are dropped (and forgotten) on the way."""
    apps = _pruned()
    pinned = [a for a in apps if a.get("pinned")]
    recent = sorted((a for a in apps if not a.get("pinned")),
                    key=lambda a: a.get("openedAt") or "", reverse=True)
    running = {os.path.abspath(f) for f in running}
    out = []
    for a in pinned + recent:  # container probes happen outside the lock
        file = a["file"]
        has_icon, icon_version, has_preview, preview_version = _card_info(file)
        out.append({
            "file": file,
            "name": a.get("name") or _stem(file),
            "pinned": bool(a.get("pinned")),
            "running": file in running,
            "openedAt": a.get("openedAt"),
            "hasIcon": has_icon,
            "iconVersion": icon_version,
            "hasPreview": has_preview,
            "previewVersion": preview_version,
        })
    return out


def get_tilesize() -> int:
    """The stored tile size, or ``DEFAULT_TILESIZE``."""
    with _lock:
        size = _clamp_tilesize(_load_doc().get("tilesize"))
    return DEFAULT_TILESIZE if size is None else size


def set_tilesize(value) -> int:
    """Store ``value`` clamped to the Dock's range; a non-number resets to
    the default. Returns what was stored."""
    with _lock:
        size = _clamp_tilesize(value)
        if size is None:
            size = DEFAULT_TILESIZE
        _save(_load(), size)
    return size


def _stem(file: str) -> str:
    return os.path.splitext(os.path.basename(file))[0] or "app"
