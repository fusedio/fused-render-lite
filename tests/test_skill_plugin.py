"""The skill PLUGIN root (fused_render_app/skill_plugin.py, copied from
fused-render — D216): the packaged skills assembled into
`<home>/skill-plugin/` in the shape Claude Code's `--plugin-dir` loader wants,
and handed to every session Render App spawns.

Render App differs from fused-render in one way that matters here: it has no
repo-level `skills/` and no build hook, so `fused_render_app/skills/` (synced
by `scripts/sync_claude_tasks.py --skills`, committed) is the ONLY source
`skill_sources` resolves. The tests below pin the assembled shape, the
idempotent sync, the env contract into the chat template, and the packaging
invariant that the committed copy really is what ships.
"""
import importlib.util
import json
import os
import subprocess
import zipfile

import pytest

from fused_render_app import skill_plugin, skill_sources

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(REPO_ROOT, "fused_render_app")

SKILLS = skill_sources.skill_names()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private home dir (conftest already points FUSED_RENDER_APP_HOME at a
    tmp home for the whole run; narrowed per test so the stamp short-circuit
    is testable in isolation). Returns it as `home_dir()` resolves it."""
    monkeypatch.setenv("FUSED_RENDER_APP_HOME", str(tmp_path / "home"))
    from fused_render_app.shell.storage import home_dir

    resolved = home_dir()
    monkeypatch.setenv("FUSED_RENDER_HOME_DIR", resolved)
    return resolved


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Fake packaged sources: one dir per real skill plus the flat manifest,
    with the (non-existent here anyway) repo root pointed at nothing."""
    packaged = tmp_path / "packaged"
    for name in SKILLS:
        (packaged / name).mkdir(parents=True)
        (packaged / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    (packaged / "plugin.json").write_text(
        json.dumps({"name": "fused-render", "description": "d"}), encoding="utf-8")
    monkeypatch.setattr(skill_sources, "REPO_SKILLS_DIR", str(tmp_path / "no-repo"))
    monkeypatch.setattr(skill_plugin, "_REPO_MANIFEST",
                        str(tmp_path / "no-repo" / "plugin.json"))
    monkeypatch.setattr(skill_sources, "PACKAGED_SKILLS_DIR", str(packaged))
    monkeypatch.setattr(skill_plugin, "_PACKAGED_MANIFEST", str(packaged / "plugin.json"))
    return packaged


# ------------------------------------------------------------ the assembly

def test_the_sync_builds_a_loadable_plugin_root(home, sources):
    root = skill_plugin.sync_skill_plugin()
    assert root == os.path.join(home, skill_plugin.PLUGIN_SUBDIR)
    manifest = os.path.join(root, skill_plugin.MANIFEST_DIR, skill_plugin.MANIFEST_NAME)
    assert json.load(open(manifest, encoding="utf-8"))["name"] == "fused-render"
    for name in SKILLS:
        assert os.path.isfile(os.path.join(root, skill_plugin.SKILLS_SUBDIR, name, "SKILL.md"))


def test_a_second_sync_with_unchanged_sources_touches_nothing(home, sources):
    root = skill_plugin.sync_skill_plugin()
    before = os.stat(root).st_mtime_ns
    assert skill_plugin.sync_skill_plugin() == root
    assert os.stat(root).st_mtime_ns == before


def test_a_changed_skill_is_picked_up(home, sources):
    root = skill_plugin.sync_skill_plugin()
    target = sources / SKILLS[0] / "SKILL.md"
    target.write_text("# changed\n", encoding="utf-8")
    os.utime(target, (0, 0))  # same size class, different mtime
    skill_plugin.sync_skill_plugin()
    copied = os.path.join(root, skill_plugin.SKILLS_SUBDIR, SKILLS[0], "SKILL.md")
    assert open(copied, encoding="utf-8").read() == "# changed\n"


def test_a_gutted_root_is_rebuilt_rather_than_trusted(home, sources):
    """A root with the manifest but a skill dir missing loads fine and teaches
    nothing; the stamp must not keep handing it out."""
    root = skill_plugin.sync_skill_plugin()
    import shutil
    shutil.rmtree(os.path.join(root, skill_plugin.SKILLS_SUBDIR, SKILLS[0]))
    skill_plugin.sync_skill_plugin()
    assert os.path.isfile(os.path.join(root, skill_plugin.SKILLS_SUBDIR, SKILLS[0], "SKILL.md"))


def test_no_source_at_all_is_not_an_error(home, tmp_path, monkeypatch):
    for attr in ("REPO_SKILLS_DIR", "PACKAGED_SKILLS_DIR"):
        monkeypatch.setattr(skill_sources, attr, str(tmp_path / "gone"))
    for attr in ("_REPO_MANIFEST", "_PACKAGED_MANIFEST"):
        monkeypatch.setattr(skill_plugin, attr, str(tmp_path / "gone.json"))
    assert skill_plugin.sync_skill_plugin() is None


# ------------------------------------------- publishing the root to a session

def test_the_export_publishes_the_root(home, sources):
    root = skill_plugin.export_skill_plugin_env()
    assert root == skill_plugin.plugin_dir()
    assert os.environ[skill_plugin.PLUGIN_DIR_ENV] == root


def test_a_failed_sync_clears_a_stale_publication(home, tmp_path, monkeypatch):
    monkeypatch.setenv(skill_plugin.PLUGIN_DIR_ENV, "/stale")
    for attr in ("REPO_SKILLS_DIR", "PACKAGED_SKILLS_DIR"):
        monkeypatch.setattr(skill_sources, attr, str(tmp_path / "gone"))
    for attr in ("_REPO_MANIFEST", "_PACKAGED_MANIFEST"):
        monkeypatch.setattr(skill_plugin, attr, str(tmp_path / "gone.json"))
    assert skill_plugin.export_skill_plugin_env() is None
    assert skill_plugin.PLUGIN_DIR_ENV not in os.environ


def test_the_export_never_spawns_a_subprocess(home, sources, monkeypatch):
    """`make_server` runs this before the bind; a `claude` probe here once
    cost fused-render its startup budget (D228). Filesystem-only."""
    def no_spawn(*a, **kw):
        raise AssertionError("export_skill_plugin_env must not run a subprocess")

    for name in ("run", "Popen", "check_output", "call", "check_call"):
        monkeypatch.setattr(subprocess, name, no_spawn)
    assert skill_plugin.export_skill_plugin_env() == skill_plugin.plugin_dir()


def test_make_server_exports_the_root(home, monkeypatch):
    """The server-side end of the env contract: the var is set (from the REAL
    packaged skills) before anything is served."""
    from fused_render_app import server

    monkeypatch.delenv(skill_plugin.PLUGIN_DIR_ENV, raising=False)
    srv = server.make_server(0)
    try:
        root = os.environ[skill_plugin.PLUGIN_DIR_ENV]
        assert root.startswith(home)
        assert os.path.isfile(os.path.join(root, skill_plugin.SKILLS_SUBDIR,
                                           "fused-render-authoring", "SKILL.md"))
    finally:
        srv.server_close()


# ------------------------------------------------- the chat template's end

def _agent():
    path = os.path.join(PACKAGE, "templates", "claude", "agent.py")
    spec = importlib.util.spec_from_file_location("_agent_claude", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_spawned_session_is_handed_the_plugin(tmp_path, monkeypatch):
    """`_plugin_argv` reads the server's decision through appenv and emits one
    `--plugin-dir <root>`; no var → no flag (a plain turn), never a guess."""
    agent = _agent()
    monkeypatch.setenv(skill_plugin.PLUGIN_DIR_ENV, "/somewhere/skill-plugin")
    argv = agent._plugin_argv(str(tmp_path / "page.html"))
    assert argv == ["--plugin-dir", "/somewhere/skill-plugin"]
    monkeypatch.delenv(skill_plugin.PLUGIN_DIR_ENV, raising=False)
    assert agent._plugin_argv(str(tmp_path / "page.html")) == []


def test_appenv_names_the_var_the_server_exports():
    """The two ends of the env contract, in files that never import each other."""
    appenv = open(os.path.join(PACKAGE, "templates", "shared", "appenv.py"),
                  encoding="utf-8").read()
    assert skill_plugin.PLUGIN_DIR_ENV in appenv
    src = open(os.path.join(PACKAGE, "templates", "claude", "agent.py"),
               encoding="utf-8").read()
    assert "from appenv import skill_plugin_dir as _skill_plugin_dir" in src
    assert skill_plugin.PLUGIN_SUBDIR not in src


def test_the_pane_prompt_names_the_skill_and_the_runtime():
    """The prompt promises `fused-render-authoring` — a promise that was empty
    before the plugin root existed — and now also says the session is on
    Render App, which is what makes the skill's Render App paragraph apply."""
    src = open(os.path.join(PACKAGE, "templates", "claude", "agent.py"),
               encoding="utf-8").read()
    assert "`fused-render-authoring` skill documents that bridge" in src
    assert "this session runs on Render App (fused-render-app)" in src


# -------------------------------------------------------------- packaging

def test_the_committed_skills_are_the_packaged_source():
    """No repo-level `skills/` here, so the committed package copy is the one
    and only source — and it must be the copy `skill_sources` resolves."""
    assert not os.path.isdir(os.path.join(REPO_ROOT, "skills"))
    assert skill_sources.PACKAGED_SKILLS_DIR == os.path.join(PACKAGE, "skills")
    assert set(SKILLS) >= {"fused-render-authoring", "fused-render-ai",
                           "fused-render-tasks", "fused-render-capture"}
    assert os.path.isfile(os.path.join(PACKAGE, "skills", "plugin.json"))


def test_nothing_the_package_ships_lives_under_a_dotted_path():
    for dirpath, dirnames, filenames in os.walk(os.path.join(PACKAGE, "skills")):
        for name in dirnames + filenames:
            assert not name.startswith("."), os.path.join(dirpath, name)


def test_the_skills_say_what_render_app_lacks():
    """The skill text is synced verbatim from fused-render; Render App's
    differences live IN it (upstream), not in a lite patch. If this fails the
    sync source predates that paragraph."""
    text = open(os.path.join(PACKAGE, "skills", "fused-render-authoring", "SKILL.md"),
                encoding="utf-8").read()
    assert "is not supported on Render App" in text
    assert "fused.snapshot" in text


@pytest.mark.skipif(not os.path.isdir(os.path.join(REPO_ROOT, "dist")),
                    reason="no wheel built (bash scripts/build_dmg.sh)")
def test_a_built_wheel_carries_the_skills():
    wheels = sorted(p for p in os.listdir(os.path.join(REPO_ROOT, "dist")) if p.endswith(".whl"))
    if not wheels:
        pytest.skip("no wheel in dist/")
    names = zipfile.ZipFile(os.path.join(REPO_ROOT, "dist", wheels[-1])).namelist()
    assert "fused_render_app/skills/plugin.json" in names
    assert "fused_render_app/skills/fused-render-authoring/SKILL.md" in names
