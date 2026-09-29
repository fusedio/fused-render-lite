# Render App (lite) landing zone for Claude sessions/tasks port

Worktree: `/Users/vasu/Documents/fused-dev/fused-render-lite/.claude/worktrees/groovy-plotting-waterfall` (HEAD d2ed3d2, v0.10.0). Read-only map. Upstream = `/Users/vasu/Documents/fused-dev/fused-render/fused_render/`.

## 0. window.fused WIP search: nothing found

- No lite branch/worktree touches a new window.fused API.
  - `elegant-wishing-hamster` (branch `worktree-elegant-wishing-hamster`, created 2026-09-29 18:40) is byte-identical to this worktree (`diff -rq`). Likely the other agent's fresh worktree, nothing written yet.
  - `golden-stargazing-bentley` = branch `fused-engine`, 3 commits past main: runPython via optional fused engine, pins fused 2.9.3b10, DMG +8.3 MB -> 51.76 MB. Touches engine.py, env.py, envinstall.py, server.py, pyproject.toml, build_dmg.sh, setup_py2app.py, adds tests/test_engine.py. Unrelated to window.fused.
- `window.fused` already exists: `fused_render_app/static/runtime.js:1833`. New work presumably extends that literal (collision point).
- NOT inspected: fused-render worktrees `stateful-scribbling-sutherland` (branch `bots-subapp`) and `magical-napping-patterson`.

## 1. Server — `fused_render_app/server.py` (1023 lines)

**Framework.** stdlib `http.server.ThreadingHTTPServer` (`class Server`, :907, `daemon_threads=True`, `allow_reuse_address`). No FastAPI/uvicorn/starlette. `pyproject.toml` `dependencies = []` deliberately.

**Routing, two layers.**
- Hand-written `if route == ...` chains: `Handler.do_GET` (:185), `do_POST` (:253), `do_HEAD = do_GET` (:251). Handler at :135.
- Fallthrough `_dispatch(method, route, q)` (:654) walks ONE router `AI_ROUTER` (:89-94) = `ai_relay.router` + `ai_routes.router` + `capture_routes.router`. Misnomer: it's the general mount point for any `_web.APIRouter` (capture already rides it). Result rendered by `_emit` (:676).
- `background_routes.py` functions take the Handler directly (`status(self,q)`, `proxy(...)`, `PROXY_RE`, `STOP_RE`), called from the do_GET/do_POST chains.

**`_web.py` shim** (FastAPI-shaped, 248 lines):
- `APIRouter` with `get`/`post`/`delete` (NO `put`), `include_router`, `match` (regex from `{name}` / `{name:path}`).
- `Body`, `Header` markers; `Request` (headers lower-cased, `query_params`, `state`, `app.state` process-wide bag, `is_disconnected()` always False); `Response`, `JSONResponse`, `RedirectResponse`, `PlainTextResponse`, `HTMLResponse`, `FileResponse`, `StreamingResponse` (sync or async iterator), `BackgroundTask`, `HTTPException` (defined but NOT handled by dispatcher).
- `call_route` binds by param NAME: path params, `request`, `body`, `x_*` -> header, query values (coerces int/float/bool annotations), else default.
- No `Query`, no `Depends`, no `run_in_threadpool`, no pydantic.

**Threading.** Thread per connection. One shared asyncio loop on a daemon thread (`_web._LOOP`); async routes submitted via `run_coroutine_threadsafe`, HTTP thread blocks on result. Shared loop is required because the warm Claude subprocess pipes belong to it. A blocking call inside any async route stalls all async routes (ai_relay uses `asyncio.to_thread` for local models). Sync routes run on the HTTP thread → long-poll (upstream `/api/tasks/changes?wait=`) fits fine.

**Streaming.** `_emit` writes `StreamingResponse` chunked (Transfer-Encoding: chunked, Cache-Control no-store). Idiom in use: NDJSON (`fused.ai` stream). SSE would work mechanically (media_type `text/event-stream`) but nothing uses it. No WebSocket (capture.py docstring: upstream chunk WS dropped because stdlib speaks none). Disconnect only surfaces as BrokenPipe on write.

