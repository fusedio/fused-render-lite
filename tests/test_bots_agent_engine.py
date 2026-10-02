"""The Claude Code harness (fused_render_app/bots/agent_engine.py, botmcp.py;
docs/BOT-APP.md §6), driven end to end against a fake `claude`
(tests/_bots_fake_claude.py) that spawns the real botmcp.py from the mcp.json
the engine wrote and calls its tools over MCP. A tiny threaded HTTP server
stands in for the two routes botmcp posts to; a fake Bot and a fake Browser
stand in for bot.py / browser.py."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fused_render_app.bots import agent_engine, apptools, tools

from _claude_stub_cli import write_stub_cli

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "_bots_fake_claude.py")
BOTMCP = os.path.join(os.path.dirname(HERE), "fused_render_app", "bots", "botmcp.py")
JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"


# ----------------------------------------------------------------- fakes ---
def _el(ref, tag, text="", **kw):
    return {"ref": ref, "tag": tag, "text": text, **kw}


PAGES = {
    "https://shop.test/": {
        "title": "Shop", "text": "Welcome to the shop.",
        "elements": [_el("sb1", "a", "Products", href="/products"), _el("sb2", "button", "Buy now"),
                     _el("sb3", "input", "", name="q", empty=True)]},
    "https://shop.test/products": {
        "title": "Products", "text": "Widget $5. Gadget $7.",
        "elements": [_el("sb1", "a", "Home", href="/"), _el("sb4", "a", "Widget"), _el("sb5", "button", "Add to cart")]},
    "https://shop.test/thanks": {
        "title": "Thanks", "text": "Order placed.",
        "elements": [_el("sb1", "a", "Home"), _el("sb6", "a", "Orders"), _el("sb7", "a", "Help")]},
}
LINKS = {("https://shop.test/", "sb1"): "https://shop.test/products",
         ("https://shop.test/", "sb2"): "https://shop.test/thanks",
         ("https://shop.test/products", "sb1"): "https://shop.test/"}


class FakeBrowser:
    def __init__(self):
        self.url = "about:blank"
        self.calls = []
        self.on_goto = None
        self.started = False

    def start(self, visible):
        self.started = True

    def alive(self):
        return self.started

    def observe(self):
        page = PAGES.get(self.url, {"title": "", "text": "", "elements": []})
        return {"url": self.url, "title": page["title"], "text": page["text"],
                "elements": [dict(e) for e in page["elements"]], "tabs": []}

    def goto(self, url):
        self.calls.append(("goto", url))
        self.url = url if "://" in url else "https://" + url
        if self.on_goto:
            self.on_goto(url)
        return {"url": self.url}

    def click(self, ref="", text="", x=None, y=None, backend=None):
        self.calls.append(("click", ref, text))
        self.url = LINKS.get((self.url, ref), self.url)
        return {"url": self.url}

    def screenshot(self):
        return None

    def screenshot_jpeg(self):
        return JPEG


class FakeBot:
    def __init__(self, bid="b1"):
        self.id = bid
        self.meta = {"name": "Tester", "model": "haiku", "effort": "low", "approval": "ask", "routines": []}
        self.browser = FakeBrowser()
        self.events = []
        self.statuses = []
        self.inbox = []
        self.lock = threading.RLock()
        self.cond = threading.Condition()
        self.wake = threading.Event()
        self.pause_flag = threading.Event()
        self.stop_flag = threading.Event()
        self.asking = False
        self.window_closed = False
        self.task_origin = "manual"
        self.task_started = time.time()
        self.task_dir = None
        self.outcomes = []
        self.thumbs = 0

    # transcript / status
    def emit(self, role, text, **extra):
        with self.cond:
            ev = {"seq": len(self.events) + 1, "ts": time.time(), "role": role, "text": text,
                  **{k: v for k, v in extra.items() if v is not None}}
            self.events.append(ev)
            self.cond.notify_all()
        return ev

    def set_status(self, status, **kw):
        self.meta["status"] = status
        self.meta.update(kw)
        self.statuses.append(status)

    def wait_event(self, role, timeout=15, pred=lambda ev: True):
        end = time.time() + timeout
        with self.cond:
            while True:
                hit = next((e for e in self.events if e["role"] == role and pred(e)), None)
                if hit or time.time() > end:
                    return hit
                self.cond.wait(0.05)

    def roles(self):
        return [e["role"] for e in self.events]

    def say(self, text):
        """What Bot.send does for a running task."""
        with self.lock:
            self.inbox.append(text)
        self.wake.set()

    # the surface agent_engine uses
    def _drain_inbox(self):
        with self.lock:
            msgs, self.inbox = self.inbox, []
        return msgs

    def stop(self):
        self.stop_flag.set()
        self.pause_flag.clear()
        self.wake.set()
        self.emit("system", "Stop requested")
        agent_engine.stop(self)

    def window(self, visible):
        self.meta["visible"] = visible
        if visible:
            self.pause_flag.set()
            self.meta["control"] = True

    def _closed_window_note(self, history):
        pass

    def _recover_popup(self):
        pass

    def collect_task_artifacts(self, final_msg):
        return []

    def _routine_outcome(self, task, result, message):
        self.outcomes.append((result, message))

    def _offer(self, d, obs, history):
        history.append(f"OFFERED (build): {d.get('name')}")
        return False

    def _offer_hints(self, task):
        return []

    def _step_thumb(self):
        self.thumbs += 1
        return f"{self.thumbs}.jpg"

    def _skill_dirs(self, task):
        return []

    def memory_for_prompt(self):
        return "- the shop search is at /search"

    def skills_for_prompt(self, task):
        return ""

    def past_conversation(self):
        return ["user: hello", "bot: hi there"]

    def contacts(self):
        return []

    def contact(self, d):
        return None

    def py_ref(self, d):
        return None, None, {}

    def all_files(self):
        return []

    def task_artifacts(self):
        return []

    def declined_offers(self):
        return []

    def remember(self, note):
        return "remembered"

    def save(self):
        pass


# ---------------------------------------------------------------- server ---
BOTS = {}


class Routes(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bot(self, tail):
        parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
        if len(parts) != 4 or parts[:2] != ["api", "bots"] or parts[3] != tail:
            return None
        return BOTS.get(parts[2])

    def do_GET(self):
        bot = self._bot("tools")
        if bot is None:
            return self._send(404, {"error": "no such bot"})
        token = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query)).get("token")
        try:
            self._send(200, {"tools": agent_engine.roster_for(bot, token)})
        except agent_engine.StaleToken as e:
            self._send(409, {"error": str(e)})

    def do_POST(self):
        bot = self._bot("tool")
        if bot is None:
            return self._send(404, {"error": "no such bot"})
        if self.headers.get("X-Fused") != "1":
            return self._send(400, {"error": "missing X-Fused"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        try:
            self._send(200, agent_engine.handle_tool(bot, body.get("token"), body.get("name"), body.get("args")))
        except agent_engine.StaleToken as e:
            self._send(409, {"error": str(e)})


@pytest.fixture
def server(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Routes)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    origin = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("FUSED_RENDER_ORIGIN", origin)
    yield origin
    srv.shutdown()
    srv.server_close()
    BOTS.clear()


@pytest.fixture(autouse=True)
def quiet_apptools(monkeypatch):
    """No real apps / app tools under test: the roster and the first message
    see none."""
    monkeypatch.setattr(apptools, "available", lambda: False)
    monkeypatch.setattr(apptools, "apps", lambda *a, **k: [])
    monkeypatch.setattr(apptools, "registry", lambda *a, **k: [])
    monkeypatch.setattr(apptools, "apps_section", lambda *a, **k: "")
    monkeypatch.setattr(apptools, "prompt_section", lambda *a, **k: "")
    monkeypatch.setattr(apptools, "skill_section", lambda *a, **k: "")
    monkeypatch.setattr(agent_engine, "RETRY_SLEEP_S", 0.0)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    body = f"#!{sys.executable}\nimport runpy\nrunpy.run_path({FAKE!r}, run_name='__main__')\n"
    path = write_stub_cli(tmp_path / "bin", body)
    # Both spellings: claude_health.resolve() reads the APP one first, and
    # conftest points both at a missing file.
    monkeypatch.setenv("FUSED_RENDER_APP_CLAUDE_BIN", path)
    monkeypatch.setenv("FUSED_RENDER_CLAUDE_BIN", path)
    log = tmp_path / "fake.jsonl"
    monkeypatch.setenv("BOTS_FAKE_LOG", str(log))

    def rows(key=None):
        if not log.exists():
            return []
        out = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        return [r for r in out if key in r] if key else out
    return rows


def start(bot, script, monkeypatch, task="say hi"):
    monkeypatch.setenv("BOTS_FAKE_SCRIPT", json.dumps(script))
    BOTS[bot.id] = bot
    t = threading.Thread(target=agent_engine.run, args=(bot, task, task), daemon=True)
    t.start()
    return t


def finish(t, timeout=30):
    t.join(timeout)
    assert not t.is_alive(), "the task thread did not end"


def run_task(bot, script, monkeypatch, task="say hi"):
    finish(start(bot, script, monkeypatch, task))


# ----------------------------------------------------------------- tests ---
def test_text_then_done(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [{"text": "Hi! How can I help?"}, {"result": "Hi! How can I help?"}], monkeypatch)
    assert bot.roles() == ["system", "done"], bot.events
    assert bot.events[0]["text"] == "Task started: say hi"
    assert bot.events[-1]["text"] == "Hi! How can I help?"
    assert bot.meta["status"] == "idle"
    assert bot.outcomes == [("done", "Hi! How can I help?")]
    assert agent_engine.session(bot) is None  # the token is gone with the task

    # The spawn: docs §6 argv, mcp.json, the first message, low effort's budget clamp.
    argv = fake_cli("argv")[0]["argv"]
    for flag in ("-p", "--verbose", "--replay-user-messages", "--strict-mcp-config", "--no-session-persistence",
                 "--disable-slash-commands", "--tools=", "--setting-sources="):
        assert flag in argv
    assert "--include-partial-messages" not in argv
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--allowedTools") + 1] == "mcp__bot__*"
    with open(argv[argv.index("--system-prompt-file") + 1]) as f:
        assert f.read() == agent_engine.SYSTEM_PROMPT
    cfg = json.load(open(argv[argv.index("--mcp-config") + 1]))
    srv = cfg["mcpServers"]["bot"]
    assert srv["args"][1:3] == [server, "b1"] and srv["timeout"] == (agent_engine.APPROVAL_WAIT_S + 60) * 1000
    tools_listed = fake_cli("tools")[0]["tools"]
    assert "goto" in tools_listed and "tool" not in tools_listed and "text" not in tools_listed
    first = fake_cli("user")[0]["user"]
    assert first.startswith("YOU: 'Tester' · model haiku")
    assert "MEMORY (notes you saved" in first and "bot: hi there" in first
    assert first.rstrip().endswith("TASK: say hi")
    assert {"control": "set_max_thinking_tokens"}.items() <= fake_cli("control")[0].items()


def test_actions_carry_change_report_and_compact_page(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [
        {"text": "Opening the shop."},
        {"tool": "goto", "args": {"url": "https://shop.test/"}},
        {"text": "Going to products."},
        {"tool": "click", "args": {"ref": "sb1"}},
        {"text": "Widget costs $5."},
        {"result": "Widget costs $5."}], monkeypatch)
    roles = bot.roles()
    assert roles == ["system", "thought", "action", "thought", "action", "done"], bot.events
    acts = [e for e in bot.events if e["role"] == "action"]
    assert acts[0]["text"] == "goto https://shop.test/" and acts[0]["result"] == "ok, now at https://shop.test/"
    assert acts[1]["text"] == 'click "Products"' and acts[1]["thumb"] == "2.jpg"
    assert bot.events[-1]["text"] == "Widget costs $5."
    calls = fake_cli("call")
    assert "CHANGE: url changed to https://shop.test/" in calls[0]["result"]
    assert "CURRENT PAGE\nurl: https://shop.test/" in calls[0]["result"]
    assert 'sb2 button "Buy now"' in calls[0]["result"]
    assert "CHANGE: url changed to https://shop.test/products" in calls[1]["result"]
    assert "Widget $5." in calls[1]["result"] and calls[1]["images"] == []
    assert bot.browser.calls == [("goto", "https://shop.test/"), ("click", "sb1", "Products")]


def test_risky_click_approved(server, fake_cli, monkeypatch):
    bot = FakeBot()
    t = start(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}},
                    {"tool": "click", "args": {"ref": "sb2"}},
                    {"result": "Bought."}], monkeypatch)
    ev = bot.wait_event("approval")
    assert ev and ev["text"].startswith('About to click "Buy now". The button says "Buy now"')
    assert ("click", "sb2", "Buy now") not in bot.browser.calls
    bot.say("approve")
    finish(t)
    assert ("click", "sb2", "Buy now") in bot.browser.calls
    res = fake_cli("call")[1]["result"]
    assert res.startswith('APPROVED by the user: click "Buy now"') and "url changed to https://shop.test/thanks" in res
    assert "waiting" in bot.statuses


def test_risky_click_denied(server, fake_cli, monkeypatch):
    bot = FakeBot()
    t = start(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}},
                    {"tool": "click", "args": {"ref": "sb2"}},
                    {"result": "Did not buy."}], monkeypatch)
    assert bot.wait_event("approval")
    bot.say("no, too expensive")
    finish(t)
    assert ("click", "sb2", "Buy now") not in bot.browser.calls
    res = fake_cli("call")[1]["result"]
    assert res.startswith('DENIED by the user: click "Buy now". Do not retry it;')
    assert "too expensive" not in res  # a plain "no …" is the verdict, not an instruction (OpenBot _NO)
    assert any(e["role"] == "system" and e["text"].startswith("Denied") for e in bot.events)


def test_denied_click_is_not_asked_again(server, fake_cli, monkeypatch):
    """Seen live: the model re-issued a denied click one step later. The second
    identical call is refused without a second card; a new user message clears it."""
    bot = FakeBot()
    t = start(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}},
                    {"tool": "click", "args": {"ref": "sb2"}},
                    {"tool": "click", "args": {"ref": "sb2"}},
                    {"result": "Did not buy."}], monkeypatch)
    assert bot.wait_event("approval")
    bot.say("no")
    finish(t)
    assert ("click", "sb2", "Buy now") not in bot.browser.calls
    assert sum(1 for e in bot.events if e["role"] == "approval") == 1
    results = [r["result"] for r in fake_cli("call")]
    assert any(r.startswith("DENIED EARLIER by the user") for r in results)


def test_auto_approval_skips_the_gate(server, fake_cli, monkeypatch):
    bot = FakeBot()
    bot.meta["approval"] = "auto"
    run_task(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}},
                   {"tool": "click", "args": {"ref": "sb2"}}, {"result": "ok"}], monkeypatch)
    assert "approval" not in bot.roles()
    assert ("click", "sb2", "Buy now") in bot.browser.calls


def test_ask_waits_for_the_answer(server, fake_cli, monkeypatch):
    bot = FakeBot()
    t = start(bot, [{"tool": "ask", "args": {"message": "Which size?", "options": ["S", "M", "L"]}},
                    {"result": "Got it: M."}], monkeypatch)
    q = bot.wait_event("question")
    assert q["text"] == "Which size?" and q["options"] == ["S", "M", "L"]
    assert bot.asking and bot.meta["status"] == "waiting"
    bot.say("M")
    finish(t)
    assert fake_cli("call")[0]["result"] == "USER ANSWER: M"
    assert bot.events[-1]["role"] == "done" and not bot.asking


def test_mid_task_message_rides_on_the_next_result(server, fake_cli, monkeypatch):
    bot = FakeBot()
    bot.browser.on_goto = lambda url: bot.say("also check the prices") if url.endswith("shop.test/") else None
    run_task(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}},
                   {"tool": "click", "args": {"ref": "sb1"}},
                   {"result": "Prices: $5, $7."}], monkeypatch)
    calls = fake_cli("call")
    assert "USER INSTRUCTION" not in calls[0]["result"]
    assert calls[1]["result"].rstrip().endswith("USER INSTRUCTION (mid-task, overrides the task): also check the prices")
    assert len(fake_cli("user")) == 1


def test_message_after_last_tool_gets_its_own_turn(server, fake_cli, monkeypatch):
    bot = FakeBot()
    bot.browser.on_goto = lambda url: bot.say("and the gadget?")
    run_task(bot, [{"tool": "goto", "args": {"url": "https://shop.test/products"}},
                   {"text": "Widget is $5."}, {"result": "Widget is $5."},
                   {"text": "Gadget is $7."}, {"result": "Gadget is $7."}], monkeypatch)
    users = fake_cli("user")
    assert len(users) == 2 and users[1]["user"] == "USER INSTRUCTION (mid-task, overrides the task): and the gadget?"
    assert [e["text"] for e in bot.events if e["role"] in ("thought", "done")] == ["Widget is $5.", "Gadget is $7."]
    assert bot.events[-1]["role"] == "done"


def test_py_app_without_file_puts_the_skill_in_the_result(server, fake_cli, monkeypatch):
    """The first message is sent once, so a SKILL.md that `py app` loads mid-task
    must reach the model in that call's result (the steps engine re-renders
    APP SKILLS instead)."""
    bot = FakeBot()
    bot.py_ref = lambda d: ("/apps/ledger" if d.get("app") == "ledger" else None, str(d.get("file") or ""), d.get("args") or {})
    bot.run_py = lambda d: ("py ledger", "RESULT:\nLoaded ledger's SKILL.md. Callable files: totals.py."
                            if not d.get("file") else "RESULT:\n{\"total\": 3}")
    mounted = []
    monkeypatch.setattr(apptools, "skill_section",
                        lambda dirs: mounted.append(list(dirs)) or "\n\nAPP SKILLS (...):\n=== ledger ===\n## totals.py\nargs: none")
    run_task(bot, [{"tool": "py", "args": {"app": "ledger"}},
                   {"tool": "py", "args": {"app": "ledger", "file": "totals.py"}},
                   {"result": "Total 3."}], monkeypatch)
    calls = fake_cli("call")
    assert "Loaded ledger's SKILL.md" in calls[0]["result"] and "=== ledger ===\n## totals.py" in calls[0]["result"]
    assert "=== ledger ===" not in calls[1]["result"]  # a file run carries only its value
    assert [m for m in mounted if m] == [["/apps/ledger"]]  # (the first message mounts [] here)
    assert [e["text"] for e in bot.events if e["role"] == "action"] == ["py ledger", "py ledger"]


def test_screenshot_returns_an_image(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}},
                   {"tool": "screenshot", "args": {}}, {"result": "A shop."}], monkeypatch)
    shot = fake_cli("call")[1]
    assert shot["images"] == ["image/jpeg"] and "screenshot of https://shop.test/" in shot["result"]


def test_repeat_note_and_auto_screenshot(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [{"tool": "goto", "args": {"url": "https://shop.test/products"}},
                   {"tool": "click", "args": {"ref": "sb5"}},
                   {"tool": "click", "args": {"ref": "sb5"}},
                   {"result": "Stuck on add to cart."}], monkeypatch)
    calls = fake_cli("call")
    assert "nothing visible changed" in calls[1]["result"] and calls[1]["images"] == []
    assert "NOTE: you repeated 'click \"Add to cart\"' 2 times" in calls[2]["result"]
    assert calls[2]["images"] == ["image/jpeg"]


def test_stale_token(server, fake_cli, monkeypatch):
    bot = FakeBot()
    BOTS[bot.id] = bot
    token = agent_engine.register_task(bot)
    try:
        with pytest.raises(agent_engine.StaleToken):
            agent_engine.handle_tool(bot, "not-the-token", "observe", {})
        with pytest.raises(agent_engine.StaleToken):
            agent_engine.roster_for(bot, None)
        req = urllib.request.Request(f"{server}/api/bots/b1/tool", method="POST",
                                     data=json.dumps({"name": "observe", "args": {}, "token": "old"}).encode(),
                                     headers={"X-Fused": "1", "Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 409
        assert agent_engine.roster_for(bot, token)  # the current one still works
        old = token
        agent_engine.register_task(bot)  # a new task: the old token goes stale
        with pytest.raises(agent_engine.StaleToken):
            agent_engine.handle_tool(bot, old, "observe", {})
    finally:
        agent_engine._end_session(agent_engine.session(bot))


def test_stop_mid_tool(server, fake_cli, monkeypatch):
    bot = FakeBot()
    t = start(bot, [{"tool": "ask", "args": {"message": "Shall I continue?"}},
                    {"tool": "goto", "args": {"url": "https://shop.test/"}},
                    {"result": "never"}], monkeypatch)
    assert bot.wait_event("question")
    t0 = time.time()
    bot.stop()
    finish(t, timeout=15)
    assert time.time() - t0 < 5  # the interrupt ends it; no SIGTERM wait
    assert bot.events[-1]["role"] == "system" and bot.events[-1]["text"] == "Stopped"
    assert "error" not in bot.roles() and "done" not in bot.roles()
    assert bot.browser.calls == []  # nothing after the stop ran
    assert {"control": "interrupt"}.items() <= fake_cli("control")[-1].items()
    assert bot.outcomes == [("stopped", "")]


def test_three_failed_model_calls_end_the_task(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [{"fail": "API Error: 500 internal"}] * 3, monkeypatch)
    errs = [e for e in bot.events if e["role"] == "error"]
    assert len(errs) == 4 and errs[0]["text"] == "Model call failed: API Error: 500 internal"
    assert errs[-1]["text"].startswith("RuntimeError: model call failed")
    assert bot.meta["status"] == "error"


def test_one_failed_model_call_is_retried(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [{"fail": "API Error: 500"}, {"result": "Recovered."}], monkeypatch)
    assert [e["role"] for e in bot.events] == ["system", "error", "done"]
    assert fake_cli("user")[1]["user"].startswith("The last model call failed.")


def test_process_death_is_an_error(server, fake_cli, monkeypatch):
    bot = FakeBot()
    run_task(bot, [{"exit": 3}], monkeypatch)
    assert bot.events[-1]["role"] == "error" and "Claude Code exited (code 3)" in bot.events[-1]["text"]
    assert bot.meta["status"] == "error"


def test_step_cap(server, fake_cli, monkeypatch):
    monkeypatch.setattr(agent_engine, "MAX_STEPS", 2)
    bot = FakeBot()
    run_task(bot, [{"tool": "goto", "args": {"url": "https://shop.test/"}}] * 3 + [{"result": "Partial."}], monkeypatch)
    calls = fake_cli("call")
    assert calls[2]["result"].startswith("STEP LIMIT") and calls[2]["is_error"]
    assert bot.events[-1]["role"] == "done" and bot.events[-1]["text"] == "Partial."


def test_no_cli_is_an_error(server, monkeypatch, tmp_path):
    bot = FakeBot()
    BOTS[bot.id] = bot
    agent_engine.run(bot, "say hi", "say hi")
    assert bot.events[-1]["role"] == "error" and "Claude Code CLI" in bot.events[-1]["text"]


def test_roster_follows_the_bot(server):
    bot = FakeBot()
    names = [t["name"] for t in tools.roster(bot)]
    assert "upload" not in names and "text" not in names and "tool" not in names and "observe" in names


# ---------------------------------------------------------------- botmcp ---
class McpPipe:
    def __init__(self, argv):
        self.p = subprocess.Popen([sys.executable, BOTMCP] + argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.seq = 0

    def send(self, method, params=None, rid=None):
        if rid is None:
            self.seq += 1
            rid = self.seq
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return rid

    def read(self):
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)


def test_botmcp_round_trip(server):
    bot = FakeBot()
    bot.browser.url = "https://shop.test/"
    BOTS[bot.id] = bot
    token = agent_engine.register_task(bot)
    pipe = McpPipe([server, bot.id, token])
    try:
        pipe.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        init = pipe.read()["result"]
        assert init["capabilities"] == {"tools": {}} and init["serverInfo"]["name"] == "bot"
        pipe.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        pipe.send("tools/list")
        listed = pipe.read()["result"]["tools"]
        assert [t["name"] for t in listed] == [t["name"] for t in tools.roster(bot)]
        assert listed[0]["inputSchema"]["type"] == "object"
        pipe.send("tools/call", {"name": "observe", "arguments": {}})
        out = pipe.read()["result"]
        assert out["isError"] is False and "CURRENT PAGE\nurl: https://shop.test/" in out["content"][0]["text"]
        pipe.send("ping")
        assert pipe.read()["result"] == {}
        pipe.send("nope/nothing")
        assert pipe.read()["error"]["code"] == -32601
    finally:
        pipe.close()
        agent_engine._end_session(agent_engine.session(bot))


def test_botmcp_blocked_call_does_not_stall_ping(server):
    bot = FakeBot()
    BOTS[bot.id] = bot
    token = agent_engine.register_task(bot)
    pipe = McpPipe([server, bot.id, token])
    try:
        call = pipe.send("tools/call", {"name": "ask", "arguments": {"message": "?"}})
        assert bot.wait_event("question")
        ping = pipe.send("ping")
        first = pipe.read()
        assert first["id"] == ping and first["result"] == {}
        bot.say("yes")
        second = pipe.read()
        assert second["id"] == call and second["result"]["content"][0]["text"] == "USER ANSWER: yes"
    finally:
        pipe.close()
        agent_engine._end_session(agent_engine.session(bot))


def test_botmcp_stale_token_is_a_tool_error(server):
    bot = FakeBot()
    BOTS[bot.id] = bot
    agent_engine.register_task(bot)
    pipe = McpPipe([server, bot.id, "stale"])
    try:
        pipe.send("tools/call", {"name": "observe", "arguments": {}})
        out = pipe.read()["result"]
        assert out["isError"] is True and "409" in out["content"][0]["text"]
    finally:
        pipe.close()
        agent_engine._end_session(agent_engine.session(bot))
