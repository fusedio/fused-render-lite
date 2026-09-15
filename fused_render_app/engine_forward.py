"""Forward one server request to a managed child engine (fused.daemon).

The layer between `server.py`'s `/api/engines/<id>/proxy/<path>` route and
the child processes `engine_host` supervises: it owns the per-child
keep-alive connection pool, the heal-on-failure retry and the per-call
budget. Ported from fused-render's async `engine_forward.py` onto Render App's
threaded stdlib server: the pool, the at-most-once rule, the 504-never-heals
rule and the heal-then-retry-once flow are kept exactly; what is gone is the
`asyncio.wait` race against the browser hanging up (uvicorn's
`request.receive()` has no stdlib equivalent), so an abandoned request here
simply runs to completion on its thread instead of answering 204.
"""
from __future__ import annotations

import contextlib
import http.client
import socket
import threading
from urllib.parse import quote

from fused_render_app import engine_host

#: A proxied POST can legitimately take minutes; a GET should heal a wedged
#: child sooner. Connection-level backstops, not the per-call budget.
POST_TIMEOUT_S = 300.0
GET_TIMEOUT_S = 120.0

# What the daemon's responses carry that the page needs.
_PROXY_HEADERS = ("content-type", "cache-control")

#: The call outran its per-call budget. Unlike None, the child is alive and
#: still running the call (a warm worker does not kill its own thread), so
#: this is NOT a heal trigger and the call is never retried — it becomes a 504.
_TIMEOUT = object()

# Per-child idle-connection pool. Children speak HTTP/1.1 keep-alive, so a
# connection is reused across calls instead of a fresh TCP connect+teardown
# per request. Keyed by child.uid (unique per spawn); _drop_pool, registered
# as an engine_host terminate hook, closes a dead child's connections so they
# don't leak on restart/idle-retire. A connection is returned to the pool only
# after a clean full read; a hung-up or errored one is discarded.
_POOL_MAX = 6
_idle_pools: dict[str, list] = {}
_pool_lock = threading.Lock()


def _checkout(uid: str):
    with _pool_lock:
        pool = _idle_pools.get(uid)
        return pool.pop() if pool else None


def _checkin(uid: str, conn) -> None:
    with _pool_lock:
        pool = _idle_pools.setdefault(uid, [])
        if len(pool) < _POOL_MAX:
            pool.append(conn)
            return
    conn.close()  # pool full: don't hoard connections


def _drop_pool(child) -> None:
    with _pool_lock:
        pool = _idle_pools.pop(child.uid, None)
    for conn in pool or ():
        with contextlib.suppress(Exception):
            conn.close()


engine_host.register_terminate_hook(_drop_pool)


class ProxyResult:
    """What the Handler sends back: status, body bytes, forwarded headers."""

    __slots__ = ("status", "body", "headers")

    def __init__(self, status: int, body: bytes, headers: dict | None = None):
        self.status = status
        self.body = body
        self.headers = headers or {}


def _error(message: str, status: int) -> ProxyResult:
    import json

    return ProxyResult(status, json.dumps({"error": message}).encode("utf-8"),
                       {"Content-Type": "application/json"})


