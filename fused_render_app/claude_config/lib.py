"""Claude Code's user settings file, minimally: where it is and how to read /
write it atomically. Honours `CLAUDE_CONFIG_DIR` like the CLI does.

Also the two helpers fused-render's `user_plugin.py` (copied verbatim by
`scripts/sync_claude_tasks.py`) reaches through `claude_config.lib`:
`read_json` and `claude_cli`, with the same signatures and contracts as
fused-render's `claude_config/lib.py` so the copy needs no patch."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from typing import Any

_LOCK = threading.Lock()


def read_json(path: str, fallback: Any) -> Any:
    """Return `fallback` only when the file is ABSENT. Malformed JSON raises —
    corruption must surface, never be silently swallowed (fused-render's
    config-store rule; `user_plugin.sync_user_plugin` catches it)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback


def claude_cli(*args: str, timeout: int = 25) -> dict:
    """Run the `claude` binary with an argv array (never a shell string).
    The binary is the one this app resolved (`claude_health.resolve`: the
    `FUSED_RENDER_*_CLAUDE_BIN` overrides, then PATH, then the known install
    dirs) so a Finder-launched process with launchd's PATH still finds it.
    Best-effort: `{ok, stdout, stderr}`, bounded so a hung CLI cannot pin the
    caller's thread."""
    from fused_render_app import claude_health

    binary, _source = claude_health.resolve()
    if binary is None:
        return {"ok": False, "stdout": "", "stderr": "claude CLI not found"}
    try:
        res = subprocess.run(
            [binary, *args], capture_output=True, timeout=timeout,
            close_fds=False, text=True, encoding="utf-8", errors="replace",
        )
        return {
            "ok": res.returncode == 0,
            "stdout": res.stdout.strip(),
            "stderr": res.stderr.strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": "claude CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"claude {args[0]} timed out"}


def config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


class SettingsUnreadable(Exception):
    """The settings file exists but cannot be read as a JSON object — a
    truncated write, a parse error, a non-object. A patch must NOT proceed
    from `{}` then: it would rewrite the file with only the managed keys and
    drop hooks, env, permissions and everything else Claude Code keeps there."""


def read_settings() -> dict:
    """The settings object; `{}` only when the file does not exist."""
    path = settings_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SettingsUnreadable(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SettingsUnreadable(f"{path}: not a JSON object")
    return data


def write_settings(settings: dict) -> None:
    path = settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_catalog() -> list:
    """fused-render ships a settings catalog (`settings_catalog.json`) the
    Claude Config page renders. Render App has no such page; an empty catalog
    makes `claude_sessions._settings_model_options` fall back to the four
    short model names, which is the vocabulary the composer pills speak."""
    return []
