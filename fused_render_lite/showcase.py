"""Showcase apps shipped inside the package: ``fused_render_lite/showcase/*.fused``.

The placeholder page (``static/index.html``) lists them so a fresh install has
something to open before the user has a ``.fused`` of their own. They ride
along with the package the same way ``static/`` does — hatchling ships every
file under the package dir in the wheel, and py2app copies the whole package
into the .app — so nothing in the build has to know about them.

Adding one: drop the ``.fused`` here and give it a row in ``showcase.json``
(``title`` + ``description``, keyed by file name). Files without a row still
list, titled by the manifest ``name``. ``tests/test_showcase.py`` checks that
every shipped file opens and calls nothing lite does not support.

Opening is the normal path: the card links to ``/open?_file=<abs path>``, and
``appfile.open_app_file`` extracts into ``~/.fused-render-lite/apps`` keyed by
the file's bytes — the packaged file is only ever read, so a read-only .app
bundle is fine, and an upgraded showcase app lands in a fresh dir.
"""
from __future__ import annotations

import json
import os

from fused_render_lite import appfile, container

SHOWCASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "showcase")
SIDECAR = os.path.join(SHOWCASE_DIR, "showcase.json")
PREVIEW_MEMBER = "preview.png"
MAX_PREVIEW_BYTES = 8 * 1024 * 1024


def _sidecar() -> dict:
    try:
        with open(SIDECAR, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def showcase_files() -> list[str]:
    """Absolute paths of the shipped ``.fused`` files, sorted by name."""
    if not os.path.isdir(SHOWCASE_DIR):
        return []
    return sorted(
        os.path.join(SHOWCASE_DIR, n)
        for n in os.listdir(SHOWCASE_DIR)
        if n.lower().endswith(".fused") and os.path.isfile(os.path.join(SHOWCASE_DIR, n))
    )


def list_showcase() -> list[dict]:
    """``[{id, file, name, title, description, has_preview, size}]``, one per
    readable ``.fused``. A file whose manifest does not parse is skipped rather
    than breaking the page — it will fail the test suite instead."""
    meta = _sidecar()
    rows = []
    for path in showcase_files():
        try:
            index = appfile.read_manifest(path)
        except appfile.AppFileError:
            continue
        base = os.path.basename(path)
        info = meta.get(base) if isinstance(meta.get(base), dict) else {}
        name = index.get("name") if isinstance(index.get("name"), str) else os.path.splitext(base)[0]
        rows.append({
            "id": base,
            "file": path,
            "name": name,
            "title": str(info.get("title") or name),
            "description": str(info.get("description") or ""),
            "has_preview": container.find(index, PREVIEW_MEMBER) is not None
            if index.get("fused_app_file") == container.VERSION else False,
            "size": os.path.getsize(path),
        })
    return rows


def resolve(app_id: str) -> str | None:
    """The shipped file for ``app_id`` (its base name), or None. Only
    names that are actually in the listing resolve — no path joins from input."""
    for path in showcase_files():
        if os.path.basename(path) == app_id:
            return path
    return None


def preview_bytes(app_id: str) -> bytes | None:
    """The app's ``preview.png`` member, or None when absent."""
    path = resolve(app_id)
    if path is None:
        return None
    try:
        index = appfile.read_manifest(path)
    except appfile.AppFileError:
        return None
    if index.get("fused_app_file") != container.VERSION:
        return None
    try:
        return container.read_member(path, index, PREVIEW_MEMBER, MAX_PREVIEW_BYTES)
    except container.ContainerError:
        return None
