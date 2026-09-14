"""Per-app Python environments, built on demand with ``uv``.

Nothing is bundled. An app declares what it needs in its own
``pyproject.toml``; on open, ``uv sync`` builds a venv for it under
``~/.fused-render-lite/venvs/<key>`` (keyed by the app dir), streaming uv's
output to the open page. An app with no ``pyproject.toml`` runs on a
uv-managed CPython with the stdlib only.

``uv`` itself is found in this order: ``FUSED_RENDER_LITE_UV``, the .app's
``Contents/Resources/bin/uv`` (when a build chose to bundle it), a copy this
module downloaded earlier, ``PATH`` — and failing all of those it is
downloaded once (pinned version, sha256-verified) into
``~/.fused-render-lite/bin``.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile

from fused_render_lite import paths

logger = logging.getLogger(__name__)

PYTHON_VERSION = "3.12"
UV_VERSION = "0.12.13"
_UV_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
READY_MARKER = ".fused-lite-ready"
RUN_TIMEOUT_S = 600.0

_STRIPPED_ENV_VARS = ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE",
                      "PYTHONSTARTUP", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")


def clean_env(**overrides) -> dict:
    """This process's environment minus what would poison uv's child pythons."""
    env = dict(os.environ)
    for name in _STRIPPED_ENV_VARS:
        env.pop(name, None)
    env.update(overrides)
    return env


def _no_window() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------

def _uv_asset() -> str:
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"uv-{arch}-apple-darwin.tar.gz"
    if sys.platform == "win32":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"uv-{arch}-pc-windows-msvc.zip"
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    return f"uv-{arch}-unknown-linux-gnu.tar.gz"


def uv_bin(download: bool = False, log=None) -> str | None:
    override = os.environ.get("FUSED_RENDER_LITE_UV")
    if override and os.path.isfile(override):
        return override
    name = "uv.exe" if os.name == "nt" else "uv"
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates = (
        os.path.join(os.path.dirname(exe_dir), "Resources", "bin", name),  # macOS .app
        os.path.join(exe_dir, name),
        os.path.join(paths.bin_dir(), name),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("uv")
    if found:
        return found
    if download:
        return _download_uv(log or (lambda _msg: None))
    return None


def _fetch_with_progress(url: str, log, label: str) -> bytes:
    """GET ``url`` in chunks, logging a percent line every ~10%."""
    buf = bytearray()
    with urllib.request.urlopen(url, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        next_mark = 10
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            buf.extend(chunk)
            if total:
                pct = len(buf) * 100 // total
                if pct >= next_mark:
                    log(f"  {label}: {pct}% ({len(buf) // (1024 * 1024)} MB)")
                    next_mark = (pct // 10 + 1) * 10
    return bytes(buf)


def _download_uv(log) -> str:
    asset = _uv_asset()
    url = _UV_BASE + asset
    log(f"Downloading uv {UV_VERSION} ({asset})…")
    data = _fetch_with_progress(url, log, label="uv")
    with urllib.request.urlopen(url + ".sha256", timeout=60) as r:
        expected = r.read().decode("ascii", "replace").split()[0].strip().lower()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(f"uv download failed verification (sha256 {actual} != {expected})")
    dest_dir = paths.bin_dir()
    name = "uv.exe" if os.name == "nt" else "uv"
    dest = os.path.join(dest_dir, name)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            member = next(m for m in zf.namelist() if m.endswith(name))
            with zf.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            member = next(m for m in tf.getmembers() if m.name.endswith("/" + name) or m.name == name)
            src = tf.extractfile(member)
            assert src is not None
            with open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    os.chmod(dest, 0o755)
    log("uv ready.")
    return dest


# ---------------------------------------------------------------------------
# The base interpreter for apps
# ---------------------------------------------------------------------------

_managed_python: str | None = None
_managed_lock = threading.Lock()


def managed_python(log=None) -> str | None:
    """A uv-managed CPython ``PYTHON_VERSION`` (downloaded once if absent).

    None when uv is unavailable — the caller falls back to this process's
    interpreter, which is stdlib-only in the packaged app."""
    global _managed_python
    if _managed_python:
        return _managed_python
    with _managed_lock:
        if _managed_python:
            return _managed_python
        log = log or (lambda _m: None)
        uv = uv_bin(download=True, log=log)
        if uv is None:
            return None
        find = [uv, "python", "find", "--managed-python", "--no-project", "--system", PYTHON_VERSION]
        proc = subprocess.run(find, capture_output=True, text=True, env=clean_env(),
                              timeout=60, creationflags=_no_window())
        if proc.returncode != 0 or not proc.stdout.strip():
            log(f"Downloading Python {PYTHON_VERSION}…")
            inst = subprocess.run([uv, "python", "install", PYTHON_VERSION],
                                  capture_output=True, text=True, env=clean_env(),
                                  timeout=600, creationflags=_no_window())
            if inst.returncode != 0:
                raise RuntimeError("Failed to download Python %s:\n%s"
                                   % (PYTHON_VERSION, (inst.stderr or inst.stdout).strip()))
            proc = subprocess.run(find, capture_output=True, text=True, env=clean_env(),
                                  timeout=60, creationflags=_no_window())
        found = proc.stdout.strip()
        if proc.returncode != 0 or not found:
            raise RuntimeError("uv could not locate Python %s:\n%s"
                               % (PYTHON_VERSION, (proc.stderr or proc.stdout).strip()))
        _managed_python = found
        return found


# ---------------------------------------------------------------------------
# Per-app venvs
# ---------------------------------------------------------------------------

def pyproject_path(app_dir: str) -> str:
    return os.path.join(app_dir, "pyproject.toml")


def has_project(app_dir: str) -> bool:
    return os.path.isfile(pyproject_path(app_dir))


def venv_dir_for(app_dir: str) -> str:
    key = hashlib.sha256(os.path.abspath(app_dir).encode("utf-8")).hexdigest()[:16]
    return os.path.join(paths.venvs_dir(), f"{os.path.basename(app_dir)[:40]}-{key}")


def venv_python(venv_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def _declaration_digest(app_dir: str) -> str:
    h = hashlib.sha256()
    for name in ("pyproject.toml", "uv.lock", "uv.toml"):
        p = os.path.join(app_dir, name)
        if os.path.isfile(p):
            h.update(name.encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _bootstrap_ready() -> bool:
    """uv present and a managed 3.12 already resolved (or cached)."""
    return _managed_python is not None or (uv_bin() is not None and _managed_python_cached())


def _managed_python_cached() -> bool:
    uv = uv_bin()
    if uv is None:
        return False
    try:
        proc = subprocess.run(
            [uv, "python", "find", "--managed-python", "--no-project", "--system", PYTHON_VERSION],
            capture_output=True, text=True, env=clean_env(), timeout=20, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def is_ready(app_dir: str) -> bool:
    """True when the app's interpreter exists: the bootstrap (uv + Python) for a
    plain app, or a venv matching its declaration for one with a pyproject."""
    if not has_project(app_dir):
        return _bootstrap_ready()
    venv = venv_dir_for(app_dir)
    marker = os.path.join(venv, READY_MARKER)
    if not os.path.isfile(marker) or not os.path.isfile(venv_python(venv)):
        return False
    try:
        with open(marker, "r", encoding="utf-8") as f:
            return json.load(f).get("digest") == _declaration_digest(app_dir)
    except (OSError, ValueError):
        return False


class Install:
    """One app's environment build: status + streamed uv output."""

    def __init__(self, app_dir: str):
        self.app_dir = app_dir
        self.status = "pending"  # pending | running | done | error
        self.lines: list[str] = []
        self.error: str | None = None
        self.started = time.time()
        self.finished: float | None = None
        self._done = threading.Event()
        self._lock = threading.Lock()

    def log(self, msg: str) -> None:
        with self._lock:
            self.lines.append(msg.rstrip("\n"))
            if len(self.lines) > 400:
                del self.lines[: len(self.lines) - 400]
        logger.info("[install %s] %s", os.path.basename(self.app_dir), msg.rstrip())

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "lines": list(self.lines[-80:]),
                "error": self.error,
            }

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout)

    def run(self) -> None:
        self.status = "running"
        try:
            self._build()
            self.status = "done"
        except Exception as exc:  # noqa: BLE001 — reported to the page verbatim
            self.error = str(exc)
            self.status = "error"
            self.log(f"ERROR: {exc}")
        finally:
            self.finished = time.time()
            self._done.set()

    def _build(self) -> None:
        uv = uv_bin(download=True, log=self.log)
        if uv is None:
            raise RuntimeError("uv is not available and could not be downloaded")
        base = managed_python(self.log)
        if not has_project(self.app_dir):
            self.log("This app declares no pyproject.toml; it runs on a stdlib-only Python.")
            self.log("Environment ready.")
            return
        venv = venv_dir_for(self.app_dir)
        marker = os.path.join(venv, READY_MARKER)
        if os.path.exists(marker):
            os.remove(marker)
        os.makedirs(os.path.dirname(venv), exist_ok=True)
        # The same flags fused-render's install worker settled on: `--no-build`
        # + `--no-install-project` because an app folder is scripts, not a
        # distribution to build; `--no-default-groups` keeps dev groups out.
        # A bare `uv sync` (no --frozen) honours a shipped uv.lock when it still
        # satisfies pyproject and re-resolves when it does not.
        cmd = [uv, "sync", "--no-default-groups", "--no-build", "--no-install-project",
               "--python", base or sys.executable]
        self.log("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        env = clean_env(UV_PROJECT_ENVIRONMENT=venv, UV_NO_PROGRESS="1", PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(cmd, cwd=self.app_dir, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=_no_window())
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log(line)
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"uv sync exited with code {code} — see the log above")
        if not os.path.isfile(venv_python(venv)):
            raise RuntimeError("uv sync finished but the venv has no interpreter")
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({"digest": _declaration_digest(self.app_dir), "ts": time.time()}, f)
        self.log("Environment ready.")


