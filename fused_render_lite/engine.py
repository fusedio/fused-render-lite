"""The fused execution engine is not part of fused-render-lite. The copied
install loader (`envinstall`) reads the base interpreter off the engine's
backend; answering None there means "resolve it yourself" — a uv-managed
3.12, or this interpreter — which is what lite wants."""
from __future__ import annotations


class _NoBackend:
    @property
    def _python_executable(self):
        """The base interpreter venvs are built on — fused-render's backend was
        constructed with `envinstall.script_python()`, so answer the same:
        a uv-managed 3.12 (always, in the packaged app), or None for "ours" in
        a dev checkout already on 3.12. Never the py2app stub."""
        from fused_render_lite import envinstall

        return envinstall.script_python()


def available() -> bool:
    return True  # the loader works without the engine; see module docstring


def get_backend() -> _NoBackend:
    return _NoBackend()


import os as _os
import threading as _threading

_app_interpreter_lock = _threading.Lock()
_APP_PYTHON_ENV = "FUSED_RENDER_APP_PYTHON"
_STRIPPED = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "PYTHONSTARTUP", "VIRTUAL_ENV")


def _child_env() -> dict:
    """What a child interpreter gets: this environment minus the variables that
    would point it at OUR runtime (the same set lite's env.clean_env strips)."""
    return {k: v for k, v in _os.environ.items() if k not in _STRIPPED}


def app_satisfies(requirements: list) -> bool:
    """Lite's own interpreter is stdlib-only and never runs project code, so it
    satisfies nothing; every declaring folder gets a venv."""
    return False


def reset_app_interpreter_cache() -> None:
    return None