**Guards.** `_guarded()` (:177): 403 unless `X-Fused: 1` (forces CORS preflight; not auth). Router routes call `routes/common.py:_require_fused(x_fused)` (:104) themselves. GETs unguarded. Binds 127.0.0.1. `X-Fused-Page` = calling page path, used for job attribution.

**Static.** `_static(name)` (:302) serves `fused_render_app/static/` with path-escape check, `no-store`. Each page needs an explicit route: `/`->index.html, `/dock`, `/launcher`, `/settings`, `/static/*`. `/open` (:313) templates open.html (`__FILE_JSON__`, `__URL_JSON__`). `/render?path=` (:326) injects `<script src="/static/runtime.js">` after `<head>` — the ONLY runtime.js injection point.

**Startup / env.** `make_server` (:915) calls `paths.fix_process_env()`, exports `FUSED_RENDER_ORIGIN` (:921), `FUSED_RENDER_HOME_DIR` (:925), writes `<home>/server.json` {origin, port, pid, shared=SHARED_DIR, version, started} (`write_server_json` :930). `start_ai` (:969): Claude prewarm (`call_on_loop(ai_relay.prewarm_ai)`), model reaper, hardware refresh, hub-metadata refresh, background-app autostart resurrection. `stop_ai` (:985): engines stop, models unload, Claude session shutdown. `serve_in_thread(port)` (:1004) = make_server + start_ai + thread.

**Port 2777.** `macapp.DEFAULT_PORT = 2777` (macapp.py:34), `pick_port` tries 2777..2796 (:83). CLI `--port` default `FUSED_RENDER_APP_PORT` or 2777 (cli.py:44). dev.sh: 2778 on main, 2779 + crc32(branch)%1000 elsewhere.

**Mac app embedding.**
- `macapp.main` (:150): AppKit run loop first; `bootstrap()` (:281) on background calls `server.serve_in_thread(port)`, `wait_ready`, sets port on windows/dock/launcher, drains pending opens, starts `mac_update`.
- `kickoff` (:440) builds `mainwindow.WindowManager`, then `jobnotify.install(...)` (:460), `DockController` (:473), `_install_dock_hooks` (:482), `LauncherController` (:506/524).
- `quit_app` (:396): close windows, `server.stop_ai()`, stop captures, shutdown server, `os._exit(0)`.
- `server.native_hooks` (:110) dict: `open_files`, `focus_or_open`, `choose_file`, `show_home` (installed in `_install_dock_hooks` :91-126), `relaunch` (:353), `launcher_hotkey_bound`, `launcher_pinned_bound`, `launcher_rebind`.
- `mainwindow.py`: NSWindow + WKWebView per surface, shared data store/process pool, all pointed at `http://127.0.0.1:<port>/...`. NO WKScriptMessageHandler — every page talks to the app over HTTP only. Window identity: `app_file_of(url)` (:118) reads `/open?_file=`; any other URL is treated as a Home window by `WindowManager.show_home` (:945). Navigation policy pure in `window_policy.py` (`classify` :66 — any URL on our port = "app").

### Route table

