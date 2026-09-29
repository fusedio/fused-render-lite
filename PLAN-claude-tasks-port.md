# PLAN: port fused-render's Claude sessions / tasks to Render App

Written 2026-09-29 against fused-render `main` 992b7b29f and
fused-render-lite `main` d2ed3d2 (0.10.0).

## Status (2026-09-29, later the same day): phase 1 + basic UI landed

Ported from fused-render's `fused-tasks-api` branch, now on fused-render main (last synced at 9152c5d08,
which carries D890). The approach changed from the plan below in one way:
the WHOLE cluster is copied verbatim with import rewrites, schedule /
drafts / project queue included, instead of a hand-extracted subset. Fewer
seams, and the D890 `create` / `send` / `cancel` routes work unchanged
because they are built on the scheduler.

What is in:

- `fused_render_app/templates/claude/` (engine, verbatim; `RUNS` renamed to
  `fused_render_app_claude-<uid>`) and `templates/shared/` (the five helpers).
- `schedule`, `cron`, `recur`, `schedule_wake` (LaunchAgent label
  `io.fused.render.app.schedule-wake`), `drafts`, `project_queue`,
  `queue_manager`, `tasks_store`, `tasks_watch`, `session_liveness`,
  `claude_spawn` (agent path = the package), `claude_session_move`,
  `claude_artifacts`; routers `routes/{tasks,claude_sessions,queue_events,
  schedule,drafts,claude_artifacts,image_convert}.py`.
- Stubs for what lite lacks: `current_apps` (app dir = extract dir),
  `app_listing`, `index_ignore.MountGuard`, `shell/mounts`,
  `claude_config/{preferences,lib}` (writes `~/.claude/settings.json`),
  `shell/prefs` flags.
- `_web.py`: `Query(alias=)`, `File`/`UploadFile` + multipart, `BaseModel`
  stand-in, `put`, `run_in_threadpool`; `server.py`: `do_PUT`/`do_DELETE`,
  query params on POST, `HTTPException` → status, trusted-path `/api/run`
  for `templates/**` on the base interpreter (`env.run_python_trusted`),
  `FUSED_RENDER_HOME` + `FUSED_RENDER_CLAUDE_BIN` exported, tasks threads
  started from `start_ai`, `/api/prefs`, `/api/capture/shot-region` → 409.
- `runtime.js`: the D890 `fused.tasks` block spliced verbatim (marker
  `fused-tasks:begin`), `tasks` on the `window.fused` literal.
