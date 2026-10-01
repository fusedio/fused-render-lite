"""fused_render_app.bots.apptools (port of OpenBot tests/test_apptools.py).

Tests that discover or run real MCP tools need `fused.agent_core` (the bundled
runner of `fused app serve`), which the Render App does not ship: they take the
`core` fixture and skip when it is absent. Everything else (the write
heuristic, the prompt sections, the APPS listing, SKILL.md parsing,
resolve_app, is_owned, run_py's envelope handling) runs everywhere.
"""
import json
import os
import sys
import textwrap
import time

import pytest

from fused_render_app.bots import apptools

os.environ.setdefault("OPENFUSED_APP_SERVE_PYTHON", sys.executable)


def make_app(root, folder, tools=None, body=None):
    """A fake fused-render app under `root`: index.html with the marker, t.py with
    main(x: int=1, mode: str='a'), and an mcp.toml with one pinned tool unless
    `tools` (raw TOML text) is given. Returns the app dir."""
    d = os.path.join(root, folder)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "index.html"), "w") as f:
        f.write('<!doctype html><html><head><meta name="fused-app" /><title>Fake</title></head><body></body></html>')
    with open(os.path.join(d, "t.py"), "w") as f:
        f.write(body or textwrap.dedent('''
            def main(x: int = 1, mode: str = "a"):
                return {"x": x, "mode": mode}
        '''))
    if tools is None:
        tools = textwrap.dedent('''
            [[tool]]
            name = "fake_echo"
            description = "Echoes x back. Read-only."
            file = "t.py"
            entrypoint = "main"

            [tool.pinned]
            mode = "pinned"
        ''')
    if tools:
        with open(os.path.join(d, "mcp.toml"), "w") as f:
            f.write(tools)
    return d


@pytest.fixture
def apps_root(tmp_path):
    return str(tmp_path / "apps")


@pytest.fixture
def core():
    """Skip unless the bundled MCP runner is importable."""
    pytest.importorskip("fused.agent_core.app_mcp")
    assert apptools.available() is True


# ---- availability ------------------------------------------------------------------
def test_available_matches_import():
    try:
        import fused.agent_core.app_mcp  # noqa: F401
        have = True
    except Exception:  # noqa: BLE001
        have = False
    assert apptools.available() is have


def test_without_core_everything_is_no_tools(apps_root, monkeypatch):
    make_app(apps_root, "fake-docs")
    monkeypatch.setattr(apptools, "_HAVE", False)
    assert apptools.available() is False
    assert apptools.discover([apps_root], []) == []
    assert apptools.registry(force=True) == []


def test_defaults_skip_nothing():
    assert apptools.SKIP_DIR is None
    # Resolved at import against the real HOME (conftest re-points HOME per test).
    assert os.path.basename(apptools.FUSED_HOME) == ".fused-render"
    assert apptools.REGISTRY_FILES == [os.path.join(apptools.FUSED_HOME, "registered_apps.json"),
                                       os.path.join(apptools.FUSED_HOME, "linked_apps.json")]


# ---- discovery (needs fused.agent_core) ---------------------------------------------
def test_discover_lists_tools_minus_pins(core, apps_root):
    make_app(apps_root, "fake-docs")
    make_app(apps_root, "no-mcp", tools="")
    recs = apptools.discover([apps_root], [], skip_dir="/nonexistent")
    assert [r.name for r in recs] == ["fake_echo"]
    r = recs[0]
    assert r.app == "fake-docs"
    assert r.app_dir == os.path.join(apps_root, "fake-docs")
    assert [p.name for p in r.params] == ["x"]  # mode is pinned, so hidden
    assert r.tools_in_app == 1


def test_discover_skips_nothing_by_default(core, apps_root):
    make_app(apps_root, "one")
    assert [r.app for r in apptools.discover([apps_root], [])] == ["one"]


def test_discover_skips_invalid_manifest(core, apps_root):
    make_app(apps_root, "good")
    make_app(apps_root, "bad", tools="[[tool]]\nname = 'x'\n")  # no file -> invalid
    recs = apptools.discover([apps_root], [], skip_dir="/nonexistent")
    assert [r.app for r in recs] == ["good"]


