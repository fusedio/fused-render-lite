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