- UI (basic): `static/tasks.html` (list, peek, new task, archive / erase /
  cancel; honours `/api/tasks/ui`'s `project`, `embed`, `peek`) and
  `static/chat.html` (hosts `templates/claude/template.html` under
  `/render`, `_fusedParamBoundary` when framed). Window → Tasks (⌘⇧T) and a
  "Tasks…" menu-bar entry.
- Tests: `tests/test_tasks_port.py` (routes on the shim, state under the app
  home, trusted run) plus fused-render's pure-module tests copied
  (`test_tasks_store`, `test_tasks_watch` not yet, `test_cron`,
  `test_queue_manager`, `test_schedule`, `test_schedule_recurring`,
  `test_claude_session_host`). conftest isolates `FUSED_RENDER_HOME`, `HOME`,
  `CLAUDE_CONFIG_DIR` and the import-time `CLAUDE_DIR` constants.
- Verified live: task created from `POST /api/tasks/create`, run by the real
  `claude` CLI, listed as done; follow-up typed in the chat composer inside
  the Tasks peek, answered, row updated.

Decisions taken (defaults, revisit if wrong): D-A extract dir, no relocate
yet; D-B separate state under `~/.fused-render-app` (task numbers etc.);
D-C legacy `template.html` as the basic chat UI; D-D unchanged from D890
(scheduler-backed create, `pending:` keys exist).

Re-sync after fused-render's `fused-tasks-api` merge:
`python scripts/sync_claude_tasks.py <fused-render checkout> --runtime`, then
`git diff`, `pytest`, and re-check `/api/tasks/{create,ui}`,
`/api/tasks/{key}/{send,cancel}` against the D890 row.

Not done: relocate on re-export (D-A), TestClient-based fused-render tests,
window identity for `/chat` and `/tasks` windows (`WindowManager` counts
them as Home, so the Dock's Home tile can raise a chat window),
`claude_health` full module / install / login (phase 3), task-status
notifications (§4.4), title-bar Chat button, React UI (D-C option d), DMG
size row in STATUS.md (templates are ~2.2 MB on disk, 1 MB of it
`template.html`; py2app `packages` copies the package whole, hatch too —
confirm on the next DMG build).

Companion work, in flight elsewhere: the page-side `fused.tasks` API (D890)
lives in fused-render's `fused-tasks-api` worktree
(`fused-render/.claude/worktrees/magical-napping-patterson`, uncommitted as of
this writing). Its contract is the D890 row in that worktree's `DECISIONS.md`
and `skills/fused-render-tasks/SKILL.md`. Render App has to host the same
API, so this plan treats D890 as the driving consumer of the backend.

## 1. What "the infrastructure" actually is

Three layers in fused-render, each with a different port cost.

| layer | where | lines | port shape |
|---|---|---|---|
| Chat engine | `fused_render/templates/claude/{agent,session_host,permission_server,artifacts,condition,app}.py` + `templates/shared/{appenv,private_dir,procutil,app_entry,file_history}.py` | ~8.5k + 1.6k | copy verbatim; designed to be copied out (D166: never imports `fused_render`) |
| Server bookkeeping | `tasks_store`, `tasks_watch`, `session_liveness`, `claude_session_move`, `claude_spawn`, routers `tasks`, `claude_sessions`, `queue_events` | ~13k | port a trimmed subset onto lite's `_web.py` shim |
| UI | React `frontend/src/apps/claude/**` (47k src) + shell task pages (30k src + 10k CSS); legacy vanilla `templates/claude/template.html` (19k) | — | policy decision, see §5 |

Everything else around it in fused-render (schedule/cron/recur, project
queue, drafts, supervisor tray, claude_config, canvases, git commit-per-turn)
is either absent from lite's product or built on schedule entries. Dropped.

### 1.1 How a chat actually runs (unchanged by the port)

```
page (chat UI)  ──POST /api/run {py: <templates>/claude/agent.py, action}──▶ lite server
                                                                             │ env.run_python → child python
                                                                             ▼
                                                                         agent.py  (start/poll/decide/send/cancel/history/…)
                                                                             │ start: Popen detached
                                                                             ▼
                                                                    session_host.py  (owns CLI stdin for the session, D674)
                                                                             │ Popen, cwd = target dir
                                                                             ▼
                              claude -p --input-format stream-json --output-format stream-json
                                     --session-id <uuid4> | --resume <sid>
                                     --mcp-config run/mcp.json --permission-prompt-tool mcp__fused_approvals__approve
                                     [--permission-mode …] [--model] [--effort]
                                                                             │ stdio MCP
                                                                             ▼
                                                                  permission_server.py (perm cards, app_state tool)

run dir  $TMPDIR/fused_render_claude-<uid>/runs/<id>/   out.jsonl err.log pid host.json mcp.json inbox/ perm/ appstate/ cursor …
state    <FUSED_RENDER_HOME|~/.fused-render>/claude-sessions/  task_ids.json read.json deleted.json session_settings.json
claude   ~/.claude/projects/<munged cwd>/<sid>.jsonl   ~/.claude/sessions/<pid>.json   ~/.claude/file-history
events   session_host / permission_server ──POST ${FUSED_RENDER_ORIGIN}/api/tasks/queue/event──▶ server (turn_ended, exited, card_*)
```

Nothing in the browser parses stream-json. `agent.py` turns `out.jsonl` into
`segments[]`; the client polls `action=poll` every 400 ms. No SSE, no
WebSocket anywhere. Tasks pages long-poll `GET /api/tasks/changes?since&wait=25`
plus `GET /api/tasks/pulse`. This is good news for lite: its stdlib server
already does chunked responses and sync long-polls.

Liveness is layered (strongest first): CLI registry `~/.claude/sessions/<pid>.json`
→ transcript tail (45 s running / 90 s stale, `session_liveness`) → send mark
(`POST /api/tasks/running`, 15 s TTL) → `turn_ended`/`exited` events from
session_host. All four have to come across or the Tasks list lies.

## 2. Landing zone in Render App

- Server: stdlib `ThreadingHTTPServer` (`server.py:907`), thread per connection,
  one shared asyncio loop for async routes (`_web._LOOP`). Routers register via
  `AI_ROUTER.include_router` (`server.py:89-94`, misnamed; it is the general
  mount). Hand-written `do_GET`/`do_POST` chains for everything else.
- `_web.py` shim is FastAPI-shaped but missing what the ported routers use:
  `Query(default, alias=)`, pydantic `BaseModel` bodies, `router.put`,
  `do_PUT`/`do_DELETE`, `HTTPException → status` (today becomes 500),
  `run_in_threadpool`, and POST dispatch passes `q={}` so POST routes see no
  query params.
- `/api/run` (`server.py:562`) accepts any absolute `py` and runs it through
  `env.run_python(py, params, app_dir)` with `app_dir = dirname(py)`. For a
  dir with no `pyproject.toml` that means the shared **legacy venv**
  (`env.py:228`): a `uv sync` on first use and a legacy-venv interpreter on
  every poll. Not acceptable for `agent.py` (stdlib only). Phase 0 adds a
  trusted-path branch: `py` under `fused_render_app/templates/` runs on
  `base_python()` via `_child.py` with no `env.ensure`. Same thing
  fused-render's `executor.py` does (D72).
- Claude CLI today: one warm process in `routes/ai_relay.py` with
  `--no-session-persistence`, tools off, `/clear` per request. No sessions,
  no resume, nothing reads `~/.claude/projects`. `claude_health.py` is a
  41-line stub (resolve + candidates only). `paths.fix_process_env()` already
  fixes Finder's PATH.
- Env already exported at startup: `FUSED_RENDER_ORIGIN` (`server.py:921`),
  `FUSED_RENDER_HOME_DIR=~/.fused-render-app` (`:925`). **Not**
  `FUSED_RENDER_HOME`, which is what `tasks_store` and `agent._state_file` read.
- Apps: a `.fused` extracts to `~/.fused-render-app/apps/<slug>-<sha256[:16]>/`
  (content-hash keyed; a re-export is a new dir). `.fused/` inside the extract
  symlinks to `fused_data/<app_id>` (stable across re-exports). There is no
  write-back from an extract dir into the `.fused`.
- Windows: `mainwindow.WindowManager` treats any URL that is not
  `/open?_file=` as the Home singleton. A Tasks page needs its own identity.
- Notifications: `jobs.set_transition_hook` has one slot, owned by
  `jobnotify.install`; banners decided by `notify_policy.decide` on job id
  prefix. macOS `UNUserNotificationCenter` via `webnotify`.
- UI: hand-written static HTML per page, inline CSS tokens, no build step
  (skill `setting-up-dev-env`: "No frontend — nothing to npm install").
  `runtime.js` (1886 lines) is injected only into `/render` pages and defines
  `window.fused` at `:1833`. Shell pages fetch directly with `X-Fused: 1`.
- Packaging: zero runtime deps by policy; DMG 43.4 MB; py2app `packages`
  includes `fused_render_app` and `resources` lists only `static/`; hatch
  wheel `packages=["fused_render_app"]`. No Node anywhere in CI or
  `build_dmg.sh`.
- Tests: pytest, `conftest.client` boots `serve_in_thread(0)`;
  `tests/fake_claude.py` is a stream-json stand-in for `claude` (FAIL / CRASH
  / SLOW knobs). Reusable for session_host tests.

## 3. Decisions to make before coding

Each one changes the shape of the port. Recommendation first, alternative after.

### D-A. What is Claude's working directory for a `.fused` app?

Claude Code keys sessions by cwd. Lite's extract dir changes on every
re-export, so sessions started there are orphaned when the user gets a new
`.fused`.

- **Recommend: target = the extract dir**, same semantics as fused-render
  ("Claude edits the files the page renders"; `app_state`/pane logic keys off
  the entry html in that dir). On re-extract of the same `app_id`, call
  `claude_session_move.relocate(old_dir, new_dir)` (D548) so transcripts and
  task numbers follow. Verified: `open_app_file` reuses an existing extract
  dir (`appfile.py:364`, `reused: True`), so Claude's edits survive re-opens
  of the same `.fused`; only a re-export moves them. Lite does not record the
  previous extract dir today, so `relocate` needs a new witness: write the
  extract dir into `fused_data/<app_id>/meta.json` on open and relocate when
  the recorded dir differs from the new one.
- Alternative: target = `fused_data/<app_id>` (stable, no relocate). Loses
  "Claude edits the app"; a task can then only touch saved state. Simpler, but
  a different product than fused-render's.

### D-B. Share state with fused-render, or separate?

A straight port writes `task_ids.json`/`read.json`/`session_settings.json`
into `~/.fused-render/claude-sessions/` and sees fused-render's run dirs under
`$TMPDIR/fused_render_claude-<uid>/runs`.

- **Recommend: separate.** Set `FUSED_RENDER_HOME=~/.fused-render-app` in
  `make_server` (next to `FUSED_RENDER_HOME_DIR`), and patch the `RUNS`
  constant in the copied `agent.py` to `fused_render_app_claude-<uid>`. Lite's
  cwd buckets are distinct anyway, so shared numbering buys nothing and shared
  run scans cost a permission-card stat over the other app's runs.
- Alternative: share. One patch fewer in `agent.py`; task numbers consistent
  if a user opens the same folder in both. Not lite's case.

### D-C. UI: React tree, or something else?

The user asked for "the UI we have built", which is the React tree
(`apps/claude` + shell task pages). fused-render made native React chat the
default on 2026-09-17; `template.html` is its escape hatch and already drifts
(last touched Sep 22; agent.py Sep 25). Discriminating constraint: does lite
accept Node in CI and `build_dmg.sh`?

- **Recommend: (d) standalone chat+tasks bundle built in fused-render,
  vendored into lite as static files.** A Vite lib-mode entry in fused-render
  (`mountChat(el, opts)`, `mountTasks(el)`) with sched/queue/drafts stubbed and
  the `@shell` back-edges cut. Lite keeps its no-build rule; fused-render owns
  the build it already has. Cost: cross-repo release coupling, and a
  `check-boundaries` carve-out in fused-render. Output ≈ 600 KB JS + 90 KB CSS
  before adding task pages (current `ClaudeChat-*.js` chunk), against a 43 MB
  DMG.
- Alternative (a): add Vite/React/Tailwind 4/base-ui to lite and copy the
  tree. Full parity and the bun tests come along, but it adds Node to
  `test.yml` and `build_dmg.sh` and reverses the no-build rule.
- Stopgap (b1): serve `template.html` under lite's `/render`. Lite's
  `runtime.js` already implements every `fused.*` it calls. Days of work, but
  it lacks recap, turn breaks, context meter, held answers, project queue, and
  it will keep drifting. Useful only as a phase-1 smoke test of the engine.
- Not viable: shipping the built `shell-dist` chunk (it imports the whole
  shell `main-*.js`, ~2.2 MB raw); hand-porting 47k lines to vanilla.

### D-D. `fused.tasks.create` without a scheduler

D890 implements `POST /api/tasks/create` as `schedule.create + run_now` and
keys the row `pending:<entry>` until Claude starts. Lite has no schedule.

- **Recommend: spawn directly.** `agent._start` already mints the uuid4
  `--session-id` before spawning, so lite can answer `create` with the real
  session id and never emit a `pending:` key. The `TaskHandle` contract
  tolerates this (`h.key` is a getter). Flag to the window.fused agent so the
  runtime does not assume a rekey always happens.
- Consequence: `due` in `create` is rejected (400) in lite, or accepted only
  as "now".

### D-E. Scope of the task surface (which of fused-render's pages)

- **Recommend for v1:** chat (split pane + chat-only + peek), Recent list,
  Tasks list view, side peek, kebab (archive / delete / erase), permission /
  question / plan cards, attachments and paste, snapshots. That is the "must"
  column in §5.
- Defer: Board, Calendar, Cards wall, New task modal (5.6k lines, pulls the
  explorer), Comment/annotation mode (6.9k, needs pane + capture + transcribe),
  scheduling UI, project queue UI, drafts sync.

## 4. Backend port inventory

Target layout in lite:

```
fused_render_app/
  templates/claude/     agent.py session_host.py permission_server.py artifacts.py condition.py app.py vendor/  [+ template.html if b1]
  templates/shared/     appenv.py private_dir.py procutil.py app_entry.py file_history.py   (reconcile with lite's shared/appenv.py)
  tasks_store.py  tasks_watch.py  session_liveness.py  claude_session_move.py  claude_spawn.py
  claude_health.py (full, macOS branches only)  claude_install.py  claude_login.py       [phase 3]
  routes/tasks.py  routes/claude_sessions.py  routes/queue_events.py  routes/claude_health.py [phase 3]
  _web.py  (+Query, +put, +HTTPException mapping, +BaseModel-free bodies)
  server.py (+do_PUT/do_DELETE, +trusted-path /api/run, +FUSED_RENDER_HOME, +tasks_watch.start, +warm)
```

| module (fused-render) | lines | verdict | notes |
|---|---|---|---|
| `templates/claude/agent.py` | 6885 | must, verbatim + 1 patch | `RUNS` constant per D-B. Reads `FUSED_RENDER_HOME`, `FUSED_RENDER_CLAUDE_BIN`, `CLAUDE_CONFIG_DIR`. `_commit_turn` (git) is a no-op outside `workspace_dir()`; canvases / `Bash(fused:*)` / plugin-dir paths inert without their env vars. |
| `templates/claude/session_host.py` | 457 | must, verbatim | posts `turn_ended`/`exited` to `${FUSED_RENDER_ORIGIN}/api/tasks/queue/event`. |
| `templates/claude/permission_server.py` | 788 | must, verbatim | stdio MCP; posts `card_raised`/`card_cleared`. |
| `templates/claude/{condition,app}.py`, `vendor/` | ~1.4k | must | `app.py` resolves the pane entry via `shared/app_entry`. |
| `templates/claude/artifacts.py` + `claude_artifacts.py` + `GET /api/claude-artifacts` | ~730 | optional | decoration; stub the mount check to False. |
| `templates/shared/{private_dir,procutil,app_entry,file_history}.py` | ~1.4k | must, verbatim | agent.py puts `../shared` on `sys.path`; must sit beside `templates/claude/`. `file_history` only used by snapshot actions. |
| `templates/shared/appenv.py` (239) vs lite `shared/appenv.py` (239, identical today) | — | reconcile | keep one copy; lite's `shared/` can become a symlink or the file moves under `templates/shared/`. |
| `session_liveness.py` | 439 | must, verbatim | pure. |
| `tasks_store.py` | 1794 | must | needs `_view_url_codec.canonical_fs_path` (lite has it). STATE_DIR per D-B. |
| `tasks_watch.py` | 1121 | must, adapted | `_wake_schedule` → no-op; the lazy back-edge into `routers.tasks` for `_agent_module`/`_run_sessions` → point at `claude_spawn.load_agent`. Start its daemon thread from `server.start_ai`. |
| `claude_spawn.py` | 175 | must (part) | `load_agent()` (in-process exec of agent.py for read paths: `/history`, tasks rows). `spawn_helper` needed for `fused.tasks.create`/`send` from the server (D-D) and for resuming a task with no live host. Repoint `agent_path()`; drop `core_templates` import. |
| `claude_session_move.py` | 369 | must if D-A = extract dir | new trigger in `appfile.open_app_file` when an `app_id` maps to a new extract dir. |
| `routers/tasks.py` | 7104 | must, extract | keep: `/api/tasks` (+`?scope=app`/`?under=` from D890), `/changes`, `/pulse`, `/running`, `/idle`, `/{key}/messages`, `/read`, `/settings` GET+POST, `/archive`, `/unarchive`, `/delete`, `/erase`; D890's `/create`, `/{key}/send`, `/{key}/cancel` (re-implemented per D-D). Core helpers to lift: `_scan`, `_collect`, `_status`, `_running_now`, `_live`, `_parked_runs`, `_attention_of`, `_row`, `_numbers`, `_mark_unread`, `warm`, `_build_task_rows`, `_thread`, `_erase_session_files`. Stub `schedule` (`list_entries()→[]`, `busy_sessions()→set()`), drop `drafts` (48 refs), drop queue (86 refs), replace `current_apps.observe` and `_page_scope`'s `current_apps.app_dir_for` with "extract dir of the `X-Fused-Page` path". Drop `/scheduled`, `/queue/*`. |
| `routers/claude_sessions.py` | 1239 | must (part) | `/history` (in-process via `load_agent`, avoids a 7k-line exec per click), `/liveness`, `/defaults` GET+PUT. Optional: `/recap` (spawns `claude -p --max-turns 1 --model haiku`), `/triage`. Drop `/`, `/home`, `/summaries` (Explorer listings). |
| `routers/queue_events.py` | 211 | must, reduced | keep the endpoint so host/MCP posts do not 404; `turn_ended`/`exited` → `tasks_watch.mark_turn_ended`; answer `{ok, ignored}` for card events. ~30 lines. |
| `claude_health.py` (full) | 1289 | phase 3 | `probe_version`, `auth status`, `doctor`, login-shell PATH probe, `claude-health.json`. Drop win32/linux branches. |
| `claude_install.py`, `claude_login.py`, `routers/claude_health.py` | ~1k | phase 3 | onboarding: `curl install.sh | bash`, `claude auth login`; progress through lite's `jobs.py`. |
| `project_queue.py`, `queue_manager.py`, `routers/run.py` gate | ~2.7k | drop | built on schedule entries; default-off flag. |
| `schedule.py`, `cron.py`, `recur.py`, `schedule_wake.py`, `routers/schedule.py` | ~6.3k | drop | stub module keeps the tasks router's shape. |
| `drafts.py`, `routers/drafts.py` | ~1.9k | drop | composer drafts stay client-side (sessionStorage, as the legacy template does). |
| `executor.py`, `supervisor/*`, `claude_config/*`, `canvases.py` | — | drop | lite has `env.run_python`, `macapp.py`; config editor out of scope. |

### 4.1 `_web.py` / `server.py` additions (prerequisite for any router)

1. `Query(default, alias=None)` marker honoured by `call_route` (tasks.py uses `alias="from"`).
2. `APIRouter.put` + `Handler.do_PUT`, and `do_DELETE` → `_dispatch`.
3. `_dispatch` maps `HTTPException(status_code, detail)` to that status with `{"error": detail}`.
4. POST/PUT dispatch passes parsed query params, not `{}`.
5. Bodies: rewrite pydantic models to `body: dict` + a tiny validator, or add a 30-line `BaseModel` stand-in (`__init__(**kw)`, attribute access, unknown keys dropped). Prefer the stand-in: fewer edits in 7k lines of router.
6. `run_in_threadpool` → `asyncio.to_thread` alias in the shim.
7. `X-Fused` guard on every task write route (D890 closed that gap upstream; keep it closed here).

### 4.2 Wiring in `server.py`

- `make_server`: export `FUSED_RENDER_HOME` (D-B) and
  `FUSED_RENDER_CLAUDE_BIN=<claude_health.resolve()>`. `agent._claude_bin`
  reads only the latter, while lite's relay prefers `FUSED_RENDER_APP_CLAUDE_BIN`;
  exporting it keeps agent.py, the warm relay and `fake_claude.py` tests on
  one binary.
- `_api_run`: trusted-path branch for `fused_render_app/templates/**` → `base_python()` + `_child.py`, no `env.ensure`, 600 s cap stays.
- `start_ai`: `tasks_watch.start()`, `tasks.warm` thread, `claude_health.warm_in_background` (phase 3).
- `stop_ai`: nothing new; session hosts are detached by design and reap themselves after 30 s idle.
- `/api/fs/stat` must return `templates: [{mode: "claude", path: <agent dir>}]` for the target so the chat can resolve the agent dir (`resolveAgentDir`). Simplest: a constant entry for every path.
- Static routes for the new pages (`/tasks`, `/chat`), plus `app_file_of` / window identity so a Tasks window is not the Home singleton.

### 4.3 `fused.tasks` in lite's `runtime.js`

Copy the D890 block from the `fused-tasks-api` worktree's `runtime.js` diff
(~580 lines: `taskFetch`, `tasksListing`, shared `/changes` feed per scope,
`TaskHandle`) into lite's `window.fused` literal (`runtime.js:1833`). Same
literal the window.fused agent is editing: coordinate so one of the two owns
the merge. Differences to encode: no `pending:` keys (D-D), `due` rejected,
`queued` always false.

### 4.4 Notifications

task-status-notify's three transitions (`in_progress→done`, `→blocked`,
`→needs_attention`) map onto `notify_policy.decide` with a new `sys:task:<sid>`
job-id prefix, so they ride the existing one-slot `jobs.set_transition_hook`
via `jobnotify`. Click target = the task's chat page (`jobnotify.page_target`).
Emit from `tasks_watch` on status change of a row (status is derived per
listing, so debounce ~15 s to avoid the flicker D890 documents).

## 5. UI port inventory (assuming D-C = vendored bundle built in fused-render)

Chat half (`frontend/src/apps/claude/`):

| verdict | modules | why |
|---|---|---|
| must | `protocol/*` (agent, types, run-controller, controller-api, segments, wire, history, summaries, markdown, typer, trouble, quota, inbox, snapshots); `ui/` Transcript, Turn, SegmentView, ToolChip, Thinking/Notice/WorkingLine, Perm/Question/Plan cards, Composer + Model/Effort/Permission pills, Topbar, Kebab, TroubleView, Home + Lists (Recent, Snapshots); `params/`; CSS chat/transcript/composer/home/hljs | the conversation |
| must | `pane/` AppPane + paneUrl + appState | lite's whole product is "the app beside the chat"; pane iframe = `/render?path=<entry>` which lite already serves |
| optional | `shots/` paste/drop/native-capture; `ui/Attach*`; recap; ContextMeter; `live/watch` | one endpoint each; lite has `/api/capture/screenshot`, `/api/fs/upload`, `/api/fs/raw` |
| defer | `ann/` (Comment mode + voice walkthrough) | 6.9k lines, needs pane + capture + `/api/ai/transcribe` (lite has it) |
| drop | `sched/`, SchedButton/SchedBlock/Waiting, queue admit/decide in run-controller, drafts sync, feature-flag + ChatMount legacy switch, xo-capture, git/`_listing` pane modes, canvases host | no backend in lite |

Tasks half (`frontend/src/shell/`):

| verdict | modules | why |
|---|---|---|
| must | list over `/api/tasks` + `/changes` long-poll; `tasks-lib` subset (status, title, sort, read/unread, attention rows); `tasksPulse`; TaskPeek + `task-peek-store`; TaskPeekWho | minimal task surface; peek only frames the chat |
| cheap once chat exists | TaskCards (grid of chat mounts) | |
| optional | task-status-notify (3 transitions) → lite native banners (§4.4) | |
| drop | Board, Calendar, ScheduleCalendar, NewJobModal, schedule-lib, draft-run, ActivityDock, GlobalSidebar/Home task strips | no schedule; lite has no sidebar |

Third-party runtime deps of the bundle: react, react-dom, marked, dompurify,
highlight.js, @base-ui/react, lucide-react, clsx/tailwind-merge/cva, tailwind 4
(shadcn primitives only). All bundled; nothing new in lite's Python deps.

Shell back-edges to cut for a standalone entry: `@shell/tasks-lib` (5.5k),
`@shell/tasksPulse`, `@shell/ScheduleTaskViews` (5.9k, imported by Lists and
Topbar for schedule chips), `@shell/TaskPeekWho`, and the 5.8k `@platform/lib/api`
monolith (slim to runPy/statPath/rawUrl/getTasks/getTaskChanges/prefs).

Lite-side host pages (hand-written, index.html palette): `static/chat.html`
(mounts the bundle for one `_file` + `session_id`, split with the app pane),
`static/tasks.html` (list + peek). Entry points: title-bar button next to
Edit / Open in Browser / Home, Dock and launcher rows, menu-bar item.

## 6. Phases

**Phase 0: verify and spike (no product change).**
- Trusted-path `/api/run` for `templates/**` (§4.2). Measure poll cost on
  `base_python()` vs today's child spawn.
- Copy `templates/claude` + `templates/shared` in, run `template.html` under
  `/render` against a showcase app as the smoke test (option b1, throwaway).
  Proves: CLI spawn, permission MCP, session_host events reaching lite,
  `~/.claude/projects` bucket for an extract dir, `fake_claude.py` in tests.
- Confirm py2app carries `templates/` (non-`.py` files: `vendor/`, html) and
  hatch wheel includes them; add to `resources` if not.
- Decide D-A, D-B, D-C, D-D with the owner.

**Phase 1: engine + core routes + `fused.tasks`.**
- `_web.py`/`server.py` shim work (§4.1, §4.2).
- Port `session_liveness`, `tasks_store`, `tasks_watch`, `claude_spawn`,
  `claude_session_move` (per D-A).
- `routes/tasks.py` (trimmed), `routes/claude_sessions.py` (history,
  liveness, defaults), `routes/queue_events.py` (reduced).
- D890's `create`/`send`/`cancel` per D-D; `fused.tasks` block in `runtime.js`.
- Tests: ported `tests/test_tasks_*` subset from fused-render on
  `fake_claude.py`; new tests for the shim additions and the trusted-path run.
  Isolation: these modules read `~/.claude/projects` and `~/.claude/sessions`;
  lite's conftest isolates only `FUSED_RENDER_APP_HOME`, so monkeypatch `HOME`
  and `CLAUDE_CONFIG_DIR` per test as fused-render's conftest does, or the
  suite reads the developer's real transcripts.
- Deliverable: a `.fused` page can drive tasks headlessly through
  `fused.tasks`; no chat UI yet.

**Phase 2: UI.**
- fused-render side: standalone Vite entry (`mountChat`, `mountTasks`), stubs
  for sched/queue/drafts, boundaries carve-out, a `bun run build:lite-chat`
  that emits `dist-lite/`.
- Lite side: `static/chat.html`, `static/tasks.html`, vendored bundle under
  `static/vendor/chat/`, routes, window identity, title-bar/Dock/launcher
  entry points, `STATUS.md` size row.
- Notifications (§4.4).

**Phase 3: onboarding.**
- Full `claude_health`, `claude_install`, `claude_login`, health route; a
  "Claude not installed / not signed in" strip on Home and in chat (fused-render's
  `ClaudeHealthStrip`, TroubleView D300/D328).

## 7. Size and packaging impact

| addition | approx |
|---|---|
| Python: engine + shared + store/watch/liveness/move/spawn + 3 routers | ~20k lines source, pure Python, ~1 MB unpacked, zero new deps |
| `template.html` + `vendor/` (only if b1 kept) | ~1.1 MB raw / 340 KB gz |
| chat+tasks bundle (D-C = d) | ~0.7 MB JS+CSS before task pages; budget ≤ 1.5 MB |
| DMG delta | well under 2 MB on 43.4 MB; record in `STATUS.md` |

No pydantic, no FastAPI, no Node in lite. Every megabyte still has to earn
its place; the bundle is the one line item to watch.

## 8. Coordination with the window.fused agent

- Same `runtime.js` literal (`:1833`). Agree who merges the `fused.tasks` block.
- D890 create/send/cancel routes are not yet in the fused-render diff; pin
  lite's implementation to the D-row text and the skill, and re-read the
  worktree before phase 1.
- D-D: lite never emits `pending:` keys; `due` unsupported; `queued` always
  false. Runtime must not assume a rekey.
- `_page_scope`: `X-Fused-Page` already carries the page path; lite resolves
  the app dir as the extract dir containing it (`appfile.extract_dir_for`).
- Hosted gate: unchanged (`fused.env === "local"`; present-and-throws).

## 9. Open questions for the owner

1. D-A: extract dir (Claude edits the app; relocate on re-export) or
   `fused_data` (stable, state-only)?
2. D-C: vendored bundle built in fused-render, or Node in lite?
3. Is the Tasks page a first-class window (Dock tile, ⌥-shortcut row) or a
   panel inside the app window?
4. Erase (deletes the transcript) from lite's UI: yes as in fused-render's
   kebab, or Tasks-page only as D890 keeps it for pages?
5. Phase 3 install/login: ship in the same release or later?

## Sources

- Mapper reports, full module-level inventories: `docs/claude-port-maps/{be-map,fe-map,lite-map}.md`.
- fused-render `DECISIONS.md` rows: D72, D161, D166, D239, D246–D248, D299,
  D300/D328, D307, D322, D394, D415, D548, D674–D696, D740, D890.
- fused-render `docs/CLAUDE-TEMPLATE-POC.md`; `apps/claude/README.md`
  (its `.claude-design/` references are absent from the repo).
