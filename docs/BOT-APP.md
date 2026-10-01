# Browser Bots on Render App — architecture and wire contract

Status: the design every port in this tree follows. Written 2026-10-02 from
the OpenBot reference app (`~/Fused/sandbox/Showcase Drafts/OpenBot`, a
fused-render folder app: `index.html` + `src/*.js` + `app.css` + `agents.py`
+ `browser.py` + `apptools.py` + `imessage.py` + helpers) and from a spike
against `claude 2.1.287` (section 6).

Render App stops being "opens a `.fused`" and becomes the Browser Bots app:
the OpenBot UI, behaviour for behaviour, as React + shadcn, on a Python
backend that lives inside the Render App server process. The platform stays
(server, `_web` router shim, AI relay, Claude tasks cluster, `env`/uv,
capture, native window shell, updater, skills). The product surface goes
(showcase, dock, launcher, hotkey, settings page, `/open` flows, editlink,
Edit/Home title-bar buttons). Package, bundle and release pipeline keep their
names.

## 1. Layout

```
fused_render_app/bots/            the backend package (in-process; no daemon, no fused.daemon)
  __init__.py
  paths.py        state roots: <home>/bots/data/<id>, <home>/bots/cache/<id>, ~/Fused/bots (inbox), ~/Fused/app (apps)
  store.py        bot dirs, bot.json (atomic write), events.jsonl, usage.jsonl ledger + summary
  browser.py      per-bot Chrome over CDP (port of OpenBot browser.py) + AX-tree snapshot + change report
  bot.py          class Bot: lifecycle, memory, skills, artifacts/inbox, routines, offers, builds, send/pause/resume/stop,
                  takeover/giveback/window, file inbox (botsend), summary(); the engine-neutral half of agents.py
  steps_engine.py the OpenBot JSON-action loop (agents.py Bot._run/_prompt/_parse/_risk/_describe/_execute), via fused_ai
  agent_engine.py the Claude Code harness (section 6): one `claude -p` per task, tools over MCP
  tools.py        the tool table shared by both engines: names, schemas, descriptions, the risk rule, execution
  botmcp.py       stdio MCP server spawned by `claude`; forwards tools/call to POST /api/bots/<id>/tool (stdlib only)
  apptools.py     port of OpenBot apptools.py (APPS / APP TOOLS / SKILL.md); `available()` False when `fused.agent_core` is absent
  imessage.py     port of OpenBot imessage.py (bridge thread, texts, contacts)
  apps.py         ports of listapps.py, importapp.py, mkbuild.py, revealapp.py
  registry.py     the bot registry, scheduler thread (routines, file inbox), iMessage thread, slow-call log
  routes.py       the HTTP API (section 3), an `_web.APIRouter` included from server.py
  botsend.py      CLI: `python -m fused_render_app.bots.botsend <bot> "<task>"` (drops into the bot's inbox dir)
frontend/
  bots.html                 the page `/` serves (second Vite input beside lite.html)
  src/apps/bots/            the React app (section 4)
tests/test_bots_*.py        store, routes (fake claude), steps engine, agent engine, apptools, offers
docs/BOT-APP.md             this file
```

State: `paths.home()/bots/` (so `~/.fused-render-app/bots/data/<id>/…`,
`…/bots/cache/<id>/…`), never beside the code. Everything OpenBot wrote under
`.fused/data/bots` and `.fused/cache/bots` moves there one-to-one. The Inbox
root stays `~/Fused/bots/<bot name>/`; the apps root stays `~/Fused/app`
(`_config()["fused_dir"] + "/app"`; `FUSED_RENDER_DIR` overrides the
workspace as everywhere else).

Three OpenBot fallbacks read `~/.fused-render/server.json`; here they are
`os.environ["FUSED_RENDER_ORIGIN"]` (set by `make_server`) else
`paths.pid_path()`: `_fused_ai`, `_server_origin`, `browser.page_origin()`.
`page_origin()` feeds `--remote-allow-origins`, so a wrong value silently
breaks the live view.