| Method | Path | Handler |
|---|---|---|
| GET | `/` | index.html |
| GET | `/static/<name>` | `_static` |
| GET | `/open?_file=` / `?_url=` | open.html templated |
| GET | `/render?path=` | page + runtime.js injected |
| GET | `/dock`, `/launcher`, `/settings` | static pages |
| GET | `/favicon.ico` | 204 |
| GET | `/api/open/status?file=` | env install status |
| GET | `/api/fs/raw?path=&base=` | bytes, Range |
| GET | `/api/fs/stat?path=` | stat |
| GET | `/api/health` | {ok,version,pid} |
| GET | `/api/update` | self-update status |
| GET | `/api/jobs` | `jobs.list_jobs(mark_read=True)` |
| GET | `/api/showcase`, `/api/showcase/preview?id=` | home rows |
| GET | `/api/launcher?q=` | launcher results |
| GET | `/api/launcher/settings` (alias `/api/launcher/hotkey`) | settings |
| GET | `/api/dock`, `/api/dock/icon?file=&theme=`, `/api/dock/preview?file=&v=` | dock |
| GET | `/api/apps/background/status?html=`, `/api/apps/background/running` | background_routes |
| GET | `/api/engines/running` | background_routes |
| GET/HEAD | `/api/engines/<id>/proxy/<path>` | unguarded proxy |
| POST | `/api/open`, `/api/fetch`, `/api/drop`, `/api/run` | guarded |
| POST | `/api/fs/write`, `/api/fs/upload`, `/api/fs/mkdir` | guarded |
| POST | `/api/jobs`, `/api/jobs/clear`, `/api/jobs/<id>/cancel`, `/api/jobs/<id>/dismiss` | guarded |
| POST | `/api/dock/{open,pin,remove,order,reveal,choose,home,size}` | guarded |
| POST | `/api/launcher/settings` (alias `/hotkey`) | guarded |
| POST | `/api/update/{check,install,cancel,relaunch}` | guarded; 404 w/o manager |
| POST | `/api/apps/background/{start,stop,restart,autostart}` | guarded |
| POST | `/api/engines/<id>/stop`, `/api/engines/<id>/proxy/<path>` | guarded |
| POST | `/api/ai` | ai_relay.py:2270 |
| GET | `/api/ai/metrics?minutes=` | ai_relay.py:2287 |
| GET | `/api/ai/runtime`, `/api/ai/catalog` | ai_routes.py:780, 1356 |
| POST | `/api/ai/runtime/{load,unload,download}`, `/api/ai/cancel` | ai_routes.py:1433-1509 |
| POST | `/api/ai/{image,video,transcribe,embed}` | ai_routes.py:1541, 1828, 2045, 2377 |
| GET | `/api/capture` | capture.py:32 |
| POST | `/api/capture/start`, `/api/capture/{cid}/stop`, `/{cid}/cancel`, `/api/capture/screenshot` | capture.py:43-99 |

No `do_PUT`, `do_DELETE`, `do_OPTIONS` → a `router.delete` route is unreachable today.

## 2. Copied fused-render pieces — diff vs upstream

Counts: lite lines / upstream lines, difflib +added −removed (lite relative to upstream).

| lite | upstream | lines | +/- | verdict |
|---|---|---|---|---|
| claude_health.py | claude_health.py | 41 / 1289 | +18 −1266 | STUB: only `resolve()`, `CANDIDATES`, `executable()`, `candidates()`. No install/login/doctor/health report. |
| routes/ai_relay.py | server/ai.py | 2300 / 2417 | +45 −162 | Copy + import swaps (`fused_render_app._web` for fastapi). Windows .cmd shim / taskkill / CREATE_NO_WINDOW removed. BEHIND upstream: missing `thinking` tri-state (D886). |
| routes/ai_routes.py | server/routers/ai_runtime.py | 2535 / 2759 | +27 −251 | Trimmed older snapshot. |
| routes/ai_metrics.py | server/ai_metrics.py | 419 / 419 | +1 −1 | Verbatim (import). |
| routes/common.py | server/common.py | 115 / 389 | +6 −280 | Trimmed to AI frame helpers + `_require_fused`. |
| routes/capture.py | server/routers/capture.py | 113 / 227 | +27 −141 | Ported; WebSocket dropped. |
| jobs.py | jobs.py | 1192 / 1338 | +84 −230 | DIVERGED: lacks upstream `source`, `group`, `GROUP_MAX` (quiet-notifications); adds lite-only `set_transition_hook` (:464). |
| shared/fused_ai.py | templates/shared/fused_ai.py | 827 / 883 | +6 −62 | Older: no `decide`, no `thinking`. |
| shared/appenv.py | templates/shared/appenv.py | 239 | 0 | Identical. |
| shared/background_app.py | templates/shared/background_app.py | 258 | 0 | Identical. |
| engine.py | engine.py | 49 / 1380 | — | Stub `_NoBackend` (fused-engine branch replaces). |
| engine_forward.py | server/engine_forward.py | 200 / 241 | +117 −158 | Rewritten async→threaded stdlib. |
| engine_host.py | server/engine_host.py | 825 / 872 | +69 −116 | Template path removed; darwin posix_spawn bootstrap. |
| engine_worker.py | engine_worker.py | 269 / 268 | +13 −12 | Near-verbatim. |
| ai/supervisor.py | ai/supervisor.py | 3158 / 3197 | +46 −85 | Older: no `source` threading; uses `_app_env.uv_bin(download=True)`. |
| background_apps.py | background_apps.py | 417 / 448 | +32 −63 | Import swaps. |
| background_routes.py | server/routers/background_apps.py + engines.py | 212 / 257 | +163 −208 | Rewritten for stdlib Handler. |
| _child.py | _child.py | 70 / 128 | +9 −67 | Trimmed. |
| envinstall.py / projectenv.py / _env_install_worker.py | same | 2184/2228, 1428/1434, 2211/2211 | small | Near-verbatim. |
| ai/registry, catalog, hub_cache, tasks, fit, hw_detect | same | — | small | Near-verbatim (older). |
| shell/prefs.py | shell/prefs.py | 29 / 953 | — | STUB of constants. |
| shell/storage.py | shell/storage.py | 138 / 146 | +7 −15 | Near-verbatim (read_json/write_json). |
| container.py | appfile_container.py | 366 / 367 | +1 −2 | Verbatim. |
| appfile.py | appfile.py | 618 / 907 | +511 −800 | Lite's own. |
| paths.py | paths.py | 93 / 9 | — | Lite's own. |
| static/runtime.js | static/runtime.js | 1886 / 5982 | — | Lite's own; fused.ai / daemon / capture blocks lifted from upstream. |