_installs: dict[str, Install] = {}
_installs_lock = threading.Lock()


def ensure(app_dir: str) -> Install | None:
    """Start (or return the in-flight) build for ``app_dir``; None when no build is needed."""
    app_dir = os.path.abspath(app_dir)
    with _installs_lock:
        current = _installs.get(app_dir)
        if current is not None and current.status in ("pending", "running"):
            return current
        if is_ready(app_dir):
            return current if (current and current.status == "done") else None
        inst = Install(app_dir)
        _installs[app_dir] = inst
        threading.Thread(target=inst.run, name=f"uv-sync {os.path.basename(app_dir)}",
                         daemon=True).start()
        return inst


def status(app_dir: str) -> dict:
    app_dir = os.path.abspath(app_dir)
    with _installs_lock:
        inst = _installs.get(app_dir)
    if inst is None:
        return {"status": "done" if is_ready(app_dir) else "pending", "lines": [], "error": None}
    return inst.snapshot()


def interpreter_for(app_dir: str) -> str:
    if has_project(app_dir):
        return venv_python(venv_dir_for(app_dir))
    try:
        return managed_python() or sys.executable
    except RuntimeError as exc:
        logger.warning("no managed python: %s; falling back to %s", exc, sys.executable)
        return sys.executable


# ---------------------------------------------------------------------------
# Running a .py
# ---------------------------------------------------------------------------

