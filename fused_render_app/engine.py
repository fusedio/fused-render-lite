"""``fused.runPython`` through the fused execution engine, when installed.

Render App used to have no engine of its own: ``_child.py`` (a stdlib worker
spawned per call in the app's venv) ran every script, and this module only
stubbed the one backend attribute the copied install loader (``envinstall``)
reads. This is fused-render's posture (its D69): the ``fused`` package is
**optional**. When ``fused.agent_core.backends.local.python_compute`` is
importable, ``env.run_python`` runs code through ``LocalPythonComputeBackend``
-- a fresh subprocess per call in a temp exec dir, params delivered via
``_params.json``; when it is not, the built-in worker runs unchanged.
``fused_available()`` is the probe, ``active()`` the per-process choice, and
``FUSED_RENDER_APP_ENGINE=child|fused`` forces one (tests, and a way out of a
broken install without reinstalling).

Two things do NOT change with the engine:

  * **Which interpreter a script gets.** Render App always has a venv for the
    app -- its own (``pyproject.toml``) or the shared legacy set -- and
    ``env.run_python`` hands that interpreter to the backend through the
    documented subclass contract ``_execute_sync(interpreter=...)``. The public
    ``execute(requirements=...)`` is never used: it would let the backend build
    a venv of its own from a requirements list and bypass the legacy set.
  * **The code contract.** A bare ``main(**params)``, bound by ``_binding.py``
    exactly as ``_child.py`` binds it (the binding source is embedded in the
    generated wrapper because the child cannot import this package -- the
    backend strips PYTHONPATH). The fused contract's ``@fused.udf`` /
    ``result = ...`` forms are accepted too, as in fused-render, so a script
    written for hosted fused runs here unchanged.

The wire shape returned is ``_child.py``'s ``{ok, result, error: {type,
message, traceback}, stdout}`` plus additive ``stderr`` / ``duration_ms``, so
runtime.js consumes one shape whichever engine ran the code.

Cost of the engine, measured on fused 2.9.3b9 (STATUS.md has the sizes):
importing the backend takes several seconds cold and loads pandas / numpy /
aiohttp / pydantic into the server process, so ``get_backend()`` is a lazy
singleton and ``warm()`` lets the server pay that on a background thread at
startup rather than on the first ``runPython``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import traceback

logger = logging.getLogger(__name__)

#: `child` forces the built-in worker, `fused` forces the engine (and fails
#: loudly when it is not importable); unset picks the engine when available.
ENGINE_ENV = "FUSED_RENDER_APP_ENGINE"
_ENGINE_CHOICES = ("child", "fused")

# A traceback frame header: `  File "<path>", line N[, in func]`. SyntaxError
# frames have no `, in func` part.
_FRAME_LINE = re.compile(
    r'^  File "(?P<file>[^"]*)", line (?P<line>\d+)(?P<rest>, in (?P<func>\S+))?'
)

_APP_PYTHON_ENV = "FUSED_RENDER_APP_PYTHON"
_STRIPPED = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONSTARTUP", "VIRTUAL_ENV")
_app_interpreter_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Availability / choice
# ---------------------------------------------------------------------------

class _NoBackend:
    """What the install loader reads when the fused package is absent: the
    base interpreter venvs are built on. fused-render's backend was constructed
    with `envinstall.script_python()`, so answer the same -- None for "this
    interpreter" when it already runs 3.12 (the packaged app's bundled python
    included), else a uv-managed 3.12."""

    @property
    def _python_executable(self):
        from fused_render_app import envinstall

        return envinstall.script_python()


def available() -> bool:
    """The install loader works with or without the fused package (it reads one
    attribute, which `_NoBackend` answers), so the loader-facing probe is always
    true. `fused_available()` is the engine-facing one."""
    return True


def fused_available() -> bool:
    """True iff the fused execution backend is importable in THIS process."""
    try:
        from fused.agent_core.backends.local import python_compute  # noqa: F401
    except ImportError:
        return False
    except Exception:  # noqa: BLE001 -- a half-installed fused is "not available"
        logger.exception("importing the fused backend failed")
        return False
    return True


def forced_override() -> str | None:
    """`child` / `fused` from the environment, or None. An unknown value is
    ignored with a warning rather than honoured as either."""
    value = (os.environ.get(ENGINE_ENV) or "").strip().lower()
    if not value:
        return None
    if value not in _ENGINE_CHOICES:
        logger.warning("%s=%r is not one of %s; ignoring", ENGINE_ENV, value, _ENGINE_CHOICES)
        return None
    return value


def active() -> bool:
    """Whether `env.run_python` should route through the fused engine."""
    forced = forced_override()
    if forced == "child":
        return False
    if forced == "fused":
        if not fused_available():
            raise RuntimeError(
                f"{ENGINE_ENV}=fused but the `fused` package is not importable; "
                "install it with `pip install 'fused-render-app[fused]'`"
            )
        return True
    return fused_available()


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------

_backend = None
_backend_lock = threading.Lock()


def get_backend():
    """Lazy singleton. The real backend when fused is importable, else the stub
    the install loader can still read its one attribute off.

    `python_executable` is `envinstall.script_python()` -- passed HERE, at the
    one place the backend is constructed, because `envinstall._python_executable()`
    reads the attribute straight back off this instance: the resolution and the
    venv key it feeds are one value with one source. `timeout_seconds` is the
    same 600 s cap `_child.py` runs under.
    """
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        if not fused_available():
            _backend = _NoBackend()
            return _backend
        from fused.agent_core.backends.local.python_compute import LocalPythonComputeBackend

        from fused_render_app import envinstall
        from fused_render_app.env import RUN_TIMEOUT_S

        # cache_storage=None disables result caching explicitly: fresh execution
        # every call. It is the upstream default today; don't rely on that.
        _backend = LocalPythonComputeBackend(
            timeout_seconds=int(RUN_TIMEOUT_S),
            cache_storage=None,
            python_executable=envinstall.script_python(),
        )
        return _backend


def reset_backend() -> None:
    """Drop the singleton so the next `get_backend()` re-resolves its base
    interpreter. `envinstall.reset_script_python_cache()` calls this, because a
    backend constructed with a stale `python_executable` would key venvs
    differently from the loader that builds them."""
    global _backend
    with _backend_lock:
        _backend = None


def warm() -> None:
    """Import and construct the backend off the request path. Safe to call when
    fused is absent (a no-op) and cheap to call twice."""
    try:
        get_backend()
    except Exception:  # noqa: BLE001 -- the first runPython reports it properly
        logger.exception("warming the fused engine failed")


# ---------------------------------------------------------------------------
# Interpreter helpers the loader shares (unchanged from the stub era)
# ---------------------------------------------------------------------------

def _child_env() -> dict:
    """What a child interpreter gets: this environment minus the variables that
    would point it at OUR runtime (the same set Render App's env.clean_env strips)."""
    return {k: v for k, v in os.environ.items() if k not in _STRIPPED}


def app_satisfies(requirements: list) -> bool:
    """Render App's own interpreter never runs project code, so it satisfies
    nothing; every declaring folder gets a venv. Bundling the fused package puts
    pandas/duckdb next to the server, and this stays False on purpose: user code
    keeps running in its own venv."""
    return False


def reset_app_interpreter_cache() -> None:
    return None


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

def _binding_source() -> str:
    """The text of `fused_render_app/_binding.py`, for embedding into the
    wrapper. Read through importlib.resources rather than `__file__` so it
    still works inside the py2app bundle."""
    from importlib.resources import files

    return files("fused_render_app").joinpath("_binding.py").read_text(encoding="utf-8")


def build_code(user_code: str, script_dir: str, script_path: str = "script") -> str:
    """Wrap user code so it runs exactly as `_child.py` runs it, inside the
    backend's exec dir.

    Order matters and mirrors the worker: read `_params.json` from the exec cwd
    FIRST (the backend writes it there), then `chdir` to the script's dir and
    put it at `sys.path[0]` (relative data paths and sibling imports resolve
    next to the .py, module-level code included), then exec the user's source
    as **its own compile unit under its real filename** so every traceback
    frame carries the real file and exact line.

    `__file__` / `__name__` are set because `exec(compile(...))` sets neither,
    while the worker's `spec_from_file_location` does; `__name__` is
    `"__fused_module__"` under both, so `if __name__ == "__main__":` blocks stay
    dormant.

    The epilogue then resolves the entrypoint, in this order:

      * a registered `@fused.udf` function (the hosted fused contract; inert
        locally, where the real wheel registers nothing);
      * a bare `main()`, called with `_binding.py`'s own coercion -- `main()`
        wins even if the module also assigned `result`, as the worker always
        calls `main` and overwrites it;
      * a module-level `result`, left untouched;
      * otherwise the worker's "does not define a callable 'main'" error,
        extended with the fused-contract alternatives.
    """
    binding_source = _binding_source()
    preamble = (
        "import json as _fused_json, os as _fused_os, sys as _fused_sys\n"
        "_fused_params = {}\n"
        "_fused_pf = _fused_os.path.join(_fused_os.getcwd(), '_params.json')\n"
        "if _fused_os.path.exists(_fused_pf):\n"
        "    with open(_fused_pf) as _fused_f:\n"
        "        _fused_params = _fused_json.load(_fused_f) or {}\n"
        f"_fused_os.chdir({script_dir!r})\n"
        f"_fused_sys.path.insert(0, {script_dir!r})\n"
        f"__file__ = {script_path!r}\n"
        '__name__ = "__fused_module__"\n'
        f"exec(compile({user_code!r}, {script_path!r}, 'exec'), globals())\n"
    )
    epilogue = f"""
try:
    import fused as _fused_shim
    _fused_udfs = getattr(_fused_shim, "_registered_udfs", None)
except ImportError:
    _fused_udfs = None
if _fused_udfs:
    _fused_udf = _fused_udfs[-1]
    _fused_inner = _fused_udf._fn
    def _fused_chdir_call(*_a, **_k):
        _fused_os.chdir({script_dir!r})
        return _fused_inner(*_a, **_k)
    _fused_udf._fn = _fused_chdir_call
else:
    # `_binding.py`'s REAL source, exec'd into its own namespace: the user's
    # module shares these globals and `coerce` / `bind_params` are names a
    # script could define itself. `__name__ = "__main__"` so ParamError's
    # module is one traceback omits from the final line, which `_split_error`
    # turns into the same `error.type` the worker reports.
    _fused_binding_ns = {{"__name__": "__main__"}}
    exec(compile({binding_source!r}, "<fused_render_app/_binding.py>", "exec"), _fused_binding_ns)
    _fused_bind = _fused_binding_ns["bind_params"]

    def _fused_run_main():
        _fn = globals().get("main")
        if not callable(_fn):
            if "result" in globals():
                return globals()["result"]
            raise AttributeError(
                _fused_os.path.basename({script_path!r})
                + " does not define a callable 'main' function, a "
                "@fused.udf-decorated function, or a 'result' variable"
            )
        _out = _fn(**_fused_bind(_fn, _fused_params))
        try:
            _fused_json.dumps(_out)
        except (TypeError, ValueError):
            raise TypeError(
                "main() returned " + type(_out).__name__ + ", which is not "
                "JSON-serializable; return dict/list/str/number/bool/None "
                "(e.g. df.to_dict('records'))"
            ) from None
        return _out

    result = _fused_run_main()
"""
    return preamble + epilogue


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------

def _clean_error(error_text: str, script_path: str) -> str:
    """Drop plumbing frames so a traceback starts at the user's real file.

    User frames already carry the script's path (build_code). What remains is
    noise around them: backend internals (`_runner.py`) above the first user
    frame, and the `<lambda_exec>` wrapper/epilogue frames. Text with no
    `<lambda_exec>` frame (timeouts, backend messages) passes through unchanged.
    """
    if '  File "<lambda_exec>"' not in error_text:
        return error_text
    try:
        out = []
        seen_user_frame = False
        dropping = False
        for line in error_text.splitlines():
            m = _FRAME_LINE.match(line)
            if m:
                if m.group("file") == script_path:
                    seen_user_frame = True
                    dropping = False
                    out.append(line)
                    continue
                dropping = m.group("file") == "<lambda_exec>" or not seen_user_frame
                if not dropping:
                    out.append(line)
                continue
            if line.startswith("    ") and dropping:
                continue
            out.append(line)
        return "\n".join(out) + ("\n" if error_text.endswith("\n") else "")
    except (ValueError, AttributeError):
        return error_text


def _split_error(cleaned: str) -> tuple[str, str]:
    """(type, message) from a traceback's final `SomeError: message` line.

    The backend's own timeout text ("Execution timed out after Ns") maps to the
    worker's `TimeoutError`, so a page sees one type for one condition. Anything
    else not in the standard form falls back to ("Error", <last line>).
    """
    if cleaned.lstrip().startswith("Execution timed out"):
        return "TimeoutError", cleaned.strip()
    for line in reversed(cleaned.splitlines()):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.]*)\s*:\s*(.*)$", line)
        if m and (m.group(1).endswith("Error") or m.group(1).endswith("Exception")):
            return m.group(1), m.group(2)
        return "Error", line
    return "Error", cleaned.strip() or "execution failed"


