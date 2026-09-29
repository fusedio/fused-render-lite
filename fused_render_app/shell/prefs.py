"""Preferences, fixed. fused-render read these from prefs.json behind a
Preferences page; Render App has no page, so every pref is its fused-render default.
The functions keep their names because the copied AI code calls them."""

AUTO_ENGINE = "auto"


def selected_engine() -> str:
    return "builtin"


def effective_engine() -> str:
    return "builtin"


def default_model() -> str:
    """The user's preferred Claude short name — unset here."""
    return ""


def engine_for_capability(capability: str) -> str:
    """Runner preference per capability: always let the registry's platform
    order decide."""
    return AUTO_ENGINE


def effective_ai_idle_unload_minutes() -> int:
    """Idle minutes before a resident model is evicted (fused-render's default)."""
    return 15


def project_queue_enabled() -> bool:
    """One folder runs one task at a time, everything else queues — fused-render's
    opt-in beta flag. Off: every send spawns, as in fused-render's default."""
    return False


def native_chat_enabled() -> bool:
    """fused-render's React chat vs the legacy `templates/claude` page. Render
    App serves the legacy page, so this is off."""
    return False
