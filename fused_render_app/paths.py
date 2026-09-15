"""Where fused-render-app keeps its state: ``~/.fused-render-app``.

Override with ``FUSED_RENDER_APP_HOME`` (tests, side-by-side installs).
"""
import os


def home() -> str:
    root = os.environ.get("FUSED_RENDER_APP_HOME") or os.path.join(
        os.path.expanduser("~"), ".fused-render-app"
    )
    os.makedirs(root, exist_ok=True)
    return root


def apps_dir() -> str:
    """Extracted .fused payloads, one dir per (file, content hash)."""
    return _sub("apps")


def venvs_dir() -> str:
    """Per-app venvs built by ``uv sync`` from the app's pyproject.toml."""
    return _sub("venvs")


def dropped_dir() -> str:
    """.fused files dropped onto the browser placeholder (bytes only arrive)."""
    return _sub("dropped")


def bin_dir() -> str:
    """Tools fetched on demand (uv)."""
    return _sub("bin")


def log_path() -> str:
    return os.path.join(home(), "app.log")


def pid_path() -> str:
    return os.path.join(home(), "server.json")


def _sub(name: str) -> str:
    path = os.path.join(home(), name)
    os.makedirs(path, exist_ok=True)
    return path


def fix_process_env() -> None:
    """Repair what the py2app bootstrap leaves behind, before anything spawns.

    py2app points SSL_CERT_DIR (and sometimes SSL_CERT_FILE) at a path inside
    the bundle that does not exist, which makes uv — and our own urllib — trust
    no certificates at all: every download fails. Drop the dangling values and
    fall back to the system CA bundle. Also make sure a Finder-launched app,
    whose PATH is launchd's minimal one, can still find Homebrew/uv installs.
    """
    for key in ("SSL_CERT_DIR", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        value = os.environ.get(key)
        if value and not os.path.exists(value):
            os.environ.pop(key, None)
    if "SSL_CERT_FILE" not in os.environ:
        for candidate in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.exists(candidate):
                os.environ["SSL_CERT_FILE"] = candidate
                break
    extra = [os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    for p in extra:
        if os.path.isdir(p) and p not in parts:
            parts.append(p)
    os.environ["PATH"] = os.pathsep.join(parts)
