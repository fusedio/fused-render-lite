"""Per-app Python environments, built on demand with ``uv``.

Nothing is bundled. An app declares what it needs in its own
``pyproject.toml``; on open, ``uv sync`` builds a venv for it under
``~/.fused-render-app/venvs/<key>`` (keyed by the app dir), streaming uv's
output to the open page. An app with no ``pyproject.toml`` runs in one
shared "legacy" venv holding fused-render's old ``[bundled]`` set
(``LEGACY_DEPS``), also built on demand.

``uv`` itself is found in this order: ``FUSED_RENDER_APP_UV``, the .app's
``Contents/Resources/bin/uv`` (when a build chose to bundle it), a copy this
module downloaded earlier, ``PATH`` — and failing all of those it is
downloaded once (pinned version, sha256-verified) into
``~/.fused-render-app/bin``.
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

from fused_render_app import paths

logger = logging.getLogger(__name__)

PYTHON_VERSION = "3.12"
UV_VERSION = "0.12.13"
# Oldest uv whose CLI has every flag we pass (`uv sync --no-default-groups`,
# `uv python find --managed-python`). An older uv found on PATH is skipped in
# favour of downloading UV_VERSION — a user's stale ~/.local/bin/uv otherwise
# fails with "unexpected argument '--managed-python'".
UV_MIN_VERSION = (0, 8, 0)
_UV_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
READY_MARKER = ".fused-render-app-ready"
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


# ---------------------------------------------------------------------------
# uv
# ---------------------------------------------------------------------------

def _uv_asset() -> str:
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
    return f"uv-{arch}-apple-darwin.tar.gz"


def uv_bin(download: bool = False, log=None) -> str | None:
    override = os.environ.get("FUSED_RENDER_APP_UV")
    if override and os.path.isfile(override):
        return override
    name = "uv"
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates = (
        os.path.join(os.path.dirname(exe_dir), "Resources", "bin", name),  # macOS .app
        os.path.join(exe_dir, name),
        os.path.join(paths.bin_dir(), name),
    )
    if os.path.isfile(candidates[0]):
        return candidates[0]  # shipped with the app: trusted as-is, like fused-render's
    for candidate in list(candidates[1:]) + [shutil.which("uv")]:
        if candidate and os.path.isfile(candidate) and _uv_recent(candidate):
            return candidate
    if download:
        return _download_uv(log or (lambda _msg: None))
    return None


def _uv_recent(path: str) -> bool:
    """Does `uv --version` report at least UV_MIN_VERSION?"""
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              timeout=15)
        ver = proc.stdout.split()[1].split("+")[0]
        return tuple(int(x) for x in ver.split(".")[:3]) >= UV_MIN_VERSION
    except Exception:  # noqa: BLE001 - an unparseable uv is not one we rely on
        return False


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
    name = "uv"
    dest = os.path.join(dest_dir, name)
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

def base_python() -> str:
    """The interpreter every venv is built on — resolved exactly as
    fused-render's install worker does it (`envinstall.script_python`): this
    process when it already runs 3.12 (a dev checkout on 3.12, or the packaged
    app, whose bundled `Contents/MacOS/python` self-locates through the
    `Contents/lib` symlink build_dmg.sh adds), else a uv-managed 3.12."""
    from fused_render_app import envinstall

    return envinstall.script_python() or sys.executable


# ---------------------------------------------------------------------------
# Per-app venvs
# ---------------------------------------------------------------------------

def pyproject_path(app_dir: str) -> str:
    return os.path.join(app_dir, "pyproject.toml")


def has_project(app_dir: str) -> bool:
    return os.path.isfile(pyproject_path(app_dir))


# The environment an app WITHOUT a pyproject.toml gets: fused-render's old
# `[bundled]` extra minus the cloud credential chains (botocore, google-auth)
# and the fused engine (+ mcp). Those apps were written against this implicit
# set, so Render App installs it once, on demand, into one shared venv.
LEGACY_DEPS = (
    "numpy",
    "pandas",
    "requests",
    "pillow",
    "openpyxl",
    "python-pptx",
    "msgpack>=1.0",
    "fpdf2>=2.8.7",
    "drain3>=0.9.11",
)
LEGACY_DEPS_ENV = "FUSED_RENDER_APP_LEGACY_DEPS"  # comma list override (tests)


def legacy_deps() -> tuple[str, ...]:
    override = os.environ.get(LEGACY_DEPS_ENV)
    if override is not None:
        return tuple(d.strip() for d in override.split(",") if d.strip())
    return LEGACY_DEPS


def legacy_project_dir() -> str:
    """A generated project whose pyproject.toml declares LEGACY_DEPS; rewritten
    whenever the set changes, which invalidates the venv's digest marker."""
    root = os.path.join(paths.home(), "legacy")
    os.makedirs(root, exist_ok=True)
    deps = "".join(f'    "{d}",\n' for d in legacy_deps())
    text = ("# Generated by fused-render-app: the implicit environment for .fused apps\n"
            "# that ship no pyproject.toml (fused-render's old [bundled] set minus\n"
            "# cloud credential chains and the fused engine). Do not edit.\n"
            "[project]\nname = \"fused-render-app-legacy\"\nversion = \"1\"\n"
            f"requires-python = \">={PYTHON_VERSION}\"\ndependencies = [\n{deps}]\n\n"
            "[tool.uv]\npackage = false\n")
    path = os.path.join(root, "pyproject.toml")
    try:
        current = open(path, encoding="utf-8").read()
    except OSError:
        current = None
    if current != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return root


