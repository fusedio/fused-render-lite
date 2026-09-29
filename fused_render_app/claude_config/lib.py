"""Claude Code's user settings file, minimally: where it is and how to read /
write it atomically. Honours `CLAUDE_CONFIG_DIR` like the CLI does."""
from __future__ import annotations

import json
import os
import tempfile
import threading

_LOCK = threading.Lock()


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
