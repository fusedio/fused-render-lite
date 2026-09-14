"""The slice of FastAPI/Starlette the copied AI route modules use, without
FastAPI. ``APIRouter`` records ``(method, path) -> function``; the stdlib
server's dispatcher (``server.py``) resolves a route, builds the arguments
the function declared (``body``, ``x_fused``, ``request``, query params) and
renders whatever it returns: a ``dict`` (JSON 200), a ``JSONResponse``, a
``StreamingResponse`` (chunked), or a ``Response``.

Only what those modules touch is here. Anything else should be added, not
guessed at.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import threading
from collections.abc import AsyncIterator, Iterable

# ONE long-lived event loop on its own thread. fused-render's relay keeps a
# persistent asyncio subprocess (the warm Claude instance) whose pipes belong
# to the loop that spawned it, so every async route — and the prewarm — must
# run on the same loop. Requests arrive on http.server threads and submit
# coroutines here with run_coroutine_threadsafe.
_LOOP = asyncio.new_event_loop()


def _run_loop() -> None:
    asyncio.set_event_loop(_LOOP)
    _LOOP.run_forever()


threading.Thread(target=_run_loop, name="fused-lite asyncio", daemon=True).start()


def loop() -> asyncio.AbstractEventLoop:
    return _LOOP


def run_async(coro, timeout: float | None = None):
    """Run a coroutine on the shared loop from any thread; return its result."""
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result(timeout)


def call_on_loop(fn, *args) -> None:
    """Schedule a plain callable on the loop thread (for code that calls
    asyncio.ensure_future without a running loop, like prewarm_ai)."""
    _LOOP.call_soon_threadsafe(fn, *args)


class _Marker:
    """Stands in for ``Body(...)`` / ``Header(default=None)`` defaults; the
    dispatcher reads the parameter NAME, not the marker."""

    def __init__(self, default=None):
        self.default = default


def Body(default=..., **_kw):  # noqa: N802 - FastAPI's spelling
    return _Marker(default)


def Header(default=None, **_kw):  # noqa: N802
    return _Marker(default)


class _App:
    def __init__(self):
        self.state = type("State", (), {})()


APP = _App()


class Request:
    """What a route sees as ``request``: headers, query, a ``state`` bag."""

    def __init__(self, method: str, path: str, headers: dict, query: dict):
        self.method = method
        self.url_path = path
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.query_params = query
        self.state = type("State", (), {})()
        self.client = ("127.0.0.1", 0)
        # `request.app.state` — one process-wide bag, like the FastAPI app's.
        self.app = APP

    async def is_disconnected(self) -> bool:
        return False


class Response:
    media_type = "application/octet-stream"

    def __init__(self, content: bytes | str = b"", status_code: int = 200,
                 headers: dict | None = None, media_type: str | None = None,
                 background=None):
        self.body = content.encode("utf-8") if isinstance(content, str) else content
        self.status_code = status_code
        self.headers = dict(headers or {})
        if media_type:
            self.media_type = media_type
        self.background = background


class JSONResponse(Response):
    media_type = "application/json"

    def __init__(self, content, status_code: int = 200, headers: dict | None = None,
                 media_type: str | None = None, background=None):
        super().__init__(json.dumps(content).encode("utf-8"), status_code, headers,
                         media_type, background)


class RedirectResponse(Response):
    def __init__(self, url: str, status_code: int = 307, headers: dict | None = None, background=None):
        super().__init__(b"", status_code, {**(headers or {}), "Location": url}, "text/plain", background)


class PlainTextResponse(Response):
    media_type = "text/plain; charset=utf-8"


class HTMLResponse(Response):
    media_type = "text/html; charset=utf-8"


class FileResponse(Response):
    def __init__(self, path: str, media_type: str | None = None, headers: dict | None = None,
                 status_code: int = 200, background=None, filename: str | None = None):
        with open(path, "rb") as f:
            data = f.read()
        super().__init__(data, status_code, headers, media_type or "application/octet-stream",
                         background)
        if filename:
            self.headers.setdefault("Content-Disposition", f'attachment; filename="{filename}"')


class StreamingResponse:
    """A body produced by a sync or async iterator; the dispatcher writes it
    chunked."""

    def __init__(self, content, status_code: int = 200, headers: dict | None = None,
                 media_type: str | None = None, background=None):
        self.content = content
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.media_type = media_type or "application/octet-stream"
        self.background = background

    def iter_bytes(self) -> Iterable[bytes]:
        content = self.content
        if inspect.isasyncgen(content) or isinstance(content, AsyncIterator):
            agen = content.__aiter__()
            while True:
                try:
                    piece = run_async(agen.__anext__())
                except StopAsyncIteration:
                    break
                yield piece if isinstance(piece, bytes) else str(piece).encode("utf-8")
            return
        for piece in content:
            yield piece if isinstance(piece, bytes) else str(piece).encode("utf-8")


class BackgroundTask:
    def __init__(self, func, *args, **kwargs):
        self.func, self.args, self.kwargs = func, args, kwargs

    def __call__(self):
        self.func(*self.args, **self.kwargs)


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str = ""):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class APIRouter:
    def __init__(self, **_kw):
        self.routes: list[tuple[str, re.Pattern, list[str], object]] = []

    def _add(self, method: str, path: str, fn):
        names = re.findall(r"{(\w+)(?::path)?}", path)
        pattern = "^" + re.sub(r"{(\w+):path}", r"(?P<\1>.+)", re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", path)) + "$"
        self.routes.append((method, re.compile(pattern), names, fn))
        return fn

    def get(self, path: str, **_kw):
        return lambda fn: self._add("GET", path, fn)

    def post(self, path: str, **_kw):
        return lambda fn: self._add("POST", path, fn)

    def delete(self, path: str, **_kw):
        return lambda fn: self._add("DELETE", path, fn)

    def include_router(self, other: "APIRouter", **_kw):
        self.routes.extend(other.routes)

    def match(self, method: str, path: str):
        for m, pattern, names, fn in self.routes:
            if m != method:
                continue
            hit = pattern.match(path)
            if hit:
                return fn, hit.groupdict()
        return None, None


def call_route(fn, *, body, headers: dict, query: dict, path_params: dict, request: Request):
    """Bind a FastAPI-style signature and call it (async functions run on a
    fresh loop in this thread). Returns whatever the route returned."""
    sig = inspect.signature(fn)
    lower = {k.lower(): v for k, v in headers.items()}
    kwargs = {}
    for name, param in sig.parameters.items():
        if name in path_params:
            kwargs[name] = path_params[name]
        elif name == "request":
            kwargs[name] = request
        elif name == "body":
            kwargs[name] = body if body is not None else {}
        elif isinstance(param.default, _Marker) or name.startswith("x_"):
            header = name.replace("_", "-")
            kwargs[name] = lower.get(header, param.default.default if isinstance(param.default, _Marker) else None)
        elif name in query:
            value = query[name]
            ann = param.annotation
            try:
                if ann in (int, float, bool) or (isinstance(ann, str) and ann in ("int", "float", "bool")):
                    conv = {"int": int, "float": float, "bool": lambda v: v in ("1", "true", "True")}
                    value = (conv[ann] if isinstance(ann, str) else ann)(value)
            except (TypeError, ValueError):
                pass
            kwargs[name] = value
        elif param.default is not inspect.Parameter.empty:
            kwargs[name] = param.default
        else:
            kwargs[name] = None
    if inspect.iscoroutinefunction(fn):
        return run_async(fn(**kwargs))
    result = fn(**kwargs)
    if inspect.isawaitable(result):
        result = run_async(result)
    return result
