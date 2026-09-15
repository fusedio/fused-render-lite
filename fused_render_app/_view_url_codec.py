"""Path canonicalisation shared with the copied AI code."""
import re

_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_drive_path(fs_path: str) -> bool:
    return bool(_DRIVE_RE.match(fs_path or ""))


def canonical_fs_path(fs_path: str) -> str:
    """Forward slashes on a drive-letter path; POSIX paths untouched (a
    backslash is a legal filename character there). Idempotent."""
    return fs_path.replace("\\", "/") if _is_drive_path(fs_path) else fs_path
