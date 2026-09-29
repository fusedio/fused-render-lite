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


def read_settings() -> dict:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