Lite-only (no upstream counterpart):
- `webnotify.py` (742): WebKit C-API notification provider via ctypes on the shared WKProcessPool; `new Notification()` from own origin → UNUserNotificationCenter inside the bundle, log-only unbundled. API: `notify(identifier, title, body, sound=)`, `remove(identifier)`, `register_click_handler(prefix, fn)`, `install(pool, port_getter, ...)`.
- `notify_policy.py` (242): pure `decide(prev, after) -> Banner|None` from job tier + id prefix (`sys:ai-model:`, `sys:ai-image:`, `sys:ai-video:`, `sys:ai-transcribe:`, `sys:ai-text:`, `sys:ai-claude:`, `sys:ai-benchmark-`, `sys:env-install:`). Banner identifier = `"job:" + job_id` (replaces in place).
- `jobnotify.py`: `install(manager, apps_root, remembered_files=)` (:68) → `jobs.set_transition_hook(on_transition)` (:122); click routing via pure `page_target` (:43).

## 3. Claude CLI usage today

- Resolution: `claude_health.resolve()`: env `FUSED_RENDER_APP_CLAUDE_BIN`, then `FUSED_RENDER_CLAUDE_BIN`, then `shutil.which("claude")`, then `~/.local/bin/claude`, `/opt/homebrew/bin/claude`, `/usr/local/bin/claude`, `~/.claude/local/claude`, `~/.bun/bin/claude`. `ai_relay._claude_bin()` (:242) walks same list. `paths.fix_process_env()` appends `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` to PATH (Finder launch has launchd PATH).
- Spawn: only in `routes/ai_relay.py`: `_ai_cmd` (:274), `_spawn_claude_stream` (:310, `create_subprocess_exec`, `close_fds=False` → posix_spawn, 16 MiB line limit), `_ai_spawn` (:333), `_ai_reap` (:359).
  ```
  claude -p --input-format stream-json --output-format stream-json
    --include-partial-messages --verbose --model <m> --system-prompt-file <tmp>
    --tools= --setting-sources= --no-session-persistence
  ```