def project_dir_for(app_dir: str) -> str:
    """The folder whose pyproject.toml governs ``app_dir``: itself, or the legacy set."""
    return app_dir if has_project(app_dir) else legacy_project_dir()


def venv_dir_for(app_dir: str) -> str:
    key = hashlib.sha256(os.path.abspath(app_dir).encode("utf-8")).hexdigest()[:16]
    return os.path.join(paths.venvs_dir(), f"{os.path.basename(app_dir)[:40]}-{key}")


def venv_python(venv_dir: str) -> str:
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


def is_ready(app_dir: str) -> bool:
    """True when the venv governing ``app_dir`` (its own, or the shared legacy
    one for an app without a pyproject) exists and matches its declaration."""
    project = project_dir_for(app_dir)
    venv = venv_dir_for(project)
    marker = os.path.join(venv, READY_MARKER)
    if not os.path.isfile(marker) or not os.path.isfile(venv_python(venv)):
        return False
    try:
        with open(marker, "r", encoding="utf-8") as f:
            return json.load(f).get("digest") == _declaration_digest(project)
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
        base = base_python()
        project = project_dir_for(self.app_dir)
        if project != self.app_dir:
            self.log("This app declares no pyproject.toml; installing fused-render's legacy "
                     "compatibility set (" + ", ".join(legacy_deps()) + ") into a shared "
                     "environment. Shared by every such app; installed once.")
        venv = venv_dir_for(project)
        marker = os.path.join(venv, READY_MARKER)
        if os.path.exists(marker):
            os.remove(marker)
        os.makedirs(os.path.dirname(venv), exist_ok=True)
        # The same flags fused-render's install worker settled on: `--no-build`
        # + `--no-install-project` because an app folder is scripts, not a
        # distribution to build; `--no-default-groups` keeps dev groups out.
        # A bare `uv sync` (no --frozen) honours a shipped uv.lock when it still
        # satisfies pyproject and re-resolves when it does not.
        cmd = [uv, "sync", "--no-default-groups", "--no-install-project",
               "--python", base]
        if project == self.app_dir:
            # An app folder is scripts, not something to build — and a wheel-less
            # dependency in a user's declaration should fail loudly rather than
            # compile on their machine. The legacy set is ours: drain3 ships only
            # an sdist (pure Python), so source builds stay allowed there.
            cmd.append("--no-build")
        self.log("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        env = clean_env(UV_PROJECT_ENVIRONMENT=venv, UV_NO_PROGRESS="1", PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(cmd, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log(line)
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"uv sync exited with code {code} — see the log above")
        if not os.path.isfile(venv_python(venv)):
            raise RuntimeError("uv sync finished but the venv has no interpreter")
        with open(marker, "w", encoding="utf-8") as f:
            json.dump({"digest": _declaration_digest(project), "ts": time.time()}, f)
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
    return venv_python(venv_dir_for(project_dir_for(app_dir)))


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
            close_fds=False,
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
                "fused-render's legacy compatibility set (" + ", ".join(legacy_deps()) + "). "
                "Declare its dependencies in a pyproject.toml next to the entry page and "
                "re-export the .fused."
            )
        logger.warning("run failed for %s: %s: %s", path, err.get("type"), err.get("message"))
    return result
