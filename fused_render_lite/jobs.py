"""In-memory job rows behind ``fused.trackJob`` / ``fused.watchJob``.

A page (or a worker it spawned) reports progress with ``POST /api/jobs``
``{id, title, kind, unit, state, done, total, detail, message, cancellable}``
and reads it back with ``GET /api/jobs``. ``POST /api/jobs/<id>/cancel``
flips ``cancel_requested``; honouring it is the reporter's job. Rows live in
this process only (lite has no shell to show them in — they exist so a
page can follow work across reloads and so a worker can be told to stop).
Terminal rows are dropped after ``TERMINAL_TTL_S``.
"""
from __future__ import annotations

import threading
import time

STATES = ("running", "waiting", "done", "error", "cancelled")
TERMINAL = ("done", "error", "cancelled")
TERMINAL_TTL_S = 600.0
MAX_JOBS = 500

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class JobError(ValueError):
    pass


def _gc_locked(now: float) -> None:
    dead = [k for k, j in _jobs.items()
            if j["state"] in TERMINAL and now - j["updated"] > TERMINAL_TTL_S]
    for k in dead:
        del _jobs[k]
    if len(_jobs) > MAX_JOBS:
        for k in sorted(_jobs, key=lambda k: _jobs[k]["updated"])[: len(_jobs) - MAX_JOBS]:
            del _jobs[k]


def upsert(body: dict) -> dict:
    if not isinstance(body, dict):
        raise JobError("job report must be a JSON object")
    job_id = body.get("id")
    if not isinstance(job_id, str) or not job_id or len(job_id) > 200:
        raise JobError("'id' must be a non-empty string")
    state = body.get("state")
    if state is not None and state not in STATES:
        raise JobError("'state' must be one of: " + ", ".join(STATES))
    now = time.time()
    with _lock:
        _gc_locked(now)
        job = _jobs.get(job_id)
        if job is None:
            job = {"id": job_id, "title": "Working…", "detail": "", "kind": "task", "unit": "",
                   "done": None, "total": None, "cancellable": False, "cancel_requested": False,
                   "state": "running", "message": None, "created": now, "updated": now}
            _jobs[job_id] = job
        for key in ("title", "detail", "kind", "unit", "message"):
            if key in body and (body[key] is None or isinstance(body[key], str)):
                job[key] = body[key] if body[key] is not None else job[key]
        for key in ("done", "total"):
            if key in body:
                v = body[key]
                job[key] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if "cancellable" in body:
            job["cancellable"] = bool(body["cancellable"])
        if state is not None:
            job["state"] = state
        job["updated"] = now
        return dict(job)


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def list_all() -> list[dict]:
    with _lock:
        _gc_locked(time.time())
        return [dict(j) for j in sorted(_jobs.values(), key=lambda j: j["created"])]


def cancel(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        job["cancel_requested"] = True
        job["updated"] = time.time()
        return dict(job)


def dismiss(job_id: str) -> bool:
    with _lock:
        return _jobs.pop(job_id, None) is not None


def clear() -> int:
    with _lock:
        gone = [k for k, j in _jobs.items() if j["state"] in TERMINAL]
        for k in gone:
            del _jobs[k]
        return len(gone)