def _error_dict(err_type: str, message: str, tb: str = "") -> dict:
    return {
        "ok": False,
        "error": {"type": err_type, "message": message, "traceback": tb},
        "stdout": "",
    }


# ---------------------------------------------------------------------------
# Entry point (sync: the server is a threaded http.server)
# ---------------------------------------------------------------------------

def run_python(path: str, params: dict, interpreter: str) -> dict:
    """Run `path`'s `main(**params)` on `interpreter` through the backend.

    The caller (`env.run_python`) has already made sure the app's venv exists
    and picked `interpreter` from it; this function never resolves one. The
    timeout is the backend's (`RUN_TIMEOUT_S`, fixed at construction).
    """
    abs_path = os.path.abspath(path)
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            user_code = f.read()
    except OSError as e:
        return _error_dict("OSError", f"cannot read {path}: {e}")

    try:
        backend = get_backend()
        if not hasattr(backend, "_execute_sync"):
            raise RuntimeError(
                "this fused build has no LocalPythonComputeBackend._execute_sync, so "
                "a script cannot be run on the app's own venv interpreter. Refusing to "
                "run it in a backend-built venv instead (the app's dependencies would "
                "be missing). Pin a fused version that provides `_execute_sync`."
            )
        code = build_code(user_code, os.path.dirname(abs_path), abs_path)
        # Keywords, not positionals: `_execute_sync` takes ten parameters and a
        # reordering upstream would silently pass `interpreter` as something
        # else. `requirements` is deliberately omitted -- the interpreter wins.
        r = backend._execute_sync(
            code=code,
            input_files={"_params.json": json.dumps(params or {}).encode()},
            interpreter=interpreter,
        )
    except Exception:  # noqa: BLE001 -- the engine itself, not the user's script
        logger.exception("fused engine execute failed for %s", path)
        return _error_dict(
            "EngineError",
            f"Render App internal error (not your script) while running {path}",
            traceback.format_exc(),
        )

    if r.error:
        cleaned = _clean_error(r.error, abs_path)
        err_type, message = _split_error(cleaned)
        return {
            "ok": False,
            "error": {"type": err_type, "message": message, "traceback": cleaned},
            "stdout": r.stdout or "",
            "stderr": r.stderr or "",
            "duration_ms": r.duration_ms,
        }

    # The backend hands return_value back JSON-encoded; decode so the wire
    # carries real values. NaN/Infinity decode to their literal names rather
    # than floats the response serializer would re-emit as bare NaN (which the
    # browser's JSON.parse rejects).
    return_value = r.return_value
    if isinstance(return_value, str) and not (
        getattr(r, "response", None) and getattr(r.response, "body_encoding", None) == "base64"
    ):
        try:
            return_value = json.loads(return_value, parse_constant=lambda c: c)
        except ValueError:
            pass
    return {
        "ok": True,
        "result": return_value,
        "stdout": r.stdout or "",
        "stderr": r.stderr or "",
        "duration_ms": r.duration_ms,
    }