## 2. Wire shapes (byte-compatible with OpenBot)

Event (one line of `events.jsonl`, one item of `bot.events` in a status reply):

```
{seq, ts, role, text, result?, thumb?, detail?, options?, offer?, app?, reply?, trace?, artifacts?}
role: user | thought | action | approval | question | done | error | system
thumb:  "<seq>.jpg" (cache/<id>/steps/<seq>.jpg); the page loads /api/bots/<id>/steps/<seq>.jpg
offer:  {kind: "use"|"build", name, dir, spec}
app:    {name, dir, params?, tools?}
reply:  {seq, role, text}   (quoted message the user replied to)
```

Bot summary (`Bot.summary(light, detail)`): every key of `bot.json`
(`id, name, model, effort, status, instructions, created, task, step, url,
title, note, updated, approval, build_access, face, routines, pinned, hidden,
reactions, encrypt, chrome_profile, imessage, imessage_to, builds,
pending_offer, offers_declined, artifacts_dir, control, visible, dl_pct`) plus
`seq`, `browser: {running, url, title, visible, sealed, encrypt, tabs?[{i,id,title,url,active,ws}], files?, artifacts?, artifacts_dir?}`,
`memory` (detail only), `skills` (detail only), `shot` (the shot URL,
`/api/bots/<id>/shot`, or null), `shot_ts`, `viewport: [1280, 800]`, `events`
(since the page's cursor).

Status reply: `{bots: [summary…], ts, usage: null|summary, imessage: null|state}`.
Usage summary and iMessage state are the OpenBot shapes (`_usage_summary`,
`imessage.current_state`).

`status` values: `idle | running | waiting | paused | error`. `approval`:
`ask | auto`. `build_access`: `scoped | full`. Models: `haiku sonnet opus
fable local-4b local-9b`. Efforts: `low medium high xhigh`.

## 3. HTTP API (`fused_render_app/bots/routes.py`, prefix `/api/bots`)

Reads are plain GETs; every POST/DELETE needs `X-Fused: 1` (same guard as the
rest of the server). Errors: `{"error": "<sentence>"}` with 400/404/409.
Bodies are JSON. The page polls `GET /api/bots` every 1.5 s (400 ms while the
live view is open), exactly as OpenBot polled `status`.

```
GET    /api/bots?cursors=<json {id: seq}>&shot_for=<id>&fast=0|1       -> status reply (section 2)
POST   /api/bots                      {name, model, effort, instructions, approval, build_access, encrypt} -> {ok, id}
GET    /api/bots/profiles             -> {ok, profiles: [{dir, name, email}]}
GET    /api/bots/usage                -> the usage summary
GET    /api/bots/imessage             -> the bridge state
POST   /api/bots/<id>/send            {text, reply_to?}                     -> {ok}      (also answers approvals/questions/offers)
POST   /api/bots/<id>/pause | resume | stop | takeover | giveback | wake    -> {ok}
POST   /api/bots/<id>/window          {visible}                             -> {ok}
POST   /api/bots/<id>/goto            {url}                                 -> {ok, url}
POST   /api/bots/<id>/nav             {op: back|forward|reload}             -> {ok, url}
POST   /api/bots/<id>/tab             {tab: new|switch|close, url?, index?} -> {ok, url, tabs}
POST   /api/bots/<id>/attach          {name, data: <base64>}                -> {ok, name}   (8 MB cap)
POST   /api/bots/<id>/react           {seq, emoji}                          -> {ok, reactions}
POST   /api/bots/<id>/flag            {pinned?, hidden?, face?}             -> {ok}
POST   /api/bots/<id>/settings        {name?, model?, effort?, instructions?, memory?, approval?, build_access?,
                                       encrypt?, imessage_handle?, imessage_to?}  -> {ok}   ("rename" in OpenBot;
                                       POST because the server has no do_PATCH)
POST   /api/bots/<id>/profile         {profile}                             -> {ok}   (import a Chrome profile; background)
POST   /api/bots/<id>/clone           {name?}                               -> {ok, id}
DELETE /api/bots/<id>                                                       -> {ok}
POST   /api/bots/<id>/routines        {op: add, text, kind, minutes?, time?, weekdays?, at?} -> {ok, routine}
                                      {op: delete|enable|disable|run, rid}  -> {ok}
POST   /api/bots/<id>/skills          {op: save, name, trigger, text, rid?} | {op: delete, rid} | {op: learn} -> {ok, skills}
GET    /api/bots/<id>/export          -> {ok, name, text}  (transcript as Markdown)
POST   /api/bots/<id>/reveal          {path?}                               -> {ok, path}
GET    /api/bots/<id>/shot?t=         -> image/png (the latest screenshot; 404 when none)
GET    /api/bots/<id>/steps/<n>.jpg   -> image/jpeg (a step thumbnail; 404 when gone)
GET    /api/bots/<id>/tools?token=    -> {tools: [{name, description, inputSchema}]}   (botmcp's roster; section 6)
POST   /api/bots/<id>/tool            {name, args, token}                   -> {content: [...], isError}   (botmcp only; section 6)
GET    /api/bots/builds               -> {builds: [{entryId, name, dir, createdAt, doneAt?}]}   (<home>/bots/builds.json)
POST   /api/bots/builds               {builds: [...]}                       -> {ok}

GET    /api/apps                      -> {root, apps: [{folder, dir, name, desc, tools, skill, icon, mtime}]}
POST   /api/apps/import               {name, data: <base64>}                -> {dir, folder, files, fusedApp}  (64 MB cap)
POST   /api/apps/mkdir                {dir}                                 -> {dir, existed}
POST   /api/apps/reveal               {dir}                                 -> {dir}
GET    /api/apps/icon?dir=<abs>       -> the app's icon.svg / icon.png, else 404
```

The server also keeps: `/api/tasks/*` (Builds), `/api/run` (the `py` action
and embedded apps), `/api/fs/raw` (inbox downloads, attached files),
`/api/capture/*` + `/api/ai/transcribe` (dictation), `/api/ai` (steps
engine), `/render?path=` (apps). `server.app_dir_for()` gains a third rule:
a path under `~/Fused/app/<x>` belongs to that folder.

Routes `/`, `/index.html` serve `static/shell-dist/bots.html`. `/tasks`,
`/chat`, `/explorer/*` keep serving `lite.html` (the Builds iframe and the
task peek). A `GET /embed?path=<abs html>&…` route serves the page like
`/render` does (runtime injected) so the apps gallery and cards can frame an
app without `/explorer/embed`. `runtime.js`'s `findTarget()` returns the
frame's own `window` when `_preview=1` is on its URL, so a gallery
thumbnail's params never land on the host page's URL (twelve sandboxed
thumbnails would otherwise fight over `?bot=`); the viewer, the side app and
the inline card carry no `_preview`, so their params write to the host URL
exactly as in OpenBot (HOST_KEYS = `bot`, "Copy state" reads them back).

## 4. Frontend (`frontend/src/apps/bots/`)

Second Vite input `frontend/bots.html` → `src/bots.tsx` → `src/apps/bots/App.tsx`.
Imports `styles/tailwind.css` and `src/apps/bots/styles/bots.css` (a verbatim
port of OpenBot `app.css`: same token names on `:root` / `:root[data-theme=light]`,
same class names where markup is ported one-to-one). Does NOT import
`shell.css`. shadcn (base-nova, `@platform/shadcn/ui`) supplies primitives:
Dialog, Select, Tooltip, DropdownMenu/ContextMenu, Checkbox, Textarea, Input,
ScrollArea; each is restyled to OpenBot's pill buttons / 20 px bubbles /
radii so the default shadcn look never shows. Icons: the inline SVGs OpenBot
uses, kept as small components (lucide only where identical).

One module per OpenBot file so behaviour can be diffed:

| OpenBot | React | holds |
| --- | --- | --- |
| core.js | `state/store.ts`, `lib/api.ts`, `lib/format.ts`, `lib/md.ts`, `components/Face.tsx`, `lib/face.ts` | poll loop (one in flight), events merge, 600-event cap, toasts, md(), face SVG + anime.js moods, select(), URL `?bot=` |
| chat.js | `components/BotList.tsx`, `components/Thread.tsx`, `components/Composer.tsx`, `components/ThreadSearch.tsx`, `components/ToBottom.tsx`, `components/BotMenu.tsx`, `lib/unread.ts`, `lib/notify.ts` | list order (pinned → waiting-unread → last user ts), FLIP glide, unread/seen (localStorage `browser-bot.seen`), New rule, tobottom pill, reactions, reply quote, attachments (paste/drop), dictation, search, notifications, document.title |
| live.js | `components/PreviewPane.tsx`, `components/LiveView.tsx`, `lib/cdp.ts`, `lib/layout.ts` | right column (shot, cap, inbox, routines, usage strip, side app), full-screen live view: CDP screencast WebSocket to `tabs[active].ws`, take over / hand back, input forwarding (toPage, keyParams), tab strip, popup follow, panel widths + collapse (localStorage `browser-bot.layout`), fit hysteresis |
| dialogs.js | `dialogs/BotDialog.tsx`, `dialogs/FacePicker.tsx`, `dialogs/Confirm.tsx`, `dialogs/Routines.tsx`, `dialogs/Skills.tsx`, `dialogs/Usage.tsx` | the six modals, dirty guard, iMessage status line, profiles list |
| builds.js | `builds/BuildsPanel.tsx`, `builds/BuildDialog.tsx`, `builds/builds.ts` | iframe to `/tasks?embed=1&scope=all&view=list` (+`&peek=<key>`), the row filter stylesheet, chip count, `builds.json` under the app home via `/api/fs/*`… see note |
| apps.js | `apps/AppsPanel.tsx`, `apps/AppViewer.tsx`, `apps/AppCard.tsx`, `apps/SideApp.tsx`, `apps/apps.ts` | gallery (`/embed?...&_preview=1` thumbnails), viewer, kebab menu, upload/drop, app cards in the thread, side app, Copy state, appFromText |

Builds note: OpenBot kept `builds.json` in its own `.fused/data`; here it is
`GET/POST /api/bots/builds` (`registry.builds_json`, stored at
`<home>/bots/builds.json`). `fused.tasks.*` calls become `tasks-lib`-style
fetches to `/api/tasks/*` with explicit `target` (no `X-Fused-Page` here):
`POST /api/tasks/create {prompt, target, title, model, effort, permission_mode}`,
`GET /api/tasks`, `GET /api/tasks/changes` (long poll), `POST /api/tasks/read`.

Keep: every string the user sees, every keyboard shortcut (⌘F search, Esc
chains, Enter send, ⌘↩ in the build dialog, Alt+←/→ and ⌘[ ⌘] ⌘R in the live
view), every tooltip, the 1.5 s / 400 ms poll, the toast labels table, the
notification roles, the mood table, the face shapes/colours, the layout
limits (`MID_MIN 450`, `R_MIN 280`, `LIM`), the `md()` rules, the `_preview`
gate (the page is never a preview here, so `renderPreviewOnly` is dead code
and dropped).

## 5. Behaviour the backend keeps (from agents.py)

Everything in `agents.py` that is not the model loop: create/clone/delete,
greet, rename/settings, flag, react, routines (`_next_run`, spacing from
disk, circuit breaker after 3 fails), skills (`learn`, `skills_for`), memory
(`remember`, caps), artifacts/Inbox (`save_file`, `collect_task_artifacts`,
README.md per task, `index.jsonl`), attachments (`save_bytes`,
`resolve_file`), file inbox (`drain_file_inbox`, botsend / iMessage), offers
(`_offer`, `_answer_pending_offer`, declined for 7 days, 10 min wait),
builds (`build`, `_watch_build`, `_build_prompt` update/new), `show_app`,
`_resolve_app`, `_app_at`, the risk rule (`_risk`, `_RISKY_BTN`, `_YES`,
`_NO`), the approval gate, the stuck detector, the `py`/`tool` one-run rule,
idle sleep after 10 min, window pop-out/dock, `takeover`/`giveback`, the
usage ledger, export to Markdown, Chrome profile import, encryption at rest.
Step thumbnails (`_step_thumb`) stay; `_keep_bad_reply` stays for the steps
engine.

The steps engine is the OpenBot loop verbatim (prompt text included), used
for `local-4b`/`local-9b` and when no `claude` CLI is resolved. The agent
engine is the default for `haiku sonnet opus fable`. A bot setting `engine`
(`auto | steps | agent`, default `auto`) is stored but not exposed in the
dialog yet (Advanced can grow it later); `auto` = the rule above.

## 6. The agent engine (harness) — `agent_engine.py`, `tools.py`, `botmcp.py`

Verified on claude 2.1.287 (scratch spike, 2026-10-02):

- `claude -p --input-format stream-json --output-format stream-json --verbose
  --replay-user-messages --include-partial-messages --model <m> --effort <e>
  --system-prompt-file <f> --tools= --setting-sources= --mcp-config <mcp.json>
  --strict-mcp-config --allowedTools "mcp__bot__*" --no-session-persistence
  --disable-slash-commands` connects our stdio server, lists its tools, calls
  them with ZERO permission prompts under the subscription login.
- A tool result `[{"type":"image","data":<b64>,"mimeType":"image/png"}, {"type":"text",…}]`
  reaches the model (it named the colour of a 64×64 test frame).
- A user message written to stdin MID-TURN is absorbed into the running turn
  (echoed by `--replay-user-messages`) but the model read it as a "system
  reminder / injection" and ignored it. So mid-task instructions travel in
  TOOL RESULTS (below), never on stdin while a turn runs.
- A user message written while the process is IDLE starts a new turn in the
  same process (`system/init` again, then a `result`): follow-ups keep the
  conversation.
- `{"type":"control_request","request_id":…,"request":{"subtype":"interrupt"}}`
  ends the running tool call at once (`result` with
  `subtype: error_during_execution`), the process stays alive and answers the
  next message. That is Stop.
- `result` events carry `num_turns`, `total_cost_usd`, `usage`, `modelUsage`,
  `stop_reason`, `session_id`, `permission_denials`, `queued_turn_count`.
- The per-server `timeout` in mcp.json is honoured; set it to
  `(approval wait + 60) * 1000` ms like `templates/claude/agent.py` does so an
  approval card can sit for an hour.

Process model: ONE `claude` process per bot per TASK, spawned by
`start_task`, killed when the task ends (`done`, Stop, error, step cap);
`--max-turns` does not exist on this CLI, so the engine counts `tool_use`
blocks itself (MAX_STEPS = 60, same as OpenBot) and interrupts past the cap.
The first user message carries what OpenBot's `_prompt` carried once per task:
YOU (config), STANDING INSTRUCTIONS, MEMORY, PLAYBOOKS, CONVERSATION SO FAR,
APPS / APP TOOLS / APP SKILLS / CONTACTS / FILES, OFFER hints, the APP GUIDE
when triggered, then `TASK: …`. The system prompt file holds the role, the
rules and the tool semantics (the OpenBot SYSTEM_PROMPT rewritten for native
tools: no JSON envelope, no "you have no tools" paragraph, no
`_TOOL_CONFUSION`).

Tool table (`tools.py`; names are the MCP tool names, so the model sees
`mcp__bot__<name>`): `observe`, `screenshot`, `goto`, `click`, `type`,
`press`, `select`, `hover`, `scroll`, `wait`, `read`, `back`, `tab`,
`upload`, `save`, `remember`, `learn`, `ask`, `login`, `text`, `texts`,
`tool` (app MCP tools), `py`, `build`, `show`, `offer`. Every browser action
returns `ok, now at <url>` + a CHANGE REPORT (url/title/dialog diff, N new /
gone controls by role+name, "nothing visible changed") + the fresh
observation (elements with refs, text excerpt capped at 3000 chars, TABS,
POPUP OPEN). Context is the budget: an action result carries the COMPACT view (at most
40 elements, viewport-first, 1200 chars of text); `observe` returns the full
observation (160 elements, 6000 chars of text) and is one call away.
`screenshot` returns the current frame as image content (JPEG, 1280 wide,
quality 60) and is auto-attached to an action result when the harness sees
the second repeat of the same action label or a page with fewer than 3
interactive elements (canvas apps). `read` returns up to 6000 chars. The
ledger records `usage.input_tokens` per assistant turn so context growth is
measured, not guessed; `--autocompact` is left at its default as a net.

Observation = `browser.observe()` (its private `_snapshot(ws)`): `Accessibility.getFullAXTree` on the
main frame (plus same-process child frames from `Page.getFrameTree`),
interactive roles + headings/dialogs/tabs/menus/named images, states
(expanded/checked/selected/disabled/focused). Refs stay MECHANICAL: for each
kept AX node the snapshot resolves `backendDOMNodeId` with `DOM.resolveNode`
and stamps `data-sb-ref="sb<n>"` on the element via `Runtime.callFunctionOn`,
so `_find_js` and every existing click/type/press/select/hover/scroll/upload
path keep working unchanged. A node `document.querySelector` cannot reach
(a closed shadow root) keeps its `backendDOMNodeId` in the observation and
falls back to a `DOM.getBoxModel` centre click with
`DOM.scrollIntoViewIfNeeded` first (the click path already dispatches real
mouse events at a computed centre). The existing `SNAPSHOT_JS` DOM scan
still runs and supplements the AX list with role-less `cursor:pointer`
clickables, `<select>` options and file inputs; the whole thing falls back
to `SNAPSHOT_JS` alone when the AX call fails. Cap 160 elements, viewport
first. The element line format stays OpenBot's (`sb12 button "Sign in" …`).

Spawn details: `--include-partial-messages` is NOT passed (thoughts are
emitted per assistant text block, not streamed; the flag floods stdout).
`--replay-user-messages` echoes (user events whose content is text, not
tool_result) are skipped when building events. Assistant content blocks are
deduplicated by message id + block index. Effort `low` on a model that
ignores `--effort` (haiku) gets the relay's trick: a
`set_max_thinking_tokens: 0` control request right after `system/init`.
The roster is built per task by the server (`GET /api/bots/<id>/tools`):
`tool` is omitted when `apptools.available()` is False, `text`/`texts` when
the bot has no contacts, `upload` when it has no files and no attachments;
a tool that can only error gets called anyway. The task token is registered
BEFORE mcp.json is written and the process spawned, because `tools/list`
fires at connect.

Control flow inside the tools (the harness's job, in the server process):

- Pause: every tool waits on `pause_flag` before and after running.
- Stop: set `stop_flag` FIRST (it releases every blocked wait: approval,
  ask, login, offer), then the `interrupt` control request, then SIGTERM
  after 5 s, then SIGKILL. The `error_during_execution` result that the
  interrupt produces is not an error while stopping.
- Mid-task user messages: drained from `bot.inbox` and appended to the NEXT
  tool result as `USER INSTRUCTION (mid-task, overrides the task): …`; an
  instruction clears the `py`/`tool` one-run ledger as in OpenBot.
- `ask(message, options?)`: emits a `question` event, status `waiting`,
  blocks until `send()` or Stop; returns `USER ANSWER: …` (plus the
  take-over note when the user drove meanwhile). `login(message)`: pops the
  window (`bot.window(True)`), emits the question, waits for a reply or
  hand-back, docks, returns.
- Approval: `_risk(name, args, obs)` decides; when non-empty and approval is
  `ask`, emit `approval` (`About to <describe>. <why> Approve?`), wait;
  denied → return `DENIED by the user: … Do not retry it.`; approved → run.
- `offer`: the OpenBot `_offer` semantics (one per task, 10 min wait, yes →
  build/show, no → declined for 7 days), returned as text.
- Repeats: same label twice in a row → `NOTE: you repeated …` in the result;
  six steps over ≤2 labels → the stuck `question` (OpenBot text) once per
  task.
- Events: `thought` for every assistant text block that precedes a tool
  call (from `assistant` events; partial text is not emitted), `action` per
  tool call (`label -> result[:400]`, `thumb` for browser steps), `done` for
  the final text of the turn (or `Done.`); `error` on `is_error` results /
  process death (3 strikes → task error). Usage ledger: one line per
  assistant turn (`_usage_log`), with `total_cost_usd` from `result`.
- The engine ends the task when the turn's `result` arrives and no tool is
  blocking; `collect_task_artifacts(final)` runs as in OpenBot.

`botmcp.py` (spawned by claude; stdlib; UTF-8 stdio; copies the framing of
`templates/claude/permission_server.py`): argv = `<server origin> <bot id>
<token>`; `initialize` → capabilities.tools; `tools/list` → the table from
`GET /api/bots/<id>/tools?token=` (so the roster lives in one place);
`tools/call` → `POST /api/bots/<id>/tool {name, args, token}` with no HTTP
timeout, result passed through verbatim (`content`, `isError`). The token is
minted per task (`secrets.token_urlsafe`) and checked by the route; the route
rejects a stale task's token with 409 so a leftover process cannot drive a
new task.

Local tier (`local-4b`, `local-9b`) and no-CLI: `steps_engine.py`, unchanged
semantics, `fused_ai.text(prompt, system_prompt, model, effort)` per step.

## 7. Server and shell changes

- `server.py`: include `bots.routes.router` and `apps` routes; `/` →
  `bots.html`; add `/embed`; third `app_dir_for` rule; remove the showcase /
  dock / launcher / settings / open routes and their imports; `start_ai` adds
  `bots.registry.start()` (scheduler + iMessage) and `stop_ai` adds
  `bots.registry.shutdown()` (stop every bot's Chrome).
- `macapp.py`: startup window → `/`; drop the menu-bar Dock tray, the
  launcher panel, the global hotkey, `dock_store` recording; keep the
  menu-bar item (Open in app / Open in browser / Tasks… / Open app logs /
  Quit), Finder-open of `.fused` goes (the document type stays registered
  for now; opening one shows the bot app).
- `mainwindow.py`: drop the Edit and Home title-bar buttons and menu items
  (keep Open in Browser, Tasks ⌘⇧T, Edit menu, Window menu); the window title
  is "Browser Bots".
- Deleted (UI surfaces only): `showcase.py`, `showcase/`, `dock_store.py`,
  `menubar_dock.py`, `launcher.py`, `launcher_panel.py`, `hotkey.py`,
  `editlink.py`, `icon_color.py`, `static/{index,open,dock,launcher,settings}.html`,
  their tests (`test_showcase`, `test_dock`, `test_launcher`, `test_editlink`).
  KEPT as plumbing: `appfile.py`, `container.py`, `localapps.py` (the task
  peek's `current_apps` and `app_dir_for` use it), `/api/open` (and
  `test_server.py::test_open_run_and_fs`, which covers `/render`, `/api/run`
  and `/api/fs/*` through it). `test_placeholder_and_open_page` changes: `/`
  now serves the bots page. Run `pytest -q` after EACH deletion. Before
  deleting a module, grep `scripts/setup_py2app.py`, `scripts/build_dmg.sh`
  and `.github/workflows/*.yml` for it.
- `README.md` rewritten for the bot app; `STATUS.md` gets a `## 0.11.0`
  section. Version bump to `0.11.0` in `fused_render_app/__init__.py`.

## 8. Verification

`scripts/dev.sh` (per-branch port + home), Chrome with
`--remote-debugging-port` driven through Argent (`describe` / `tap` /
`screenshot`), the OpenBot page open in FusedRender.app beside it for
comparison. `pytest -q` green; `cd frontend && bun run build` (typecheck +
boundaries) green; `bun test` for the ported pure libs (`md`, `format`,
`unread`, `layout`).

## 9. Status (2026-10-02)

Landed and checked live against a dev server on 2799 (Chrome driven through
Argent over CDP; OpenBot open in FusedRender.app in the next tab):

- Backend: 1048 pytest green (2 skipped). Real `claude` (haiku) + real
  Chrome through the agent engine: goto → thought/action/done with step
  thumbnails; `ask` with options answered from the chat; the approval gate
  on a "Buy now" click, deny path, and the denied-action memory (a refused
  action is not re-asked until the user speaks again — added after haiku
  retried a denied click); Stop mid-approval ends the task with `system
  "Stopped"` and leaves no `claude` / `botmcp.py` process behind; the usage
  ledger records cost and the turn's full input context (fresh +
  cache-write + cache-read tokens).
- Apps as data and functionality (the point of the product): `py <app>`
  loads an app's SKILL.md onto that same tool result (the first message is
  never rebuilt — fixed after the load silently never surfaced), and the bot
  answers from it (tip-calculator's `bill` / `tip` params and defaults);
  `tool` runs an app's mcp.toml tools natively (no FusedRender module
  needed: AST-derived params, the app's own venv via `env.run_python` with
  the manifest's entrypoint) — `linkedin_list_pending` ran read-only with
  no approval card, `linkedin_add_messages` raised the approval card, ran on
  "Approve" and reported the new queue item; `show` opens a built app beside
  the chat; a real `build` through fused.tasks ends in the "ready" card; an
  `offer` can be declined; an instruction typed mid-task reaches the model
  on the next tool result; `save` lands in the Inbox folder.
- Theme: the shell's appearance reaches every rendered page — lite's
  runtime.js now carries fused-render's theme block (a bot's own build had
  reported "the runtime ignores the theme attribute"); the tip-calculator
  embed renders light/dark with the shell and flips live across tabs.
- Local tier (steps engine, Gemma 4 E4B through fused_ai on this Mac): a
  cold model asks "download now?" from the first task, not the greeting
  (the greeting had swallowed the first message as the answer); download
  progress ticks are accepted from the model worker (they were refused as
  page writes and the row went stalled); the model's habit of filing
  goto's address under `to` is repaired before the step runs. The 4.8 GB
  weights were removed again afterwards (disk), so the first local task on
  this Mac downloads.
- Frontend: 95 bun tests green, `bun run build` green. Bot list, New bot
  dialog (+ Advanced), Settings, row context menu, preview pane with inbox /
  routines / usage strip, live view (screencast, Take over, URL bar
  navigation, tab strip, Hand back), Builds panel (filtered to builds — fixed
  a recorded build with an empty entry id matching every bare session row),
  Apps panel (gallery thumbnails through `/embed?…&_preview=1` with the host
  URL left clean, viewer, kebab menu, Beside chat side app), Usage, Routines
  (add / delete with confirm), Skills, light theme, the composer send with
  the "Task started" toast and the session divider.
- Native shell from source (`python -m fused_render_app.macapp`): a
  "Browser Bots" window on `/`, Open in Browser as the only title-bar
  button, 0.11.0 in `server.json`.

Not exercised live: dictation (needs a microphone grant), iMessage (needs
Full Disk Access), Chrome profile import and encryption at rest (unit
tested in browser.py).
