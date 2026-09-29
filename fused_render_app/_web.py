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


threading.Thread(target=_run_loop, name="fused-render-app asyncio", daemon=True).start()


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
    """Stands in for ``Body(...)`` / ``Header(default=None)`` /
    ``Query(...)`` / ``File(...)`` defaults. ``kind`` tells the dispatcher
    where the value comes from; ``alias`` is ``Query(alias=...)``."""

    def __init__(self, default=None, kind: str = "header", alias: str | None = None):
        self.default = None if default is ... else default
        self.required = default is ...
        self.kind = kind
        self.alias = alias


def Body(default=..., **_kw):  # noqa: N802 - FastAPI's spelling
    return _Marker(default, "body")


def Header(default=None, **_kw):  # noqa: N802
    return _Marker(default, "header")


def Query(default=..., alias: str | None = None, **_kw):  # noqa: N802
    return _Marker(default, "query", alias)


def File(default=..., **_kw):  # noqa: N802
    return _Marker(default, "file")


class UploadFile:
    """One multipart file part, the slice of Starlette's ``UploadFile`` the
    copied routers read: ``filename``, ``content_type``, ``await read()``."""

    def __init__(self, filename: str | None, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


def parse_multipart(content_type: str, body: bytes) -> tuple[dict, dict]:
    """``(fields, files)`` from a ``multipart/form-data`` body. Text parts land
    in ``fields`` (str), file parts in ``files`` (``UploadFile``)."""
    import email
    import email.policy

    msg = email.message_from_bytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
        policy=email.policy.HTTP)
    fields: dict = {}
    files: dict = {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            files[name] = UploadFile(filename, part.get_content_type(), payload)
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


class BaseModel:
    """A pydantic-shaped request model without pydantic: class-level
    annotations name the fields, class attributes are their defaults, unknown
    keys are dropped, and a missing field with no default raises
    ``HTTPException(422)`` the way FastAPI's validation would. No coercion
    beyond ``int``/``float``/``bool``/``str`` on scalar annotations."""

    def __init__(self, **data):
        hints = {}
        for klass in reversed(type(self).__mro__):
            hints.update(getattr(klass, "__annotations__", {}) or {})
        for name, ann in hints.items():
            if name in data:
                setattr(self, name, _coerce(ann, data[name]))
            elif hasattr(type(self), name):
                setattr(self, name, getattr(type(self), name))
            else:
                raise HTTPException(422, f"field required: {name}")

    def dict(self) -> dict:  # noqa: A003 - pydantic's spelling
        return dict(vars(self))

    model_dump = dict


def _coerce(ann, value):
    if value is None:
        return None
    target = ann if isinstance(ann, str) else getattr(ann, "__name__", "")
    target = str(target).replace(" ", "")
    for typ, names in ((bool, ("bool", "bool|None")), (int, ("int", "int|None")),
                       (float, ("float", "float|None")), (str, ("str", "str|None"))):
        if target in names:
            if typ is bool and isinstance(value, str):
                return value in ("1", "true", "True")
            try:
                return typ(value)
            except (TypeError, ValueError):
                raise HTTPException(422, f"invalid value for {target}: {value!r}") from None
    return value


async def run_in_threadpool(fn, *args, **kwargs):
    """``fastapi.concurrency.run_in_threadpool``: run a blocking callable off
    the shared loop's thread."""
    return await asyncio.to_thread(fn, *args, **kwargs)


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

    def put(self, path: str, **_kw):
        return lambda fn: self._add("PUT", path, fn)

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


def _query_value(param, value):
    ann = param.annotation
    try:
        if ann in (int, float, bool) or (isinstance(ann, str) and ann in ("int", "float", "bool")):
            conv = {"int": int, "float": float, "bool": lambda v: v in ("1", "true", "True")}
            return (conv[ann] if isinstance(ann, str) else ann)(value)
    except (TypeError, ValueError):
        pass
    return value


def _model_class(fn, param):
    """The ``BaseModel`` subclass a parameter is annotated with, else None.
    Annotations may be strings under ``from __future__ import annotations``,
    so the name is looked up in the function's globals."""
    ann = param.annotation
    if isinstance(ann, str):
        ann = getattr(fn, "__globals__", {}).get(ann)
    return ann if inspect.isclass(ann) and issubclass(ann, BaseModel) else None


def call_route(fn, *, body, headers: dict, query: dict, path_params: dict, request: Request,
               files: dict | None = None):
    """Bind a FastAPI-style signature and call it (async functions run on the
    shared loop). Returns whatever the route returned."""
    sig = inspect.signature(fn)
    lower = {k.lower(): v for k, v in headers.items()}
    kwargs = {}
    for name, param in sig.parameters.items():
        marker = param.default if isinstance(param.default, _Marker) else None
        model = _model_class(fn, param)
        if name in path_params:
            kwargs[name] = path_params[name]
        elif name == "request":
            kwargs[name] = request
        elif model is not None:
            kwargs[name] = model(**(body if isinstance(body, dict) else {}))
        elif name == "body" or (marker is not None and marker.kind == "body"):
            if body is None:
                kwargs[name] = marker.default if marker is not None else {}
            else:
                kwargs[name] = body
        elif marker is not None and marker.kind == "query":
            key = marker.alias or name
            if key in query:
                kwargs[name] = _query_value(param, query[key])
            elif marker.required:
                raise HTTPException(422, f"query parameter required: {key}")
            else:
                kwargs[name] = marker.default
        elif marker is not None and marker.kind == "file":
            if name in (files or {}):
                kwargs[name] = files[name]
            elif marker.required:
                raise HTTPException(422, f"file required: {name}")
            else:
                kwargs[name] = marker.default
        elif marker is not None or name.startswith("x_"):
            header = name.replace("_", "-")
            kwargs[name] = lower.get(header, marker.default if marker is not None else None)
        elif name in query:
            kwargs[name] = _query_value(param, query[name])
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
