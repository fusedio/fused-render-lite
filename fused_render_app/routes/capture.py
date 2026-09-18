"""`/api/capture` — native screen, microphone and still capture (SPEC §45),
ported from fused-render's server/routers/capture.py onto the `_web` shim.

Route wiring only; everything real is `fused_render_app/capture/`. Shaped after
`/api/ai/transcribe` (routes/ai_routes.py): the reply comes back with the
OUTPUT PATH already decided, so a page needs no second lookup and a page that
navigated away can still find what it recorded.

Guarded with `X-Fused` like every other mutating route (D3/D36). A capture is
not a read: it turns on the microphone and the screen.

Dropped from upstream: the chunk WebSocket `/api/capture/{cid}/stream`. That
feed exists for platforms where the PAGE encodes (Windows, Linux); this app is
macOS-only and records natively, and the stdlib server speaks no WebSocket.
"""

from __future__ import annotations

from urllib.parse import unquote

from fused_render_app import capture
from fused_render_app._web import APIRouter, Body, Header, Response
from fused_render_app.routes.common import _error, _require_fused

router = APIRouter()


def _internal(e: Exception):
    return _error(f"{e.__class__.__name__}: {e}".rstrip(": "), status=500)


@router.get("/api/capture")
def api_capture_list():
    """What this machine can capture, plus every recording running right now.

    One GET rather than a `/sources` beside a `/list`: a page opening a recorder
    UI wants both in the same paint. Unguarded, like the other read-only routes
    — and `sources()` never prompts, which is what makes that safe.
    """
    return {"sources": capture.sources(), "active": capture.active()}


@router.post("/api/capture/start")
def api_capture_start(body: dict = Body(...),
                      x_fused: str | None = Header(default=None),
                      x_fused_page: str | None = Header(default=None)):
    """Begin a recording. `mode` is "screen" or "audio".

    `X-Fused-Page` names the page that started the capture (the same header and
    `unquote` channel the jobs routes use for a page-owned job's `page` field),
    so the row this creates knows where a click on it in Notifications goes.
    """
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    mode = body.get("mode") or "screen"
    page = unquote(x_fused_page) if x_fused_page else ""
    try:
        return capture.start(mode, body, page=page)
    except capture.CaptureError as e:
        return _error(str(e), status=400)
    except capture.Unsupported as e:
        # 409, not 400: the request was fine, the machine is not — the same
        # split `/api/ai/*` makes between "you asked wrong" and "no runner here".
        return _error(str(e), status=409)
    except Exception as e:                      # noqa: BLE001 - never a traceback
        return _internal(e)


@router.post("/api/capture/{cid}/stop")
def api_capture_stop(cid: str, x_fused: str | None = Header(default=None)):
    """End a recording and keep the file. Resolves with it."""
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    try:
        return capture.stop(cid)
    except capture.CaptureError as e:
        # `stop()` raises CaptureError("no such capture: …") for an unknown id.
        return _error(str(e), status=404)
    except Exception as e:                      # noqa: BLE001
        return _internal(e)


@router.post("/api/capture/{cid}/cancel")
def api_capture_cancel(cid: str, x_fused: str | None = Header(default=None)):
    """End a recording and DELETE the file — the ✕'s meaning, made explicit."""
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    try:
        return capture.stop(cid, discard=True)
    except capture.CaptureError as e:
        return _error(str(e), status=404)
    except Exception as e:                      # noqa: BLE001
        return _internal(e)


@router.post("/api/capture/screenshot")
def api_capture_screenshot(body: dict = Body(...),
                           x_fused: str | None = Header(default=None)):
    """One frame, now. No job row — it is milliseconds, not minutes."""
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    try:
        return capture.screenshot(body)
    except capture.CaptureError as e:
        return _error(str(e), status=400)
    except capture.Unsupported as e:
        return _error(str(e), status=409)
    except Exception as e:                      # noqa: BLE001
        return _internal(e)


@router.post("/api/capture/shot-region")
def api_capture_shot_region(body: dict = Body(...),
                            x_fused: str | None = Header(default=None)):
    """The pixels under a browser-measured screen rect, as a PNG body.

    The shell's export capture (SPEC AF-11): `{rect: [x, y, w, h], dpr}` in
    the browser's own screen units, bytes back — no file in recordings and no
    `fused.capture` surface. Errors are the ordinary JSON `_error` shape, so a
    caller branches on `res.ok` alone.
    """
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    try:
        png = capture.shot_region(body)
    except capture.CaptureError as e:
        return _error(str(e), status=400)
    except capture.Unsupported as e:
        return _error(str(e), status=409)
    except Exception as e:                      # noqa: BLE001
        return _internal(e)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})
