"""Where the bots keep their state (docs/BOT-APP.md §1).

OpenBot kept everything beside its own code (`.fused/data/bots`,
`.fused/cache/bots`); here the roots hang off the app home, and the two
user-visible folders stay where OpenBot put them: the Inbox under
`~/Fused/bots/` and the apps under `~/Fused/app/` (`FUSED_RENDER_DIR`
overrides `~/Fused`, as everywhere in fused-render).
"""
from __future__ import annotations

import json
import os
import re

from fused_render_app import paths as app_paths


def root() -> str:
    """`<home>/bots` — made on first use."""
    p = os.path.join(app_paths.home(), "bots")
    os.makedirs(p, exist_ok=True)
    return p


def data_dir() -> str:
    """`<home>/bots/data/<id>/…`: bot.json, events.jsonl, memory.md, skills/, profile/, downloads/, files/, inbox/."""
    p = os.path.join(root(), "data")
    os.makedirs(p, exist_ok=True)
    return p


def cache_dir() -> str:
    """`<home>/bots/cache/<id>/…`: shot.png, session.json, steps/*.jpg, badjson/. Deletable any time."""
    p = os.path.join(root(), "cache")
    os.makedirs(p, exist_ok=True)
    return p


def bot_dir(bid: str) -> str:
    return os.path.join(data_dir(), bid)


def bot_cache_dir(bid: str) -> str:
    return os.path.join(cache_dir(), bid)


def usage_path() -> str:
    """One line per model call (the usage ledger)."""
    return os.path.join(root(), "usage.jsonl")


def slow_log_path() -> str:
    return os.path.join(cache_dir(), "slow.jsonl")


def builds_path() -> str:
    """The Builds panel's list (`GET/POST /api/bots/builds`)."""
    return os.path.join(root(), "builds.json")


def imessage_dir() -> str:
    """imessage.json (cursor), imessage.lock, imessage-state.json."""
    return root()


def workspace_dir() -> str:
    """fused-render's workspace root: `FUSED_RENDER_DIR`, else `~/Fused`."""
    return os.path.abspath(os.path.expanduser(os.environ.get("FUSED_RENDER_DIR") or "~/Fused"))


def apps_root() -> str:
    """`<workspace>/app`: where builds land and every bot's APPS live."""
    return os.path.join(workspace_dir(), "app")


def artifacts_root() -> str:
    """`<workspace>/bots`: the Inbox, one folder per bot, one subfolder per task."""
    return os.path.join(workspace_dir(), "bots")


def slug(name: str | None) -> str:
    """OpenBot `_slug`: lower-case, `-` for runs of anything else, 40 chars, `app` when empty."""
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", (name or "").lower()))[:40] or "app"


def server_origin() -> str:
    """Where this server listens (`FUSED_RENDER_ORIGIN`, set by `make_server`),
    else what `server.json` says. Raises RuntimeError when neither is known."""
    o = os.environ.get("FUSED_RENDER_ORIGIN")
    if o:
        return o.rstrip("/")
    try:
        with open(app_paths.pid_path(), encoding="utf-8") as f:
            o = (json.load(f).get("origin") or "").rstrip("/")
    except (OSError, ValueError):
        o = ""
    if not o:
        raise RuntimeError("the Render App server is not running (no origin known)")
    return o


def server_origin_quiet() -> str:
    try:
        return server_origin()
    except RuntimeError:
        return ""
