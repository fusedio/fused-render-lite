"""The whole HTTP surface of fused-render-app, on the stdlib server.

Pages
  GET  /                    placeholder: drop a .fused here / open one
  GET  /open?_file=<abs>    opens the .fused: extracts, builds its env, iframes the entry
  GET  /open?_url=<http(s)> downloads (POST /api/fetch), then navigates to _file
  GET  /render?path=<abs>   an app page with runtime.js injected into <head>

API (the six supported fused.* calls, plus what the shell needs)
  POST /api/open            {file}            -> {dir, entry, name, app_id, view}
  GET  /api/open/status?file=<abs>            -> {status, lines, error}
  POST /api/drop            raw bytes + X-Filename -> {file}
  POST /api/fetch           {url}             -> {file}   (fetch.py; downloads/<app_id>.fused)
  POST /api/run             {py, html, params} -> runPython envelope
  GET  /api/fs/raw?path=&base=                 bytes (Range honoured)
  GET  /api/fs/stat?path=                      {path,name,is_dir,size,mtime,writable}
  POST /api/fs/write        {path, content, expected_mtime?, create?} -> stat
  POST /api/fs/upload?path=&base=   raw bytes -> stat (fused.uploadFile)
  POST /api/fs/mkdir        {path} -> stat; 409 when it exists
  GET  /api/jobs            {jobs:[...]}   POST /api/jobs {id, ...} -> row
  POST /api/jobs/<id>/cancel | /dismiss, /api/jobs/clear
  GET  /api/health                             {ok, version, pid}
  Self-update (update/mac.py; packaged mac app only — the launcher page's banner):
  GET  /api/update                             {update: null | {state, current_version, latest_version,
                                                 progress, progress_total, phase, error, check_only, check_error}}
  POST /api/update/check | /install {expected_version?} | /cancel | /relaunch
                                               404 when no update manager runs (dev server, CLI)
  Menu-bar dock (dock_store.py; GET /dock serves static/dock.html):
  GET  /api/dock                               {apps:[{file,name,pinned,running,openedAt,
                                                 hasIcon,iconVersion,hasPreview,previewVersion}], tilesize}
  GET  /api/dock/icon?file=<abs>[&theme=light|dark]  the app's icon.svg (currentColor
                                               resolved for the theme, icon_color.py), else
                                               its icon.png as is, or 404
  GET  /api/dock/preview?file=<abs>[&v=]       the app's preview.png (the hover bubble's
                                               picture; ``v`` = previewVersion, so it caches), or 404
  POST /api/dock/open|pin|remove|order|reveal|choose|home|size   (size: {tilesize} -> {tilesize})
  Launcher (launcher.py; GET /launcher serves static/launcher.html, GET /settings its settings page):
  GET  /api/launcher?q=                        {query, apps:[{file,name,title,description,pinned,running,
                                                 showcase,icon}]} (empty q: the pinned apps; else search)
  GET  /api/launcher/settings                  {hotkey: "alt+space", display: "⌥Space", bound: bool|null,
                                                 rowModifier: "alt", rowModifierDisplay: "⌥", pinnedBound: bool|null}
  POST /api/launcher/settings {hotkey?, rowModifier?}  stores (+ rebinds); 400 on a bad
                                               spec, nothing written -> same shape. /api/launcher/hotkey = alias.
  GET  /api/showcase                           {recent:[{file,name,title,description,preview,opened_at,showcase_id}],
                                                showcase:[{id, file, title, description, has_preview, preview, ...}]}
                                               (home page: dock entries newest first, then the showcase apps not among them)
  GET  /api/showcase/preview?id=<file name>    the app's preview.png, or 404
  fused.daemon (background_routes.py, copied from fused-render):
  GET  /api/apps/background/status?html=       {running, autostart, pid, version, engine_id, protocol}
  POST /api/apps/background/start|stop|restart {html}     /autostart {html, autostart}
  GET  /api/apps/background/running            {running: {folder: true}}
  GET  /api/engines/running                    {engines: [...]}
  POST /api/engines/<id>/stop
  ANY  /api/engines/<id>/proxy/<path>          forwarded to that daemon (POST guarded)
  /api/ai, /api/ai/*        fused-render's own AI routers (server/ai_relay.py, server/ai_routes.py),
                            copied verbatim and mounted through _web.APIRouter

Binds 127.0.0.1 only. Mutating/executing POSTs require ``X-Fused: 1``, which
forces a CORS preflight a foreign origin cannot pass — same guard as
fused-render, no more (it is not authentication).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fused_render_app import __version__, appfile, background_apps, background_routes, dock_store, engine_host, env, fetch, hotkey, icon_color, launcher, showcase, jobs, paths
from fused_render_app.update import mac as mac_update
from fused_render_app._web import APIRouter, Request, Response, StreamingResponse, call_on_loop, call_route, run_async
from fused_render_app.routes import ai_relay, ai_routes

AI_ROUTER = APIRouter()
AI_ROUTER.include_router(ai_relay.router)
AI_ROUTER.include_router(ai_routes.router)

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_DROP_BYTES = 1024 * 1024 * 1024
_HEAD_RE = re.compile(r"<head[^>]*>", re.I)

#: What the native shell (macapp.py) plugs in so the dock page can drive it:
#:   "open_files":    () -> set[str]   .fused files with a window open right now
#:   "focus_or_open": (file) -> None   raise that window or open a new one (non-blocking)
#:   "choose_file":   () -> None       the native open-file panel
#:   "show_home":     () -> None       the placeholder window
#:   "relaunch":      () -> None       quit and respawn from the bundle on disk (after an update)
#: Absent (CLI run, tests) the routes answer `native: false` and the page
#: navigates itself instead.
native_hooks: dict = {}

mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/geo+json", ".geojson")


def _js(value: str) -> str:
    """A JS string literal safe inside an inline <script>: json.dumps leaves
    ``<``/``>`` alone, so a query value carrying ``</script>`` would end the
    block. Escape them."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")