def test_discover_reads_registry_files(core, apps_root, tmp_path):
    other = str(tmp_path / "elsewhere")
    d = make_app(other, "linked-app")
    reg = tmp_path / "linked_apps.json"
    reg.write_text(json.dumps({"entries": [{"name": "Linked", "path": d}]}))
    recs = apptools.discover([apps_root], [str(reg)], skip_dir="/nonexistent")
    assert [r.app for r in recs] == ["linked-app"]


def test_discover_ignores_bad_registry_file(core, apps_root, tmp_path):
    make_app(apps_root, "good")
    bad = tmp_path / "registered_apps.json"
    bad.write_text("{not json")
    recs = apptools.discover([apps_root], [str(bad), str(tmp_path / "missing.json")], skip_dir="/nonexistent")
    assert [r.app for r in recs] == ["good"]


def test_discover_dedupes_realpath(core, apps_root, tmp_path):
    d = make_app(apps_root, "one")
    link = tmp_path / "link-to-one"
    os.symlink(d, link)
    reg = tmp_path / "linked_apps.json"
    reg.write_text(json.dumps({"entries": [{"path": str(link)}]}))
    recs = apptools.discover([apps_root], [str(reg)], skip_dir="/nonexistent")
    assert len(recs) == 1


def test_discover_honours_explicit_skip(core, apps_root):
    d = make_app(apps_root, "skip-me")
    recs = apptools.discover([apps_root], [], skip_dir=d)
    assert recs == []


def test_discover_ignores_registry_entries_of_wrong_type(core, apps_root, tmp_path):
    make_app(apps_root, "good")
    bad = tmp_path / "registered_apps.json"
    bad.write_text(json.dumps({"entries": 5}))
    bad2 = tmp_path / "linked_apps.json"
    bad2.write_text(json.dumps([1, 2]))
    recs = apptools.discover([apps_root], [str(bad), str(bad2)], skip_dir="/nonexistent")
    assert [r.app for r in recs] == ["good"]


def test_registry_paths_tolerates_junk(tmp_path):
    good = tmp_path / "a.json"
    good.write_text(json.dumps({"entries": [{"path": "/x"}, {"path": ""}, {"nope": 1}, "str", {"path": 5}]}))
    bad = tmp_path / "b.json"
    bad.write_text("{not json")
    assert apptools._registry_paths([str(good), str(bad), str(tmp_path / "missing.json")]) == ["/x"]


def test_count_tools(apps_root):
    d = make_app(apps_root, "fake-docs")
    assert apptools.count_tools(d) == 1
    assert apptools.count_tools(os.path.join(apps_root, "nope")) == 0


def test_registry_caches_and_refreshes_on_mtime(core, apps_root, monkeypatch):
    make_app(apps_root, "a")
    monkeypatch.setattr(apptools, "ROOTS", [apps_root])
    monkeypatch.setattr(apptools, "REGISTRY_FILES", [])
    first = apptools.registry(force=True)
    assert [r.app for r in first] == ["a"]
    make_app(apps_root, "b")
    assert [r.app for r in apptools.registry()] == ["a"]  # within TTL: cached
    monkeypatch.setattr(apptools, "_cache_at", 0.0)  # TTL expired
    assert sorted(r.app for r in apptools.registry()) == ["a", "b"]


def test_registry_refreshes_when_entrypoint_edited(core, apps_root, monkeypatch):
    d = make_app(apps_root, "a")
    monkeypatch.setattr(apptools, "ROOTS", [apps_root])
    monkeypatch.setattr(apptools, "REGISTRY_FILES", [])
    rec = apptools.registry(force=True)[0]
    assert json.loads(apptools.run_tool(rec, {"x": 3}).text)["x"] == 3
    with open(os.path.join(d, "t.py"), "w") as f:
        f.write("def main(x: int = 1, mode: str = 'a'):\n    return {'x': x * 100, 'mode': mode}\n")
    os.utime(os.path.join(d, "t.py"), (time.time() + 5, time.time() + 5))  # make the mtime change unmistakable
    monkeypatch.setattr(apptools, "_cache_at", 0.0)
    rec2 = apptools.registry()[0]
    assert json.loads(apptools.run_tool(rec2, {"x": 3}).text)["x"] == 300