def _proxy(child, method: str, path: str, body: bytes, req_headers: dict,
           call_timeout: float | None = None, at_most_once: bool = False):
    """Forward one request to the child. None when the request provably never
    reached a handler (the healing trigger); an HTTP error from a live child
    is an answer; `_TIMEOUT` when `call_timeout` elapsed.

    `at_most_once` marks a call that runs user `main()` with side effects (the
    warm /call): it never rides a pooled keep-alive, and once the request is
    on the wire a failure is surfaced rather than retried, so `main()` runs at
    most once. Idempotent traffic pools and retries freely.

    `call_timeout` is the per-call budget (the shipped worker's /call gets
    engine_host.CALL_TIMEOUT_S). Applied as the socket timeout in place of the
    connection-level one; when it elapses the child is left running — it does
    not kill its own thread, and concurrent calls on it must survive — so this
    neither heals nor retries."""
    timeout = POST_TIMEOUT_S if method == "POST" else GET_TIMEOUT_S
    if call_timeout is not None:
        timeout = call_timeout
    separator = "&" if "?" in path else "?"
    target = f"{path}{separator}t={quote(child.token, safe='')}"
    idempotent = method in ("GET", "HEAD")

    for reused in ((False,) if at_most_once else (True, False)):
        connection = _checkout(child.uid) if reused else None
        if connection is None:
            if reused:
                continue  # nothing pooled — fall through to the fresh attempt
            connection = http.client.HTTPConnection("127.0.0.1", child.port, timeout=timeout)
        if connection.sock is not None:
            connection.sock.settimeout(timeout)  # a reused conn may carry another

        sent = False
        try:
            headers = {}
            rng = req_headers.get("range")
            if rng:
                headers["Range"] = rng
            content_type = req_headers.get("content-type")
            if content_type:
                headers["Content-Type"] = content_type
            payload = body if method == "POST" else None
            connection.request(method, target, body=payload, headers=headers)
            sent = True  # on the wire: a failure past here may have run main()
            answer = connection.getresponse()
            payload_out = answer.read()
        except socket.timeout:
            # Sever the socket so the child sees the hangup; leave the child.
            sock = connection.sock
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
            connection.close()
            if call_timeout is not None:
                return _TIMEOUT
            if at_most_once and sent:
                return _error(f"the {child.engine_id} worker dropped the call after it "
                              "was sent; not retried, to avoid re-running main()", 502)
            if reused and idempotent:
                continue
            return None
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            # An at-most-once call whose request was already on the wire may
            # have run main(): surface it rather than re-running a
            # side-effecting call. (A failure before it was sent means main()
            # never ran — fall through to a safe retry.)
            if at_most_once and sent:
                return _error(f"the {child.engine_id} worker dropped the call after it "
                              "was sent; not retried, to avoid re-running main()", 502)
            # A pooled keep-alive the child dropped after its idle timeout
            # raises RemoteDisconnected *before* the request is handled —
            # main() never ran — so retry it on a fresh connection to the
            # same, still-warm child rather than declaring the child gone.
            retryable = idempotent or isinstance(exc, http.client.RemoteDisconnected)
            if reused and retryable:
                continue  # stale pooled connection — retry with a fresh one
            return None
        if at_most_once:
            connection.close()  # never pooled: a side-effecting call rides fresh
        else:
            _checkin(child.uid, connection)  # clean read: keep it warm for next time
        out = {k: v for k, v in answer.headers.items() if k.lower() in _PROXY_HEADERS}
        return ProxyResult(answer.status, payload_out, out)
    return None


def forward(engine_id: str, method: str, path: str, body: bytes, req_headers: dict,
            call_timeout: float | None = None, at_most_once: bool = False) -> ProxyResult:
    child = engine_host.current(engine_id)
    if child is None:
        return _error(f"the {engine_id} engine is not running; call fused.daemon.start()", 409)
    response = _proxy(child, method, path, body, req_headers, call_timeout, at_most_once)
    if response is _TIMEOUT:
        # The call outran its budget on a reachable child. Don't heal or retry
        # (that would kill the still-running worker and re-run its main).
        return _error(f"the {engine_id} call exceeded its {call_timeout:g}s budget", 504)
    if response is None:
        try:
            child = engine_host.restart(engine_id, child)
        except engine_host.EngineError:
            # Torn down between current() and the restart (quit cleared it).
            return _error(f"the {engine_id} engine is not running; call fused.daemon.start()",
                          409)
        response = _proxy(child, method, path, body, req_headers, call_timeout, at_most_once)
    if response is _TIMEOUT:
        return _error(f"the {engine_id} call exceeded its {call_timeout:g}s budget", 504)
    if response is None:
        return _error(f"the {engine_id} engine did not answer, even after a restart", 502)
    return response
