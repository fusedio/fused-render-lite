"""The title bar's Edit button: hand the window's ``.fused`` to fused-render.

Render App only *runs* a ``.fused``. Editing it means fused-render, the full
editor, so the button builds a ``fused-render://open?file=<path>`` deep link
and lets LaunchServices deliver it. fused-render clones the file into its
workspace (``~/Fused/local/<slug>``; when a copy is already there it asks
whether to overwrite it or open it as is) and lands on the editable copy —
see fused-render's ``deeplink.py``.

Contract with fused-render: the path is percent-encoded ONCE here
(``quote(path, safe="")``) and unquoted ONCE there. ``&``, ``#``, spaces and
``%`` in a filename all survive that round trip; nothing else is escaped.

When no app claims the ``fused-render`` scheme the button offers the latest
DMG instead. The download page's manifest (``render.fused.io/latest.json``)
names it under ``dmg_url``; if that fetch fails the page itself is the
fallback, which has the same button.

Pure functions, no AppKit: ``mainwindow.py`` does the NSWorkspace / NSAlert
half around these.
"""
from __future__ import annotations

import json
import logging
import urllib.parse

from fused_render_app.update import common

logger = logging.getLogger(__name__)

SCHEME = "fused-render"
#: Probe URL: any app registered for the scheme answers for it.
PROBE_URL = f"{SCHEME}://open"
DOWNLOAD_PAGE = "https://render.fused.io"
MANIFEST_URL = f"{DOWNLOAD_PAGE}/latest.json"
FETCH_TIMEOUT_S = 4.0


def edit_url(app_file: str) -> str:
    """The deep link that opens ``app_file`` for editing in fused-render."""
    return f"{SCHEME}://open?file=" + urllib.parse.quote(app_file, safe="")


def download_url_from(manifest: object) -> str:
    """The DMG link the download page's manifest names, else the page."""
    if isinstance(manifest, dict):
        url = manifest.get("dmg_url")
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return DOWNLOAD_PAGE


def download_url(*, urlopen_fn=None) -> str:
    """Fetch the latest fused-render DMG URL; never raises."""
    if urlopen_fn is None:
        urlopen_fn = common.urlopen
    try:
        with urlopen_fn(MANIFEST_URL, FETCH_TIMEOUT_S) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — any failure means "use the page"
        logger.warning("fused-render download manifest unavailable", exc_info=True)
        return DOWNLOAD_PAGE
    return download_url_from(manifest)