def test_registry_drops_tool_whose_entrypoint_vanished(core, apps_root, monkeypatch):
    d = make_app(apps_root, "a")
    monkeypatch.setattr(apptools, "ROOTS", [apps_root])
    monkeypatch.setattr(apptools, "REGISTRY_FILES", [])
    assert len(apptools.registry(force=True)) == 1
    os.remove(os.path.join(d, "t.py"))
    monkeypatch.setattr(apptools, "_cache_at", 0.0)
    assert apptools.registry() == []


# ---- run_tool (needs fused.agent_core) ----------------------------------------------
def _one(apps_root, **kw):
    make_app(apps_root, "fake-docs", **kw)
    return apptools.discover([apps_root], [], skip_dir="/nonexistent")[0]


def test_run_tool_returns_value_with_pin_applied(core, apps_root):
    rec = _one(apps_root)
    res = apptools.run_tool(rec, {"x": 7})
    assert res.ok is True
    assert json.loads(res.text) == {"x": 7, "mode": "pinned"}
    assert res.dropped == []


def test_run_tool_cannot_override_pin(core, apps_root):
    rec = _one(apps_root)
    res = apptools.run_tool(rec, {"x": 1, "mode": "hacked"})
    assert json.loads(res.text)["mode"] == "pinned"
    assert res.dropped == ["mode"]


def test_run_tool_drops_unknown_keys(core, apps_root):
    rec = _one(apps_root)
    res = apptools.run_tool(rec, {"x": 2, "bogus": 1})
    assert res.ok and res.dropped == ["bogus"]
    assert json.loads(res.text)["x"] == 2


def test_run_tool_args_not_object(core, apps_root):
    rec = _one(apps_root)
    for bad in ('{"x": 1}', [1, 2], None):
        res = apptools.run_tool(rec, bad)
        if bad is None:
            assert res.ok  # None means "no args"
        else:
            assert res.ok is False and res.text.startswith("error:")


def test_run_tool_args_not_object_without_core():
    # The type check comes before the runner is touched.
    rec = apptools.ToolRec(app="a", app_dir="/a", name="t", description="")
    for bad in ('{"x": 1}', [1, 2], 3):
        res = apptools.run_tool(rec, bad)
        assert res.ok is False and "must be a JSON object" in res.text


def test_run_tool_runtime_failure_is_error(core, apps_root):
    body_fail = "def main(x: int = 1, mode: str = 'a'):\n    raise RuntimeError('boom')\n"
    rec = _one(apps_root, body=body_fail)
    res = apptools.run_tool(rec, {})
    assert res.ok is False and res.text.startswith("error:") and "boom" in res.text


def test_run_tool_non_json_result(core, apps_root):
    body = "import datetime\ndef main(x: int = 1, mode: str = 'a'):\n    return {'when': datetime.date(2026, 9, 29)}\n"
    rec = _one(apps_root, body=body)
    res = apptools.run_tool(rec, {})
    assert res.ok is False and res.text.startswith("error:") and "not JSON serializable" in res.text


def test_run_tool_timeout_warns_it_may_still_complete(core, apps_root):
    body = "import time\ndef main(x: int = 1, mode: str = 'a'):\n    time.sleep(5)\n    return 1\n"
    rec = _one(apps_root, body=body)
    res = apptools.run_tool(rec, {}, timeout_s=1)
    assert res.ok is False and "timed out" in res.text
    assert "may still complete" in res.text and "do not retry" in res.text


def test_run_tool_timeout_and_errors_with_fake_runner(monkeypatch):
    """The executor plumbing without the bundled runner: a slow call times out,
    a raising one is error text, a value is JSON."""
    rec = apptools.ToolRec(app="a", app_dir="/a", name="t", description="",
                           params=[type("P", (), {"name": "x"})()])

    def fake_call(r, kwargs):
        if kwargs.get("x") == "slow":
            time.sleep(2)
        if kwargs.get("x") == "boom":
            raise RuntimeError("boom")
        if kwargs.get("x") == "obj":
            return {"x": object()}
        return {"got": kwargs}
    monkeypatch.setattr(apptools, "_call", fake_call)
    ok = apptools.run_tool(rec, {"x": 1, "y": 2})
    assert ok.ok and json.loads(ok.text) == {"got": {"x": 1}} and ok.dropped == ["y"]
    slow = apptools.run_tool(rec, {"x": "slow"}, timeout_s=0.2)
    assert not slow.ok and "timed out" in slow.text and "do not retry" in slow.text
    boom = apptools.run_tool(rec, {"x": "boom"})
    assert not boom.ok and boom.text == "error: boom"
    obj = apptools.run_tool(rec, {"x": "obj"})
    assert obj.ok  # default=str stringifies anything