_CHILD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_child.py")


def _error(err_type: str, message: str, detail: str = "") -> dict:
    return {"ok": False, "error": {"type": err_type, "message": message, "traceback": detail},
            "stdout": ""}


def run_python(path: str, params: dict, app_dir: str, timeout: float = RUN_TIMEOUT_S) -> dict:
    started = time.monotonic()
    if not os.path.isfile(path):
        return _error("FileNotFoundError", f"no such Python file: {path}")
    inst = ensure(app_dir)
    if inst is not None and inst.status in ("pending", "running"):
        if not inst.wait(timeout):
            return _error("EnvironmentNotReady", "the app's environment is still installing")
    if inst is not None and inst.status == "error":
        return _error("EnvironmentError",
                      "the app's environment failed to install: " + (inst.error or ""),
                      "\n".join(inst.lines[-40:]))
    python = interpreter_for(app_dir)
    request = json.dumps({"path": path, "params": params or {}})
    try:
        proc = subprocess.run(
            [python, _CHILD], input=request, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=clean_env(),
            close_fds=False, creationflags=_no_window(),
        )
    except subprocess.TimeoutExpired:
        return _error("TimeoutError", f"execution exceeded {timeout:g}s and was killed")
    except OSError as e:
        return _error("ExecutorError", f"could not start worker process: {e}")
    result: dict | None = None
    lines = proc.stdout.strip().splitlines()
    if lines:
        try:
            parsed = json.loads(lines[-1])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            result = parsed
            if proc.stderr:
                result.setdefault("stderr", proc.stderr[-4000:])
    if result is None:
        result = _error("ExecutorError",
                        f"worker exited with code {proc.returncode} without producing a result",
                        proc.stderr[-4000:])
    result.setdefault("duration_ms", round((time.monotonic() - started) * 1000))
    if not result.get("ok"):
        err = result.get("error") or {}
        if err.get("type") == "ModuleNotFoundError" and not has_project(app_dir):
            err["message"] = (
                f"{err.get('message', '')} — this app ships no pyproject.toml, so it runs on "
                "a stdlib-only Python. Declare its dependencies in a pyproject.toml next to "
                "the entry page and re-export the .fused."
            )
        logger.warning("run failed for %s: %s: %s", path, err.get("type"), err.get("message"))
    return result
