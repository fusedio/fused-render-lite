"""The whole HTTP surface of fused-render-lite, on the stdlib server.

Pages
  GET  /                    placeholder: drop a .fused here / open one
  GET  /open?_file=<abs>    opens the .fused: extracts, builds its env, iframes the entry
  GET  /render?path=<abs>   an app page with runtime.js injected into <head>

API (the six supported fused.* calls, plus what the shell needs)
  POST /api/open            {file}            -> {dir, entry, name, view}
  GET  /api/open/status?file=<abs>            -> {status, lines, error}
  POST /api/drop            raw bytes + X-Filename -> {file}
  POST /api/run             {py, html, params} -> runPython envelope
  GET  /api/fs/raw?path=&base=                 bytes (Range honoured)
  GET  /api/fs/stat?path=                      {path,name,is_dir,size,mtime,writable}
  POST /api/fs/write        {path, content, expected_mtime?, create?} -> stat
  POST /api/fs/upload?path=&base=   raw bytes -> stat (fused.uploadFile)
  POST /api/fs/mkdir        {path} -> stat; 409 when it exists
  GET  /api/jobs            {jobs:[...]}   POST /api/jobs {id, ...} -> row
  POST /api/jobs/<id>/cancel | /dismiss, /api/jobs/clear
  GET  /api/health                             {ok, version, pid}
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
import tempfile
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fused_render_lite import __version__, appfile, env, jobs, paths
from fused_render_lite._web import APIRouter, Request, Response, StreamingResponse, call_on_loop, call_route, run_async
from fused_render_lite.routes import ai_relay, ai_routes

AI_ROUTER = APIRouter()
AI_ROUTER.include_router(ai_relay.router)
AI_ROUTER.include_router(ai_routes.router)

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_DROP_BYTES = 1024 * 1024 * 1024
_HEAD_RE = re.compile(r"<head[^>]*>", re.I)

mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/geo+json", ".geojson")


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
    server_version = f"fused-render-lite/{__version__}"
    protocol_version = "HTTP/1.1"

    # ---- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # quiet access log -> logger.debug
        logger.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, status: int, body: bytes, ctype: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
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
            if route == "/api/jobs":
                return self._json({"jobs": jobs.list_jobs(mark_read=True)})
            if route == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
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
            if route == "/api/jobs/clear":
                return self._guarded() and self._json({"cleared": jobs.clear_finished()})
            m = re.match(r"^/api/jobs/([^/]+)/(cancel|dismiss)$", route)
            if m:
                return self._jobs_action(urllib.parse.unquote(m.group(1)), m.group(2))
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
        if not file:
            return self._static("index.html")
        with open(os.path.join(STATIC_DIR, "open.html"), "r", encoding="utf-8") as f:
            page = f.read()
        self._html(page.replace("__FILE_JSON__", json.dumps(file)))

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


def make_server(port: int = 0, host: str = "127.0.0.1") -> Server:
    paths.fix_process_env()
    srv = Server((host, port), Handler)
    # Workers spawned by runPython inherit this, so a detached process can
    # keep reporting to /api/jobs after its page is gone (fused-render's
    # documented pattern: plain JSON over HTTP, no fused_render_lite import).
    os.environ["FUSED_RENDER_ORIGIN"] = f"http://{host}:{srv.server_address[1]}"
    return srv


def start_ai() -> None:
    """The AI subsystem's background threads, as fused-render wires them at
    startup: the warm Claude process, the idle-model reaper, hardware and
    Hub-metadata refresh. Each is best-effort."""
    for name, fn in (("prewarm_ai", lambda: call_on_loop(ai_relay.prewarm_ai, None)),
                     ("reaper", ai_routes.supervisor.start_reaper),
                     ("hardware", ai_routes.supervisor.start_hardware_refresh),
                     ("hub-metadata", ai_routes.supervisor.start_hub_metadata_refresh)):
        try:
            fn()
        except Exception:  # noqa: BLE001
            logger.exception("ai startup hook %s failed", name)


def stop_ai() -> None:
    """Evict resident models (kills their worker processes) and the warm
    Claude instance. Called on quit."""
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
    thread = threading.Thread(target=srv.serve_forever, name="fused-render-lite http", daemon=True)
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
