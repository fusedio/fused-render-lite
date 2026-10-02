"""`/api/dock*` — the menu-bar dock over HTTP (bots/dock.py).

    GET  /api/dock                       -> dock.entries(): {pinned, recent_bots, recent_apps}
    POST /api/dock/pin  {dir, pinned}    -> {ok, pinned_apps}   (X-Fused: 1)

Same conventions as bots/routes.py: reads are plain GETs, every POST needs
`X-Fused: 1`, and a bad request is `{"error": "<sentence>"}` with a 400. The
menu bar itself reads `dock.entries()` in-process; these routes are for the
page (the app viewer's "Pin to menu bar").
"""
from __future__ import annotations

from fused_render_app._web import APIRouter, Body, Header
from fused_render_app.bots import dock
from fused_render_app.routes.common import _error, _require_fused

router = APIRouter()


@router.get("/api/dock")
def dock_get():
    return dock.entries()


@router.post("/api/dock/pin")
def dock_pin(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    body = body if isinstance(body, dict) else {}
    try:
        pins = dock.set_app_pinned(body.get("dir") or "", bool(body.get("pinned")))
    except ValueError as e:
        return _error(str(e) or "bad request", 400)
    return {"ok": True, "pinned_apps": pins}
