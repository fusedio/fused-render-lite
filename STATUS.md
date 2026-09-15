# fused-render-lite status

One section per version: what the `fused.*` runtime supports, what it does
not, and what the build weighs. Sizes come from `bash scripts/build_dmg.sh`
(`app size` line and `done:` line), macOS arm64, ad-hoc signed.

## Size by version

Shipped size = the DMG attached to the GitHub release (built by CI on
`macos-26`, python.org framework Python, Developer ID signed + notarized since 0.8.3). That is what end
users download.

| version | shipped DMG | Δ vs previous | .app unpacked | what changed |
| --- | --- | --- | --- | --- |
| fused-render (full) | ~hundreds of MB | — | ~400 MB installed packages | reference point |
| 0.8.6 | 42.50 MB (42,497,237 B) | +0.01 MB | 98 MB | dock green running badge, Home leads pinned zone, palette placeholders, Home title-bar button, GitHub links; no packaging change |
| 0.8.5 | 42.48 MB (42,482,659 B) | −0.00 MB | 98 MB | dock Home tile focus fix, softer magnify, hollow menubar icon; no packaging change |
| 0.8.4 | 42.48 MB (42,482,819 B) | +0.00 MB | 98 MB | Homebrew cask + `bump-homebrew` release job (PR #7); no code change |
| 0.8.3 | 42.48 MB (42,479,175 B) | −0.19 MB | 98 MB | first Developer ID-signed + notarized + stapled DMG; DMG container codesigned (PR #6); no code change |
| 0.8.2 | 42.67 MB (42,673,807 B) | +0.04 MB | 98 MB | menu-bar Dock popover; Open in Browser title-bar button; no packaging change |
| 0.8.1 | 42.63 MB (42,631,560 B) | +1.66 MB | 98 MB | showcase refreshed to 14 .fused bundles (+~2 MB of app files); no packaging change |
| 0.8.0 | 40.98 MB (40,975,944 B) | +0.00 MB | 96 MB | auto-fit card grid; no packaging change |
| 0.7.2 | 40.98 MB (40,975,692 B) | +0.03 MB | 96 MB | venv-symlink fix; server.json + shared/ (fused_ai, appenv, background_app) |
| 0.7.1 | 40.95 MB (40,945,024 B) | +0.00 MB | 96 MB | rebuild from main head; no code change |
| 0.7.0 | 40.94 MB (40,944,835 B) | +0.10 MB | 96 MB | fused.daemon (pure-Python source) + native windows / menu bar; no packaging change |
| 0.6.1 | 40.85 MB (40,848,435 B) | −0.07 MB | 95 MB | rename to Render Lite; no packaging change |
| 0.6.0 | 40.92 MB (40,915,928 B) | +16.52 MB | 95 MB | build/CI parity with fused-render: uv bundled in the app (`Resources/bin/uv`, ~+15 MB), apple tier helper compiled + bundled (macos-26 runner), Mach-O/minos probes, prepare-release job, DMG smoke build in test.yml |
| 0.5.8 | 24.39 MB (24,393,863 B) | −3.30 MB | 60 MB | size: drop Tcl/Tk, ncurses and CPython's `_test*` fixtures that the whole-stdlib copy dragged in (lite-only trim on top of fused-render's list) |
| 0.5.7 | 27.69 MB (27,690,249 B) | +0.1 KB | 69 MB | fix: a stale `uv` on PATH (no `--managed-python`/`--no-default-groups`) is skipped; the pinned uv is downloaded instead |
| 0.5.6 | 27.69 MB (27,690,138 B) | +8.16 MB | 69 MB | fix: Python packaged exactly as fused-render (whole stdlib, `Contents/lib` symlink, self-locating bundled interpreter builds every venv; no uv-managed Python detour). App size up (full stdlib). |
| 0.5.5 | 19.53 MB (19,526,644 B) | +0.2 KB | 29 MB | fix: opener materialises `<app>/.fused/{data,cache}` + `meta.json` on every open (fused-render convention) so `writeFile` into `.fused/data` works without `mkdir` |
| 0.5.4 | 19.53 MB (19,526,414 B) | +0.5 KB | 29 MB | fix: runner venvs really built on the uv-managed Python in the packaged app (0.5.2 missed the install worker's interpreter slot) |
| 0.5.3 | 19.53 MB (19,525,879 B), ad-hoc until secrets are set | +0.5 KB | 29 MB | release pipeline: Developer ID signing + notarization + stapling (needs repo secrets) |
| 0.5.2 | 19.53 MB (19,525,398 B) | −1.3 KB | 29 MB | fix: local runners could not build in the packaged app (stub interpreter, dangling SSL_CERT_DIR) |
| 0.5.1 | 19.53 MB (19,526,708 B) | +1.80 MB vs 0.4.0 | 29 MB | 0.5.0 minus an accidental pillow bundle (py2app followed lazy `PIL` imports in runner-side modules; excluded) |
| 0.5.0 | 26.22 MB (26,215,796 B) | +8.49 MB | 42 MB | local inference: fused-render's AI subsystem copied in (text/image/video/transcribe/embed). ~7 MB of the Δ was pillow + libjpeg/libtiff/liblzma pulled in by mistake — fixed in 0.5.1 |
| 0.4.0 | 17.73 MB (17,729,166 B) | +1.79 KB | 25 MB | legacy env for apps without `pyproject.toml`; `autoReload(true)` throws |
| 0.3.0 | 17.73 MB (17,727,372 B) | −1.77 KB | 25 MB | `uploadFile`, `mkdir`, `trackJob`/`watchJob`, `autoReload(false)` no-op, runPython timeout 600 s |
| 0.2.0 | 17.73 MB (17,729,146 B) | +5.99 KB | 25 MB | `fused.ai.text` (Claude CLI tier) |
| 0.1.0 | 17.72 MB (17,723,156 B) | — | 25 MB | first lite build |

The Claude tier costs nothing beyond one Python module and ~150 lines of
runtime JS: inference runs in the user's own `claude` CLI, which is not
bundled.

A local `bash scripts/build_dmg.sh` on Homebrew's python@3.12 comes out
~5.5 MB smaller (12.2 MB) because the Homebrew bottle is leaner than the
python.org framework; it is not the shipped artifact and runs only on the
building macOS version.

Constant across versions: 0 runtime Python deps (`rumps` + `pyobjc-framework-Cocoa`
only in the `[app]` extra); no bundled data packages (each app's
`pyproject.toml` → `uv sync`); `uv` downloaded on first use (0.12.13,
sha256-verified) unless built with `FUSED_RENDER_BUNDLE_UV=1`.

---

## 0.8.7

Dock: drag the separator to resize the tray like the Dock (16–128 px, ⌥ snaps
to 16/32/64/128, clamped to the screen-fit cap after snapping); size persists
in dock.json as `tilesize` (GET /api/dock, POST /api/dock/size). Every metric
in dock.html derives from `--tile` so the tray scales as one piece; the native
panel is sized once per drag and the resize cursor is held natively. Window:
closing destroys the WKWebView and unloads the page (pagehide/unload fire,
media stops) instead of just ordering out, breaking the _Window ref cycle so
it deallocs by refcount; quit closes all windows first. No packaging change.

## 0.8.6

Dock: running indicator is a green dot inset in the tile's upper-left corner
(headroom above tiles removed); Home tile leads the pinned zone wearing the
app icon; placeholder tiles show the first letter on a palette-hashed
background until a real icon arrives. Window title bar gains a Home button
next to Open in Browser. GitHub links added to the dev and lite UIs. No
packaging change.

## 0.8.5

Fix (dock): the tray Home tile and "Open in app" focus an open Home window
or open a new one, even while app windows are open; the macOS Dock-icon
reopen keeps its front-window behaviour. Hover magnification capped at 1.5.
Menu bar icon regenerated from the app glyph with the hollow centre.

## 0.8.4

Homebrew: `brew install --cask fusedio/tap/fused-render-lite` (PR #7). The
release workflow gained a `bump-homebrew` job that rewrites the cask's
`version`/`sha256` in fusedio/homebrew-tap after the DMG lands on the Release;
this is the first release that exercises it. No runtime change.

## 0.8.3

Release pipeline: first Developer ID-signed + notarized + stapled DMG. The 7
signing/notary secrets are now set on the repo. `build_dmg.sh` also codesigns
the DMG container itself after `dmgbuild` (PR #6) — without it notarization
and stapling succeeded but Gatekeeper's `spctl -a -t open` rejected the DMG
with `no usable signature`. No runtime change.

## 0.8.2

Menu bar: Dock popover listing pinned and recent apps (PR #4).

Window title bar: "Open in Browser" button (PR #3). No packaging change.

## 0.8.1

Launcher: showcase cards redesigned as a horizontal scroll rail with a
hover peek preview; showcase set refreshed to 14 apps (OpenBot,
DoodleShooter, OpenRelax, ClaudeUsage, OpenWhisper, OpenColor, OpenDesign,
OpenSVG, OpenCPU, OpenScreen, ShatteredGlass, Focusly, FusedWeb,
Transcripto), file names numbered without spaces.

Dev: `dev-iframe.html` gains a reload button and version display.

## 0.8.0

Card grid: `auto-fill` → `auto-fit` with centered content, so the launcher
lays out correctly at narrow viewport widths (e.g. embedded in an iframe).

Dev: `dev-iframe.html` at the repo root hosts the UI in an iframe for
testing the embedded case. No packaging change.

## 0.7.2

Fix: `fused.daemon.start()` refused every real app venv with "not an
interpreter from the project venv store" — `engine_host._validate_interpreter`
realpath'd the venv's `bin/python`, a symlink to the base interpreter outside
`~/.fused-render-lite/venvs`. Now resolves the venv directory instead.

Fix: apps written to fused-render's background-apps contract could not find
the server — lite never exported `FUSED_RENDER_HOME_DIR` nor wrote
`<home>/server.json`. `make_server` now does both (`{origin, port, pid,
shared, version, started}`), and `fused_render_lite/shared/` ships
`appenv.py`, `fused_ai.py`, `background_app.py` verbatim from fused-render so
`sys.path.insert(0, info["shared"])` works the same way.

## 0.7.1

Rebuild of 0.7.0 from main head — confirms the DMG carries the native-window /
menu-bar work (PR #2) alongside `fused.daemon`. No code change.

## 0.7.0

`fused.daemon` supported — fused-render's background-apps feature copied in
(`background_apps.py`, `engine_host.py`, `engine_worker.py`,
`background_app.py`, runtime.js block verbatim). Lite-specific: routes ported
from FastAPI onto the stdlib `Handler` (`background_routes.py`), the proxy is
synchronous (`engine_forward.py`: same pool / at-most-once / 504-never-heals /
heal-then-retry-once rules, minus the browser-hangup 204 path), the daemon
runs on the venv `env.py` built for the app, macOS spawns through a
posix_spawn-safe bootstrap (no fork; setsid + chdir in the child), and the
cache lives under `~/.fused-render-lite/engines/<id>/`.

| member | status | notes |
| --- | --- | --- |
| `fused.daemon.status / start / stop / restart / setAutostart` | ✅ new | `[tool.fused-render.app]` manifest in the app's pyproject.toml |
| `fused.daemon.run(params)` | ✅ new | `main =` apps; warm `engine_worker.py`, 60 s call budget, reaped after 15 min idle |
| `fused.daemon.call(path, body)` | ✅ new | `daemon =` apps' own HTTP routes, proxied |
| `fused.daemon.watch(cb)` | ✅ new | 5 s poll while visible |

Server routes added: `GET/POST /api/apps/background/{status,start,stop,restart,autostart,running}`,
`GET /api/engines/running`, `POST /api/engines/<id>/stop`, `ANY /api/engines/<id>/proxy/<path>`.

## 0.6.1

App renamed to **Render Lite**: `RenderLite.app`, `RenderLite-<version>.dmg`, menu-bar title,
placeholder page and runtime error text. Python package (`fused_render_lite`), state dir
(`~/.fused-render-lite`), env vars and bundle id `io.fused.render.lite` unchanged so existing
installs keep their venvs and settings.

## 0.5.0

Changes from 0.4.0: **local inference**, by copying fused-render's AI subsystem
verbatim (`fused_render_lite/ai/`: registry, catalog, fit, hw_detect, hub_cache,
supervisor, and every runner folder; `routes/ai_relay.py` + `routes/ai_routes.py`
are fused-render's own `/api/ai*` routers mounted through a 250-line FastAPI-compat
layer, `_web.py`). Runners are folders with a `pyproject.toml` + `worker.py`; the
supervisor builds each runner's venv with `uv sync` on first use, spawns the
worker on that venv, and talks HTTP to it. Nothing ML ships in the DMG.

Dropped from the copy: benchmarking (`benchmark`, `bench_store`, `speed`,
`gguf_sources`), the AI Models / Preferences pages. Preferences are fixed to
fused-render's defaults (`shell/prefs.py`: engine `auto`, idle unload 15 min).
The Apple-Intelligence helper is not bundled in the DMG.

0.5.2–0.5.4 worked around venv builds failing in the packaged app by detouring
to a uv-managed 3.12; 0.5.6 removed the detour once the real cause was found:
the bundle lacked fused-render's `Contents/lib -> Resources/lib` symlink (so the
bundled python, run with PYTHONHOME scrubbed, resolved `sys.prefix` to the build
machine's framework and died on `encodings`) and shipped a traced stdlib subset.
The bundle is now packaged exactly as FusedRender.app (whole stdlib, symlink,
self-locate + stdlib-complete probes in build_dmg.sh) and every venv — app,
legacy, runner — is built on `Contents/MacOS/python`. The dangling
`SSL_CERT_DIR` py2app exports is still dropped at startup (lite-only).

Verified on this Mac (M-series, macOS 26): local text (LFM2.5-1.2B, 4-bit),
embed (nomic modernbert, 768-d), transcribe (whisper-tiny on a `say` clip,
exact transcript), image (FLUX.2-Klein 4B, 256×256 PNG), Claude tier, cancel of
a running job, page-driven calls in headless Chrome.

| member | status | notes |
| --- | --- | --- |
| `fused.runPython`, `params`, `readFile/stat/writeFile/rawUrl`, `uploadFile/mkdir`, `trackJob/watchJob`, `autoReload(false)` | ✅ | as 0.4.0 |
| `fused.ai.text` — Claude tier | ✅ | fused-render's relay: one warm `claude` stream-json process, `/clear` between calls, `effort`, streaming |
| `fused.ai.text` — local tier (repo id / `.gguf` / `provider: "local"`) | ✅ new | first call → `model_loading` + `err.jobId`, `watchJob` it, retry; `history`, `raw`, `images` (vision models), `temperature/maxTokens/topP` honoured |
| `fused.ai.image` | ✅ new | job-backed; mflux on Apple Silicon, diffusers elsewhere; `onProgress` with `previewUrl` |
| `fused.ai.video` | ✅ new | job-backed; LTX-2 via MLX, Apple Silicon only (28 GB model) |
| `fused.ai.transcribe` | ✅ new | job-backed; mlx-whisper on Apple Silicon, faster-whisper elsewhere; progressive segments via `onChunk`, `diarize`, `words` |
| `fused.ai.embed` | ✅ new | direct; mlx-embeddings / onnx; `kind: query\|document`, `paths` on dual encoders |
| `fused.ai.models.list / catalog / load / download / unload`, `fused.ai.cancel(capability)` | ✅ new | fused-render's contract |
| `fused.ai.text` / `transcribe` with `provider: "apple"` / `afm-*` ids | ⚠️ checkout only | fused-render's host compiles the Swift helper on demand when Xcode with the macOS 26 SDK is present (verified here: `afm-2025` answered). The DMG ships without the helper → `unavailable` |
| `fused.capture.*`, `fused.fileIndex.*`, `fused.snapshot`, `autoReload(true)` | ❌ throws | |

Disk on first use (this Mac, measured): mlx-text runner venv 581 MB + the default
0.7 GB text model; other capabilities pull their own runner venv (200 MB–4 GB)
and model on first call. Models live in the Hugging Face cache; worker state
under `~/.fused-render-lite/ai/`.

Routes added: `POST /api/ai/image|video|transcribe|embed`, `GET /api/ai/runtime`,
`GET /api/ai/catalog`, `POST /api/ai/runtime/load|download|unload`,
`POST /api/ai/cancel`, `GET /api/ai/metrics`. `/api/jobs` now runs fused-render's
`jobs.py` (tiers, stall detection) behind the same page contract.

---

## 0.4.0

Changes from 0.3.0: an app that ships **no `pyproject.toml`** no longer runs on a
stdlib-only Python. It runs in one shared "legacy" venv holding fused-render's
old `[bundled]` set minus the cloud credential chains and the fused engine —
built by `uv sync` on the first such open (~120 MB on disk, once), so `.fused`
files exported before pyproject was required keep working. `fused.autoReload(true)`
now throws (only `autoReload(false)` is a no-op).

Legacy set: `numpy`, `pandas`, `requests`, `pillow`, `openpyxl`, `python-pptx`,
`msgpack>=1.0`, `fpdf2>=2.8.7`, `drain3>=0.9.11`. Not carried over from
`[bundled]`: `botocore`, `google-auth`, `fused`, `mcp`. Override for tests with
`FUSED_RENDER_LITE_LEGACY_DEPS` (comma list). Generated project lives at
`~/.fused-render-lite/legacy/pyproject.toml`; a changed set invalidates the venv.

API table: identical to 0.3.0 except `fused.autoReload(true)` → ❌ throws.

---

## 0.3.0

Changes from 0.2.0: `fused.uploadFile` and `fused.mkdir` land; `fused.trackJob` /
`fused.watchJob` land on an in-process job store (no shell UI, but rows survive a
page reload and a worker can be told to stop via `cancel_requested`);
`fused.autoReload(false)` is accepted as a no-op instead of throwing (a `.fused`
extract never changes under the page); `autoReload(true)` still throws; `runPython` timeout 60 s → 600 s, matching
fused-render. Workers spawned by `runPython` get `FUSED_RENDER_ORIGIN` so a
detached process can `POST /api/jobs`.

| member | status | notes |
| --- | --- | --- |
| `fused.runPython(py, params, opts?)` | ✅ | 600 s timeout; `opts.key` supersession, `opts.signal` |
| `fused.params.get / getAll / set / onChange` | ✅ | |
| `fused.readFile` / `stat` / `writeFile` / `rawUrl` | ✅ | as 0.2.0 |
| `fused.uploadFile(path, blob)` | ✅ new | raw body to `/api/fs/upload?path=&base=`; 403 `readonly` → `err.type` |
| `fused.mkdir(path)` | ✅ new | 409 → `type: "exists"`, 403 → `readonly` |
| `fused.trackJob(spec)` | ✅ new | `update/finish/fail/cancelled`, `cancelRequested`, `state`; fire-and-forget |
| `fused.watchJob(id)` | ✅ new | `get()`, `watch(cb, ms)`, `stop()`, `cancel()` |
| `fused.autoReload(false)` | ✅ no-op | opting out of live reload is accepted; nothing to watch in a .fused extract |
| `fused.autoReload(true)` | ❌ throws | live reload needs a file watcher lite does not have; the app should know |
| `fused.env` / `fused.device` / `fused.lite` | ✅ | |
| `fused.ai.text` | ✅ Claude only | as 0.2.0 |
| `fused.ai.models.list() / catalog()`, `fused.ai.cancel()` | ✅ | as 0.2.0 |
| `fused.ai.text` with `history`/`raw`/`images` | ❌ `bad_request` | |
| `fused.ai.text` with `provider: local/apple`, repo-id/`.gguf` model | ❌ `unavailable` | |
| `fused.ai.image / video / transcribe / embed`, `ai.models.load/download/unload` | ❌ `unavailable` | |
| `fused.capture.*` | ❌ throws | |
| `fused.fileIndex.*` | ❌ throws | |
| `fused.daemon.*` | ❌ throws | |
| `fused.snapshot` | ❌ throws | |

Server routes added: `POST /api/fs/upload`, `POST /api/fs/mkdir`, `GET/POST /api/jobs`, `POST /api/jobs/<id>/cancel|dismiss`, `POST /api/jobs/clear`.

---

## 0.2.0

| member | status | notes |
| --- | --- | --- |
| `fused.runPython(py, params, opts?)` | ✅ | `main(**params)` in the app's own venv; `opts.key` supersession, `opts.signal` |
| `fused.params.get / getAll / set / onChange` | ✅ | URL-backed, `_keys` reserved, batched history writes |
| `fused.readFile(path)` | ✅ | text |
| `fused.stat(path)` | ✅ | `{path, name, is_dir, size, mtime, writable}` |
| `fused.writeFile(path, content, opts?)` | ✅ | `expectedMtime` → 409 `conflict`; `create` → 409 `exists`; 403 `readonly` |
| `fused.rawUrl(path)` | ✅ | Range requests honoured |
| `fused.env` / `fused.device` / `fused.lite` | ✅ | `"local"` / `"desktop"` / `true` |
| `fused.ai.text({prompt, ...})` | ✅ Claude only | one `claude -p` per call. `model`: `haiku` (default), `sonnet`, `opus`, `fable`, `claude-*`. `systemPrompt`, `effort` (`low` = no thinking, `medium`, `high`, `xhigh`), `onChunk` (NDJSON over chunked HTTP), `abortSignal` (kills the CLI). `temperature`/`maxTokens`/`topP` → `warnings[]`. Errors: `ai_unavailable`, `bad_request`, `unavailable`, `ai_error`, `timeout` 600 s, `cancelled` |
| `fused.ai.models.list() / catalog()` | ✅ | Claude catalog; `catalog().unsupported` names the local capabilities |
| `fused.ai.cancel()` | ✅ | resolves `false`; use `abortSignal` |
| `fused.ai.text` with `history` / `raw` / `images` | ❌ `bad_request` | need a local model |
| `fused.ai.text` with `provider: "local"` / `"apple"`, repo-id or `.gguf` model | ❌ `unavailable` | no local inference |
| `fused.ai.image / video / transcribe / embed` | ❌ `unavailable` | async rejection, `catch` branches keep working |
| `fused.ai.models.load / download / unload` | ❌ `unavailable` | |
| `fused.capture.*` | ❌ throws | screen / audio / screenshot |
| `fused.fileIndex.*` | ❌ throws | filesystem index |
| `fused.daemon.*` | ❌ throws | folder daemons |
| `fused.trackJob`, `fused.watchJob` | ❌ throws | jobs |
| `fused.uploadFile`, `fused.mkdir` | ❌ throws | |
| `fused.autoReload` | ❌ throws | live reload |
| `fused.snapshot` | ❌ throws | git snapshots |

"throws" = `Error("<name> is not supported on fused-render-lite")`, `err.type === "unsupported"`, on property read for namespaces (Proxy) or on call.

Server routes: `GET /`, `/open?_file=`, `/render?path=`, `/api/health`, `/api/fs/raw`, `/api/fs/stat`, `/api/open/status`, `/api/ai/runtime`, `/api/ai/catalog`; `POST /api/open`, `/api/drop`, `/api/run`, `/api/fs/write`, `/api/ai` (JSON or chunked NDJSON), `/api/ai/cancel`; other `POST /api/ai/*` → 409 `unavailable`. POSTs need `X-Fused: 1`. 127.0.0.1 only.

Requires the `claude` CLI on the machine for the AI tier (`FUSED_RENDER_LITE_CLAUDE_BIN`, PATH, `~/.claude/local`, `~/.local/bin`, `~/.bun/bin`, Homebrew).

---

## 0.1.0

| member | status | notes |
| --- | --- | --- |
| `fused.runPython` | ✅ | as above |
| `fused.params.*` | ✅ | as above |
| `fused.readFile` / `stat` / `writeFile` / `rawUrl` | ✅ | as above |
| `fused.env` / `fused.device` / `fused.lite` | ✅ | |
| `fused.ai.*` (all verbs, `models`, `cancel`) | ❌ throws | whole namespace a Proxy |
| `fused.capture.*`, `fused.fileIndex.*`, `fused.daemon.*` | ❌ throws | |
| `fused.trackJob`, `watchJob`, `uploadFile`, `mkdir`, `autoReload`, `snapshot` | ❌ throws | |

Server routes: as 0.2.0 minus every `/api/ai*` route.

---

## Removed from fused-render (never in lite)

Explorer shell (React/Vite), ~50 preview templates and their vendored JS,
file index, LAN sharing, background daemons, jobs, capture, git snapshots,
Claude chat sidebar, local/Apple AI runners (mlx, llama.cpp, whisper, image
and video models), bookmarks, drafts, updater, Windows/Linux/iOS shells,
bundled data stack (numpy, pandas, pyarrow, duckdb, botocore, fused engine…).
