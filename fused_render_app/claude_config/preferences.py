"""`main("patch", '{"model": ..., "effortLevel": ...}')` — write the global
Claude Code defaults the chat's model / effort pills read back through
`agent._global_defaults()`. Same two top-level keys fused-render's
preferences module manages for these fields; a `None` value deletes the key
(reset to Claude's own default)."""
from __future__ import annotations

import json

from fused_render_app.claude_config import lib

MANAGED_KEYS = ("model", "effortLevel")


def main(action: str = "get", payload: str = "") -> dict:
    if action == "get":
        try:
            settings = lib.read_settings()
        except lib.SettingsUnreadable as exc:
            return {"ok": False, "error": str(exc)}
        return {"schema": [], "prefs": {k: settings.get(k) for k in MANAGED_KEYS}}
    if action == "patch":
        try:
            body = json.loads(payload) if payload else {}
        except ValueError as exc:
            return {"ok": False, "error": f"bad payload: {exc}"}
        if not isinstance(body, dict):
            return {"ok": False, "error": "payload must be an object"}
        unknown = [k for k in body if k not in MANAGED_KEYS]
        if unknown:
            return {"ok": False, "error": f"unmanaged keys: {unknown}"}
        with lib._LOCK:
            # Read-modify-write of the WHOLE file: an unreadable file is a
            # refusal, never an empty object to overwrite it with.
            try:
                settings = lib.read_settings()
            except lib.SettingsUnreadable as exc:
                return {"ok": False, "error": f"could not read the Claude settings: {exc}"}
            changed = []
            for key, value in body.items():
                if value is None:
                    settings.pop(key, None)
                else:
                    settings[key] = value
                changed.append(key)
            try:
                lib.write_settings(settings)
            except OSError as exc:
                return {"ok": False, "error": f"could not write {lib.settings_path()}: {exc}"}
        return {"ok": True, "changed": changed}
    return {"ok": False, "error": f"unknown action: {action}"}
