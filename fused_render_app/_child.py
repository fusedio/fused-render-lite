"""Worker-process entry point for ``fused.runPython``.

Reads a JSON request ``{"path": ..., "params": {...}}`` from stdin, imports
the target module, calls its ``main(**params)`` and prints ONE JSON result
line to stdout. Runs in its own process (the app's own venv interpreter) so
user code cannot take down the server; the parent enforces the timeout.

Stdlib only, and invoked as a standalone script — ``_binding.py`` beside it
resolves through sys.path[0]. User ``print()`` output is captured and
returned in the payload so it cannot corrupt the stdout protocol.
"""
import importlib.util
import io
import json
import os
import sys
import traceback

from _binding import bind_params


def run():
    req = json.load(sys.stdin)
    path = os.path.abspath(req["path"])
    params = req.get("params") or {}

    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured
    out = {"ok": False}
    try:
        module_dir = os.path.dirname(path)
        os.chdir(module_dir)  # relative data paths in user code resolve next to the .py
        sys.path.insert(0, module_dir)
        spec = importlib.util.spec_from_file_location("__fused_module__", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        fn = getattr(mod, "main", None)
        if not callable(fn):
            raise AttributeError(
                f"{os.path.basename(path)} does not define a callable 'main' function"
            )

        result = fn(**bind_params(fn, params))
        try:
            json.dumps(result)
        except (TypeError, ValueError):
            raise TypeError(
                f"main() returned {type(result).__name__}, which is not JSON-serializable; "
                "return dict/list/str/number/bool/None (e.g. df.to_dict('records'))"
            ) from None
        out = {"ok": True, "result": result}
    except BaseException as e:  # noqa: BLE001 — includes SystemExit from user code
        out = {
            "ok": False,
            "error": {
                "type": type(e).__name__,
                "message": str(e),
                "traceback": traceback.format_exc(),
            },
        }
    finally:
        sys.stdout = real_stdout
    out["stdout"] = captured.getvalue()
    print(json.dumps(out))


if __name__ == "__main__":
    run()
