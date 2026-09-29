# Migration map: fused-render Claude sessions/tasks backend → fused-render-lite

Source: /Users/vasu/Documents/fused-dev/fused-render (read-only survey). Line refs are `file:line` against that tree unless prefixed `lite:` (= fused-render-lite worktree `fused_render_app/`).

## 0. Decisions to make first

1. **The chat engine is not in `fused_render/`.** It lives in `fused_render/templates/claude/` and is designed to be copied out as a unit. Both chat UIs (legacy `template.html`, React `frontend/src/apps/claude/protocol/agent.ts`) reach it only through `POST /api/run` with `py=<templates>/claude/agent.py` and an `action` param.
   - Lite's `/api/run` (lite:`server.py:562` `_api_run`) accepts any absolute `py` and runs it via `env.run_python(py, params, app_dir)` (lite:`env.py:403`); `app_dir` falls back to `dirname(py)`. So `fused_render_app/templates/claude/agent.py` can be called as-is, provided `env.ensure()` finds no pyproject there and builds nothing. agent.py is stdlib-only, so any interpreter works.
2. **Missing shared helpers.** agent.py imports `appenv`, `private_dir`, `procutil`, `app_entry`, `file_history` from `templates/shared/` (`agent.py:92-100`, `:716`, `:5879`). Lite's `fused_render_app/shared/` has only `appenv.py`, `background_app.py`, `fused_ai.py`.
   - Must bring (~1,390 lines): `file_history.py` (1084), `private_dir.py` (116), `procutil.py` (100), `app_entry.py` (87).
   - agent.py puts `../shared` on `sys.path`, so they must live at `templates/shared/` beside `templates/claude/`.
   - Copy verbatim; keep Windows branches; do not fork.
3. **Claude's cwd changes on every re-export in lite.** Lite extracts to `~/.fused-render-app/apps/<slug>-<sha256 prefix>/` (lite:`appfile.py:7`, `:205`). Claude Code keys sessions by cwd (`~/.claude/projects/<munged cwd>/`), so a changed `.fused` gets a new bucket and its conversations are orphaned.
   - Option A: on re-extract call `claude_session_move.relocate(old, new)`.
   - Option B: pin the chat target to a stable dir (e.g. `fused_data/<app_id>`) — breaks "Claude edits the files the page renders".
4. **State location / sharing with fused-render.**
   - `tasks_store.STATE_DIR` = `$FUSED_RENDER_HOME or ~/.fused-render` + `/claude-sessions` (`tasks_store.py:130`). agent.py `_state_file` uses the same rule (`agent.py:1488`).
   - Lite exports `FUSED_RENDER_HOME_DIR=~/.fused-render-app` (lite:`server.py:925`) but NOT `FUSED_RENDER_HOME` → a straight port writes task numbers, read marks, session settings into fused-render's `~/.fused-render/claude-sessions/`.
   - `RUNS` is hardcoded `$TMPDIR/fused_render_claude-<uid>/runs` (`agent.py:102-123`) → lite and fused-render see each other's runs in `_live_run`, `tasks_watch._read_permission_cards`, `project_queue.scan_runs`.
   - Sharing keeps task numbers consistent across apps; separating requires patching both constants. Lead's call.
5. **Lite's `_web.py` shim gaps.** No `Query`, no pydantic `BaseModel`, no `fastapi.concurrency.run_in_threadpool`, no `HTTPException(detail=)` semantics. `routers/tasks.py` uses `Query` + `BaseModel` (e.g. `RunningPatch` `:4737`); `queue_events.py` and the `claude_health` router use `run_in_threadpool`. Each ported router: extend the shim or rewrite to `body: dict`.
6. **Lite already exports `FUSED_RENDER_ORIGIN`** (lite:`server.py:921`). Consumers: `session_host`/`permission_server` POST events to `${ORIGIN}/api/tasks/queue/event`; `artifacts.py` GETs `/api/claude-artifacts`; `agent._custom_env` GETs `/api/env/custom-env`. All four fail silently when the route is absent.

## 1. Chat engine: `fused_render/templates/claude/`

| file | lines | purpose |
|---|---|---|
| `agent.py` | 6885 | runPython entry `main(action=...)` `:6759` |
| `session_host.py` | 457 | owns CLI stdin for the life of a session (D674) |
| `permission_server.py` | 788 | stdio MCP server `fused_approvals`, tools `approve` + `app_state` (D161) |
| `artifacts.py` | 292 | artifact list via `/api/claude-artifacts` or live transcript scan |
| `condition.py` | 98 | template gate |
| `app.py` | 20 | resolves pane entry html via `shared/app_entry.py` |
| `template.html` | 19378 | legacy vanilla chat UI (fe-map scope) |
| `vendor/` | ~1285 | marked, purify, highlight |
| `templates/claude_split/` | 0 | empty; stale `__pycache__` only |

**Actions** (`agent.py:6759-6885`): turn control `start`, `poll`, `decide`, `app_state`, `send`, `live_host`, `live_run`, `cancel`; reads `sessions`, `defaults`, `history`; snapshots `snapshots`, `snapshot_plan`, `snapshot_revert`; misc `shots_dir`, `image_to_png`, `terminal_command`.

