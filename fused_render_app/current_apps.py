"""Stand-in for fused-render's `current_apps` (the sidebar's "Current apps"
registry over workspace folders). Render App's notion of an app folder is the
extract dir a `.fused` was unpacked into (`paths.apps_dir()/<slug>-<hash>`)
or a folder app under `~/Fused/local` (localapps.py), so `app_dir_for`
answers those and nothing is ever observed or listed."""
from __future__ import annotations

import os

from fused_render_app import localapps, paths


def app_dir_for(project: str) -> str | None:
    """The app root `project` lives under (or is), else None."""
    if not project:
        return None
    root = os.path.realpath(paths.apps_dir())
    real = os.path.realpath(os.path.expanduser(project))
    if real != root and real.startswith(root + os.sep):
        rel = real[len(root) + 1:]
        top = rel.split(os.sep, 1)[0]
        return os.path.join(root, top)
    return localapps.app_dir_for(real)


def observe(rows: list[dict]) -> None:  # noqa: ARG001 - signature parity
    """fused-render records task activity per current app here; Render App has
    no current-apps section, so there is nothing to record."""
    return None


def list_apps() -> list[dict]:
    """The desk (`GET /api/current-apps`), the shape fused-render's
    `current_apps.list_apps` answers: every app folder Render App knows —
    extracted `.fused` apps under `paths.apps_dir()`, folder apps under
    `~/Fused/local`, and the folder of every task whose folder declares an
    entry page — each with its `entry` (what the Tasks peek previews). No
    added/opened bookkeeping: Render App keeps no desk of its own."""
    from fused_render_app import app_listing
    from fused_render_app._view_url_codec import canonical_fs_path

    folders: dict[str, None] = {}
    try:
        root = paths.apps_dir()
        for name in sorted(os.listdir(root)):
            folders.setdefault(os.path.join(root, name), None)
    except OSError:
        pass
    try:
        for row in localapps.list_local():
            path = row.get("dir") or row.get("path") or ""
            if path:
                folders.setdefault(path, None)
    except Exception:  # noqa: BLE001 — a listing that cannot be read adds nothing
        pass
    try:
        from fused_render_app.routes import tasks as tasks_routes

        for row in tasks_routes._task_rows():
            project = str(row.get("project") or "")
            if project:
                folders.setdefault(project, None)
    except Exception:  # noqa: BLE001 — same
        pass
    out = []
    for path in folders:
        try:
            exists = os.path.isdir(path)
            entry = app_listing.app_entry(path) if exists else None
        except OSError:
            exists, entry = False, None
        if not entry:
            continue
        canon = canonical_fs_path(path).rstrip("/")
        out.append({
            "path": canon,
            "name": os.path.basename(canon) or canon,
            "kind": "workspace",
            "entry": canonical_fs_path(entry),
            "exists": exists,
            "icon": None,
            "icon_mtime": None,
            "added_at": None,
            "opened_at": 0,
            "unread": False,
        })
    return out
