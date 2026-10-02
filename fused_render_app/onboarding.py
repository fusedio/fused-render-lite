"""First-run setup wizard state — the one flag behind "show the wizard?", and
the facts the wizard's steps check (ported from fused-render's
``fused_render/shell/onboarding.py``, adapted to FusedBot).

The wizard itself is the bots page's (frontend/src/apps/bots/onboarding/).
This module owns WHETHER it auto-shows, and owns it server-side on purpose:
the server walks ports when its default one is taken, and every port is a
different browser origin with a fresh localStorage — a flag kept there would
replay the wizard on the next port drift or a second browser. The flag lives
in its own JSON under the app home (``storage.home_dir()/onboarding.json``);
fused-render kept it in prefs.json, which this app does not have.

Three timestamps, kept distinct: ``opened_at`` (the wizard was on screen,
stamped by the first write of any kind), ``dismissed_at`` ("Skip for now" /
✕ / any other way out) and ``completed_at`` (the user reached the end). The
auto-show rule is "all three are None": the wizard is for someone who has
NEVER seen it; once opened, however they left, ``/`` is the bots page again
and the "Set up this Mac" entry is the way back in.

``stages`` is the fourth field: one record per wizard step,
``{status, meta, updated_at}``, ``status`` one of
``pending | partial | complete | n/a``. Each step decides its own status from
the facts it already checks and POSTs it here; ``meta`` is free-form. The
stored status is what the wizard last SAW, and for the stages the server can
check cheaply it is overruled on every read by what is true now
(``_observe``): Claude Code from claude_health's disk cache (never a spawn),
Chrome from the bots' own candidate list, local models from the Hub cache,
"first bot" from the bots data dir. So a bot made without the wizard moves
the stage without the wizard being reopened.

``seed_for_existing_users`` is the upgrade edge: "first time they open the
app" means a NEW user, and an install upgrading into this build has no flag
either. A bots data dir that already holds a bot is the evidence someone has
been here; stamp it completed once, at startup, before the shell ever asks.

Nothing here raises on a read: a flag that takes /api/config down is worse
than a wizard shown once too often.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from fused_render_app._web import APIRouter, Header, JSONResponse
from fused_render_app.routes.common import _require_fused
from fused_render_app.shell import storage

log = logging.getLogger(__name__)

router = APIRouter()

#: Recorded with every write so a later build can key a one-time re-show on
#: it; bumping it does not re-show the wizard today.
VERSION = 1

#: The wizard's step ids (frontend apps/bots/onboarding/OnboardingWizard
#: STEPS). A closed set: an unknown id is refused rather than stored.
STEPS = ("about", "claude", "chrome", "models", "bot")

STATUSES = ("pending", "partial", "complete", "n/a")

#: Largest `meta` a stage write may carry, in JSON bytes.
META_MAX_BYTES = 4096

#: `FUSED_RENDER_ONBOARDING=1` forces the auto-show (the flags read as fresh)
#: so a dev server renders the wizard without deleting the file; `=0` forces
#: it off (tests, and a dev server that is sick of it). Read per request.
FORCE_ENV = "FUSED_RENDER_ONBOARDING"

#: The wizard's route. `/` redirects here while the auto-show rule holds
#: (server.py), and the bots page routes on it client-side.
PATH = "/onboarding"


def _path() -> str:
    return os.path.join(storage.home_dir(), "onboarding.json")


def _read() -> dict:
    try:
        data = storage.read_json(_path())
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


#: Every write is a read-modify-write of the whole file, and the routes run
#: on http.server threads — two stage reports landing in the same tick would
#: each read the old `stages` and the loser's vanish. One reentrant lock.
_LOCK = threading.RLock()


def _write(patch: dict, *, opened: bool = True) -> dict:
    """Merge `patch` into the stored state. Every write the WIZARD makes is
    proof it was on screen, so `opened_at` is stamped on the first of them.
    `opened=False` is for the startup seed, which is not the user doing
    anything."""
    with _LOCK:
        state = _read()
        if opened and state.get("opened_at") is None:
            state["opened_at"] = time.time()
        state.update(patch)
        state["version"] = VERSION
        storage.write_json(_path(), state)
        return state


def _stages(state: dict) -> dict:
    """The stored stage records, validated (an unknown id or a malformed
    record is dropped, not served), then overruled by what the server sees."""
    raw = state.get("stages")
    out: dict = {}
    if isinstance(raw, dict):
        for sid, rec in raw.items():
            if sid not in STEPS or not isinstance(rec, dict):
                continue
            if rec.get("status") not in STATUSES:
                continue
            meta = rec.get("meta")
            out[sid] = {
                "status": rec["status"],
                "meta": meta if isinstance(meta, dict) else {},
                "updated_at": rec.get("updated_at"),
            }
    _observe(out)
    return out


def _put(stages: dict, sid: str, status: str, **meta: object) -> None:
    rec = stages.get(sid) or {"status": "pending", "meta": {}, "updated_at": None}
    if rec["status"] == status and all(rec["meta"].get(k) == v for k, v in meta.items()):
        stages.setdefault(sid, rec)
        return
    stages[sid] = {
        "status": status,
        "meta": {**rec["meta"], **meta, "observed": True},
        "updated_at": rec.get("updated_at"),
    }


def chrome_snapshot() -> dict:
    """`{found, path}`: the first browser from the bots' own candidate list
    (bots/browser.py CHROME_CANDIDATES) that exists on this machine. The same
    list `find_chrome` walks when a bot starts, so this and the bot cannot
    disagree."""
    try:
        from fused_render_app.bots.browser import CHROME_CANDIDATES

        for p in CHROME_CANDIDATES:
            if p and os.path.exists(p):
                return {"found": True, "path": p}
    except Exception:  # noqa: BLE001
        log.debug("onboarding: chrome probe failed", exc_info=True)
        return {"found": None, "path": None}
    return {"found": False, "path": None}


def _bot_count() -> int:
    from fused_render_app.bots import paths as bpaths

    root = bpaths.data_dir()
    if not os.path.isdir(root):
        return 0
    with os.scandir(root) as it:
        return sum(1 for e in it if e.is_dir() and not e.name.startswith("."))


def _observe(stages: dict) -> None:
    """Overrule the stored status with the truth where it is cheap to know.
    Mutates `stages` in place; never raises."""
    # Claude Code: the health module's DISK CACHE only — a fresh measure
    # spawns processes, and /api/config is read on every page load.
    try:
        from fused_render_app import claude_health

        cached = getattr(claude_health, "cached", None)
        h = cached() if cached else None
        if h is not None:
            runnable = bool(h.get("found")) and not h.get("broken")
            ok = (
                runnable
                and h.get("version") is not None
                and not h.get("outdated")
                and h.get("signed_in") is True
            )
            status = "complete" if ok else "partial" if runnable else "pending"
            acct = h.get("account") or {}
            _put(
                stages,
                "claude",
                status,
                version=h.get("version"),
                signed_in=h.get("signed_in"),
                account=acct.get("email") or acct.get("method"),
            )
    except Exception:  # noqa: BLE001
        log.debug("onboarding: claude observe failed", exc_info=True)

    # Chrome: a file stat per candidate.
    try:
        chrome = chrome_snapshot()
        if chrome["found"] is True:
            _put(stages, "chrome", "complete", path=chrome["path"])
        elif chrome["found"] is False and stages.get("chrome", {}).get("status") == "complete":
            _put(stages, "chrome", "pending", path=None)
    except Exception:  # noqa: BLE001
        log.debug("onboarding: chrome observe failed", exc_info=True)

    # Local models: one of the bots' local models on disk = complete; none
    # here but one downloading = partial. Otherwise only a stored
    # complete/partial is walked back to pending.
    try:
        picks = local_model_picks()
        here = [p["id"] for p in picks if p["downloaded"]]
        if here:
            _put(stages, "models", "complete", here=here)
        else:
            downloading = [p["id"] for p in picks if p["downloading"]]
            if downloading:
                _put(stages, "models", "partial", here=[], downloading=downloading)
            elif stages.get("models", {}).get("status") in ("complete", "partial"):
                _put(stages, "models", "pending", here=[], downloading=[])
    except Exception:  # noqa: BLE001
        log.debug("onboarding: models observe failed", exc_info=True)

    # First bot: any bot under the bots data dir.
    try:
        n = _bot_count()
        if n:
            _put(stages, "bot", "complete", bot_count=n)
    except Exception:  # noqa: BLE001
        log.debug("onboarding: bot observe failed", exc_info=True)


def local_model_picks() -> list[dict]:
    """The local models the Models step offers: the bots' own
    `LOCAL_MODELS` table (bot.py), NOT the AI catalog's `recommended` row —
    the catalog recommends Qwen, the bots run Gemma, and a wizard that
    fetched one for the other would leave the bot asking for a download on
    its first task anyway. One row per alias: `{alias, id, label, size_gb,
    downloaded, downloading, fit}`; `fit` is fit.py's verdict dict (or None
    when it cannot judge)."""
    from fused_render_app.bots import bot as botmod

    rows = []
    for alias, repo_id in botmod.LOCAL_MODELS.items():
        size_gb = botmod.LOCAL_MODEL_SIZES_GB.get(repo_id)
        downloaded = False
        try:
            from fused_render_app.ai import hub_cache

            downloaded = bool(hub_cache.has_cached_snapshot(repo_id))
        except Exception:  # noqa: BLE001
            log.debug("onboarding: hub cache read failed", exc_info=True)
        downloading = False
        try:
            from fused_render_app import jobs
            from fused_render_app.ai import supervisor

            for r in jobs.list_jobs():
                if (
                    str(r.get("id", "")).startswith(supervisor.JOB_PREFIX)
                    and r.get("kind") == "download"
                    and r.get("state") == "running"
                    and (r.get("model") == repo_id or r.get("id", "").endswith(repo_id))
                ):
                    downloading = True
                    break
        except Exception:  # noqa: BLE001
            log.debug("onboarding: jobs read failed", exc_info=True)
        fit = None
        try:
            from fused_render_app.ai import fit as fitmod

            fit = fitmod.verdict("text-generation", repo_id, size_gb=size_gb)
        except Exception:  # noqa: BLE001
            log.debug("onboarding: fit verdict failed", exc_info=True)
        rows.append(
            {
                "alias": alias,
                "id": repo_id,
                "label": _label(alias, repo_id),
                "size_gb": size_gb,
                "downloaded": downloaded,
                "downloading": downloading,
                "fit": fit,
            }
        )
    return rows


def _label(alias: str, repo_id: str) -> str:
    # The model picker's own words (bot.py's settings copy: "Gemma 4B and
    # 12B, local models that run on this Mac").
    if alias == "local-4b":
        return "Gemma 4B"
    if alias == "local-9b":
        return "Gemma 12B"
    return repo_id.rsplit("/", 1)[-1]


def should_auto_show() -> bool:
    """The redirect rule for `/`: never completed, never dismissed AND never
    opened — the server's half of the frontend's `shouldAutoShow`."""
    s = snapshot(observe=False)
    return s["completed_at"] is None and s["dismissed_at"] is None and s["opened_at"] is None


def snapshot(*, observe: bool = True) -> dict:
    """The `onboarding` field of /api/config and the body of
    GET /api/onboarding: `{completed_at, dismissed_at, opened_at, stages,
    chrome, version}`. `observe=False` skips the stage probes (the redirect
    only needs the flags)."""
    force = os.environ.get(FORCE_ENV)
    state = _read()
    stages = _stages(state) if observe else {}
    chrome = chrome_snapshot() if observe else {"found": None, "path": None}
    if force == "1":
        return {
            "completed_at": None,
            "dismissed_at": None,
            "opened_at": None,
            "stages": stages,
            "chrome": chrome,
            "version": VERSION,
        }
    if force == "0" and state.get("dismissed_at") is None:
        # Reads as dismissed without writing anything.
        state = {**state, "dismissed_at": time.time()}
    return {
        "completed_at": state.get("completed_at"),
        "dismissed_at": state.get("dismissed_at"),
        "opened_at": state.get("opened_at"),
        "stages": stages,
        "chrome": chrome,
        "version": VERSION,
    }


def seed_for_existing_users() -> None:
    """One-shot at startup: an install that already has a bot predates this
    wizard — mark it completed so an upgrade never greets a returning user
    with a first-run screen. No-op once any flag is set; never raises."""
    try:
        state = _read()
        if state.get("completed_at") is not None or state.get("dismissed_at") is not None:
            return
        if _bot_count():
            _write({"completed_at": time.time(), "seeded": True}, opened=False)
            log.info("onboarding: existing bots found, wizard marked completed")
    except Exception:  # noqa: BLE001
        log.exception("onboarding: seed check failed (continuing)")


@router.get("/api/onboarding")
def api_onboarding_get():
    return snapshot()


@router.get("/api/onboarding/models")
def api_onboarding_models():
    return {"models": local_model_picks()}


@router.post("/api/onboarding/complete")
def api_onboarding_complete(x_fused: str | None = Header(default=None)):
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    _write({"completed_at": time.time()})
    return snapshot()


@router.post("/api/onboarding/dismiss")
def api_onboarding_dismiss(x_fused: str | None = Header(default=None)):
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    _write({"dismissed_at": time.time()})
    return snapshot()


@router.post("/api/onboarding/opened")
def api_onboarding_opened(x_fused: str | None = Header(default=None)):
    """The wizard is on screen. Writes nothing but the `opened_at` stamp —
    the auto-show's "never opened" leg, from a visit that may otherwise leave
    no other mark (opened, looked, pressed Back)."""
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    _write({})
    return snapshot()


@router.post("/api/onboarding/stage")
def api_onboarding_stage(body: dict, x_fused: str | None = Header(default=None)):
    """A step reports its status — `{stage, status, meta?}`. The status is
    replaced; `meta` is MERGED over what the stage stored before."""
    guard = _require_fused(x_fused)
    if guard is not None:
        return guard
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be an object"}, status_code=400)
    stage = body.get("stage")
    status = body.get("status")
    meta = body.get("meta") or {}
    if stage not in STEPS:
        return JSONResponse({"error": f"unknown stage {stage!r}"}, status_code=400)
    if status not in STATUSES:
        return JSONResponse({"error": f"unknown status {status!r}"}, status_code=400)
    if not isinstance(meta, dict):
        return JSONResponse({"error": "meta must be an object"}, status_code=400)
    try:
        if len(json.dumps(meta)) > META_MAX_BYTES:
            return JSONResponse({"error": f"meta over {META_MAX_BYTES} bytes"}, status_code=400)
    except (TypeError, ValueError):
        return JSONResponse({"error": "meta must be JSON"}, status_code=400)
    with _LOCK:
        state = _read()
        stages = dict(state["stages"]) if isinstance(state.get("stages"), dict) else {}
        prev = stages.get(stage) if isinstance(stages.get(stage), dict) else {}
        prev_meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
        stages[stage] = {"status": status, "meta": {**prev_meta, **meta}, "updated_at": time.time()}
        _write({"stages": stages})
    return snapshot()