# ---- is_write / prompt_section ---------------------------------------------------
def _rec(name, desc=""):
    return apptools.ToolRec(app="a", app_dir="/a", name=name, description=desc)


@pytest.mark.parametrize("name,desc,want", [
    ("docs_status", "Check whether a key is saved.", False),
    ("docs_read", "Read a document's text.", False),
    ("mail_list_messages", "Lists threads.", False),
    ("mail_send", "Sends a new email message.", True),
    ("docs_set_key", "Save the service-account key JSON.", True),
    ("docs_replace", "Replace text in a tab and update headings.", True),
    ("mail_label_messages", "Adds or removes labels on a thread.", True),
    ("get_thread", "Retrieves a full email thread. Does not mark it read.", False),  # "mark" is a noun-ish word: name check only
    ("search", "Search the library by keyword.", False),
])
def test_is_write(name, desc, want):
    assert apptools.is_write(_rec(name, desc)) is want


@pytest.mark.parametrize("name,desc", [
    ("docs_rename_doc", "Give a saved document a new friendly name."),
    ("docs_add_doc", "Remember a document under a friendly name."),
    ("docs_append_content", "Append Markdown to the end of a tab."),
    ("docs_resolve_comment", "Close a comment thread."),
    ("docs_reopen_comment", "Bring a comment thread back."),
    ("docs_run_command", "Perform a natural-language edit on a tab."),
    ("docs_insert_heading", "Put a heading above a paragraph."),
    ("docs_edit_tab", "Change text in a tab."),
    ("mail_submit_form", "Fills and files the form."),
])
def test_is_write_catches_docs_mutations(name, desc):
    assert apptools.is_write(_rec(name, desc)) is True


@pytest.mark.parametrize("name,desc", [
    ("compose_message", "Sends a message to a contact."),
    ("sendMail", ""),
    ("docs_x", "Creates a new tab."),
    ("docs_y", "Appends text to the end."),
    ("share_doc", ""),
    ("forward_mail", ""),
    ("cancel_booking", ""),
    ("comment_on_thread", ""),
    ("pay_invoice", ""),
])
def test_is_write_inflected_and_more_verbs(name, desc):
    assert apptools.is_write(_rec(name, desc)) is True


def test_is_write_still_passes_reads():
    for name, desc in [("docs_status", "Check whether a key is saved."), ("list_threads", "Lists threads in a label."),
                       ("search", "Search the library."), ("get_thread", "Retrieves a full thread.")]:
        assert apptools.is_write(_rec(name, desc)) is False, name


def test_write_rx_alias():
    assert apptools.WRITE_RX is apptools.WRITE_NAME_RX
    assert apptools.WRITE_DESC_RX.search("Posts a thing") is None  # "post" is a noun in prose
    assert apptools.WRITE_NAME_RX.search("post")


def test_prompt_section_empty():
    assert apptools.prompt_section([]) == ""


class _P:
    def __init__(self, name, annotation=None, default=None, has_default=False):
        self.name, self.annotation, self.default, self.has_default = name, annotation, default, has_default


def test_prompt_section_lists_by_app_hand_built():
    recs = [apptools.ToolRec(app="fake-docs", app_dir="/x", name="fake_echo", description="Echoes x back. Read-only.",
                             params=[_P("x", int, 1, True)]),
            apptools.ToolRec(app="fake-docs", app_dir="/x", name="mail_send", description="Sends mail.",
                             params=[_P("to", str), _P("opts", object, {"a": 1}, True)])]
    s = apptools.prompt_section(recs)
    assert s.startswith("\n\nAPP TOOLS")
    assert "fake-docs\n- fake_echo(x: int=1): Echoes x back. Read-only.\n" in s
    assert '- mail_send(to: str, opts={"a": 1}): Sends mail. [approval]' in s


def test_prompt_section_lists_by_app(core, apps_root):
    make_app(apps_root, "fake-docs")
    recs = apptools.discover([apps_root], [], skip_dir="/nonexistent")
    s = apptools.prompt_section(recs)
    assert s.startswith("\n\nAPP TOOLS")
    assert "fake-docs" in s
    assert "- fake_echo(x: int=1): Echoes x back. Read-only." in s


