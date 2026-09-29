"""Stand-in for fused-render's `shell.mounts` (rclone-backed remote mounts).
Render App mounts nothing, so no path is ever mount-backed. Imported by the
copied tasks / schedule / claude_artifacts modules."""
from __future__ import annotations


def is_mount_backed(path: str) -> bool:  # noqa: ARG001 - signature parity
    return False
