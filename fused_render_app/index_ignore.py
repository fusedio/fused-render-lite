"""Stand-in for fused-render's `index.ignore.MountGuard` (the file index's
structural refusal of paths inside a fused-render home). Render App has no
file index and no mounts, so nothing is ever blocked. Imported by the copied
`project_queue.py`."""
from __future__ import annotations


class MountGuard:
    def blocks(self, path: str) -> bool:  # noqa: ARG002 - signature parity
        return False