def test_prompt_section_caps_long_descriptions():
    s = apptools.prompt_section([_rec("t", "y" * 500)])
    assert "y" * 160 in s and "y" * 161 not in s


def test_prompt_section_compacts_when_huge():
    recs = [apptools.ToolRec(app=f"app{i}", app_dir="/x", name=f"tool_{i}_{j}", description="d" * 150)
            for i in range(6) for j in range(30)]
    s = apptools.prompt_section(recs)
    assert "(30 tools; first 12 shown" in s
    assert len(s) < len(apptools._render(recs, None))  # compact form is what got returned
    assert "tool_0_12(" not in s and "tool_0_11(" in s


# ---- tool_ref / find ---------------------------------------------------------------
def test_tool_ref_uses_same_fallbacks_everywhere():
    # risk, describe and execute must resolve the model's decision identically,
    # or a name placed in `value` would run a write tool without its gate.
    assert apptools.tool_ref({"app": "a", "name": "t", "args": {"x": 1}}) == ("a", "t", {"x": 1})
    assert apptools.tool_ref({"value": "t"}) == ("", "t", {})
    assert apptools.tool_ref({"name": "", "value": "t", "args": None}) == ("", "t", {})
    assert apptools.tool_ref({}) == ("", "", {})
    assert apptools.tool_ref({"name": "t", "args": "x"}) == ("", "t", "x")  # run_tool refuses it later


def test_find_matches_app_loosely_and_name_exactly():
    recs = [apptools.ToolRec(app="Fake_Docs", app_dir="/x", name="t", description=""),
            apptools.ToolRec(app="other", app_dir="/y", name="u", description="")]
    assert apptools.find(recs, "fake docs", "t") is recs[0]
    assert apptools.find(recs, "FAKE-DOCS", " t ") is recs[0]
    assert apptools.find(recs, "", "u") is recs[1]
    assert apptools.find(recs, "fake-docs", "u") is None
    assert apptools.find(recs, "fake-docs", "T") is None


# ---- every app (with or without tools) ---------------------------------------------
def test_list_apps_sees_apps_without_tools(apps_root, tmp_path):
    make_app(apps_root, "with-tools")
    d = make_app(apps_root, "no-tools", tools="")
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# Plain Page\n\nShows *one* thing.\n")
    os.makedirs(os.path.join(apps_root, "not-an-app"))
    with open(os.path.join(apps_root, "not-an-app", "index.html"), "w") as f:
        f.write("<html></html>")
    os.makedirs(os.path.join(apps_root, ".hidden"))
    apps = apptools.list_apps([apps_root], skip_dir="/nonexistent")
    assert [a["folder"] for a in apps] == ["no-tools", "with-tools"]
    plain, tools = apps
    assert plain["name"] == "Plain Page" and plain["desc"] == "Shows one thing." and plain["tools"] == 0
    assert tools["name"] == "Fake" and tools["tools"] == 1  # <title> when there is no README
    assert apptools.list_apps([apps_root], skip_dir=d) == [tools]
    assert apptools.list_apps([apps_root]) == apps  # default: skip nothing
    assert apptools.list_apps([str(tmp_path / "missing")]) == []


def test_list_apps_marks_skill_apps(apps_root):
    d = make_app(apps_root, "skilled", tools="")
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write("---\nname: s\ndescription: Does sums.\n---\n## a.py\nAdds.\n")
    d2 = make_app(apps_root, "bare-skill", tools="")
    with open(os.path.join(d2, "SKILL.md"), "w") as f:
        f.write("# no frontmatter\n")
    by = {a["folder"]: a for a in apptools.list_apps([apps_root])}
    assert by["skilled"]["skill"] == "Does sums."
    assert by["bare-skill"]["skill"] == "-"
    s = apptools.apps_section(list(by.values()))
    assert "- skilled: Fake — Does sums. [py]" in s and "- bare-skill: Fake [py]" in s


