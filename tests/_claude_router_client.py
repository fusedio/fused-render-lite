"""A starlette-TestClient-shaped client over the `_web` router shim, for the
copied Claude health/install/login tests: just `routes/claude_health.router`
on the dispatcher, no server (fused-render's tests build "just this router on
a bare app" for the same reason — a start dir, the template registry and the
shell build are not what three endpoints are about)."""
from __future__ import annotations

import json

from fused_render_app import _web
from fused_render_app.routes import claude_health as routes


class _Resp:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._body or b"null")


class RouterClient:
    def _call(self, method: str, path: str, body=None, headers=None):
        fn, params = routes.router.match(method, path)
        if fn is None:
            return _Resp(404, b'{"error":"not found"}')
        request = _web.Request(method, path, headers or {}, {})
        try:
            result = _web.call_route(fn, body=body, headers=headers or {}, query={},
                                     path_params=params or {}, request=request)
        except _web.HTTPException as e:
            return _Resp(e.status_code, json.dumps({"error": e.detail}).encode())
        if isinstance(result, _web.Response):
            return _Resp(result.status_code, result.body)
        return _Resp(200, json.dumps(result).encode())

    def get(self, path: str, headers=None):
        return self._call("GET", path, None, headers)

    def post(self, path: str, json=None, headers=None):  # noqa: A002 - TestClient's spelling
        return self._call("POST", path, json, headers)


def client() -> RouterClient:
    return RouterClient()