**Imports:** none from `fused_render` (SPEC PY-15 / D166). Facts via env through `shared/appenv.py`: `origin`, `workspace_dir`, `canvases_root`, `skill_plugin_dir`, `workbench_plugin_dir`, `fused_cli_dir`.

**Spawn chain (page-started chat):**
1. `/api/run` child python → `agent._start` (`:2593`).
2. `_start` creates `run_dir`, writes `meta.json` + empty `err.log`, writes first `inbox/*.json`.
3. Popens `[sys.executable, session_host.py]` detached (`start_new_session`, `_DETACH` `:2271`); request JSON on stdin, never argv.
4. Writes host pid to `pid` (O_EXCL); returns `{run_id, session_id}`.
5. `session_host.main` (`:289`) builds `agent._claude_argv` (`:2277`), Popens CLI with `stdin=PIPE`, `cwd=_workdir(file)` (`:624`: dir itself, or file's parent), `env=_spawn_env()` (`:2398`).
6. Host rewrites `pid` to CLI pid, writes `host.json`; `_reap_loop` (`:350`) drains inbox every 0.2s, reaps after 30s idle.

**CLI argv** (`agent.py:2294-2396`):
```
claude -p --input-format stream-json --output-format stream-json --verbose
  --include-partial-messages --replay-user-messages
  --mcp-config <run_dir>/mcp.json
  --permission-prompt-tool mcp__fused_approvals__approve
  --allowed-tools "mcp__fused_approvals__app_state(if pane),Read(<SHOTS>/**),Read(<extra dirs>),Bash(fused:*)(if fused wrapper)"
  [--plugin-dir <skill_plugin_dir>] [--plugin-dir <workbench_plugin_dir> if in canvases root]
  --append-system-prompt <_split_system_prompt|_system_prompt + _fused_cli_note>
  [--permission-mode plan|acceptEdits|auto]  (PERMISSION_MODES :268; "prompt" = no flag)
  [--resume <sid>] | [--session-id <minted uuid4>]   (mutually exclusive)
  [--model M] [--effort E]
```

**Other spawns:**
- `mcp.json` (`_write_mcp_config` `:1353`): `sys.executable permission_server.py <perm_dir> [<state_dir>]`, env `FUSED_RENDER_PERMISSION_TIMEOUT`, `PYTHONUTF8=1`, timeout `(PERMISSION_WAIT+60)*1000`.
- Control via stdin rows `{"type":"control_request","request":{"subtype":"interrupt"|"set_model"|"set_permission_mode"}}` (`_write_control_request` `:2514`; used `:3383`, `:3397`, `:6616`).
- Cancel: `interrupt` first, then `os.killpg(pid, SIGTERM)` (`:6754`); Windows `taskkill /T /F` (`:6748`).
- `_claude_bin` `:468`: `FUSED_RENDER_CLAUDE_BIN` → `which` → `_POSIX_CANDIDATES` `:181` (~/.local/bin, /opt/homebrew/bin, /usr/local/bin).

**Env vars:**
- `_spawn_env`: pops `FUSED_ENV`, setdefault `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING=1` (D394).
- agent.py reads `CLAUDE_CONFIG_DIR`, `FUSED_RENDER_CLAUDE_BIN`, `FUSED_RENDER_HOME`, `FUSED_RENDER_PERMISSION_TIMEOUT`.
- host reads `FUSED_CLAUDE_HOST_IDLE_REAP_SECONDS` (30), `FUSED_CLAUDE_HOST_DRAIN_INTERVAL_SECONDS` (0.2) (`session_host.py:59-62`), `FUSED_RENDER_ORIGIN`.
- MCP server reads `FUSED_RENDER_PERMISSION_TIMEOUT` (3600, `:93`), `FUSED_RENDER_APP_STATE_TIMEOUT` (20, `:100`), `FUSED_RENDER_ORIGIN`.

**On-disk — run dir `RUNS/<YYYYmmdd-HHMMSS-hex>/` (0700):**

| file | holds |
|---|---|
| `meta.json` | `file`, `message`, `resumed_from`, `mode`, `session_id`, `draft_key` |
| `out.jsonl` | CLI stream-json stdout |
| `err.log` | CLI stderr (+ host errors) |
| `pid` | CLI pid |
| `host.json` | `pid`, `session_id`, `file`, `mode`, `model`, `effort`, `read_dirs` (presence = live host) |
| `mcp.json` | MCP config |
| `inbox/*.json`, `inbox/done/` | queued stdin rows |
| `perm/<id>.req.json`, `perm/<id>.res.json` | permission cards; res = O_EXCL latch |
| `appstate/` | pane state channel |
| `cursor`, `bg_tasks.json`, `pending_echo`, `interrupted_offset`, `cancelled`, `recorded`, `session` | poll bookkeeping |

Other stores:
- `$TMPDIR/fused_render_claude-<uid>/shots/` — screenshots/DOM outlines, 30-day TTL, 1000 cap (`:141-153`).
- `<FUSED_RENDER_HOME>/claude-sessions/session_settings.json` (`:1485`), `held_answers.json` (`:1417`, legacy).
- Reads `~/.claude/projects/<munge(cwd)>/*.jsonl` (sessions/history), `~/.claude/settings.json` (defaults `_global_defaults` `:5505`), `~/.claude/file-history` via `file_history.py`.

**Events to server:** session_host POSTs `turn_ended` (edge on `result` row) + `exited` to `/api/tasks/queue/event` with `X-Fused: 1`, 5s timeout, one retry (`:208-286`). permission_server POSTs `card_raised` / `card_cleared` (`:219-248`).

**Threads:** none in agent.py; host process runs drain/reap loop + daemon event-post threads.

**fused-render-only coupling:**
- `_commit_turn` (`:2793`) — git commit in workspace app folders; no-op outside `workspace_dir()` (lite never hits it).
- `_in_canvases_root` / `_plugin_argv` workbench plugin — inert without canvas env.
- `_fused_cli_note` / `Bash(fused:*)` — only if `fused_cli_dir()` set.
- `_custom_env` → `/api/env/custom-env` (lite lacks; returns None silently).
- Scattered win32 branches (ignored on macOS).
- `_has_pane`/`_is_app_dir` use `app_entry.entry_html` (`<meta name="fused-app">`) — should work unchanged for a lite extract dir.

## 2. Package modules

### `claude_spawn.py` (175) — detached session from the server process
- Imports: `core_templates.ensure_core_templates` (`:41`).
- Routes/threads: none (record loop runs on caller's thread).
- API: `agent_path()` `:33`; `load_agent()` `:47` (exec agent.py in-process, the sanctioned read door); `record_session_when_ready(agent, run_id, on_tick)` `:57` (polls `_poll` every 2s ×1800 ≈ 1h); `SESSION_HELPER` `:101` / `spawn_helper()` `:116` → `[sys.executable, "-c", SESSION_HELPER]`, request on stdin, 60s timeout, `close_fds=False` (posix_spawn path). Exists because libproj's atfork handler SIGSEGVs a `fork()` from the server.
- Callers: `schedule.py:110`, `project_queue.py:297` (load_agent), `canvases.py`.
- Lite: no pyproj/osgeo/rasterio/duckdb in `fused_render_app/` (grep empty); page chats already `_start` inside the `/api/run` child, so `spawn_helper` only matters if lite spawns from the server. Port `load_agent` (repoint path); keep `spawn_helper` (cheap).

### `tasks_store.py` (1794) — task numbers, unread, tombstones, settings, transcript heads
- Imports: `_view_url_codec.canonical_fs_path` (`:112`; lite has it).
- Routes/threads/subprocesses: none.
- State `STATE_DIR = <FUSED_RENDER_HOME|~/.fused-render>/claude-sessions` (`:130`), not branch-nested, fcntl-locked:
  - `task_ids.json` `{key:{project,n}}`; keys = session id | `pending:<entry-id>` | `spent:`; allocate-once per project (`ensure_ids` `:389`, `rekey` `:479`, `task_number` `:518`).
  - `read.json` `{__initialized_at__, <sid>:{last_read_at, read_ids, read_floor}}` (`initialize` `:542`, `mark_read` `:618`, `mark_read_many` `:627`).
  - `deleted.json` (`mark_deleted` `:718`), `session_settings.json` (`record_settings` `:792`), `forget_session` `:826`.
- Reads `~/.claude/projects` (`transcripts` `:1761`, `backfill` `:1770`, `head` `:1558`, `user_row_after` `:1659`).
- Machinery-tag helpers (`:1000-1450`): strip `live-app-state`, `pane-shot`, `annotations`, `task-notification` from user text — deliberate duplicate of agent.py's.
- Lite coupling: `project_of(cwd)` `:1749` = folder is a project; fits extract dirs except the hash churn (§0.3).

### `tasks_watch.py` (1121) — Tasks change signal
- Imports: `session_liveness`, `tasks_store` (`:52`); lazy `schedule` (`:775`, try/except); lazy `server.routers.tasks` (`:980`, for `_agent_module`, `_run_sessions`) — a real back-edge into routers.
- Routes: none (exposed via `/api/tasks/changes`).
- Thread: daemon `fused-tasks-watch`, `tick()` every 1s (`TICK_SEC`), `start()` `:1087`:
  - `_read_registry` `:782` — stat `~/.claude/sessions/<pid>.json` (`sessionId`, `cwd`, `status`; `busy`/`shell` = running `:73`), pid checks.
  - `_read_live_transcripts` `:919` — stat only live sessions' transcripts.
  - `_read_permission_cards` `:950` — stamp `perm/` of newest 120 run dirs, only when something is live.
  - `_expire_marks` `:652`.
- State: in-memory; generation counter + ring of 200 change sets; `wait(since, timeout)` `:373` blocks on `threading.Condition` ≤25s (`MAX_WAIT_SEC` `:66`).
- Marks: `mark_running` `:447` (15s TTL `MARK_TTL_SEC` `:132`; `turn`, `text`, `file`), `mark_idle` `:519`, `mark_turn_ended` `:577` (from queue event), `sent_marks` `:346`, `is_turn_ended` `:281`, `live_from_registry` `:246`.
- Subprocesses: none.
- Lite: `_wake_schedule` → no-op; repoint permission scan at an agent-loader helper, not the tasks router.

### `session_liveness.py` (439) — mid-turn from transcript tail
- Imports: `tasks_store.is_interrupt_mark`, `leading_machinery_tag` (`:48`).
- Routes/threads/subprocesses: none.
- Rule: last 16KB walked backwards, skip `HOUSEKEEPING_TYPES={system,last-prompt,summary}`; <45s = running (`RUNNING_WINDOW_SEC`), >90s skip read (`STALE_TAIL_SEC`), `VERDICT_ECHO_SEC=15` (`:60-85`).
- API: `tail_activity` `:100`, `transcript_running` `:149`, `transcript_turn_open` `:243`, `transcript_path` `:350`, `session_activity` `:370`, `session_running` `:396`, `session_turn_open` `:416`.
- Callers: tasks_watch, claude_sessions router, schedule, claude_session_move, app_doctor_ai.
- Lite coupling: none. Port verbatim.

### `claude_session_move.py` (369) — carry sessions when a folder moves
- Imports: `session_liveness` (`:60`), lazy `_view_url_codec` (`:197`).
- Callers: `app_fused_dir._ensure_meta` (D548 `.fused/meta.json` witness), `server/routers/current_apps.py`.
- `relocate(old_root, new_root)` `:281`: moves `~/.claude/projects/<bucket>/<id>.jsonl` + `<id>/` sidecar, rewrites `cwd` (`_rewrite_cwd` `:231`), membership decided by transcript's own `cwd` (`transcript_cwd` `:100`), skips running sessions (`~/.claude/sessions` pid check `_live_session_ids` `:122` + `transcript_turn_open`), never overwrites. `munge` `:78`.
- Routes/threads/subprocesses: none.
- Lite: caller is workspace-specific; lite needs its own trigger (re-extract old→new dir).

### `claude_artifacts.py` (405) + `routers/claude_artifacts.py` (34)
- Imports: `_view_url_codec`; lazy `shell.mounts.is_mount_backed` (`:150`).
- Route: `GET /api/claude-artifacts?cwd=` (router `:25`), unguarded.
- State: in-memory per-file cache keyed (mtime,size) (`_CACHE` `:79`); parses `frame-link` rows + Artifact `tool_use`.
- Lite: stub mount check → False.

### `claude_health.py` (1289) — is claude installed/current/signed in
- Imports: `shell.storage` (`:42`).
- Subprocesses: `claude --version` (`probe_version` `:368`), `claude auth status` (`_auth_status` `:401`), `claude doctor` (`_doctor` `:844`), login-shell PATH probe (`_shell_probe` `:253`); `_probe_cmd` `:142`.
- State: `<storage.home_dir()>/claude-health.json` (`:951`).
- Thread: `warm_in_background` `:1240`.
- Router `routers/claude_health.py` (208), POSTs guarded: `GET /api/claude/health` `:27`, `POST /api/claude/health/refresh` `:39`, `POST|GET /api/claude/install` `:60/:84`, `POST /api/claude/link-path` `:92`, `POST /api/claude/doctor` `:123`, `POST|GET /api/claude/login` `:169/:191`, `POST /api/claude/login/cancel` `:199`.
- Lite: lite:`claude_health.py` is a 41-line stub (`resolve`, `executable`, `CANDIDATES`, `BIN_ENV`, `APP_BIN_ENV`) used by `routes/ai_relay.py:22`. install/login need `_probe_cmd`, `snapshot`, `summary`, `adopt`, etc. → port full module (drop win32/linux) or trim.

### `claude_login.py` (436) — `claude auth login` child
- Imports: `claude_health`, `claude_install` (`:59`).
- Subprocess: `claude_health._probe_cmd(path, "auth", "login")` (`:379`), Popen `:387`.
- Threads: drain + watchdog (`:406`). State in-memory; success = `claude auth status`.

### `claude_install.py` (372) — install / update
- Imports: `claude_health`, `jobs` (`:44`).
- Subprocess: `bash -c "curl -fsSL https://claude.ai/install.sh | bash"` (`install_argv` `:69-91`) or `claude update` (`update_argv` `:94`).
- Thread: one worker (`_run` `:191`); progress via `jobs.upsert` (`_report` `:125`).

### `project_queue.py` (645) — folder key + runs-tree reads
- Imports: `current_apps`, `tasks_store`, `tasks_watch`, `_view_url_codec`, `index.ignore.MountGuard` (`:52-54`); lazy `shell.prefs` (`:131`), `claude_spawn` (`:297`).
- Routes/threads: none.
- Flag `enabled()` `:122` → `shell/prefs.py:282` `project_queue_enabled` (default off, read fresh).
- `queue_key` `:160` (app folder via `current_apps.app_dir_for` → nearest `.git` ancestor → folder; never `$HOME` or `/`), `agent_module()` `:280` (single cached `load_agent`), `scan_runs` `:392` (newest 120 `RUN_SCAN_LIMIT` `:79`, memo 1s `SCAN_TTL` `:93`), `run_sessions` `:308`, `run_alive` `:531`, `run_waiting` `:540`, `run_permissions` `:491`, `read_legacy_held_answers` `:583`.
- Lite coupling: workspace (`current_apps`), git, `MountGuard`.

### `queue_manager.py` (2021) — the queue dispatcher
- Rule: one folder, one owner, one spawn site; event-driven, no polls.
- Imports: `tasks_store` (`:31`); lazy `project_queue` (`:210`), `schedule.SpawnBusy` (`:239`).
- State: `STATE_DIR/queue_index.json` (`INDEX_FILE` `:35`); migrates `held_answers.json`.
- Injected callables `spawn/deliver/running/blocked/pending_due/notify` from router factory (`routers/tasks.py:6125`).
- API: `QueueManager` `:247`, `enqueue` `:1000`, `claim`/`claim_for_send`/`consume_claim`/`restore_claim` `:1118-1280`, `started` `:1322`, `card_raised`/`card_answered`/`card_cleared` `:1407-1480`, `turn_ended` `:1547`, `exited` `:1557`, `reconcile` `:1846`, `set_factory` `:1973`, `get()` `:2003`. `PLACEHOLDER_PREFIX="admit:"` `:57`.
- **Why it drops for lite:** queued items ARE schedule entries — `_queue_spawn` → `schedule.dispatch_entry` (`tasks.py:5875-5918`); `api_queue_admit` stores a waiting message as a pending schedule entry (`tasks.py:6152+`). No schedule ⇒ nothing to dispatch.

### `executor.py` (413) — `run_python`
- In-process allowlist + `_child.py` subprocess, 60s timeout (D72); agent.py is off the allowlist (always subprocess). Lite has `env.run_python` (lite:`env.py:403`). **Drop.**

### `schedule.py` (4621), `cron.py` (136), `recur.py` (420), `schedule_wake.py` (203)
- schedule.py: store `storage.home_dir()/scheduled_messages.json` (`:117`, branch-aware) + `task-shots/` (`:880`); thread `fused-schedule`, 30s poll (`POLL_INTERVAL_S` `:148`) with `wake()` `:380`; `dispatch_entry` `:679`; `_send` `:2676` → `claude_spawn.spawn_helper` with permission mode `auto` (`:184`) + `record_session_when_ready` thread (`:2780`); `tick` `:4020`; `start` `:4609`; jobs rows prefixed `sys:schedule:` (`:219`).
- schedule_wake.py: LaunchAgent `~/Library/LaunchAgents/io.fused.render.schedule-wake.plist` (`:43`, `:58`) via `/bin/launchctl`.
- Router `routers/schedule.py` (905, POSTs guarded): `GET /api/schedule` `:31`, `GET /api/schedule/events` `:60`, `POST /api/schedule/events/ack` `:80`, `POST /api/schedule/shot` `:117`, `POST /api/schedule` `:463`, `GET /api/schedule/queue` `:643`, `POST /api/schedule/queue/cancel` `:678`, `POST /api/schedule/restore` `:710`, `POST /api/schedule/run-now` `:732`, `POST /api/schedule/resend` `:830`, `POST /api/schedule/cancel` `:886`.
- **Drop** (see tasks router stub).

### `jobs.py` (1338) — server job registry
- Lite has a copy (1192 lines); `/api/jobs*` already in lite `server.py`. Routes in fused-render `routers/jobs.py`: `GET/POST /api/jobs`, `POST /api/jobs/clear`, `POST /api/jobs/{id}/cancel|dismiss`.

### `supervisor/*` (~1345) — Linux/Windows desktop wrapper
- Tray, single-instance, child env. `paths.py:213-236` deliberately leaves `CLAUDE_CONFIG_DIR` unset; sets `TMPDIR`. **Drop** (lite has `macapp.py`).

### `server/ai.py` (2417) — fused.ai relay
- Routes `POST /api/ai` `:2387`, `GET /api/ai/metrics` `:2404`; persistent warm claude (D169). Already in lite as `routes/ai_relay.py`. No tie to tasks/sessions.

### `claude_config/` (3094) — out of scope
- `GET /api/claude-config/status` → `{available: isdir(~/.claude)}`; `POST /api/claude-config/{module}` (guarded) for `claude_md, git_ops, marketplaces, mcp, memory, plugins, preferences, profiles, refresh_catalog, skills, statusline`.

### `static/` — nothing to port
- No legacy chat JS. `runtime.js` "claude" mentions are fused.ai provider docs; `shell-dist/` is the built React bundle.

## 3. Routers

### `server/routers/tasks.py` (7104)
- Imports: `current_apps, drafts, project_queue, queue_manager, schedule, session_liveness, tasks_store, tasks_watch`, `_view_url_codec`, `server.common`, `routers.claude_sessions`, `routers.schedule`, `shell.prefs` (`:125-139`). Registers queue factory at import (`_wire_manager()` `:6125`, called right after).
- Guard: POSTs call `_require_fused` (`server/common.py:220`, D3); docstring says reads + read-mark are unguarded.

| method | path | line |
|---|---|---|
| GET | `/api/tasks` | 4538 |
| GET | `/api/tasks/changes?since&wait` (long-poll, not SSE) | 4587 |
| GET | `/api/tasks/pulse` | 4719 |
| POST | `/api/tasks/running` `{session_id, turn?, text?, file?}` | 4754 |
| POST | `/api/tasks/idle` `{session_id, turn?}` | 4804 |
| GET | `/api/tasks/{key}/messages` | 4854 |
| GET | `/api/tasks/scheduled?from&to` | 4880 |
| POST | `/api/tasks/read` | 4979 |
| GET | `/api/tasks/settings` | 5124 |
| POST | `/api/tasks/settings` | 5158 |
| POST | `/api/tasks/archive` | 5235 |
| POST | `/api/tasks/unarchive` | 5297 |
| POST | `/api/tasks/delete` | 5425 |
| POST | `/api/tasks/erase` | 5553 |
| POST | `/api/tasks/queue/admit` | 6151 |
| POST | `/api/tasks/queue/skip` | 6597 |
| POST | `/api/tasks/queue/force` | 6721 |
| POST | `/api/tasks/queue/decide` | 6972 |

- Core to extract for lite: `_scan` `:565`, `_collect` `:2376` (transcripts + send marks + schedule entries), `_status` `:1983` (STATUSES `:184`), `_running_now` `:1339`, `_live` `:2792`, `_parked_runs` `:1594`, `_attention_of` `:1565`, `_agent_module` `:1532`, `_run_sessions` `:1552`, `_row` `:2982`, `_numbers` `:2716`, `_mark_unread` `:4282`, `warm` `:4363`, `_build_task_rows` `:4388`, `_thread` `:4834`, archive/delete/erase `:5236-5790` (`_erase_session_files` `:5717`).
- Coupling counts: 77 `schedule.*` (stub: `list_entries()→[]`, `busy_sessions()→set()`, state constants); 48 `drafts.*` (`_settle_new_chats` `:3922`, `_draft_rows` `:4114`); 86 queue refs; `current_apps.observe` (workspace "current apps").
- Threads: `warm` at startup; queue ops via injected callables.

### `server/routers/claude_sessions.py` (1239)
- Imports: `session_liveness`, `tasks_store`, `_view_url_codec`, `server.common`.
- Routes: `GET /api/claude-sessions` `:100`, `GET /home` `:172`, `GET /summaries` `:494` (Explorer/Schedule listings); `GET|PUT /defaults` `:511/:545` (PUT guarded); `GET /history?file&session_id&native` `:627` (in-process `agent._history` via `tasks._agent_module()`; uses `tasks_store.erased` to tell erased vs new); `GET /recap?file&session_id&for_uuid` `:1019`; `GET /liveness?path` `:1052` (D415); `POST /triage` `:1113`.
- State: `STATE_DIR/session_names.json`, `triage.json` (fcntl-locked).
- Subprocess (recap `:949`): `claude -p --no-session-persistence --input-format stream-json --max-turns 1 --tools "" --model haiku --output-format stream-json --verbose --system-prompt <_RECAP_SYSTEM>`; cwd `agent._workdir`, env `agent._spawn_env()`.

### `server/routers/queue_events.py` (211)
- `POST /api/tasks/queue/event` `:140`, guarded. Body `{kind: turn_ended|exited|card_raised|card_cleared, run_id, session_id?, request_id?, code?, at?}`.
- `turn_ended`/`exited` always → `tasks_watch.mark_turn_ended` (not flag-gated); rest gated on `project_queue.enabled()`, answers `{ok, ignored}` when off.
- Imports: `project_queue`, `queue_manager`, `tasks_watch`; uses `run_in_threadpool`.

### `server/routers/run.py` — claude branch of `/api/run`
- `_claude_agent(resolved)` `:20` (suffix `/templates/claude/agent.py`); `_queue_target` `:24`; `_file_owner` (after spawn, records owner) `:36+`; `_folder_busy` gate (~`:169`) on `start`/`send`. All no-ops with flag off. Not needed in lite unless the queue is ported.

### `server/routers/drafts.py` (377) + `drafts.py` (1561)
- `GET /api/drafts` `:194`, `PUT|DELETE /api/drafts/chat/{key}` `:217/:255`, `PUT|DELETE /api/drafts/task/{id}` `:291/:357`. State `STATE_DIR/drafts.json`. Optional.

### Startup wiring (`server/app.py:484-544`)
- `_startup_queue_manager` (`_wire_manager`; `reconcile` thread if flag on), `_startup_schedule` (`schedule.start()`), `_startup_tasks_watch` (`tasks_watch.start()`), `_startup_tasks_warm` (`tasks.warm` thread). Lite needs the tasks_watch + warm equivalents in `server.main`.

### Backend endpoints the React chat uses
From `frontend/src/apps/claude/protocol/*.ts`: `/api/run`; `/api/tasks`, `/api/tasks/running`, `/api/tasks/idle`, `/api/tasks/changes`, `/api/tasks/erase`; `/api/tasks/queue/admit`, `/api/tasks/queue/*`; `/api/claude-sessions/history`, `/api/claude-sessions/recap`; `/api/fs/stat`.
Legacy `template.html` additionally: `/api/claude-sessions/liveness`, `/api/schedule`, `/api/schedule/cancel`, `/api/tasks/archive|unarchive|erase`, `/api/prefs`, `/api/fs/raw|stat|upload|conditions`, `/api/capture/shot-region`.

## 4. Dependency graph (`A → B` = A imports B; `(lazy)` = inside a function)

```
templates/claude/agent.py ──(file path)── session_host.py ──HTTP POST──▶ /api/tasks/queue/event
      │  └─ shared/{appenv,private_dir,procutil,app_entry,file_history}
      ├─ writes mcp.json → permission_server.py ──HTTP POST──▶ /api/tasks/queue/event
      └─ artifacts.py ──HTTP GET──▶ /api/claude-artifacts
(no python import edge templates → fused_render, by rule)

claude_spawn → core_templates                       (load_agent / spawn_helper → agent.py)
tasks_store  → _view_url_codec
session_liveness → tasks_store
claude_session_move → session_liveness, (_view_url_codec lazy)
tasks_watch → session_liveness, tasks_store, (schedule lazy), (server.routers.tasks lazy)  ← back-edge
project_queue → current_apps, tasks_store, tasks_watch, _view_url_codec, index.ignore, (shell.prefs lazy), (claude_spawn lazy)
queue_manager → tasks_store, (project_queue lazy), (schedule.SpawnBusy lazy)
schedule → claude_spawn, cron, recur, shell.storage, (session_liveness, tasks_watch, project_queue,
           queue_manager, server.routers.tasks, tasks_store, jobs, schedule_wake: all lazy)
schedule_wake → (schedule lazy)
claude_artifacts → _view_url_codec, (shell.mounts lazy)
claude_health → shell.storage
claude_install → claude_health, jobs
claude_login → claude_health, claude_install
jobs → (projectenv lazy)

routers/tasks → current_apps, drafts, project_queue, queue_manager, schedule, session_liveness,
                tasks_store, tasks_watch, routers/claude_sessions, routers/schedule, shell.prefs
routers/claude_sessions → session_liveness, tasks_store  (+ agent via tasks._agent_module, lazy)
routers/queue_events → project_queue, queue_manager, tasks_watch
routers/claude_artifacts → claude_artifacts
routers/claude_health → claude_health, claude_install, claude_login
routers/schedule → drafts, recur, schedule, tasks_store, tasks_watch, image_convert
routers/run → (project_queue, queue_manager lazy); executor
```

## 5. Must port / optional / drop (lite target)

| module | verdict | reasoning |
|---|---|---|
| `templates/claude/{agent,session_host,permission_server,condition,app}.py`, `vendor/` | **Must, verbatim** | The whole engine; designed to be copied out. Place at `fused_render_app/templates/claude/`. |
| `templates/shared/{private_dir,procutil,app_entry,file_history}.py` | **Must** | Hard imports of agent.py (`file_history` only inside snapshot actions). Reconcile lite's `appenv.py` with fused-render's 239-line version (plugin/canvas/cli-dir fns). |
| `templates/claude/artifacts.py`, `claude_artifacts.py`, `/api/claude-artifacts` | Optional | Decoration; small, read-only, one route. Stub mount check. |
| `templates/claude/template.html` | Optional | Only if lite serves the legacy UI instead of React chat (fe-map). |
| `claude_spawn.py` | **Must (part)** | `load_agent()` for in-process reads. Keep `spawn_helper` only if lite spawns server-side (cheap; lite has no libproj). |
| `session_liveness.py` | **Must** | Pure; every liveness reader depends on it. |
| `tasks_store.py` | **Must** | Numbers, read state, settings, tombstones, heads. Decide STATE_DIR first (share `~/.fused-render/claude-sessions` or set `FUSED_RENDER_HOME`). |
| `tasks_watch.py` | **Must, adapted** | Change signal for Tasks/sidebar + `mark_turn_ended` sync. `_wake_schedule` → no-op; permission scan via agent-loader helper, not tasks router. Start from lite `server.main`. |
| `routers/claude_sessions.py` | **Must (part)** | Port `/history` (in-process; avoids a ~7k-line exec per click) and `/liveness`. `/recap`, `/defaults`, `/triage` optional. Drop Explorer listings `/`, `/home`, `/summaries`. |
| `routers/queue_events.py` | **Must, reduced** | Keep endpoint so host/MCP posts don't 404; handle `turn_ended`/`exited` → `mark_turn_ended`, answer `ignored` otherwise (~30 lines). |
| `routers/tasks.py` | **Must, rewrite/extract** | Extract core (§3) into a lite router: `/api/tasks`, `/changes`, `/pulse`, `/running`, `/idle`, `/{key}/messages`, `/read`, `/settings`, `/archive`, `/delete`, `/erase`. Stub `schedule`, drop drafts + queue, replace `current_apps.observe`. Extend `_web.py` (Query/BaseModel) or use `body: dict`. |
| `claude_session_move.py` | **Decision** | Needed if Claude cwd = hash-keyed extract dir (call `relocate` on re-extract); not needed if cwd pinned to a stable dir. |
| `claude_health.py` (full) | Optional | Lite's 41-line stub suffices for ai_relay; full module only with install/login. |
| `claude_install.py`, `claude_login.py`, `routers/claude_health.py` | Optional | Onboarding (`curl install.sh`, `claude auth login`). Needs full `claude_health` + lite's existing `jobs.py`. macOS only → drop win32 branches. |
| `jobs.py`, `server/ai.py` | Already in lite | `jobs.py`, `routes/ai_relay.py`. |
| `project_queue.py`, `queue_manager.py`, `/api/tasks/queue/{admit,skip,force,decide}`, `run.py` gate | **Drop** | Default-off flag; built on schedule entries. Lite's one-app-per-window weakens the one-task-per-folder need. |
| `schedule.py`, `cron.py`, `recur.py`, `schedule_wake.py`, `routers/schedule.py` | **Drop** | No schedule in lite; leave a stub module so ported tasks code keeps its shape. |
| `drafts.py`, `routers/drafts.py` | Drop / optional later | Keep composer drafts client-side. |
| `executor.py` | Drop | Lite `env.run_python` covers it. |
| `supervisor/*` | Drop | Linux/Windows tray; lite has `macapp.py`. |
| `claude_config/*`, `routers/claude_config.py` | Drop | Settings editor, out of scope. |
| `canvases.py` + agent workbench-plugin paths | Drop | Inert without canvas env vars. |

## 6. Design decisions that constrain the port

`ARCHITECTURE.md` does not document the tasks subsystem (one line on `ai.py` `:51`, one on executor routing `:366`). `DECISIONS-LOG.md` has nothing on tasks/sessions/queue (only `_claude_bin` test-mocking notes). Decisions live in `DECISIONS.md` D-rows and module docstrings. The `design.md` cited by `queue_manager`/`drafts`/`session_host` docstrings is not in the tree. Related doc: `docs/CLAUDE-TEMPLATE-POC.md`.

- **D72 / PY-6:** agent.py always runs as a subprocess on `/api/run`. Read paths moved in-process via `load_agent()` for latency: `/history` (`claude_sessions.py:627`) and tasks `_agent_module`.
- **D166 / PY-15:** templates never import `fused_render`; facts via env (`appenv`), events back over HTTP — hence the event endpoint.
- **D161 / D247 / D248 / D407:** approvals via `--permission-prompt-tool` + stdio MCP server. Cards `perm/<id>.req.json` / `.res.json`, ids we mint, O_EXCL first-writer-wins latch. AskUserQuestion answers ride `updatedInput.answers`; ExitPlanMode = plan card; always an "Other" row.
- **D674 / D676 / D679 / D690:** one session_host + one CLI per *session*, not per turn. Follow-ups are inbox files drained in filename order; `live_host` probe before send. New `read_dirs`/`effort` forces respawn (`--allowed-tools` fixed at spawn). Reap after 30s idle; `finally` drains once more, then removes `host.json`.
- **D675 / D678 / D686 / D687 / D692 / D693 / D696:** `_turn_state` reads only `out.jsonl`; `_poll` reads only the current turn via byte cursor; `pending_echo` holds idle false until the replayed user row appears; bg tasks persist to `bg_tasks.json`; sidechain rows (`parent_tool_use_id`) skipped.
- **D415 / D307:** `done` is per turn = a `result` with nothing after it, or a dead process. Page asks `live_run` and polls `/api/claude-sessions/liveness` for turns started outside the app (terminal `claude`).
- **Liveness layers (strongest first):** (1) CLI registry `~/.claude/sessions/<pid>.json` (`busy`/`shell`); (2) transcript tail 45s running / 90s stale (`session_liveness`); (3) send mark 15s TTL from `/api/tasks/running`, retired by `/idle`; (4) `turn_ended`/`exited` events from session_host, ordered by host's `at` (`mark_turn_ended`).
- **Session id minted up front:** `_start` mints uuid4 → `--session-id`, so the task row is keyed at send time; `--resume` and `--session-id` exclusive by construction.
- **D322:** a task IS a Claude session (1:1); project = folder; `TASK-nnn` per project, allocate-once, never renumbered; `MSG-nnn` positional.
- **D306 / D307 (tasks router sense):** delete tombstones the row, transcript kept, new activity revives it; erase removes transcript + sidecars + every app record.
- **D394:** `CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING=1` default, so `-p` runs write file-history for the snapshots panel.
- **D334 / D216:** `Bash(fused:*)` pre-allow and `--plugin-dir` only when the matching env var exists — lite exports neither, so both inert.
- **D217 / D239:** app-state DOM outline travels as a file path; a folder with no page gets no `app_state` tool; the page's `has_pane` wins over disk.
- **D289 / D290 / D322 (schedule + queue):** schedule fires from inside the app to keep env + TCC grants; missed one-offs queue, repeats coalesce. Project queue (one folder, one task) is flag-gated, default off, and its message queue is the schedule store. All dropped for lite.
- **D548:** transcripts follow a moved folder by rewriting `cwd` — the tool for lite's hash-keyed extract dirs if that route is chosen.