def test_apps_section_lists_every_app_with_links(apps_root):
    make_app(apps_root, "with-tools")
    make_app(apps_root, "no-tools", tools="")
    apps = apptools.list_apps([apps_root], skip_dir="/nonexistent")
    s = apptools.apps_section(apps, link=lambda d: "http://x/render?path=" + d)
    assert s.startswith("\n\nAPPS (")
    assert "- no-tools: Fake  http://x/render?path=" + os.path.join(apps_root, "no-tools") in s
    assert "- with-tools: Fake [1 tool]  http://x/render?path=" in s
    assert apptools.apps_section([]) == ""


def test_apps_section_truncates(monkeypatch):
    monkeypatch.setattr(apptools, "APPS_CAP", 80)
    items = [{"folder": f"app-{i}", "dir": f"/x/app-{i}", "name": f"App {i}", "desc": "d" * 30, "tools": 0} for i in range(10)]
    s = apptools.apps_section(items)
    assert s.count("\n- ") < 10 and "10 apps in all" in s


def test_apps_cache_refreshes_after_ttl(apps_root, monkeypatch):
    make_app(apps_root, "a", tools="")
    monkeypatch.setattr(apptools, "ROOTS", [apps_root])
    assert [a["folder"] for a in apptools.apps(force=True)] == ["a"]
    make_app(apps_root, "b", tools="")
    assert [a["folder"] for a in apptools.apps()] == ["a"]
    monkeypatch.setattr(apptools, "_apps_cache_at", 0.0)
    assert [a["folder"] for a in apptools.apps()] == ["a", "b"]


def test_readme_summary(tmp_path):
    (tmp_path / "README.md").write_text("\n## Title here\n### sub\n\n`Code` and **bold** line\nsecond\n")
    assert apptools.readme_summary(str(tmp_path)) == ("Title here", "Code and bold line")
    assert apptools.readme_summary(str(tmp_path / "nope")) == ("", "")