- Claude tier of `fused.ai.text` (`POST /api/ai`): model id w/o `/` (and not apple `afm-*`, not `.gguf`) → Claude. Default `claude-haiku-4-5-20251001` (:70); short names fable/opus/sonnet/haiku → `claude-{fable-5,opus-5,sonnet-5,haiku-4-5}` (:90). `effort` low|medium|high|xhigh. Timeout 600 s.
- `_AiSession`: ONE warm process (prewarmed at startup), per request `/clear` + `set_model` control_request (model + system prompt) + thinking/effort control requests; serialized by `asyncio.Lock`; respawn on crash/wedge. Each call opens a job row `sys:ai-claude:<uuid>` (not cancellable).
- Sessions/resume: NONE. `--no-session-persistence`, tools disabled, `/clear` every request, `history` refused with 400 on Claude tier (:1514). Nothing reads `~/.claude/projects`, no `--resume`. `runtime.js` header "(Claude CLI tier only)" is stale (local + apple tiers work since 0.5.0).
- `jobs._ORIGIN_BY_ROUTE` still lists upstream routes (`/claude-config`, `/tasks`, `/ai-models/local`...) that lite has no pages for.

Upstream backend to port (for sizing): `server/routers/claude_sessions.py` 1239, `server/routers/tasks.py` 7104, `tasks_store.py` 1794, `tasks_watch.py` 1121, `claude_spawn.py` 175, `session_liveness.py` 439, `claude_session_move.py` 369, `claude_install.py` 372, `claude_login.py` 436, `claude_health.py` 1289, `server/routers/claude_health.py` 208, `claude_config.py` 128, `queue_events.py` 211, `queue_manager.py` 2021, `project_queue.py` 645 (~17.5k lines). They import `fastapi` `APIRouter, Body, Header, HTTPException, Query`, `fastapi.concurrency.run_in_threadpool`, `pydantic.BaseModel`; use `@router.put` (claude_sessions.py:545) and `Query(..., alias="from")` (tasks.py:4881).

## 4. UI — `fused_render_app/static/`

- No build step, by policy (skill `setting-up-dev-env`: "No frontend — nothing to npm install; runtime.js is hand-written"). Static edits need only a refresh. Each page = one self-contained HTML with inline `<style>` + `<script>`. No shared CSS/JS module, no CDN/external assets.

| page | lines | role |
|---|---|---|
| index.html | 616 | Home: drop zone, path/URL form (POST /api/fetch, /api/drop), showcase rail (/api/showcase), update banner (/api/update), version footer. Opens via `location.href="/open?_file=..."`. |
| open.html | 116 | POST /api/open, polls /api/open/status every 700 ms, then mounts `<iframe src=view>` (`/render?path=`), `allow="clipboard-read; clipboard-write; fullscreen; microphone; camera"`. Keeps `_file` in URL = window identity. |
| dock.html | 927 | Menu-bar popover tray (/api/dock*). |
| launcher.html | 392 | Spotlight-like panel (/api/launcher). |
| settings.html | 158 | Launcher hotkey settings. |
| runtime.js | 1886 | Injected into /render pages only. |

- Theming: per-page `:root` custom props + `color-scheme: light dark` + `@media (prefers-color-scheme: dark)`. index.html and settings.html share palette `--fg #1f2023, --muted #6b7078, --bg #f6f7f8, --card #fff, --line #d9dce1, --accent #ebfd66, --accent-ink #0d0d0f, --err #c62828` (dark: `--fg #e8eaed, --bg #131417, --card #1b1d21, --line #2c2f35`). open.html uses a subset; dock/launcher use their own glass tokens (`--tray-bg`, `--sel-bg`, ...). `dev-iframe.html` (repo root) uses `--fused-*` vars + `data-fused-theme="shell"`.
- Shell pages call `fetch` directly with `{"X-Fused":"1","Content-Type":"application/json"}` (dock/launcher define `const HDRS`). Shell pages do NOT get runtime.js.
- runtime.js → `window.fused = {env:"local", device:"desktop", renderApp:true, runPython, rawUrl, stat, readFile, writeFile, params:{get,getAll,set,onChange}, uploadFile, mkdir, trackJob, watchJob, autoReload, snapshot(throws), daemon, ai, capture, fileIndex(throws)}` (:1833). Transport = direct same-origin `fetch` (page is a same-origin iframe of /open); no postMessage. Params live in topmost same-origin window's URL (`findTarget` :65). `callHeaders` (:683) adds `X-Fused-Page` from own `?path=`. Unsupported → `unsupportedFn` / `unsupportedNamespace` Proxy (:39-60). Error overlay on unhandled rejection with traceback.
- Upstream UI: React 18 + Vite + TS (`frontend/`, build = `check-boundaries && tsc --noEmit && vite build`). Sessions/tasks UI in `frontend/src/shell/` (TaskCards.tsx, TaskPeek.tsx, ScheduleTaskViews.tsx, ...) and `frontend/src/shell/claude*`. Built output `fused_render/static/shell-dist/` = 4.2 MB, gitignored, built at package time. Direct conflict with lite's no-build rule.

