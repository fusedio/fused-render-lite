"""Folder apps in fused-render's workspace: ``~/Fused/local/<app>``.

fused-render, the editor, keeps every app it works on as a plain folder one
level under ``local/`` in its workspace (``FUSED_RENDER_DIR``, default
``~/Fused`` — the same override ``shared/appenv.py`` honours). Render App
lists those folders on its home page and in the launcher next to the
showcase, and opens them IN PLACE through the ordinary ``/open?_file=<dir>``
path (`appfile.open_app_dir`): no ``.fused`` export step between editing an
app and running it here.

What makes a folder an app is fused-render's own rule (`appfile.dir_entry`):
a non-hidden direct-child ``.html`` carrying ``<meta name="fused-app">``. A
folder without one is skipped, as are hidden folders. The card's title is the
entry page's ``<title>`` (else the folder name) and its description comes
from a ``metadata.json`` beside it when there is one (fused-render's
community-catalog sidecar: ``{"name", "description", ...}``).

Nothing is stored: the listing is computed from the directory on every
request, newest first by the later of the folder's and its entry page's
mtime (a folder's own mtime moves only when a direct child is added or
removed), so the app just edited in fused-render tends to be the first card.
"""
from __future__ import annotations

import json
import os

from fused_render_app import appfile

LOCAL_SUBDIR = "local"
_METADATA_NAME = "metadata.json"
_METADATA_CAP = 64 * 1024


def workspace_dir() -> str:
    """fused-render's workspace root (``FUSED_RENDER_DIR``, else ``~/Fused``)."""
    return os.path.abspath(os.path.expanduser(os.environ.get("FUSED_RENDER_DIR") or "~/Fused"))


def local_dir() -> str:
    """``<workspace>/local`` — never created here; absent means no local apps."""
    return os.path.join(workspace_dir(), LOCAL_SUBDIR)


def app_dir_for(path: str) -> str | None:
    """The ``local/<app>`` folder ``path`` sits under (or is), or None.
    Path-only: no marker check, so a nested ``.py`` of an app maps to the
    folder its ``pyproject.toml`` governs (`server.app_dir_for`)."""
    root = os.path.realpath(local_dir())
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        return None
    top = real[len(root) + 1:].split(os.sep, 1)[0]
    return os.path.join(root, top) if top else None


def _metadata(dir_path: str) -> dict:
    p = os.path.join(dir_path, _METADATA_NAME)
    try:
        if os.path.getsize(p) > _METADATA_CAP:
            return {}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_local() -> list[dict]:
    """``[{file, name, title, description, has_preview, preview_version,
    mtime}]``, one per app folder, newest (folder or entry mtime) first.
    ``file`` is the folder's absolute path — the same key the dock and the
    windows use."""
    root = local_dir()
    try:
        names = os.listdir(root)
    except OSError:
        return []
    rows = []
    for n in names:
        if n.startswith("."):
            continue
        d = os.path.join(root, n)
        if os.path.islink(d) or not os.path.isdir(d):
            continue
        entry = appfile.dir_entry(d)
        if entry is None:
            continue
        try:
            mtime = max(os.stat(d).st_mtime, os.stat(entry).st_mtime)
        except OSError:
            continue
        meta = _metadata(d)
        name = appfile.dir_name(d, entry)
        meta_name = meta.get("name")
        title = meta_name.strip() if isinstance(meta_name, str) and meta_name.strip() else name
        description = meta.get("description") if isinstance(meta.get("description"), str) else ""
        preview_version = None
        preview = os.path.join(d, appfile.PREVIEW_NAME)
        try:
            pst = os.stat(preview)
            if os.path.isfile(preview) and 0 < pst.st_size <= appfile.PREVIEW_MAX_BYTES:
                preview_version = pst.st_mtime_ns
        except OSError:
            pass
        rows.append({
            "file": os.path.abspath(d),
            "name": name,
            "title": title,
            "description": description,
            "has_preview": preview_version is not None,
            "preview_version": preview_version,
            "mtime": mtime,
        })
    rows.sort(key=lambda r: (-r["mtime"], r["file"]))
    return rows
