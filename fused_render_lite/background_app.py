"""A background daemon's client for the background-apps API, about ITSELF.
Copied from fused-render's `templates/shared/background_app.py`.

Every endpoint under `/api/apps/background/*` keys off `html` — the page's
own path — and resolves the app folder from it server-side. A background
daemon spawned by `engine_host.ensure_background` has no page and no `html`
path of its own, so this module gives it a way to ask the server about
itself: check whether it is still enabled, tell the server to stop it, or
turn its autostart off.

The missing piece is `FUSED_RENDER_APP_DIR` — exported into a background
child's environment only (`engine_host._spawn_env`, keyed on `Child.folder`)
— the app folder the daemon's own manifest declared. This module reads that
var, synthesizes a stand-in `html` path inside the folder (the server takes
its enclosing app dir, so the leaf name is arbitrary), and speaks the same
endpoints a page's `fused.daemon.*` calls speak.

Run state and autostart are independent (D511): `stop()` only kills this
process, `set_autostart(bool)` only flips the persisted flag.

**Stdlib only, no `import fused_render_lite`**: a background daemon runs in
its app's own venv with PYTHONPATH stripped, so the package is never
importable there. A `daemon =` author who wants this vendors the file next to
their daemon (it has no siblings to load).

Origin resolution: `FUSED_RENDER_ORIGIN` (set by `server.make_server`, so
every process this server spawned inherits it), else a connect-probed
`server.json` under the lite home dir, else `ServerNotRunning`.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

_PROBE_TIMEOUT_S = 0.35
_DEFAULT_TIMEOUT_S = 10.0

ORIGIN_ENV = "FUSED_RENDER_ORIGIN"
HOME_ENV = "FUSED_RENDER_LITE_HOME"
SERVER_JSON_NAME = "server.json"

# The env var `engine_host._spawn_env` exports for a background child only.
APP_DIR_ENV = "FUSED_RENDER_APP_DIR"

_STANDIN_HTML_NAME = "index.html"


class NotUnderEngine(Exception):
    """`FUSED_RENDER_APP_DIR` is unset — this process is not a background
    daemon `engine_host.ensure_background` spawned."""


class ServerNotRunning(Exception):
    """No reachable Render Lite server for this process."""


class BackgroundAppError(Exception):
    """One failed call to `/api/apps/background/*` — the `{"error": message}`
    body the server sends, off any non-2xx response."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


# ------------------------------------------------------------- origin lookup


def _home_dir() -> str:
    return os.environ.get(HOME_ENV) or os.path.join(os.path.expanduser("~"), ".fused-render-lite")


def _server_json_path() -> str:
    return os.path.join(_home_dir(), SERVER_JSON_NAME)


def _probe(origin: str, timeout: float = _PROBE_TIMEOUT_S) -> bool:
    parsed = urlparse(origin)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_origin() -> str:
    origin = os.environ.get(ORIGIN_ENV)
    if origin:
        return origin
    path = _server_json_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ServerNotRunning(
            f"no Render Lite server is running: {ORIGIN_ENV} is unset and {path} could not be read"
        ) from None
    origin = data.get("origin") if isinstance(data, dict) else None
    if not isinstance(origin, str) or not origin or not _probe(origin):
        raise ServerNotRunning(
            f"{path} names {origin!r}, but nothing answered there — the server that wrote it "
            "is not running any more"
        )
    return origin


def _self_html_path() -> str:
    app_dir = os.environ.get(APP_DIR_ENV)
    if not app_dir:
        raise NotUnderEngine(
            f"{APP_DIR_ENV} is not set: this process is not running as a background app "
            "daemon spawned by Render Lite (engine_host.ensure_background), so it has no app "
            "folder to act on."
        )
    return os.path.join(app_dir, _STANDIN_HTML_NAME)


# ------------------------------------------------------------------ transport


def _request(method: str, path: str, body: dict | None = None,
             timeout: float = _DEFAULT_TIMEOUT_S):
    origin = resolve_origin()
    url = origin.rstrip("/") + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if method == "POST":
        headers["X-Fused"] = "1"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        message = e.reason
        try:
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                message = payload["error"]
        except (ValueError, UnicodeDecodeError):
            pass
        raise BackgroundAppError(message, status=e.code) from None
    except urllib.error.URLError as e:
        raise BackgroundAppError(str(e.reason)) from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


# ----------------------------------------------------------------------- API


def status() -> dict:
    """`GET /api/apps/background/status` for THIS daemon's own app —
    `{"running", "autostart", "pid", "version", "engine_id", "protocol"}`."""
    return _request("GET", "/api/apps/background/status?html=" + quote(_self_html_path()))


def stop() -> dict:
    """`POST /api/apps/background/stop`: kills this process WITHOUT touching
    autostart. Expect to be killed shortly after this returns."""
    return _request("POST", "/api/apps/background/stop", body={"html": _self_html_path()})


def set_autostart(autostart: bool) -> dict:
    """`POST /api/apps/background/autostart`: persist whether this app comes
    back at the next server start. Starts/stops nothing."""
    return _request("POST", "/api/apps/background/autostart",
                    body={"html": _self_html_path(), "autostart": bool(autostart)})


def restart() -> dict:
    """`POST /api/apps/background/restart` — respawns the daemon."""
    return _request("POST", "/api/apps/background/restart", body={"html": _self_html_path()})
