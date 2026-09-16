"""`jobs.set_transition_hook` — the registry's one outbound signal.

Pure-Python tests against the module, no server. The hook is process-wide
(like the registry itself), so the autouse fixture clears it on BOTH sides
of every test: a test that installs one and then fails must not leave it
firing into another test file's `upsert` calls.
"""
from __future__ import annotations

import logging
import threading

import pytest

from fused_render_app import jobs


@pytest.fixture(autouse=True)
def clean_registry():
    jobs.reset()
    jobs.set_transition_hook(None)
    yield
    jobs.set_transition_hook(None)
    jobs.reset()


def _recorder():
    calls: list[tuple[dict | None, dict]] = []

    def hook(prev, after):
        calls.append((prev, after))

    return calls, hook


def test_fires_on_creation_with_prev_none():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    rec = jobs.upsert({"id": "sys:ai-model:x", "title": "Model", "state": "running"},
                      server=True)
    assert len(calls) == 1
    prev, after = calls[0]
    assert prev is None
    assert after["state"] == "running"
    assert after is rec  # the very dict upsert returns


def test_creation_in_terminal_state_still_fires():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.upsert({"id": "p1", "title": "T", "state": "error"})
    assert len(calls) == 1
    assert calls[0][0] is None
    assert calls[0][1]["state"] == "error"


def test_progress_tick_same_state_does_not_fire():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.upsert({"id": "p1", "title": "T", "state": "running"})
    calls.clear()
    jobs.upsert({"id": "p1", "done": 5, "total": 10, "detail": "step 2"})
    jobs.upsert({"id": "p1", "state": "running", "done": 6})
    assert calls == []


def test_running_to_done_fires_with_prev_and_after():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.upsert({"id": "p1", "title": "T", "state": "running"})
    calls.clear()
    rec = jobs.upsert({"id": "p1", "state": "done"})
    assert len(calls) == 1
    prev, after = calls[0]
    assert prev["state"] == "running"
    assert prev["id"] == "p1"
    assert after["state"] == "done"
    assert after is rec


def test_done_to_running_reopen_fires():
    # The model re-download case: `sys:ai-model:<repo>` is reused for a
    # later download after an earlier one finished on the same row.
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jid = "sys:ai-model:acme--thing"
    jobs.upsert({"id": jid, "title": "Model", "state": "done", "tier": "silent"},
                server=True)
    calls.clear()
    jobs.upsert({"id": jid, "state": "running", "tier": "trail"}, server=True)
    assert len(calls) == 1
    prev, after = calls[0]
    assert prev["state"] == "done"
    assert prev["tier"] == "silent"
    assert after["state"] == "running"
    assert after["tier"] == "trail"


def test_running_to_waiting_fires():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jid = "sys:env-install:abc"
    jobs.upsert({"id": jid, "title": "Install", "state": "running"}, server=True)
    calls.clear()
    jobs.upsert({"id": jid, "state": "waiting", "message": "Install anyway?"},
                server=True)
    assert len(calls) == 1
    assert calls[0][0]["state"] == "running"
    assert calls[0][1]["state"] == "waiting"


def test_late_tick_on_dismissed_id_does_not_fire():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.upsert({"id": "p1", "title": "T", "state": "running"})
    jobs.upsert({"id": "p1", "state": "done"})
    assert jobs.dismiss("p1")
    calls.clear()
    rec = jobs.upsert({"id": "p1", "done": 99})
    assert calls == []
    assert rec["id"] == "p1"  # answered as if stored, per the dismissed path
    # ...and also nothing for a terminal late tick.
    jobs.upsert({"id": "p1", "state": "done"})
    assert calls == []


def test_page_rows_fire_too():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.upsert({"id": "page-row", "title": "T", "state": "running"}, page="/x.html")
    assert len(calls) == 1
    assert calls[0][1]["owner"] == "page"


def test_raising_hook_does_not_break_upsert(caplog):
    def hook(prev, after):
        raise RuntimeError("banner exploded")

    jobs.set_transition_hook(hook)
    with caplog.at_level(logging.ERROR, logger="fused_render_app.jobs"):
        rec = jobs.upsert({"id": "p1", "title": "T", "state": "running"})
    assert rec["id"] == "p1"
    assert rec["state"] == "running"
    assert jobs.list_jobs()[0]["id"] == "p1"
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert errors, "hook failure must be logged with a traceback"


def test_hook_runs_outside_lock():
    # With the hook called under `_lock` (a plain, non-reentrant Lock), a
    # `list_jobs()` inside it would deadlock forever. Run in a daemon thread
    # and bound the wait so a regression fails instead of hanging pytest.
    seen: list[list[dict]] = []

    def hook(prev, after):
        seen.append(jobs.list_jobs())

    jobs.set_transition_hook(hook)
    t = threading.Thread(
        target=jobs.upsert,
        args=({"id": "p1", "title": "T", "state": "running"},),
        daemon=True,
    )
    t.start()
    t.join(timeout=2)
    assert not t.is_alive(), "upsert deadlocked: hook fired under _lock"
    assert len(seen) == 1
    assert seen[0][0]["id"] == "p1"


def test_reset_keeps_hook_installed():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.reset()
    jobs.upsert({"id": "p1", "title": "T", "state": "running"})
    assert len(calls) == 1


def test_set_none_clears_hook():
    calls, hook = _recorder()
    jobs.set_transition_hook(hook)
    jobs.set_transition_hook(None)
    jobs.upsert({"id": "p1", "title": "T", "state": "running"})
    assert calls == []
