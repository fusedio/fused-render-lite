#!/usr/bin/env python3
"""A stand-in for the ``claude`` CLI: speaks just enough ``stream-json``.

Reads the prompt from stdin, echoes the flags it was given, and emits the
same event shapes the real CLI does (probed on claude 2.1.270): a couple of
``stream_event`` text deltas, then one terminal ``result``. Behaviour knobs
via the prompt text: ``FAIL`` -> is_error result, ``CRASH`` -> exit 3 with
stderr and no result, ``SLOW`` -> sleeps between deltas.
"""
import json
import os
import sys
import time


def main() -> int:
    args = sys.argv[1:]
    prompt = sys.stdin.read()
    model = args[args.index("--model") + 1] if "--model" in args else "?"
    effort = args[args.index("--effort") + 1] if "--effort" in args else "?"
    sp = ""
    if "--system-prompt-file" in args:
        with open(args[args.index("--system-prompt-file") + 1], encoding="utf-8") as f:
            sp = f.read()
    out = sys.stdout

    def emit(obj):
        out.write(json.dumps(obj) + "\n")
        out.flush()

    emit({"type": "system", "subtype": "init", "model": model})
    if "CRASH" in prompt:
        sys.stderr.write("boom: simulated crash\n")
        return 3
    pieces = ["Hello", " from", " fake claude"]
    for p in pieces:
        if "SLOW" in prompt:
            time.sleep(0.2)
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
                                                "delta": {"type": "text_delta", "text": p}}})
    text = "".join(pieces)
    meta = {"model": model, "effort": effort, "system_prompt": sp,
            "thinking_env": os.environ.get("MAX_THINKING_TOKENS"), "prompt": prompt}
    if "FAIL" in prompt:
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "result": "simulated failure", "duration_ms": 5})
        return 0
    emit({"type": "result", "subtype": "success", "is_error": False,
          "result": text + " " + json.dumps(meta), "duration_ms": 123,
          "usage": {"input_tokens": 7, "output_tokens": 3},
          "modelUsage": {model: {"outputTokens": 3}}, "session_id": "sess-1"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
