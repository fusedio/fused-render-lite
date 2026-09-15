"""Where the ``claude`` CLI is. The copied AI relay resolves the binary through
these names; Render App's own ``ai.py`` keeps ``FUSED_RENDER_APP_CLAUDE_BIN`` as the
override and this module honours both spellings."""
from __future__ import annotations

import os
import shutil

BIN_ENV = "FUSED_RENDER_CLAUDE_BIN"
APP_BIN_ENV = "FUSED_RENDER_APP_CLAUDE_BIN"

WINDOWS_CANDIDATES = (
    r"%USERPROFILE%\.local\bin\claude.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\claude.exe",
    r"%APPDATA%\npm\claude.exe",
    r"%APPDATA%\npm\claude.cmd",
)
POSIX_CANDIDATES = (
    "~/.local/bin/claude",
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    "~/.claude/local/claude",
    "~/.bun/bin/claude",
)


def executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def candidates() -> tuple[str, ...]:
    raw = WINDOWS_CANDIDATES if os.name == "nt" else POSIX_CANDIDATES
    return tuple(os.path.expandvars(os.path.expanduser(c)) for c in raw)


def resolve(allow_shell: bool = True) -> tuple:
    """``(path, source)`` or ``(None, None)``."""
    for env_name in (APP_BIN_ENV, BIN_ENV):
        override = os.environ.get(env_name)
        if override:
            return (override, "env") if executable(override) else (None, None)
    found = shutil.which("claude")
    if found:
        return found, "path"
    for cand in candidates():
        if executable(cand):
            return cand, "known-location"
    return None, None
