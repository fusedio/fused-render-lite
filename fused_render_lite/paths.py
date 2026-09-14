"""Where fused-render-lite keeps its state: ``~/.fused-render-lite``.

Override with ``FUSED_RENDER_LITE_HOME`` (tests, side-by-side installs).
"""
import os


def home() -> str:
    root = os.environ.get("FUSED_RENDER_LITE_HOME") or os.path.join(
        os.path.expanduser("~"), ".fused-render-lite"
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