def app_dir_for(path: str) -> str | None:
    """The extracted-app root that ``path`` lives under, or None."""
    root = os.path.realpath(paths.apps_dir())
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep):
        return None
    rel = real[len(root) + 1:]
    top = rel.split(os.sep, 1)[0]
    return os.path.join(root, top)


class Handler(BaseHTTPRequestHandler):
    server_version = f"fused-render-app/{__version__}"
    protocol_version = "HTTP/1.1"

    # ---- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # quiet access log -> logger.debug
        logger.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, status: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"))

    def _error(self, message: str, status: int = 400, **fields) -> None:
        self._json({"error": message, **fields}, status)

    def _html(self, text: str, status: int = 200) -> None:
        self._send(status, text.encode("utf-8"), "text/html; charset=utf-8")

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict | None:
        try:
            data = json.loads(self._body() or b"{}")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _guarded(self) -> bool:
        if self.headers.get("X-Fused") != "1":
            self._error("missing or invalid X-Fused header", 403)
            return False
        return True

    # ---- routing ----------------------------------------------------------

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        route = url.path
        try:
            if route == "/":
                return self._static("index.html")
            if route.startswith("/static/"):
                return self._static(route[len("/static/"):])
            if route == "/open":
                return self._open_page(q)
            if route == "/render":
                return self._render(q)
            if route == "/api/open/status":
                return self._open_status(q)
            if route == "/api/fs/raw":
                return self._fs_raw(q)
            if route == "/api/fs/stat":
                return self._fs_stat(q)
            if route == "/api/health":
                return self._json({"ok": True, "version": __version__, "pid": os.getpid()})
            if route == "/api/update":
                manager = mac_update.manager()
                return self._json({"update": manager.status() if manager else None})
            if route == "/api/jobs":
                return self._json({"jobs": jobs.list_jobs(mark_read=True)})
            if route == "/api/showcase":
                return self._json(showcase.home())
            if route == "/api/showcase/preview":
                return self._showcase_preview(q)
            if route == "/dock":
                return self._static("dock.html")
            if route == "/launcher":
                return self._static("launcher.html")
            if route == "/settings":
                return self._static("settings.html")
            if route == "/api/launcher":
                return self._json({"query": q.get("q") or "",
                                   "apps": launcher.results(q.get("q") or "", self._dock_running())})
            if route in ("/api/launcher/settings", "/api/launcher/hotkey"):
                return self._json(self._launcher_status())
            if route == "/api/dock":
                return self._json({"apps": dock_store.list_apps(self._dock_running()),
                                   "tilesize": dock_store.get_tilesize()})
            if route == "/api/dock/icon":
                return self._dock_icon(q)
            if route == "/api/dock/preview":
                return self._dock_preview(q)
            if route == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if route == "/api/apps/background/status":
                return background_routes.status(self, q)
            if route == "/api/apps/background/running":
                return background_routes.running(self)
            if route == "/api/engines/running":
                return background_routes.engines_running(self)
            m = background_routes.PROXY_RE.match(route)
            if m:  # proxied GET/HEAD: read-only, unguarded like every other GET
                return background_routes.proxy(self, m.group(1), m.group(2), self.command, b"")
            if self._dispatch("GET", route, q):
                return
            self._error("not found", 404)
        except Exception as exc:  # noqa: BLE001
            logger.exception("GET %s failed", self.path)
            self._error(f"internal error: {exc}", 500)

    do_HEAD = do_GET  # noqa: N815

    def do_POST(self):  # noqa: N802
        route = urllib.parse.urlsplit(self.path).path
        try:
            if route == "/api/open":
                return self._api_open()
            if route == "/api/fetch":
                return self._api_fetch()
            if route == "/api/drop":
                return self._api_drop()
            if route == "/api/run":
                return self._api_run()
            if route == "/api/fs/write":
                return self._fs_write()
            if route == "/api/fs/upload":
                return self._fs_upload()
            if route == "/api/fs/mkdir":
                return self._fs_mkdir()
            if route == "/api/jobs":
                return self._jobs_report()
            if route.startswith("/api/dock/"):
                return self._dock(route[len("/api/dock/"):])
            if route in ("/api/launcher/settings", "/api/launcher/hotkey"):
                return self._launcher_settings()
            if route == "/api/jobs/clear":
                return self._guarded() and self._json({"cleared": jobs.clear_finished()})
            if route.startswith("/api/update/"):
                return self._update(route[len("/api/update/"):])
            m = re.match(r"^/api/jobs/([^/]+)/(cancel|dismiss)$", route)
            if m:
                return self._jobs_action(urllib.parse.unquote(m.group(1)), m.group(2))
            if route.startswith("/api/apps/background/"):
                return self._background(route[len("/api/apps/background/"):])
            m = background_routes.STOP_RE.match(route)
            if m:
                return self._guarded() and background_routes.engine_stop(self, m.group(1))
            m = background_routes.PROXY_RE.match(route)
            if m:
                if not self._guarded():
                    return
                return background_routes.proxy(self, m.group(1), m.group(2), "POST", self._body())
            if self._dispatch("POST", route, {}):
                return
            self._error("not found", 404)
        except Exception as exc:  # noqa: BLE001
            logger.exception("POST %s failed", self.path)
            self._error(f"internal error: {exc}", 500)

    # ---- pages ------------------------------------------------------------

    def _static(self, name: str) -> None:
        path = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(path):
            return self._error("not found", 404)
        with open(path, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    def _open_page(self, q: dict) -> None:
        file = q.get("_file") or q.get("file") or ""
        url = q.get("_url") or q.get("url") or ""
        if not file and not url:
            return self._static("index.html")
        with open(os.path.join(STATIC_DIR, "open.html"), "r", encoding="utf-8") as f:
            page = f.read()
        # A URL open renders a confirm step; the transfer happens only on the
        # user's click (POST /api/fetch), never from this GET — any web page
        # can point the browser here, and opening a .fused runs its Python.
        page = page.replace("__FILE_JSON__", _js(file)).replace("__URL_JSON__", _js(url))
        self._html(page)

    def _render(self, q: dict) -> None:
        path = q.get("path") or ""
        if not os.path.isabs(path) or not os.path.isfile(path):
            return self._error(f"no such file: {path}", 404)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
        injection = '<script src="/static/runtime.js"></script>'
        m = _HEAD_RE.search(html)
        if m:
            html = html[: m.end()] + injection + html[m.end():]
        else:
            html = injection + html
        self._html(html)

    def _showcase_preview(self, q: dict) -> None:
        data = showcase.preview_bytes(q.get("id") or "")
        if data is None or len(data) > showcase.MAX_PREVIEW_BYTES:
            return self._error("not found", 404)
        self._send(200, data, "image/png")

    # ---- open / drop ------------------------------------------------------

    def _api_open(self) -> None:
        if not self._guarded():
            return
        body = self._json_body()
        file = str((body or {}).get("file") or "")
        if not file or not os.path.isabs(file):
            return self._error("file must be an absolute .fused file path")
        try:
            result = appfile.open_app_file(file)
        except appfile.AppFileError as exc:
            return self._error(str(exc))
        try:
            dock_store.record_open(file, result["name"])
        except OSError:  # the dock is a convenience; opening the app is not
            logger.warning("could not record %s in dock.json", file, exc_info=True)
        env.ensure(result["dir"])
        result["view"] = "/render?path=" + urllib.parse.quote(result["entry"], safe="/")
        result["install"] = env.status(result["dir"])
        self._json(result)

    def _open_status(self, q: dict) -> None:
        file = q.get("file") or ""
        if not file or not os.path.isabs(file):
            return self._error("file must be an absolute .fused file path")
        try:
            result = appfile.open_app_file(file)  # re-use of the extract; cheap
        except appfile.AppFileError as exc:
            return self._error(str(exc))
        self._json(env.status(result["dir"]))

    def _api_fetch(self) -> None:
        if not self._guarded():
            return
        body = self._json_body()
        url = str((body or {}).get("url") or "").strip()
        if not fetch.is_url(url):
            return self._error("url must be an http:// or https:// link to a .fused file")
        try:
            file = fetch.download_app_file(url)
        except fetch.FetchError as exc:
            return self._error(str(exc))
        logger.info("fetched %s -> %s", url, file)
        self._json({"file": file})

    def _api_drop(self) -> None:
        if not self._guarded():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_DROP_BYTES:
            return self._error("empty or oversized upload", 413)
        raw_name = urllib.parse.unquote(self.headers.get("X-Filename") or "app.fused")
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", os.path.basename(raw_name)).strip() or "app.fused"
        if not name.lower().endswith(".fused"):
            name += ".fused"
        dest_dir = paths.dropped_dir()
        stem, ext = os.path.splitext(name)
        dest = os.path.join(dest_dir, name)
        n = 1
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(dest_dir, f"{stem} ({n}){ext}")
        fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".upload-")
        remaining = length
        with os.fdopen(fd, "wb") as out:
            while remaining > 0:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                out.write(chunk)
        os.replace(tmp, dest)
        try:
            appfile.read_manifest(dest)
        except appfile.AppFileError as exc:
            os.remove(dest)
            return self._error(str(exc))
        self._json({"file": dest})

    # ---- menu-bar dock (dock_store.py) --------------------------------------

    @staticmethod
    def _dock_running() -> set[str]:
        hook = native_hooks.get("open_files")
        if hook is None:
            return set()
        try:
            return set(hook())
        except Exception:  # noqa: BLE001 — a broken hook must not 500 the dock
            logger.exception("open_files hook failed")
            return set()

    def _dock_icon(self, q: dict) -> None:
        file = q.get("file") or ""
        data = appfile.icon_bytes(file) if os.path.isabs(file) else None
        if data is None:
            return self._error("not found", 404)
        headers = {"Cache-Control": "no-cache"}
        # The png fallback (appfile.ICON_NAMES) is a raster: nothing to
        # recolour, served as is; the tile clips it to its rounded corners.
        if appfile.is_png(data):
            return self._send(200, data, "image/png", headers)
        # A picked glyph names its colour and strokes in currentColor; the
        # dock's <img> cannot see the page's theme, so resolve it here.
        data = icon_color.theme_icon_svg(data, q.get("theme") or "")
        self._send(200, data, "image/svg+xml", headers)

    def _dock_preview(self, q: dict) -> None:
        file = q.get("file") or ""
        data = appfile.preview_bytes(file) if os.path.isabs(file) else None
        if data is None:
            return self._error("not found", 404)
        # A preview can run to megabytes and is fetched on every hover; the
        # page keys the URL on previewVersion (a new preview is a new URL),
        # so the bytes may be cached for good.
        self._send(200, data, "image/png", {"Cache-Control": "max-age=31536000, immutable"})

    def _dock(self, action: str) -> None:
        if action not in ("open", "pin", "remove", "order", "reveal", "choose", "home", "size"):
            return self._error("not found", 404)
        if not self._guarded():
            return
        body = self._json_body() or {}
        if action == "order":
            files = body.get("files")
            if not isinstance(files, list):
                return self._error("'files' must be a list of paths")
            dock_store.reorder([f for f in files if isinstance(f, str)])
            return self._json({"apps": dock_store.list_apps(self._dock_running())})
        if action == "size":
            # Separator drag: the page sends the size it is showing; the reply
            # is what was stored (clamped), so the page can settle on it.
            return self._json({"tilesize": dock_store.set_tilesize(body.get("tilesize"))})
        if action == "choose":
            hook = native_hooks.get("choose_file")
            if hook is None:
                return self._json({"ok": False})
            hook()
            return self._json({"ok": True})
        if action == "home":
            hook = native_hooks.get("show_home")
            if hook is None:
                return self._json({"ok": False, "view": "/"})
            hook()
            return self._json({"ok": True})
        file = body.get("file")
        if not isinstance(file, str) or not file or not os.path.isabs(file):
            return self._error("'file' must be an absolute .fused file path")
        file = os.path.abspath(file)
        if action == "pin":
            dock_store.set_pinned(file, bool(body.get("pinned")))
        elif action == "remove":
            dock_store.remove(file)
        elif action == "open":
            if not os.path.isfile(file):
                return self._error(f"no such file: {file}")
            hook = native_hooks.get("focus_or_open")
            if hook is None:
                return self._json({"ok": True, "native": False,
                                   "view": "/open?_file=" + urllib.parse.quote(file, safe="/")})
            hook(file)
            return self._json({"ok": True, "native": True})
        elif action == "reveal":
            subprocess.Popen(["open", "-R", file])
            return self._json({"ok": True})
        self._json({"apps": dock_store.list_apps(self._dock_running())})

    # ---- launcher ---------------------------------------------------------

    @staticmethod
    def _launcher_status() -> dict:
        out = launcher.settings()
        hook = native_hooks.get("launcher_hotkey_bound")
        bound = None
        if hook is not None:
            try:
                bound = hook()
            except Exception:  # noqa: BLE001
                logger.exception("launcher_hotkey_bound hook failed")
        out["bound"] = bound
        hook = native_hooks.get("launcher_pinned_bound")
        pinned_bound = None
        if hook is not None:
            try:
                pinned_bound = hook()
            except Exception:  # noqa: BLE001
                logger.exception("launcher_pinned_bound hook failed")
        out["pinnedBound"] = pinned_bound
        return out

    def _launcher_settings(self) -> None:
        if not self._guarded():
            return
        body = self._json_body() or {}
        try:
            if "rowModifier" in body:
                launcher.set_row_modifier(body.get("rowModifier"))
            spec = launcher.set_hotkey(body.get("hotkey")) if "hotkey" in body else None
        except hotkey.SpecError as e:
            return self._error(str(e))
        # Rebinding is native and main-thread: the hook hops there itself
        # and returns at once; the reply's ``bound`` reflects the previous
        # state, the page re-reads a moment later. spec None: only another
        # setting changed — the panel's page is told, the pinned shortcuts
        # re-read their modifier.
        hook = native_hooks.get("launcher_rebind")
        if hook is not None:
            try:
                hook(spec)
            except Exception:  # noqa: BLE001
                logger.exception("launcher_rebind hook failed")
        self._json(self._launcher_status())

    # ---- runPython --------------------------------------------------------

    def _api_run(self) -> None:
        if not self._guarded():
            return
        body = self._json_body()
        if body is None:
            return self._error("request body must be a JSON object")
        py, html, params = body.get("py"), body.get("html"), body.get("params") or {}
        if not py or not isinstance(py, str):
            return self._error("request body must include 'py': a path to a Python file")
        if not os.path.isabs(py):
            if not html:
                return self._error("'py' is relative but 'html' was not provided")
            py = os.path.normpath(os.path.join(os.path.dirname(html), py))
        app_dir = app_dir_for(py) or app_dir_for(html or "") or os.path.dirname(py)
        result = env.run_python(py, params if isinstance(params, dict) else {}, app_dir)
        result["resolved_py"] = py
        self._json(result)

    # ---- jobs ---------------------------------------------------------------

    def _jobs_report(self) -> None:
        if not self._guarded():
            return
        body = self._json_body()
        page = urllib.parse.unquote(self.headers.get("X-Fused-Page") or "")
        try:
            self._json(jobs.upsert(body if body is not None else {}, page=page))
        except jobs.JobError as exc:
            self._error(str(exc))

    def _jobs_action(self, job_id: str, action: str) -> None:
        if not self._guarded():
            return
        if action == "cancel":
            row = jobs.request_cancel(job_id)
            return self._json(row) if row else self._error("no such job", 404)
        return self._json({"dismissed": jobs.dismiss(job_id)})

    # ---- self-update (update/mac.py) -----------------------------------------

    def _update(self, action: str) -> None:
        """POST /api/update/check|install|cancel|relaunch. All mutate (network,
        a bundle swap, a quit), so all carry the X-Fused guard; 404 when no
        manager runs — a dev server or CLI run has nothing to swap."""
        if action not in ("check", "install", "cancel", "relaunch"):
            return self._error("not found", 404)
        if not self._guarded():
            return
        manager = mac_update.manager()
        if manager is None:
            return self._error("self-update is not available here", 404)
        if action == "check":
            # Throttled (mac_update.MIN_CHECK_GAP_S): the launcher fires this
            # when the app comes back to the front, and a run of focus flips
            # must not become a run of CDN fetches.
            return self._json(manager.check())
        if action == "install":
            body = self._json_body() or {}
            expected = body.get("expected_version")
            return self._json(manager.install(
                expected_version=expected if isinstance(expected, str) else None))
        if action == "cancel":
            return self._json(manager.cancel())
        # relaunch: only once the bundle on disk is the new version, and only
        # with a native shell to do the quitting.
        status = manager.status()
        if status["state"] != "installed":
            return self._error("no installed update to restart into", 409)
        relaunch = native_hooks.get("relaunch")
        if relaunch is None:
            return self._error("relaunch needs the native app", 404)
        # The reply goes out first; the hook quits on a delay.
        self._json({"relaunching": True})
        relaunch()

    # ---- fused.daemon (background_routes.py) --------------------------------

    def _background(self, action: str) -> None:
        fn = {"start": background_routes.start, "stop": background_routes.stop,
              "restart": background_routes.restart,
              "autostart": background_routes.autostart}.get(action)
        if fn is None:
            return self._error("not found", 404)
        if not self._guarded():
            return
        body = self._json_body()
        if body is None:
            return self._error("body must be a JSON object")
        fn(self, body)

    # ---- copied fused-render routers (AI) ----------------------------------

    def _dispatch(self, method: str, route: str, q: dict) -> bool:
        """Route through the FastAPI-shaped routers copied from fused-render
        (`_web.APIRouter`). Returns False when nothing matched."""
        fn, path_params = AI_ROUTER.match(method, route)
        if fn is None:
            return False
        body = None
        if method == "POST":
            body = self._json_body()
            if body is None:
                body = {}
        request = Request(method, route, dict(self.headers.items()), q)
        try:
            result = call_route(fn, body=body, headers=dict(self.headers.items()), query=q,
                                path_params=path_params or {}, request=request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s %s failed", method, route)
            self._error(f"internal error: {exc}", 500)
            return True
        self._emit(result)
        return True

    def _emit(self, result) -> None:
        if isinstance(result, StreamingResponse):
            self.send_response(result.status_code)
            self.send_header("Content-Type", result.media_type)
            for k, v in result.headers.items():
                self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for piece in result.iter_bytes():
                    if not piece:
                        continue
                    self.wfile.write(b"%x\r\n" % len(piece) + piece + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.close_connection = True
            finally:
                if result.background:
                    try:
                        result.background()
                    except Exception:  # noqa: BLE001
                        logger.exception("background task failed")
            return
        if isinstance(result, Response):
            self._send(result.status_code, result.body, result.media_type, result.headers)
            if result.background:
                try:
                    result.background()
                except Exception:  # noqa: BLE001
                    logger.exception("background task failed")
            return
        self._json(result if result is not None else {})

    # ---- fs ---------------------------------------------------------------

    def _resolve(self, q: dict) -> str | None:
        path = q.get("path") or ""
        if not path:
            return None
        if not os.path.isabs(path):
            base = q.get("base") or ""
            if not base:
                return None
            path = os.path.normpath(os.path.join(os.path.dirname(base), path))
        return path

    def _fs_raw(self, q: dict) -> None:
        path = self._resolve(q)
        if not path or not os.path.isfile(path):
            return self._error(f"no such file: {q.get('path')}", 404)
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
            ctype += "; charset=utf-8"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = 200
        if rng and rng.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:
                    start = max(size - int(m.group(2)), 0)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                self.wfile.write(chunk)

    @staticmethod
    def _stat_payload(path: str) -> dict:
        st = os.stat(path)
        is_dir = os.path.isdir(path)
        return {
            "path": path,
            "name": os.path.basename(path) or path,
            "is_dir": is_dir,
            "size": None if is_dir else st.st_size,
            "mtime": st.st_mtime,
            "writable": os.access(path, os.W_OK),
        }

    def _fs_stat(self, q: dict) -> None:
        path = self._resolve(q)
        if not path or not os.path.exists(path):
            return self._error(f"no such file or directory: {q.get('path')}", 404)
        self._json(self._stat_payload(path))

    def _write_target(self, path: str | None, base: str | None) -> str | None:
        if not path or not isinstance(path, str):
            return None
        if not os.path.isabs(path):
            if not base:
                return None
            path = os.path.normpath(os.path.join(os.path.dirname(base), path))
        return path

    def _fs_upload(self) -> None:
        """Raw request body -> file. ``?path=`` absolute, or relative to ``?base=``."""
        if not self._guarded():
            return
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}
        path = self._write_target(q.get("path"), q.get("base"))
        if not path:
            return self._error("'path' must be an absolute path, or relative with 'base'")
        if os.path.isdir(path):
            return self._error(f"path is a directory: {path}")
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            return self._error(f"parent directory does not exist: {parent}", 404)
        exists = os.path.exists(path)
        if (exists and not os.access(path, os.W_OK)) or (not exists and not os.access(parent, os.W_OK)):
            return self._json({"error": "readonly"}, 403)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_DROP_BYTES:
            return self._error("upload too large", 413)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".fused-upload-")
        try:
            with os.fdopen(fd, "wb") as out:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    out.write(chunk)
            os.replace(tmp, path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return self._error(f"cannot write {path}: {e}")
        self._json({**self._stat_payload(path), "created": not exists})

    def _fs_mkdir(self) -> None:
        if not self._guarded():
            return
        body = self._json_body() or {}
        path = self._write_target(body.get("path"), body.get("base"))
        if not path:
            return self._error("'path' must be an absolute path, or relative with 'base'")
        if os.path.isdir(path):
            return self._json({"error": "exists"}, 409)
        if os.path.exists(path):
            return self._error(f"path exists and is not a directory: {path}")
        parent = os.path.dirname(path.rstrip(os.sep))
        if not os.path.isdir(parent):
            return self._error(f"parent directory does not exist: {parent}", 404)
        if not os.access(parent, os.W_OK):
            return self._json({"error": "readonly"}, 403)
        try:
            os.mkdir(path)
        except OSError as e:
            return self._error(f"cannot create {path}: {e}")
        self._json({**self._stat_payload(path), "created": True})

    def _fs_write(self) -> None:
        if not self._guarded():
            return
        body = self._json_body()
        if body is None:
            return self._error("request body must be a JSON object")
        path, content = body.get("path"), body.get("content")
        expected_mtime, create = body.get("expected_mtime"), bool(body.get("create"))
        if not path or not isinstance(path, str) or not os.path.isabs(path):
            return self._error("'path' must be an absolute filesystem path")
        if not isinstance(content, str):
            return self._error("'content' must be a string")
        if os.path.isdir(path):
            return self._error(f"path is a directory: {path}")
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            return self._error(f"parent directory does not exist: {parent}", 404)
        exists = os.path.exists(path)
        if exists and not os.access(path, os.W_OK):
            return self._json({"error": "readonly"}, 403)
        if not exists and not os.access(parent, os.W_OK):
            return self._json({"error": "readonly"}, 403)
        if create and exists:
            return self._json({"error": "conflict"}, 409)
        if expected_mtime is not None:
            current = os.stat(path).st_mtime if exists else None
            if current is None or abs(current - float(expected_mtime)) >= 1e-6:
                return self._json({"error": "conflict", "mtime": current}, 409)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".fused-write-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            if exists:
                os.chmod(tmp, os.stat(path).st_mode & 0o777)
            os.replace(tmp, path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return self._error(f"cannot write {path}: {e}")
        self._json({**self._stat_payload(path), "created": not exists})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


SHARED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared")


def make_server(port: int = 0, host: str = "127.0.0.1") -> Server:
    paths.fix_process_env()
    srv = Server((host, port), Handler)
    # Workers spawned by runPython inherit this, so a detached process can
    # keep reporting to /api/jobs after its page is gone (fused-render's
    # documented pattern: plain JSON over HTTP, no fused_render_app import).
    os.environ["FUSED_RENDER_ORIGIN"] = f"http://{host}:{srv.server_address[1]}"
    # fused-render's background-apps contract: a daemon (or any app-side
    # script) finds the server through `<FUSED_RENDER_HOME_DIR>/server.json`
    # — `origin` to call and `shared` to sys.path for fused_ai/background_app.
    os.environ["FUSED_RENDER_HOME_DIR"] = paths.home()
    write_server_json(srv.server_address[1], host)
    return srv


def write_server_json(port: int, host: str = "127.0.0.1") -> None:
    """Publish this server's origin + the shared dir to `<home>/server.json`
    (fused-render's `write_server_json`, same payload plus `port`, which
    macapp's single-instance check reads). Write-then-rename; best-effort."""
    try:
        path = paths.pid_path()
        payload = {"origin": f"http://{host}:{port}", "port": port, "pid": os.getpid(),
                   "shared": SHARED_DIR, "version": __version__, "started": time.time()}
        tmp = path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError:
        logger.warning("could not write server.json (non-fatal)", exc_info=True)


def remove_server_json() -> None:
    """Undo `write_server_json` at shutdown — only if THIS process wrote it."""
    try:
        with open(paths.pid_path(), "r", encoding="utf-8") as f:
            if json.load(f).get("pid") != os.getpid():
                return
        os.remove(paths.pid_path())
    except (OSError, ValueError):
        pass


#: Set when the server starts quitting, so `background_apps.resurrect_autostart`
#: stops bringing daemons up (and tears down one that finished spawning after
#: `engine_host.stop_all` already ran — see its docstring).
_bg_shutdown = threading.Event()


def _start_background_apps() -> None:
    _bg_shutdown.clear()
    threading.Thread(target=background_apps.resurrect_autostart, args=(_bg_shutdown,),
                     name="background-apps-resurrect", daemon=True).start()


def start_ai() -> None:
    """The AI subsystem's background threads, as fused-render wires them at
    startup: the warm Claude process, the idle-model reaper, hardware and
    Hub-metadata refresh — plus the background-apps autostart resurrection
    (fused.daemon). Each is best-effort."""
    for name, fn in (("prewarm_ai", lambda: call_on_loop(ai_relay.prewarm_ai, None)),
                     ("reaper", ai_routes.supervisor.start_reaper),
                     ("hardware", ai_routes.supervisor.start_hardware_refresh),
                     ("hub-metadata", ai_routes.supervisor.start_hub_metadata_refresh),
                     ("background-apps", _start_background_apps)):
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("ai startup hook %s failed", name)


def stop_ai() -> None:
    """Evict resident models (kills their worker processes), the warm Claude
    instance, and every background-app daemon (fused.daemon). Called on quit."""
    _bg_shutdown.set()
    remove_server_json()
    try:
        engine_host.stop_all()
    except Exception:  # noqa: BLE001
        logger.exception("engine_host.stop_all failed")
    try:
        ai_routes.supervisor.unload_all()
    except Exception:  # noqa: BLE001
        logger.exception("unload_all failed")
    try:
        run_async(ai_relay.shutdown_ai_session(None), timeout=15)
    except Exception:  # noqa: BLE001
        logger.exception("ai shutdown failed")


def serve_in_thread(port: int = 0) -> tuple[Server, threading.Thread]:
    srv = make_server(port)
    start_ai()
    thread = threading.Thread(target=srv.serve_forever, name="fused-render-app http", daemon=True)
    thread.start()
    return srv, thread


def wait_ready(port: int, timeout: float = 10.0) -> bool:
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if json.load(r).get("ok"):
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    return False
