"""The fused.daemon HTTP surface: start/stop/restart/autostart/status for an
app's declared background daemon, plus the `/api/engines/*` proxy every
`fused.daemon.run/call` rides. Ported from fused-render's
`server/routers/background_apps.py` and `routers/engines.py` onto lite's
stdlib `Handler` (each function takes the request handler and answers
through its `_json`/`_error`/`_send`).

Every endpoint takes `html` — the page's own path — never a raw folder path,
and resolves the app folder from it server-side exactly as `/api/run`
resolves `py`: no new path-typed API to defend. The interpreter is the app's
own venv (`background_apps.interpreter_for`), falling back to
`sys.executable` when that venv is not built yet so a daemon always starts
rather than blocking a POST on a venv build (D631); when that fallback then
fails, `unbuilt_deps_reason`'s actionable message is reported instead of the
generic spawn failure.

Run state and autostart are independent (D511): `start`/`stop`/`restart`
change whether the daemon is alive RIGHT NOW and never touch the persisted
autostart flag; `autostart` changes only that flag and never starts or stops
anything. Autostart is opt-in.
"""
from __future__ import annotations

import os
import re
import sys

from fused_render_lite import background_apps, engine_forward, engine_host

PROXY_RE = re.compile(r"^/api/engines/([a-z0-9_]+)/proxy/(.+)$")
STOP_RE = re.compile(r"^/api/engines/([a-z0-9_]+)/stop$")


def _folder_for(html) -> str | None:
    """The app folder `html` (the caller's own page path) belongs to: the
    extracted .fused root when the page lives under `paths.apps_dir()` (the
    manifest's pyproject.toml sits there, even when the entry html is in a
    subfolder), else — as fused-render does — the page's own directory.
    realpath'd (D509) so it agrees with `background_apps.engine_id_for`."""
    from fused_render_lite.server import app_dir_for

    if not isinstance(html, str) or not html:
        return None
    folder = app_dir_for(html) or os.path.dirname(os.path.abspath(html))
    return os.path.realpath(folder)


def _resolve(html):
    """(folder, manifest, interpreter, unbuilt_reason) or raises _Reject."""
    folder = _folder_for(html)
    if folder is None:
        raise _Reject("request body must include 'html'", 400)
    manifest = background_apps.load_manifest(folder)
    if manifest is None:
        raise _Reject(f"{os.path.basename(folder)} has no [tool.fused-render.app] "
                      "background manifest", 404)
    interpreter = background_apps.interpreter_for(folder)
    unbuilt_reason = background_apps.unbuilt_deps_reason(folder, interpreter)
    if unbuilt_reason is not None:
        interpreter = sys.executable
    return folder, manifest, interpreter, unbuilt_reason


