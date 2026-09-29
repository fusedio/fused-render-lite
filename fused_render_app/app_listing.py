"""Stand-in for fused-render's `app_listing.app_entry` — the declared entry
page of an app folder. Same rule the chat engine uses (`templates/shared/
app_entry.entry_html`): the first non-hidden `.html` carrying the
`<meta name="fused-app">` tag."""
from __future__ import annotations

import importlib.util
import os

_SHARED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "shared")


def _load():
    spec = importlib.util.spec_from_file_location(
        "fused_render_app_tpl_app_entry", os.path.join(_SHARED, "app_entry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MOD = None


def app_entry(dir_path: str) -> str | None:
    global _MOD
    if _MOD is None:
        _MOD = _load()
    return _MOD.entry_html(dir_path)
