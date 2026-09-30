"""The published-plugin sync (fused_render_app/user_plugin.py, copied from
fused-render — D492): the `fusedio/fused-render` plugin installed or refreshed
in the user's OWN Claude config, for sessions Render App did not launch.

Same plugin id as full fused-render on purpose: both apps on one machine share
one install and one `enabledPlugins` opt-out. Every test is about a DECISION;
the CLI is faked, and the assertions are on what was decided and what argv it
produced. The one rule with teeth: an explicit `false` in `enabledPlugins` ends
this module's involvement completely.
"""
import json
import os
import threading

import pytest

from fused_render_app import server, user_plugin
from fused_render_app.claude_config import lib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def claude_dir(tmp_path, monkeypatch):
    root = tmp_path / "claude-home"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("FUSED_RENDER_APP_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(user_plugin, "_fallback_stamp", {})
    return root


class _Cli(list):
    fail: set


@pytest.fixture()
def cli(monkeypatch):
    """Record every `claude` argv instead of running one. A successful
    `plugin install`/`update` also writes `installed_plugins.json`, as the
    real CLI does, so `installed()` reads it back."""
    calls = _Cli()
    calls.fail = set()

    def fake(*args, timeout=25):
        calls.append(args)
        ok = not (calls.fail & set(args))
        if ok and args[:2] in (("plugin", "install"), ("plugin", "update")):
            path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "plugins",
                                "installed_plugins.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"plugins": {user_plugin.PLUGIN_ID: [{"version": "1.0.0"}]}}, fh)
        return {"ok": ok, "stdout": "", "stderr": "" if ok else "boom"}

    monkeypatch.setattr(lib, "claude_cli", fake)
    return calls


def _settings(claude_dir, enabled):
    (claude_dir / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}), encoding="utf-8")


def _installed(claude_dir):
    d = claude_dir / "plugins"
    d.mkdir(exist_ok=True)
    (d / "installed_plugins.json").write_text(
        json.dumps({"plugins": {user_plugin.PLUGIN_ID: [{"version": "1.0.0"}]}}),
        encoding="utf-8")


# -- the opt-out ---------------------------------------------------------------

def test_an_explicitly_disabled_plugin_is_left_completely_alone(claude_dir, cli):
    _settings(claude_dir, {user_plugin.PLUGIN_ID: False})
    out = user_plugin.sync_user_plugin()
    assert out["action"] == "skipped"
    assert cli == []


def test_force_does_not_override_the_users_no(claude_dir, cli):
    _settings(claude_dir, {user_plugin.PLUGIN_ID: False})
    user_plugin.sync_user_plugin(force=True)
    assert cli == []


def test_a_missing_key_is_not_a_refusal(claude_dir, cli):
    _settings(claude_dir, {"something-else@m": False})
    out = user_plugin.sync_user_plugin()
    assert out == {"action": "install", "ok": True}


# -- install vs update ---------------------------------------------------------

def test_the_install_adds_the_marketplace_then_installs_headlessly(claude_dir, cli):
    user_plugin.sync_user_plugin()
    assert cli[0][:3] == ("plugin", "marketplace", "add")
    assert user_plugin.MARKETPLACE_REF in cli[0]
    assert "--sparse" in cli[0]
    assert cli[1][:3] == ("plugin", "install", user_plugin.PLUGIN_ID)
    assert "-y" in cli[1]


def test_an_enabled_plugin_is_updated_not_reinstalled(claude_dir, cli):
    _installed(claude_dir)
    out = user_plugin.sync_user_plugin()
    assert out == {"action": "update", "ok": True}
    assert cli[-1][:2] == ("plugin", "update")
    assert not any(c[:2] == ("plugin", "install") for c in cli)


def test_a_failed_install_is_reported_not_raised(claude_dir, cli):
    cli.fail.add("install")
    out = user_plugin.sync_user_plugin()
    assert out["action"] == "install" and out["ok"] is False


