# FusedBot (Browser Bots on Render App) — architecture and wire contract

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
(showcase, the `.fused` dock, launcher, hotkey, settings page, `/open` flows,
editlink, the Edit title-bar button). Package, bundle and release pipeline keep their
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
  presets.py      presets()/apply_preset (agents.py); data in presets/<key>/ (preset.json + playbook .md), section 5
  starters.py     port of installapp.py; data in starters/<key>/ (complete fused apps), section 5
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
POST   /api/bots                      {name, model, effort, instructions, approval, build_access, encrypt, preset?} -> {ok, id}
                                       (preset: a key from /api/bots/presets, "" = blank; unknown key -> 400, no bot made)
GET    /api/bots/presets              -> {ok, presets: [{key, name, color, order, model, instructions, apps, skills: [title]}]}
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
POST   /api/bots/<id>/flag            {pinned?, hidden?, face?: {shape, color, icon}} -> {ok}   (icon: a preset key = brand mark)
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
GET    /api/apps/starters             -> {root, starters: [{key, name, desc, version, tools, icon, setup_tool, ready_key,
                                                            installed, dir, installed_version, update}]}
GET    /api/apps/starters/status      -> {ok, ready: {key: true|false|null}, why: {key: reason}}   (each setup tool capped at 8 s)
POST   /api/apps/starters/<key>/install                                     -> {ok, key, dir, installed, existed, name}  (400 unknown key)
POST   /api/apps/starters/<key>/update                                      -> {ok, key, dir, installed, existed, name}  (400 unknown key)
GET    /api/apps/starters/<key>/icon  -> the starter's icon.svg / icon.png from the package, else 404
```

First-run setup and Claude Code health (not under `/api/bots`; the wizard and
the empty-state link read them — `fused_render_app/onboarding.py`,
`routes/claude_health.py`, both ported from fused-render in October 2026):

```
GET    /api/onboarding                 -> {completed_at, dismissed_at, opened_at, stages: {about|claude|chrome|models|bot: {status, meta, updated_at}}, chrome: {found, path}, version}
GET    /api/onboarding/models          -> {models: [{alias, id, label, size_gb, downloaded, downloading, fit}]}   (bot.py LOCAL_MODELS + fit.py, not the catalog's `recommended`)
POST   /api/onboarding/opened | dismiss | complete                           -> the snapshot (stamps the timestamp)
POST   /api/onboarding/stage           {stage, status: pending|partial|complete|n/a, meta?} -> the snapshot (meta merged)
GET    /api/claude/health              -> fused-render's ClaudeHealth (found, version, outdated, signed_in, account, doctor, on_shell_path, …)
POST   /api/claude/health/refresh | install {action} | link-path | doctor | login | login/cancel;  GET /api/claude/install | login
```

`GET /api/config` carries the same snapshot as `onboarding`. The stored stage
statuses are overruled on every read by what the server can see: Claude Code
from `claude_health`'s disk cache (never a spawn), Chrome from
`browser.CHROME_CANDIDATES`, local models from the Hub cache, "first bot" from
the bots data dir. `FUSED_RENDER_ONBOARDING=0|1` forces the wizard off/on
(tests force it off); the state file is `<home>/onboarding.json`.

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
| dialogs.js | `dialogs/BotDialog.tsx`, `dialogs/FacePicker.tsx`, `dialogs/Confirm.tsx`, `dialogs/Routines.tsx`, `dialogs/Skills.tsx`, `dialogs/Usage.tsx`, `dialogs/PresetPicker.tsx`, `lib/presets.ts` | the six modals, dirty guard, iMessage status line, profiles list; the new-bot chooser ("+" asks for a preset or a blank bot first: four named blanks, every preset's brand face, search over names and playbook titles, Enter picks the first match) and the "Comes with N playbooks" note in the bot dialog |
| core.js (BRANDS) | `lib/face.ts`, `components/Face.tsx`, `components/faceAnim.ts` | brand avatars: a disc with a hand-drawn white mark and no eyes for bots made from a preset (`face.icon`), eye animations are no-ops on them; the picker's brands row |
| apps.js (starters) | `apps/StartersStrip.tsx`, `apps/starters.ts` | the Starter apps row above the gallery: Install / Update (confirmed) / Open, Installed / Needs setup / Ready badges from `/api/apps/starters` and its `status` |
| builds.js | `builds/BuildsPanel.tsx`, `builds/BuildDialog.tsx`, `builds/builds.ts` | iframe to `/tasks?embed=1&scope=all&view=list` (+`&peek=<key>`), the row filter stylesheet, chip count, `builds.json` under the app home via `/api/fs/*`… see note |
| apps.js | `apps/AppsPanel.tsx`, `apps/AppViewer.tsx`, `apps/AppCard.tsx`, `apps/SideApp.tsx`, `apps/apps.ts` | gallery (`/embed?...&_preview=1` thumbnails), viewer, kebab menu, upload/drop, app cards in the thread, side app, Copy state, appFromText |
| — (fused-render `shell/onboarding/`) | `onboarding/OnboardingWizard.tsx`, `AboutStep`, `ClaudeStep`, `ChromeStep`, `ModelsStep`, `FirstBotStep`, `progress.ts`, `state.ts`, `onboarding.css` | the first-run setup wizard, rendered ALONE by `bots.tsx` on `/onboarding` (no store, no poll): five skippable steps, the step id in `?step=`, pills from the server's stage statuses, every exit awaits its complete/dismiss POST then `location.assign` (the server redirects `/` while the flags are empty, so a fire-and-forget write would bounce back in); the last step hands over to `/?new=1`, which `App.tsx` reads once and opens the new-bot chooser; the empty hero's "Set up this Mac" link reopens it. The Claude step reuses fused-render's `IssueRow` (`platform/ui/ClaudeHealthStrip`) through `lib/claude-setup.ts`, minus the terminal-dock re-check (no terminal here) |

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

**Styling trap: OpenBot's CSS is unlayered and beats Tailwind.** `bots.css`
embeds `app.css` outside any `@layer` — `button { background: var(--raised);
border-radius: 999px; font-weight: 300 }` and friends — and unlayered rules win
over `@layer utilities` whatever the class list says. So a shadcn `<Button
variant="accent">` (`bg-[var(--accent)]`) renders grey here while the same
button is lime on the shell pages; checkboxes, pills and links get OpenBot's
look the same way. Two ways through: use OpenBot's own classes (`.primary`,
`.muted`) on OpenBot-shaped markup, or add an unlayered rule scoped to your
surface (`onboarding/onboarding.css` does `.onboarding button[data-slot="button"]`
plus an `onboarding-accent` class passed beside `variant="accent"`). Check the
computed `backgroundColor` over CDP before concluding a token is unmapped — the
tokens (`--accent`, `--on-accent`) were right all along the first time this bit.

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

**Presets** (`bots/presets.py`, data in `fused_render_app/bots/presets/<key>/`,
shipped inside the package). One folder per site: `preset.json` (`name`,
`color`, `order`, `model`, `instructions`, optional `apps`) plus four to six
playbook `.md` files in the Skills format. `POST /api/bots` with `preset`
runs `apply_preset` before the greeting: the playbooks are copied into the
bot's own skills (editable per bot), `meta.preset = key`, `meta.face =
{icon: key, color, shape: ""}` (the page draws the brand mark), and the
preset's standing rules become Instructions when the user typed none; the
created line reads "<name> created. Comes with N <key> playbooks." The
instructions keep the bot read-only (browse and report; pop a sign-in window
with `login`), and each playbook names exact URLs, how many items to open so
a run fits the step budget, and stops before any send/post/apply for
approval. `apps` lists starter keys installed when missing (below), so the
Google Docs, Google Sheets and Apple Notes presets run on tools, not browsing;
a system line says what was installed. Add a folder to add a preset.

**Starter apps** (`bots/starters.py`, port of OpenBot `installapp.py`; data in
`fused_render_app/bots/starters/<key>/`, shipped inside the package with their
`uv.lock`). Each is a complete fused app (index.html with the marker,
pyproject.toml, mcp.toml) plus an optional `starter.json`
`{setup_tool, ready_key}`: today Google Docs Tabs and Google Sheets Tabs
(service-account Google access) and Apple Notes (reads Notes.app). Install
copies the folder to `~/Fused/app/<key>` and never overwrites an existing
folder; it records `{starter, version, installed_at}` in
`<dir>/.fused/starter.json`. `update` (only on the user's ask) replaces the
shipped files and keeps `.fused/`, `.venv` and anything not shipped; the list
reports `update: true` when the package carries a newer version than the
record. `status` runs each installed starter's `setup_tool` through the
native app-tool runner (`apptools.run_tool`, 8 s cap) and reports `ready_key`
as true/false, or null with a reason when the tool could not run. Add a
folder to add a starter.

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
- First run (October 2026, `onboarding.py` + `routes/claude_health.py`,
  the audit in `docs/AUDIT-onboarding.md`): `/` answers 307 → `/onboarding`
  while the wizard has never been on screen — the bare front door only, so
  the dock's `/?bot=…` and any other query are honoured — and `/onboarding`
  serves `bots.html` like `/`. `make_server` calls
  `onboarding.seed_for_existing_users()` before the first request: an install
  that already has a bot under `<home>/bots/data` is stamped completed, so an
  upgrade from 0.11.x never sees a first-run screen. The CLI the server
  resolves at startup is published through `claude_health.adopt()` rather
  than by exporting `FUSED_RENDER_CLAUDE_BIN`, which fused-render's
  `resolve()` would read back as a user override. Chrome, Claude Code and
  the local models are only ever *reported*: the wizard never switches a
  bot's model, never starts a download unasked (owner's call, 2026-10-02).
- `macapp.py`: startup window → `/`; drop the launcher panel, the global
  hotkey, `dock_store` recording; the menu-bar Dock tray stays, its tiles
  now bots and apps instead of `.fused` recents (described below). Finder-open
  of `.fused` goes (the document type stays registered for now; opening one
  shows the bot app).
- `mainwindow.py`: drop the Edit title-bar button and menu item (keep Open
  in Browser, Tasks ⌘⇧T, Edit menu, Window menu). The title bar ends in Open
  in Browser and Home (SF Symbol `house`, View → Home ⌘⇧H): Home takes that
  window to the bots page `/` in place, does nothing when it is already
  there, and opens a window when none is key; the window keeps the saved
  frame it was opened with. The window title is "FusedBot" (`APP_NAME`);
  `show_url(path)` / `show_bot(bid)` for the dock.
- Name: the product is **FusedBot** (bundle `FusedBot.app`, DMG
  `FusedBot-<ver>.dmg`, icon `static/fusedbot-icon*.png` from
  `fusedbot-icon.svg`, template menu-bar icon `menubar.png`/`@2x`); the
  package, bundle id, URL scheme, env vars, app home and manifest URL keep
  their Render App names, and an installed `RenderApp.app` keeps its path on
  update.

### Menu-bar dock (`menubar_dock.py`, `bots/dock.py`, `bots/dock_routes.py`, `macapp.py`)

A left click on the FusedBot menu-bar item drops a floating, macOS-Dock-like
glass tray of tiles under it (fisheye magnification, names beneath):

```
[Home] [pinned bots, by name] [pinned apps, pin order] │ [≤3 recent bots] [≤3 recent apps]
```

Home opens FusedBot. A pinned section or a recent one that is empty is left
out, and the separator shows only when both sides have tiles. A bot tile is
its avatar face with a dot while its status is not idle; an app tile is the
app's `icon.svg` / `icon.png` (via `GET /api/apps/icon?dir=`) when it has
one. `hidden` bots are never listed. ⎋ or a click anywhere outside the
tray dismisses it.

- **Clicks.** A bot tile selects that bot in a FusedBot window: an open
  bots-page window is pointed at it in place (`?bot=` rewritten plus the
  page's own `fused:urlchange` listener, no reload), else a new window opens
  on `/?bot=<id>`, which the page reads at boot. An app tile raises a window
  already on `/render?path=<dir>/index.html` (spelled like the page's "Open
  in tab") or opens one. The tray closes first either way.
- **Tile menu (right-click).** A native `NSMenu` laid out like the Dock's:
  the name (checked while a bot is busy), Open, then Options ▸ with Keep in
  Dock / Remove from Dock, Show in Finder (apps only) and Open in Browser
  (the same `/?bot=<id>` or `/render?path=…` in the default browser).
- **Status-item right-click (or ⌃-click).** The utility menu: Open
  FusedBot, Tasks…, Open in Browser, Open App Logs, Quit FusedBot (⌘Q). If
  the tray cannot be built, rumps's plain menu with those same items stays
  on the status item, so Quit is never lost.
- **Tile size.** Dragging the separator up or down resizes the tiles, like
  the Dock; the size (16–128 px, default 52) is saved in `dock.json`.
- **Panel.** A borderless, non-activating `NSPanel` hangs under the status
  item and holds a fixed 1400×520 transparent `WKWebView` canvas (the
  `/dock` page) above a native glass view (`NSGlassEffectView` on macOS 26,
  `NSVisualEffectView` before) sized to the tray rect the page reports. The
  page lays the tray out and reports it (`size` / `tray` / `resize` / `menu`
  script messages); the panel frames that region, slides it in and out on
  its own timer, and tells the page where the status item is (`dockAnchor`),
  when it is shown (`dockShown`, which re-reads `GET /api/dock`) and when a
  tile menu closes (`dockMenuClosed`).
- **Sources.** Bots are read from disk (`store.list_ids` + each `bot.json`);
  a bot the registry already built is read from memory so its live status
  shows. The dock never constructs a `Bot` (that writes `bot.json` and loads
  the Chrome/CDP code); a status a dead process left behind (`running`,
  `waiting`, `paused`) on a bot nobody has loaded reads as idle, as
  `Bot.__init__` would reset it. Bot recency is `meta.updated`. Apps are the
  APPS scan (`apptools.list_apps` over `<workspace>/app`), recency is the
  app's `index.html` mtime.
- **Pins.** A bot's pin is the sidebar's own (`bot.json` `pinned`, written
  through the registry's Bot exactly like the flag route), so Keep in Dock on
  a bot pins it in the sidebar too. App pins live in `<home>/bots/dock.json`
  as `{"pinned_apps": [<real dir>, …], "tilesize": <px>}` in pin order, set
  from the tile menu or the app viewer's ⋯ menu ("Pin to menu bar" / "Unpin
  from menu bar", which reads `GET /api/dock` as it opens). Only a folder
  under the apps root with an `index.html` can be pinned; a pinned folder
  that disappears is no longer listed, and unpinning it still tidies
  `dock.json`.
- **HTTP** (reads are GETs; every POST needs `X-Fused: 1`; a bad request is
  a 400 `{error}`):

  | Route | Body | Reply |
  |---|---|---|
  | `GET /api/dock` | | `{pinned, recent_bots, recent_apps, tilesize}` |
  | `POST /api/dock/open` | `{kind: "bot", id}` or `{kind: "app", dir}` | `{ok, native: true}` in the app, else `{ok, native: false, view}` |
  | `POST /api/dock/home` | | `{ok, native: true}` in the app, else `{ok, native: false, view: "/"}` |
  | `POST /api/dock/reveal` | `{dir}` | `{ok}` after `open -R` (apps root only) |
  | `POST /api/dock/pin` | `{dir, pinned}` | `{ok, pinned_apps}` |
  | `POST /api/dock/order` | `{dirs}`, the pinned apps left to right after a drag | `{ok, pinned_apps}`; unpinned dirs ignored, omitted pinned ones kept at the end |
  | `POST /api/dock/pin-bot` | `{id, pinned}` | `{ok, id, pinned}` |
  | `POST /api/dock/size` | `{tilesize}` | `{ok, tilesize}`, clamped to 16–128 |

  A bot row is `{kind: "bot", id, name, face, status, running, updated,
  pinned}` (`running`: status is not idle); an app row is `{kind: "app", dir,
  name, icon, pinned, mtime}` (`icon`: the folder has an icon file). `native`
  says whether the macOS app took the action through
  `server.native_hooks["dock_open"]` / `["show_home"]` (macapp.py installs
  them; each closes the tray first); in a plain browser `view` is the page
  to go to instead. An unknown kind or bot, or a `dir` outside the apps root
  or not an app folder, gets a 400. `GET /dock` serves the built
  `static/shell-dist/dock.html`, with the same 503 as `/` when the shell is
  not built.
- **Dev.** `FUSED_RENDER_APP_DOCK_SHOW=1` shows the tray once the server is
  ready and makes `kill -USR1 <pid>` show it again, so a script can
  screenshot it without clicking.
- **Tests.** `tests/test_bots_dock.py` covers the store, the tile size, bot
  pins, the ordering, limits and exclusions of `entries()`, every route
  through the real server (open and home with and without the native hooks,
  reveal's root guard) and `/dock` built or not. The panel itself is
  AppKit-only and untested, like `mainwindow.py`.
- Deleted (UI surfaces only): `showcase.py`, `showcase/`, `dock_store.py`,
  `launcher.py`, `launcher_panel.py`, `hotkey.py`,
  `editlink.py`, `icon_color.py`, `static/{index,open,dock,launcher,settings}.html`,
  their tests (`test_showcase`, `test_dock`, `test_launcher`, `test_editlink`).
  `menubar_dock.py` was deleted and came back as the bots/apps tray; its page
  is now built from `frontend/` instead of `static/dock.html`.
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

- Backend: 1053 pytest green (2 skipped). Real `claude` (haiku) + real
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
  page writes and the row went stalled). Seen live too: the model files
  goto's address under `to` every time, so every goto failed with "no url"
  and no Gemma browsing run ever completed; the repair that moves it to
  `url` is unit-tested only — the 4.8 GB weights were removed afterwards
  (disk), so the first local task on this Mac downloads, and a Gemma task
  that browses end to end is still unverified.
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
- Packaged app (`dist/RenderApp-0.11.0.dmg`, 42 MB, python.org framework
  build, highest minos 11.0, ad-hoc signed — Developer ID signing needs the
  "flow" keychain unlocked): launched from the DMG's bundle with its own
  home beside the installed 0.10.2 — 0.11.0 on the next free port, the
  Browser Bots window, a bot created, greeted and run through the Claude
  engine (the CLI found from `~/.local/bin` inside the bundle), goto →
  answer with a step thumbnail. Found and fixed there: every update check
  failed TLS verification because openers were built before the bootstrap's
  dangling `SSL_CERT_FILE` was repaired (STATUS 0.11.0); the rebuilt bundle
  checks cleanly.

Second round, checked live in the FusedBot 0.11.0 bundle (run from the DMG
with its own home, driven through Chrome over CDP): the new-bot chooser with
the four blanks and all 25 presets; a GitHub preset bot (brand face, 6
playbooks copied, read-only instructions prefilled, "created … Comes with 6
github playbooks" line, greeting); the Starter apps strip; Apple Notes
Install → copied to `~/Fused/app/apple-notes`, viewer opened, badge
"Installed"; "Pin to menu bar" from the viewer's ⋯ → `dock.json` written and
the (then rumps-menu) dock rebuilt within one tick — that menu has since
been replaced by the glass tray; `/favicon.ico` is the FusedBot PNG; the
menu-bar cloud icon shows.

Third round, the glass tray (installed `/Applications/FusedBot.app` 0.11.0 on
the real home, shown with `FUSED_RENDER_APP_DOCK_SHOW=1` + SIGUSR1): the tray
drops under the status item on native glass with the Home tile, the
separator and the three app tiles (Apple Notes icon, T, L); the title bar
ends in Open in Browser + Home; `/dock` and `/api/dock` answer; the update
check passes. A source run with seeded bots showed bot and app tiles both
sides of the separator. Not clicked (AppKit cannot be driven from here): a
tile (`menubar_dock.item_open` → `show_bot` / `show_url`), the tile's
right-click NSMenu, the status item's right-click utility menu, pinning from
the menu, the separator drag, the Home button (`mainwindow.goHome_`); a
plate-less bot tile has not been seen natively (the page rules are in
`frontend/dock.html`); the `?bot=` boot deep link; the Google starters'
"Needs setup → Ready" badge (needs a service-account key).

Not exercised live: dictation (needs a microphone grant), iMessage (needs
Full Disk Access), Chrome profile import and encryption at rest (unit
tested in browser.py).