# ---- app Python, called directly (`py` action; fused-render SPEC §49) ----
def test_apps_root_follows_fused_render_dir(monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_DIR", "/tmp/fx")
    assert apptools.apps_root() == "/tmp/fx/app"


def test_is_owned_matches_realpath_only():
    builds = [{"dir": "/tmp/fx/app/x/"}, {"name": "no dir"}, "junk"]
    assert apptools.is_owned("/tmp/fx/app/x", builds)
    assert not apptools.is_owned("/tmp/fx/app/y", builds)
    assert not apptools.is_owned("/tmp/fx/app/x", [])
    assert not apptools.is_owned("/tmp/fx/app/x", None)


def test_resolve_app(apps_root, tmp_path, monkeypatch):
    d = make_app(apps_root, "my-ledger", tools="")
    with open(os.path.join(d, "README.md"), "w") as f:
        f.write("# Money Book\n\nTotals.\n")
    outside = make_app(str(tmp_path / "elsewhere"), "stray", tools="")
    monkeypatch.setattr(apptools, "ROOTS", [apps_root])
    monkeypatch.setattr(apptools, "_apps_cache_at", 0.0)
    assert apptools.resolve_app("my-ledger") == d
    assert apptools.resolve_app("My Ledger") == d          # -, _ and space alike
    assert apptools.resolve_app("money_book") == d         # by README name
    assert apptools.resolve_app(d) == os.path.realpath(d)  # absolute, under ROOTS
    assert apptools.resolve_app(outside) is None           # absolute, outside ROOTS
    assert apptools.resolve_app(os.path.join(apps_root, "missing")) is None
    assert apptools.resolve_app("nope") is None
    assert apptools.resolve_app("") is None and apptools.resolve_app(None) is None


_SKILL = """---
name: x
description: Totals and entries.
approve: [add.py]
---
# X

## s.py
Totals. Reads the ledger; writes nothing.
- Args: `m` (str, default "a")

## `add.py` — writes
Appends an entry.

### notes
not a file
"""


def _skill_app(tmp_path, text=_SKILL):
    d = tmp_path / "x"
    d.mkdir()
    (d / "index.html").write_text("")
    (d / "SKILL.md").write_text(text)
    return str(d)


def test_read_skill_parses_frontmatter_and_file_sections(tmp_path):
    sk = apptools.read_skill(_skill_app(tmp_path))
    assert sk["name"] == "x" and sk["description"] == "Totals and entries." and sk["approve"] == {"add.py"}
    assert sk["files"] == {"s.py": "Totals. Reads the ledger; writes nothing.", "add.py": "Appends an entry."}
    assert apptools.read_skill(str(tmp_path / "missing")) is None


def test_read_skill_rereads_on_mtime(tmp_path):
    d = _skill_app(tmp_path)
    assert apptools.read_skill(d)["description"] == "Totals and entries."
    p = os.path.join(d, "SKILL.md")
    with open(p, "w") as f:
        f.write("---\ndescription: New.\n---\n")
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert apptools.read_skill(d)["description"] == "New."


def test_frontmatter_lists_and_comments():
    fm, body = apptools._frontmatter("---\nname: 'q'  # a comment\napprove:\n  - a.py\n  - \"b.py\"\nempty:\n---\nbody\n")
    assert fm == {"name": "q", "approve": ["a.py", "b.py"], "empty": []}
    assert body == "body\n"
    assert apptools._frontmatter("no header") == ({}, "no header")
    assert apptools._frontmatter("---\nunterminated: 1\n") == ({}, "---\nunterminated: 1\n")


def test_skill_file_accepts_name_with_or_without_suffix(tmp_path):
    sk = apptools.read_skill(_skill_app(tmp_path))
    assert apptools.skill_file(sk, "s") == "s.py" and apptools.skill_file(sk, "s.py") == "s.py"
    assert apptools.skill_file(sk, "zz") is None and apptools.skill_file(sk, "") is None and apptools.skill_file(None, "s") is None


def test_run_py_rejects_non_object_args():
    r = apptools.run_py("http://127.0.0.1:1", "/tmp/fx/app/x", "s.py", "x")
    assert not r.ok and "must be a JSON object" in r.text


def test_run_py_reports_unreachable():
    r = apptools.run_py("http://127.0.0.1:1", "/tmp/fx/app/x", "s.py", {"m": 1})
    assert not r.ok and "not reachable" in r.text


def test_run_py_unwraps_envelope(monkeypatch):
    calls = []

    def fake_api(origin, method, path, body=None, timeout=20):
        calls.append((method, path, body))
        return {"ok": True, "result": {"n": 2}} if body["params"] == {"m": "a"} else {
            "ok": False, "error": {"type": "ParamError", "message": "missing required param: 'm'",
                                   "traceback": "tb1\ntb2"}, "stdout": "out"}
    monkeypatch.setattr(apptools, "_api", fake_api)
    ok = apptools.run_py("http://o", "/tmp/fx/app/x", "s.py", {"m": "a"})
    assert ok.ok and ok.text == '{"n": 2}'
    assert calls[0] == ("POST", "/api/run", {"py": "/tmp/fx/app/x/s.py", "html": "/tmp/fx/app/x/index.html", "params": {"m": "a"}})
    bad = apptools.run_py("http://o", "/tmp/fx/app/x", "s.py", {"zz": 1})
    assert not bad.ok and bad.text.startswith("error: ParamError") and "re-read its section" in bad.text and "stdout:" in bad.text


def test_run_py_against_a_real_http_server():
    """The X-Fused header and the JSON body reach the server; HTTP errors become text."""
    import http.server
    import threading

    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("X-Fused"), body))
            if body["params"].get("fail"):
                out, code = json.dumps({"error": "nope"}).encode(), 400
            else:
                out, code = json.dumps({"ok": True, "result": [1, 2]}).encode(), 200
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        origin = f"http://127.0.0.1:{srv.server_address[1]}/"
        ok = apptools.run_py(origin, "/a", "s.py", {"m": 1}, html="/a/view.html")
        assert ok.ok and ok.text == "[1, 2]"
        assert seen[0] == ("/api/run", "1", {"py": "/a/s.py", "html": "/a/view.html", "params": {"m": 1}})
        bad = apptools.run_py(origin, "/a", "s.py", {"fail": True})
        assert not bad.ok and bad.text == "error: nope"
    finally:
        srv.shutdown()
        srv.server_close()


def test_skill_section_mounts_given_apps_only(tmp_path):
    d = _skill_app(tmp_path)
    s = apptools.skill_section([d, d, str(tmp_path / "missing")])
    assert s.startswith("\n\nAPP SKILLS") and s.count("=== x ===") == 1 and "## s.py" in s and "approve:" not in s
    assert apptools.skill_section([]) == ""