# -- rate limit ----------------------------------------------------------------

def test_a_recent_attempt_is_not_repeated(claude_dir, cli):
    user_plugin.sync_user_plugin()
    n = len(cli)
    assert user_plugin.sync_user_plugin() == {"action": "skipped", "reason": "checked recently"}
    assert len(cli) == n


def test_the_stamp_is_written_per_attempt_not_per_success(claude_dir, cli):
    cli.fail.add("install")
    user_plugin.sync_user_plugin()
    assert user_plugin.sync_user_plugin()["reason"] == "checked recently"


def test_an_uninstall_is_not_silently_undone(claude_dir, cli, monkeypatch):
    """`claude plugin uninstall` leaves no `false` behind; the sticky
    `ever_installed` in our stamp is what stops a reinstall behind the
    user's back."""
    user_plugin.sync_user_plugin()
    os.remove(os.path.join(str(claude_dir), "plugins", "installed_plugins.json"))
    monkeypatch.setattr(user_plugin, "_now", lambda: 10 ** 10)
    out = user_plugin.sync_user_plugin()
    assert out == {"action": "skipped", "reason": "removed by the user"}


# -- the ids and the wiring ----------------------------------------------------

def test_the_ids_match_the_packaged_manifest_and_full_fused_render():
    """Render App ships fused-render's plugin manifest (synced) and installs
    the SAME published plugin as full fused-render: one id, one install, one
    opt-out on a machine that runs both. The marketplace half is a constant
    here (no marketplace.json is synced), pinned to the published name."""
    with open(os.path.join(REPO_ROOT, "fused_render_app", "skills", "plugin.json"),
              encoding="utf-8") as fh:
        plugin = json.load(fh)
    assert user_plugin.PLUGIN_NAME == plugin["name"] == "fused-render"
    assert user_plugin.MARKETPLACE_REF == "fusedio/fused-render"
    assert user_plugin.PLUGIN_ID == "fused-render@fused-render"


def test_the_cli_helper_never_runs_without_a_resolved_binary(monkeypatch, tmp_path):
    """`claude_config.lib.claude_cli` is Render App's own; it resolves through
    `claude_health` (honouring the FUSED_RENDER_*_CLAUDE_BIN overrides the
    conftest points at nothing) and answers `ok: False` rather than raising."""
    monkeypatch.setenv("FUSED_RENDER_CLAUDE_BIN", str(tmp_path / "missing"))
    monkeypatch.setenv("FUSED_RENDER_APP_CLAUDE_BIN", str(tmp_path / "missing"))
    out = lib.claude_cli("plugin", "list")
    assert out["ok"] is False and "not found" in out["stderr"]


def test_read_json_falls_back_only_when_absent(tmp_path):
    assert lib.read_json(str(tmp_path / "nope.json"), {"x": 1}) == {"x": 1}
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        lib.read_json(str(tmp_path / "bad.json"), {})


def test_start_runs_once_per_process(claude_dir, cli, monkeypatch):
    monkeypatch.setattr(user_plugin, "_started", False)
    done = threading.Event()
    monkeypatch.setattr(user_plugin, "sync_user_plugin",
                        lambda: (done.set(), {"action": "install"})[1])
    user_plugin.start()
    assert done.wait(timeout=5)
    done.clear()
    user_plugin.start()
    assert not done.wait(timeout=0.2)


def test_the_server_starts_the_sync_off_the_bind_path(monkeypatch):
    """`start_ai`, not `make_server`: the sync spawns `claude` and clones over
    the network, which must never sit in front of the socket bind."""
    started = []
    monkeypatch.setattr(user_plugin, "start", lambda: started.append(True))
    server._start_user_plugin()
    assert started == [True]
    src = open(os.path.join(REPO_ROOT, "fused_render_app", "server.py"), encoding="utf-8").read()
    make_server = src[src.index("def make_server"):src.index("def write_server_json")]
    assert "user_plugin" not in make_server