## 5. On-disk state

- `paths.py`: `home()` = `$FUSED_RENDER_APP_HOME` or `~/.fused-render-app` (dev.sh: `~/.fused-render-app-dev/<branch>`). `_sub(name)` makedirs: `apps/` (extracts `<slug>-<sha256[:16]>`), `fused_data/<app_id>` (shared .fused state), `venvs/`, `dropped/`, `downloads/`, `recordings/`, `bin/` (uv). Files: `app.log`, `server.json` (`pid_path`). No sessions/tasks subdir yet.
- `shell/storage.py`: `home_dir()` → `paths.home()`; atomic `read_json` (None if absent/corrupt) / `write_json` (mkstemp + os.replace). The primitive for any new store.
- `shell/prefs.py`: constants only (engine "builtin", `default_model()` "", idle unload 15 min). No prefs.json.
- `dock_store.py`: `<home>/dock.json` `{apps:[{file,name,openedAt,pinned}], tilesize}`, `threading.Lock`, MAX_RECENT 10, prunes missing files on read.
- `appfile.py` + `container.py`: `open_app_file` (:350) extracts v2 FUSEDAPP container (hardened codec) or v1 zip into `apps/<slug>-<hash>/`, writable. `app_id` from `<meta name="fused-app-id">`, validated `APP_ID_RE` `^[a-z0-9](?:[a-z0-9-]{0,47})-[0-9a-f]{8}$`. `ensure_dot_fused` (:311) symlinks `<extract>/.fused` → `fused_data/<app_id>` (migrates a real local .fused; `_link_dot_fused` :267), creates `data/`, `cache/`, `meta.json`. `extract_dir_for` (:225) reverse lookup w/o extracting.
- Also under home: `background_apps.json` (autostart), AI caches/worker status files. Jobs are in-memory only.

## 6. Packaging constraints

- `pyproject.toml`: zero runtime deps ("Every megabyte in the DMG has to earn its place"). Extras: `dev=[pytest]`, `app=[rumps, pyobjc-framework-Cocoa, -WebKit, -ScreenCaptureKit, -AVFoundation]`. hatchling; version from `fused_render_app/__init__.py` only. requires-python >=3.11, but 3.12 pinned in practice.
- py2app (`scripts/setup_py2app.py`): `packages` = fused_render_app, rumps, WebKit, ScreenCaptureKit, AVFoundation, Quartz, CoreMedia, CoreAudio + whole stdlib minus `STDLIB_EXCLUDED`; `resources` = `fused_render_app/static` (new static files ship automatically); `excludes` = setuptools, pip, PIL, pillow, packaging (py2app follows lazy imports; stray pillow once cost ~7 MB). Bundle id `io.fused.render.app`, URL scheme `render-app`, UTI `.fused`.
- `scripts/build_dmg.sh` (492): framework python → wheel → build venv → icon → py2app → prune (tests, __pycache__, Tcl/Tk, ncurses, _test*) → Contents/lib symlink → stdlib probes → bundled uv (~15 MB) → apple helper → minos floor 14.0 → codesign → dmgbuild → notarize. No Node step.
- STATUS.md: every version gets a DMG-size row. 0.9.5 = 43.43 MB; 0.10.0 TBD (first packaging change since 0.6.0: pyobjc capture frameworks). fused-engine measured +8.3 MB.
- Implications: pydantic/FastAPI must NOT be added — adapt upstream routers to the shim. A Vite build would add Node to CI (`test.yml` runs only pytest on ubuntu; `macos-desktop` DMG smoke only when packaging paths change) and `build_dmg.sh`, and either a committed bundle or a build-time step; upstream bundle is 4.2 MB.

