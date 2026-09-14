"""Preferences, fixed. fused-render read these from prefs.json behind a
Preferences page; lite has no page, so every pref is its fused-render default.
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