class _Reject(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def _protocol_for(manifest) -> str | None:
    return None if manifest is None else ("main" if manifest.main else "daemon")


# ---- /api/apps/background/* ---------------------------------------------------


def status(h, q: dict) -> None:
    # Read-only GET — no X-Fused guard, same posture as every other GET.
    folder = _folder_for(q.get("html") or "")
    if folder is None:
        return h._error("query must include 'html'")
    engine_id = background_apps.engine_id_for(folder)
    autostart = folder in background_apps.autostart_paths()
    child = engine_host.current(engine_id)
    running = child is not None and engine_host._alive(child)
    manifest = background_apps.load_manifest(folder)
    h._json({
        "running": running,
        "autostart": autostart,
        "pid": child.pid if running else None,
        "version": child.version if child is not None else None,
        "engine_id": engine_id,
        "protocol": _protocol_for(manifest),
    })


def running(h) -> None:
    folders = engine_host.background_running_folders()
    h._json({"running": {folder: True for folder in folders}})


def start(h, body: dict) -> None:
    """Spawn the daemon now. Does NOT touch the autostart flag."""
    try:
        folder, manifest, interpreter, unbuilt_reason = _resolve(body.get("html"))
    except _Reject as r:
        return h._error(r.message, r.status)
    engine_id = background_apps.engine_id_for(folder)
    try:
        version = background_apps.version_for(folder, interpreter)
    except OSError as e:
        return h._error(f"could not read {os.path.basename(folder)}'s manifest: {e}", 400)
    daemon, module = background_apps.bring_up_args(manifest)
    try:
        child = engine_host.ensure_background(
            engine_id, interpreter, daemon, background_apps.cache_dir_for(engine_id), version,
            folder, manifest.idle_timeout_s, module, retry_post=manifest.retry_post)
    except (engine_host.EngineError, OSError) as e:
        detail = unbuilt_reason if unbuilt_reason is not None else str(e)
        return h._error(f"could not start {os.path.basename(folder)}'s background app: {detail}",
                        502)
    h._json({"ok": True, "engine_id": engine_id, "pid": child.pid,
             "version": child.version, "protocol": _protocol_for(manifest)})


def autostart(h, body: dict) -> None:
    """Set the persisted autostart flag. Starts/stops nothing."""
    folder = _folder_for(body.get("html"))
    if folder is None:
        return h._error("request body must include 'html'")
    flag = bool(body.get("autostart"))
    background_apps.set_autostart(folder, flag)
    h._json({"ok": True, "autostart": flag})


def stop(h, body: dict) -> None:
    """Kill the running daemon WITHOUT touching autostart."""
    folder = _folder_for(body.get("html"))
    if folder is None:
        return h._error("request body must include 'html'")
    engine_host.stop(background_apps.engine_id_for(folder))
    h._json({"ok": True})


def restart(h, body: dict) -> None:
    """Respawn the daemon (autostart untouched). With no live child to
    restart — after a `stop()`, or first bring-up — falls back to a fresh
    `ensure_background`, same as `start`."""
    try:
        folder, manifest, interpreter, unbuilt_reason = _resolve(body.get("html"))
    except _Reject as r:
        return h._error(r.message, r.status)
    engine_id = background_apps.engine_id_for(folder)
    try:
        # Always recompute the version fresh (D510).
        version = background_apps.version_for(folder, interpreter)
        if engine_host.current(engine_id) is None:
            daemon, module = background_apps.bring_up_args(manifest)
            child = engine_host.ensure_background(
                engine_id, interpreter, daemon, background_apps.cache_dir_for(engine_id),
                version, folder, manifest.idle_timeout_s, module, retry_post=manifest.retry_post)
        else:
            child = engine_host.restart(engine_id, None, version=version)
    except (engine_host.EngineError, OSError) as e:
        return h._error(unbuilt_reason if unbuilt_reason is not None else str(e), 502)
    h._json({"ok": True, "pid": child.pid, "version": child.version,
             "protocol": _protocol_for(manifest)})


# ---- /api/engines/* -----------------------------------------------------------


def engines_running(h) -> None:
    h._json({"engines": engine_host.running_engines()})


def engine_stop(h, engine_id: str) -> None:
    """Idempotent: an unknown id is a no-op, not an error."""
    engine_host.stop(engine_id)
    h._json({"ok": True})


def proxy(h, engine_id: str, path: str, method: str, body: bytes) -> None:
    # /ping is the daemon's private liveness path (engine_host probes it with
    # the token); never a page resource, so it is not proxied.
    if path == "ping":
        return h._error("not found", 404)
    # Opaque and forwarded verbatim, but no segment may climb out of the
    # namespace the child serves.
    if ".." in path.split("/") or "\\" in path:
        return h._error("not found", 404)
    child = engine_host.current(engine_id)
    # A proxied POST runs arbitrary side-effecting code unless the manifest
    # declares `retry_post = true`: never pooled, never retried once sent.
    at_most_once = method == "POST" and child is not None and not child.retry_post
    # A child with a bounded lifetime must not be retired mid-call.
    bounded = child is not None and child.idle_timeout_s > 0
    # The 60s call budget is for the shipped worker's known request shape
    # (a `main =` app); a `daemon =` author's own routes are not truncated.
    call_timeout = engine_host.CALL_TIMEOUT_S if child is not None and child.module else None
    if bounded:
        engine_host.mark_busy(engine_id)
    try:
        result = engine_forward.forward(engine_id, method, "/" + path, body,
                                        {k.lower(): v for k, v in h.headers.items()},
                                        call_timeout=call_timeout, at_most_once=at_most_once)
    finally:
        if bounded:
            engine_host.mark_idle(engine_id)
    ctype = result.headers.pop("Content-Type", None) or result.headers.pop("content-type", None)
    h._send(result.status, result.body, ctype or "application/octet-stream", result.headers)
