"""`claude_config.preferences` writes the model / effort defaults into Claude
Code's own settings file — the whole file, read-modify-write — so it must
never rewrite a file it could not read."""
import json
import os

from fused_render_app.claude_config import lib, preferences


def _settings_path(claude_dir):
    return os.path.join(str(claude_dir), "settings.json")


def test_patch_keeps_every_other_key(_isolated_claude_home):
    path = _settings_path(_isolated_claude_home / ".claude")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"hooks": {"x": 1}, "permissions": {"allow": ["Bash"]}, "model": "sonnet"}, f)
    out = preferences.main("patch", json.dumps({"model": "opus", "effortLevel": "high"}))
    assert out == {"ok": True, "changed": ["model", "effortLevel"]}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assert data == {"hooks": {"x": 1}, "permissions": {"allow": ["Bash"]},
                    "model": "opus", "effortLevel": "high"}
    # None deletes the key, nothing else moves.
    preferences.main("patch", json.dumps({"effortLevel": None}))
    with open(path, encoding="utf-8") as f:
        assert "effortLevel" not in json.load(f)


def test_patch_refuses_an_unreadable_file(_isolated_claude_home):
    path = _settings_path(_isolated_claude_home / ".claude")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"hooks": {"x": 1}, "model": "son')  # truncated mid-write
    out = preferences.main("patch", json.dumps({"model": "opus"}))
    assert out["ok"] is False and "could not read" in out["error"]
    with open(path, encoding="utf-8") as f:
        assert f.read() == '{"hooks": {"x": 1}, "model": "son'  # untouched
    with open(path, "w", encoding="utf-8") as f:
        f.write('["not", "an", "object"]')
    assert preferences.main("patch", json.dumps({"model": "opus"}))["ok"] is False
    assert preferences.main("get")["ok"] is False


def test_missing_file_is_an_empty_object(_isolated_claude_home):
    assert lib.read_settings() == {}
    out = preferences.main("patch", json.dumps({"model": "haiku"}))
    assert out["ok"] is True
    with open(_settings_path(_isolated_claude_home / ".claude"), encoding="utf-8") as f:
        assert json.load(f) == {"model": "haiku"}
