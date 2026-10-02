"""`/api/dock*` — the menu-bar dock over HTTP (bots/dock.py). The tray page
(`/dock`, hosted by menubar_dock.py) and the app viewer's "Pin to menu bar"
read and write through these.

    GET  /api/dock                        -> {pinned, recent_bots, recent_apps, tilesize}
    POST /api/dock/open    {kind, id|dir} -> {ok, native: true}                 the app opened it
                                             {ok, native: false, view: <path>}  navigate there yourself
    POST /api/dock/home                   -> {ok, native: true} | {ok, native: false, view: "/"}
    POST /api/dock/reveal  {dir}          -> {ok}                 `open -R` (apps root only)
    POST /api/dock/pin     {dir, pinned}  -> {ok, pinned_apps}
    POST /api/dock/order   {dirs}         -> {ok, pinned_apps}    the pinned apps' new left-to-right order
    POST /api/dock/pin-bot {id, pinned}   -> {ok, id, pinned}
    POST /api/dock/size    {tilesize}     -> {ok, tilesize}       clamped to 16..128, persisted

Rows: a bot is {kind: "bot", id, name, face, status, running, updated,
pinned}, an app {kind: "app", dir, name, icon, pinned, mtime} (dock.entries).

`native` is whether the macOS app (macapp.py) took the action through
`server.native_hooks` ("dock_open", "show_home"); in a plain browser or the CLI
server it did not, and `view` is the page path to go to instead.

Same conventions as bots/routes.py: reads are plain GETs, every POST needs
`X-Fused: 1`, and a bad request is `{"error": "<sentence>"}` with a 400.
"""
from __future__ import annotations

from fused_render_app._web import APIRouter, Body, Header
from fused_render_app.bots import dock
from fused_render_app.routes.common import _error, _require_fused

router = APIRouter()


def _hooks() -> dict:
    from fused_render_app import server

    return server.native_hooks


def _guarded_body(body, x_fused):
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard, {}
    return None, (body if isinstance(body, dict) else {})


@router.get("/api/dock")
def dock_get():
    out = dock.entries()
    out["tilesize"] = dock.tilesize()
    return out


@router.post("/api/dock/open")
def dock_open(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    kind = body.get("kind")
    if kind == "bot":
        bid = str(body.get("id") or "")
        if not dock.bot_exists(bid):
            return _error(f"no such bot {bid}" if bid else "no bot id given", 400)
        key, view = bid, dock.bot_view_path(bid)
    elif kind == "app":
        try:
            key = dock.app_dir(str(body.get("dir") or ""))
        except ValueError as e:
            return _error(str(e) or "bad request", 400)
        # The listing's spelling, not the real path: a window it opened is
        # found again by URL (WindowManager.show_url / the page's Open in tab).
        key = str(body.get("dir"))
        view = dock.app_render_path(key)
    else:
        return _error("kind must be \"bot\" or \"app\"", 400)
    hook = _hooks().get("dock_open")
    if hook is not None:
        hook(kind, key)
        return {"ok": True, "native": True}
    return {"ok": True, "native": False, "view": view}


@router.post("/api/dock/home")
def dock_home(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, _body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    hook = _hooks().get("show_home")
    if hook is not None:
        hook()
        return {"ok": True, "native": True}
    return {"ok": True, "native": False, "view": "/"}


@router.post("/api/dock/reveal")
def dock_reveal(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    try:
        dock.reveal_app(str(body.get("dir") or ""))
    except ValueError as e:
        return _error(str(e) or "bad request", 400)
    return {"ok": True}


@router.post("/api/dock/pin")
def dock_pin(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    try:
        pins = dock.set_app_pinned(body.get("dir") or "", bool(body.get("pinned")))
    except ValueError as e:
        return _error(str(e) or "bad request", 400)
    return {"ok": True, "pinned_apps": pins}


@router.post("/api/dock/order")
def dock_order(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    try:
        pins = dock.set_app_order(body.get("dirs"))
    except ValueError as e:
        return _error(str(e) or "bad request", 400)
    return {"ok": True, "pinned_apps": pins}


@router.post("/api/dock/pin-bot")
def dock_pin_bot(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    bid = str(body.get("id") or "")
    if not dock.bot_exists(bid):
        return _error(f"no such bot {bid}" if bid else "no bot id given", 400)
    try:
        pinned = dock.set_bot_pinned(bid, bool(body.get("pinned")))
    except ValueError as e:
        return _error(str(e) or "bad request", 400)
    return {"ok": True, "id": bid, "pinned": pinned}


@router.post("/api/dock/size")
def dock_size(body: dict = Body(default=None), x_fused: str | None = Header(default=None)):
    guard, body = _guarded_body(body, x_fused)
    if guard is not None:
        return guard
    try:
        n = dock.set_tilesize(body.get("tilesize"))
    except ValueError as e:
        return _error(str(e) or "bad request", 400)
    return {"ok": True, "tilesize": n}