## 7. Tests

- `tests/` flat, ~300 test functions / 17 files: test_ai_routes, test_appfile, test_capture{,_darwin,_mixdown}, test_dock, test_editlink, test_fetch, test_jobnotify, test_jobs_transition_hook, test_launcher, test_notify_policy, test_server, test_showcase, test_update, test_webnotify, test_window_policy.
- `conftest.py`: autouse `app_home` sets `FUSED_RENDER_APP_HOME` to tmp; `client` fixture = `jobs.reset()` + `server.serve_in_thread(0)` + urllib `Client` (post adds `X-Fused: 1`); `v1_fused`, `v2_fused`, icon/png variants.
- `tests/fake_claude.py`: stream-json stand-in for `claude` (knobs in prompt: FAIL, CRASH, SLOW). `test_ai_routes.py` autouse points both `*_CLAUDE_BIN` env vars at a missing path.
- Run: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev,app]" && .venv/bin/python -m pytest -q` (or run `scripts/dev.sh` once). This worktree has no `.venv` yet. CI: `pip install -e ".[dev]"; pytest -q` on ubuntu-latest py3.12, native bits monkeypatched. I did not run the suite.

## Seams (port order)

1. **Route registration**: new `routes/claude_sessions.py`, `routes/tasks.py` as `_web.APIRouter`; `AI_ROUTER.include_router(...)` at `server.py:89-94`. Hand-written Handler routes go in `do_GET` (:185) / `do_POST` (:253) before the `_dispatch` fallthrough.
2. **Shim additions** (`_web.py` + `server.py`) needed before upstream routers import/behave:
   - `APIRouter.put` + `Handler.do_PUT`; `do_DELETE` → `_dispatch` (upstream `@router.put("/api/claude-sessions/defaults")`).
   - `Query(default, alias=)` marker, honoured by `call_route` (tasks.py:4881 `alias="from"`).
   - `_dispatch` must map `HTTPException` → its status (today → 500).
   - `run_in_threadpool` equivalent (`asyncio.to_thread`).
   - pydantic `BaseModel` bodies → plain dicts (`call_route` binds only a param literally named `body`).
   - POST dispatch passes `q={}` (`_dispatch("POST", route, {})`) → POST routes see no query params.
3. **Streaming/liveness**: NDJSON chunked `StreamingResponse` works; SSE works the same way; long-poll via sync routes OK; keep async routes non-blocking (shared loop); no disconnect detection.
4. **Static page**: `static/sessions.html` (hand-written, index palette) + `if route == "/sessions": return self._static("sessions.html")` next to `/dock`/`/launcher` in do_GET. A native window on it counts as Home in `WindowManager.show_home` unless `app_file_of`/identity learns the route. Entry points: DockController actions (macapp.py:473), rumps `App.menu`, launcher row, `native_hooks`.
5. **runtime.js**: new namespace in the `window.fused = {...}` literal (:1833), `callHeaders()` for `X-Fused-Page`, `unsupportedNamespace` for gaps. Coordinate with the window.fused agent — same literal.
6. **Notifications**: `jobs.set_transition_hook` holds ONE subscriber and `jobnotify.install` (jobnotify.py:122) owns it. Task banners: extend `notify_policy.decide` (new `sys:task:` prefix) + `jobnotify.page_target`, or chain the hook, or call `webnotify.notify` + `register_click_handler(prefix, fn)` directly. Port upstream `jobs` `source`/`group` first if task-notify logic depends on them.
7. **Prefs/paths**: add `paths.sessions_dir()`/`tasks_dir()` via `_sub(...)`; real prefs on `storage.read_json/write_json` (prefs.py is constants). Claude bin: `claude_health.resolve()` + `paths.fix_process_env()`.
8. **Packaging**: pure Python is ~free (package + static bundled whole). Avoid new third-party deps in `[app]`/py2app or justify in STATUS.md. Vite/React port vs hand-ported no-build page is a lead-level policy call (4.2 MB upstream bundle, no Node in CI/build_dmg.sh, skill forbids a frontend build).
