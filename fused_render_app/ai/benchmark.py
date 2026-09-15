"""Benchmarking is not part of fused-render-app. ``footprints`` still stamps
its memory store with ``machine()`` so a restored home directory is recognised
as another machine; that one function is what survives here."""
from __future__ import annotations

import os
import platform


def _total_memory_bytes() -> int | None:
    try:
        from fused_render_app.ai import hw_detect

        hw = hw_detect.cached_hardware() or {}
        total = hw.get("totalMemoryBytes") or hw.get("memoryBytes")
        if isinstance(total, int):
            return total
    except Exception:  # noqa: BLE001
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def machine() -> dict:
    return {
        "platform": platform.system() or platform.platform(),
        "arch": platform.machine(),
        "cpuCount": os.cpu_count(),
        "totalMemoryBytes": _total_memory_bytes(),
    }
