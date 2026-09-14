"""``fused.ai.text`` on the Claude provider, through the ``claude`` CLI.

One ``claude -p`` process per request: the prompt goes in on stdin, events
come out as ``stream-json`` lines, the terminal ``result`` event carries the
answer and token usage. No local inference in this build — ``provider:
"local"`` / ``"apple"`` and every non-text verb answer ``unavailable``.

Options honoured: ``prompt``, ``model`` (short ids ``haiku``/``sonnet``/
``opus``/``fable`` or a ``claude-*`` id), ``systemPrompt``, ``effort``
(``low`` default, ``medium``, ``high``, ``xhigh``). ``temperature`` /
``maxTokens`` / ``topP`` are dropped with a ``warnings[]`` entry;
``history`` / ``raw`` / ``images`` are ``bad_request`` (they need a local
model). Streaming is NDJSON over chunked HTTP, not a socket.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

BIN_ENV = "FUSED_RENDER_LITE_CLAUDE_BIN"
DEFAULT_MODEL = "claude-haiku-4-5"
SHORT_MODEL_IDS = {
    "fable": "claude-fable-5",
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}
EFFORTS = ("low", "medium", "high", "xhigh")
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
TIMEOUT_S = 600.0
_MODEL_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_SAMPLING = ("temperature", "maxTokens", "topP")
TEXT_OPTIONS = frozenset({"prompt", "provider", "model", "systemPrompt", "effort", "history",
                          "raw", "images", "temperature", "maxTokens", "topP", "stream", "base"})

_POSIX_CANDIDATES = (
    "~/.claude/local/claude",
    "~/.local/bin/claude",
    "~/.bun/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
)


class AiError(Exception):
    """A typed rejection: ``type`` is one of the page-facing error types."""

    def __init__(self, type_: str, message: str, status: int = 502):
        super().__init__(message)
        self.type = type_
        self.status = status

    def payload(self) -> dict:
        return {"type": self.type, "message": str(self)}


def claude_bin() -> str | None:
    override = os.environ.get(BIN_ENV)
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else None
    found = shutil.which("claude")
    if found:
        return found
    if os.name != "nt":
        for cand in _POSIX_CANDIDATES:
            p = os.path.expanduser(cand)
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p
    return None


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]


def runtime() -> dict:
    """What ``fused.ai.models.list()`` / ``GET /api/ai/runtime`` answer."""
    path = claude_bin()
    return {
        "provider": "claude",
        "available": path is not None,
        "bin": path,
        "default": DEFAULT_MODEL,
        "models": [{"id": v, "alias": k, "capability": "text-generation"}
                   for k, v in SHORT_MODEL_IDS.items()],
        "local": False,
    }


def catalog() -> dict:
    path = claude_bin()
    reason = None if path else (
        f"claude CLI not found; install Claude Code or set {BIN_ENV} to its location")
    return {
        "capabilities": [{
            "capability": "text-generation",
            "available": path is not None,
            "reason": reason,
            "default": DEFAULT_MODEL,
            "provider": "claude",
            "models": [{"id": v, "alias": k, "downloaded": True, "loaded": True,
                        "recommended": k == "haiku", "size_gb": None,
                        "acceptsImage": False, "acceptsPaths": False}
                       for k, v in SHORT_MODEL_IDS.items()],
        }],
        "unsupported": ["text-to-image", "text-to-video",
                        "automatic-speech-recognition", "embeddings"],
        "ramGb": None,
        "lite": True,
    }


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _unsupported(setting: str, why: str) -> dict:
    return {"type": "unsupported-setting", "setting": setting,
            "message": f"'{setting}' is not supported by the Claude tier and was ignored: {why}"}


def validate(body: dict) -> tuple[dict, list[dict]]:
    """Normalise an /api/ai body -> ``(request, warnings)`` or raise AiError."""
    if not isinstance(body, dict):
        raise AiError("bad_request", "request body must be a JSON object", 400)
    unknown = sorted(k for k in body if k not in TEXT_OPTIONS)
    if unknown:
        raise AiError("bad_request",
                      ", ".join(repr(k) for k in unknown) + " is not an option of fused.ai.text; "
                      "accepted: " + ", ".join(sorted(TEXT_OPTIONS - {"stream", "base"})), 400)
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AiError("bad_request", "'prompt' must be a non-empty string", 400)

    provider = body.get("provider")
    if provider is not None and provider != "claude":
        if provider in ("local", "apple"):
            raise AiError("unavailable",
                          f"provider {provider!r} is not available on fused-render-lite: "
                          "no local inference in this build; drop 'provider' to use Claude", 409)
        raise AiError("bad_request", "'provider' must be 'claude' (or omitted)", 400)

    model = body.get("model")
    if model is None:
        model = DEFAULT_MODEL
    if not isinstance(model, str) or not model:
        raise AiError("bad_request", "'model' must be a non-empty string", 400)
    if "/" in model or model.lower().endswith(".gguf"):
        raise AiError("unavailable",
                      f"{model!r} is a local model id; fused-render-lite has no local "
                      "inference — use a Claude model ('haiku', 'sonnet', 'opus', 'fable')", 409)
    model = SHORT_MODEL_IDS.get(model, model)
    if not _MODEL_RE.match(model):
        raise AiError("bad_request", f"'model' {model!r} carries characters a model id cannot", 400)

    effort = body.get("effort")
    if effort is not None and effort not in EFFORTS:
        raise AiError("bad_request", "'effort' must be one of: " + ", ".join(EFFORTS), 400)

    system_prompt = body.get("systemPrompt")
    if system_prompt is not None and not isinstance(system_prompt, str):
        raise AiError("bad_request", "'systemPrompt' must be a string", 400)
    if not system_prompt:
        system_prompt = DEFAULT_SYSTEM_PROMPT

    stream = body.get("stream")
    if stream is not None and not isinstance(stream, bool):
        raise AiError("bad_request", "'stream' must be a boolean", 400)

    for name, why in (
        ("history", "it needs a local model that takes a conversation; this call goes to "
                    "Claude, which answers one prompt at a time"),
        ("raw", "a template-free prompt needs a local model"),
        ("images", "image input needs a local vision model"),
    ):
        if body.get(name) is not None:
            raise AiError("bad_request", f"'{name}' is not supported on the Claude tier: {why}", 400)

    warnings = [_unsupported(name, "the Claude CLI exposes 'effort' and no sampling knobs")
                for name in _SAMPLING if body.get(name) is not None]
    return {"prompt": prompt, "model": model, "effort": effort or "low",
            "system_prompt": system_prompt, "stream": bool(stream)}, warnings


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

def _cmd(bin_path: str, model: str, effort: str, sp_file: str) -> list[str]:
    return [bin_path, "-p",
            "--output-format", "stream-json", "--include-partial-messages", "--verbose",
            "--model", model, "--effort", effort,
            "--system-prompt-file", sp_file,
            "--tools=", "--setting-sources=", "--no-session-persistence"]


def _usage(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    i, o = raw.get("input_tokens"), raw.get("output_tokens")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (i, o)):
        return None
    return {"inputTokens": i, "outputTokens": o, "totalTokens": i + o}


def result_frame(text: str, model: str, warnings: list[dict], usage, seconds, request_id) -> dict:
    return {
        "text": text,
        "provider": "claude",
        "finishReason": "stop",
        "warnings": warnings,
        "usage": usage,
        "response": {"id": request_id, "modelId": model,
                     "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "providerMetadata": {"claude": {"seconds": seconds}},
    }


class Completion:
    """One claude run. ``events()`` yields text deltas; ``result`` is set at the end."""

    def __init__(self, request: dict, warnings: list[dict]):
        self.request = request
        self.warnings = warnings
        self.result: dict | None = None
        self._proc: subprocess.Popen | None = None
        self._sp_file: str | None = None

    def kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _cleanup(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdout:
                    self._proc.stdout.close()
                if self._proc.stderr:
                    self._proc.stderr.close()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                self.kill()
        if self._sp_file:
            try:
                os.unlink(self._sp_file)
            except OSError:
                pass

    def events(self) -> Iterator[str]:
        bin_path = claude_bin()
        if not bin_path:
            raise AiError("ai_unavailable",
                          f"claude CLI not found; install Claude Code or set {BIN_ENV} to its location")
        req = self.request
        fd, self._sp_file = tempfile.mkstemp(prefix="fused_lite_sp_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(req["system_prompt"])
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)  # a nested launch from inside Claude Code must still run
        if req["effort"] == "low":
            # haiku thinks by default in stream-json mode (measured: a one-word
            # answer spent ~40 thinking tokens); "low" means no thinking, and
            # this env var is the switch --effort alone does not flip.
            env["MAX_THINKING_TOKENS"] = "0"
        started = time.monotonic()
        try:
            try:
                self._proc = subprocess.Popen(
                    _cmd(bin_path, req["model"], req["effort"], self._sp_file),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=env, cwd=tempfile.gettempdir(), creationflags=_no_window())
            except OSError as exc:
                raise AiError("ai_unavailable", f"could not start the claude CLI: {exc}")
            proc = self._proc
            assert proc.stdin and proc.stdout and proc.stderr
            timer = threading.Timer(TIMEOUT_S, self.kill)
            timer.daemon = True
            timer.start()
            try:
                try:
                    proc.stdin.write(req["prompt"].encode("utf-8"))
                    proc.stdin.close()
                except OSError as exc:
                    raise AiError("ai_error", f"could not write to the claude CLI: {exc}")
                final = None
                for raw in proc.stdout:
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    kind = event.get("type")
                    if kind == "result":
                        final = event
                        break
                    if kind == "stream_event":
                        inner = event.get("event") or {}
                        if inner.get("type") == "content_block_delta":
                            delta = inner.get("delta") or {}
                            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                                yield delta["text"]
            finally:
                timer.cancel()
            if final is None:
                code = proc.poll()
                tail = proc.stderr.read().decode("utf-8", "replace").strip()[-500:]
                if time.monotonic() - started >= TIMEOUT_S:
                    raise AiError("timeout", f"timed out after {TIMEOUT_S:.0f}s", 504)
                raise AiError("ai_error", f"claude CLI exited with code {code}"
                              + (f": {tail}" if tail else ""))
            text = final.get("result")
            if final.get("is_error") or final.get("subtype") not in (None, "success") \
                    or not isinstance(text, str):
                raise AiError("ai_error", f"claude CLI reported an error: {str(text)[:500]}")
            used = req["model"]
            mu = final.get("modelUsage")
            if isinstance(mu, dict) and len(mu) == 1:
                used = next(iter(mu))
            seconds = final.get("duration_ms")
            self.result = result_frame(
                text, used, self.warnings, _usage(final.get("usage")),
                round(seconds / 1000, 3) if isinstance(seconds, (int, float)) else None,
                final.get("session_id") or final.get("uuid"))
        finally:
            self._cleanup()


def complete(body: dict, on_chunk: Callable[[str], None] | None = None) -> dict:
    """Validate + run to completion; returns the result frame or raises AiError."""
    request, warnings = validate(body)
    run = Completion(request, warnings)
    for piece in run.events():
        if on_chunk:
            on_chunk(piece)
    assert run.result is not None
    return run.result
