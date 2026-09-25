# fused-render-app

Ships as **Render App** (`RenderApp.app`, `RenderApp-<version>.dmg`). Opens a `.fused` single-file app. Nothing else.

Double-click a `.fused` in Finder (or drop one onto the placeholder page) and
the app's entry page opens in a window of Render App — a native macOS window
hosting a WKWebView, not a browser tab. Open as many as you like: every window
is on the one local server. The URL behind a window carries the file:

```
http://127.0.0.1:2777/open?_file=/Users/you/Downloads/app.fused&n=80
```

Everything after `_file` is the app's own `fused.params` state.

### Windows

The macOS app (`macapp.py` + `mainwindow.py`) is a regular app: Dock icon,
menu bar item, a main menu, and one window per opened `.fused`.

| in a window | what happens |
| --- | --- |
| Finder open, Dock click, File → Open… (⌘O), New Window (⌘N) | a new window (Dock click focuses the front one if any) |
| `target=_blank`, `window.open`, ⌘-click / middle-click on an app link | a new window (`window.open` returns a live handle: `postMessage`, `opener`, `close()` work) |
| `window.close()` from a page | closes that window |
| a link to another site | the default browser |
| `<a download>`, `Content-Disposition: attachment`, a type WebKit can't show | saved to `~/Downloads` (Finder-style `name 2` on collision) |
| `alert` / `confirm` / `prompt`, `<input type=file>` | native panels |
| `getUserMedia`, `navigator.geolocation` from the app's own page | granted; the system camera/mic/location prompt still applies |
| `requestPointerLock` (FPS-style mouse look) | granted, Esc releases |
| `Notification.requestPermission` / `new Notification` from the app's own page | granted; shown as a macOS notification, click focuses the app |
| ⌘C/⌘V/⌘X/⌘Z/⌘A, ⌘W, ⌘R, ⌘[ ⌘], ⌘P, ⌘M | the Edit / File / View / Window menus |

The app also posts its own macOS notifications for work it runs in the
background: a model download or an app's environment install starting
(silent), an install waiting for your approval, and every download, install,
render, transcription or benchmark finishing or failing. A resident model
load and a text generation stay quiet on success, as in fused-render. One
banner per job: the "started" banner is replaced in place by the outcome.
Clicking brings the app forward and, for an install, the app it was for
(`jobnotify.py`, `notify_policy.py`).

Closing the last window does not quit. The menu-bar item has four entries:
"Open in app" (focus the front window or open the placeholder), "Open in
browser", "Open app logs", "Quit". The Dock icon does the same as "Open in
app"; ⌘Q and the Dock also quit. View → Open in Browser hands the current page
to the default browser. `FUSED_RENDER_APP_NO_BROWSER=1` suppresses the
startup window.

Every window's title bar ends in three buttons: Edit, Open in Browser, Home
(View → Edit in fused-render ⌘⇧E, Open in Browser ⌘⇧L, Home ⌘⇧H). Edit hands
the window's `.fused` to fused-render, the full editor, as a
`fused-render://open?file=<path>` deep link: fused-render clones it into its
workspace (`~/Fused/local/<name>`) and opens the copy for editing; when a
copy already exists, fused-render asks whether to overwrite it with this
`.fused` or open the copy as it is. Edit is disabled
on Home. Without fused-render installed, a dialog offers to download the
latest DMG (`render.fused.io/latest.json` → `dmg_url`, falling back to the
download page) — `editlink.py`.

⌥Space (change it in Settings — the gear on the home page — or from the
search panel's footer; right-click the menu-bar item → "Search Apps…" opens
it without a shortcut) drops a Spotlight-like search panel: empty, it lists the
apps pinned in the menu-bar Dock; typing searches every app Render App
remembers plus the showcase. ↑/↓ select, ↩ opens, ⌥1–⌥9 open the Nth row
(the modifier is a setting), esc clears then closes. The same ⌥1–9 work
from anywhere as global shortcuts for the Nth pinned Dock app; ⌥0 (and the
last row) opens Render App itself. `FUSED_RENDER_APP_LAUNCHER_SHOW=1` shows it at
startup and makes SIGUSR2 toggle it (dev). The CLI (`fused-render-app`, `scripts/dev.sh`) is unchanged
and still opens a browser tab.

## What it supports

The page runtime exposes these `fused.*` members:

| API | Notes |
| --- | --- |
| `fused.runPython(py, params, opts?)` | runs `main(**params)` from the app's own venv, 600 s cap |
| `fused.params.get/getAll/set/onChange` | URL-backed state, same semantics as fused-render |
| `fused.readFile(path)` | text |
| `fused.stat(path)` | `{path, name, is_dir, size, mtime, writable}` |
| `fused.writeFile(path, content, opts?)` | optimistic lock + create-only, as in fused-render |
| `fused.rawUrl(path)` | bytes URL, Range requests honoured |
| `fused.ai.text / image / video / transcribe / embed`, `fused.ai.models.*`, `fused.ai.cancel` | fused-render's AI subsystem: Claude CLI tier + local runners (see AI below) |
| `fused.uploadFile(path, blob)` / `fused.mkdir(path)` | binary save, directories |
| `fused.trackJob(spec)` / `fused.watchJob(id)` | in-process job rows; survive a reload, cancellable |
| `fused.autoReload(false)` | accepted, no-op; `autoReload(true)` throws (no live reload) |
| `fused.daemon.status / start / stop / restart / setAutostart / run / call / watch` | the app's own long-running daemon, fused-render's implementation copied in (see Background daemons below) |
| `fused.capture.screen / audio / screenshot / sources / list / attach` | native macOS screen / microphone / still capture, fused-render's contract, ScreenCaptureKit + AVFoundation (see Capture below) |

Every other member the full fused-render runtime has (`fileIndex`,
`snapshot`) is **not supported**. There are no stubs: calling one, or reading
any property of `fused.fileIndex`, throws `<name> is not supported on Render
App` and logs it to the console. An app that needs those belongs in full
fused-render.

## Background daemons (`fused.daemon`)

Same contract as fused-render. An app opts in with a table in its own
`pyproject.toml`, declaring exactly one of:

```toml
[tool.fused-render.app]
main = "compute.py"      # the shipped worker calls main(**params); fused.daemon.run(params)
                         # warm process, re-imported on edit, reaped after 15 min idle
# or
daemon = "daemon.py"     # your own HTTP server; fused.daemon.call(path, body)
                         # resident until stop(); must answer GET /ping?t=<token> with {"ok": true, "version": <--version>}
```

The daemon runs on the app's own venv (the one `/api/open` builds), one
instance per app, killed when Render App quits. Optional keys:
`idle_timeout_s` (0 = resident), `retry_post = true` (POSTs are idempotent,
may be retried after a heal-restart). `setAutostart(true)` brings it back at
every launch; `start()` alone never does. State lives under
`~/.fused-render-app/engines/<engine_id>/` (`daemon.log`) and
`~/.fused-render-app/background_apps.json` (autostart list).

## Capture (`fused.capture`)

Same contract as fused-render, served natively: ScreenCaptureKit records the
screen, AVFoundation the microphone (`capture/`, routes in
`routes/capture.py`). Six verbs:

```js
const rec = await fused.capture.screen({ audio: "mic", maxSeconds: 600 });
//  -> {id, jobId, path, url, state, stop(), cancel()}, resolved once recording
await rec.stop();                      // {path, url, mime, seconds, bytes}; keeps the file
await fused.capture.audio({ path: "notes.m4a" });   // mic only, same handle
await fused.capture.screenshot({ path: "shot.png" }); // {path, url, width, height, bytes, mime}
await fused.capture.sources();         // {video, audio, systemAudio, screenshot, displays, microphones}; never prompts
await fused.capture.list();            // live recordings on this machine
await fused.capture.attach(id);        // handle for one of them (a reload finds its recording here)
```

A recording is a job row (`sys:capture:<id>`, origin Capture, visible to
`fused.watchJob(rec.jobId)`): ✕ on the row = `cancel()` = stop and delete;
the `maxSeconds` cap (default 30 min) = `stop()` = keep. Files land in
`~/.fused-render-app/recordings/` as `.mov` / `.m4a` / `.png|.jpg` unless the
page names a `path`; a relative `path` resolves beside the page, like
`readFile`. The file's extension picks png vs jpeg. A recording survives the
page that started it. Rejections carry `.type`: `unavailable` (this machine
cannot), `bad_request` (the arguments, or a preview trying to record),
`capture_error` (the file failed to write on stop).

Render App-specific: macOS only, 13+ (13–14 write the movie through an
`AVAssetWriter` mux, 15+ through `SCRecordingOutput`); any other platform
gets `unavailable`. fused-render's browser fallback (MediaRecorder streamed
over a WebSocket) is not ported. A preview (`_preview=1`) refuses
`screen` / `audio` / `screenshot` with `bad_request`; `sources` / `list` /
`attach` still work there, so draw the record button off `sources()` and
start a capture only from a click. Permissions: the Screen Recording grant is
TCC, prompted on the first real capture and managed in System Settings (no
plist key or entitlement); the microphone uses the app's existing
`NSMicrophoneUsageDescription` + `audio-input` entitlement. None of this goes
through the web view's `getUserMedia`.

## AI

`fused.ai.*` is fused-render's AI subsystem, copied in. Two tiers:

- **Claude** (`haiku`/`sonnet`/`opus`/`fable`, the default): runs `claude -p`
  from Claude Code, so the machine needs the `claude` CLI installed and logged
  in. One warm process, reset between calls.
- **Local** (a Hugging Face repo id or `.gguf`, or `provider: "local"`): text,
  image, video, transcribe, embed. Each backend is a runner folder under
  `fused_render_app/ai/runners/` with its own `pyproject.toml`; the first call
  builds its venv with `uv sync`, downloads the model into the Hugging Face
  cache and spawns a worker process the server talks HTTP to. Nothing ML ships
  in the DMG. Pages get `model_loading` + a `jobId` to `watchJob` while that
  happens, then retry — fused-render's contract, unchanged.

The Apple-Intelligence tier needs a Swift helper the Render App build does not
compile; it answers `unavailable`. Streaming is NDJSON over chunked HTTP.

## Showcase apps

The placeholder page (`/`) lists the showcase apps shipped inside the package,
`fused_render_app/showcase/*.fused`, as cards; clicking one opens it through
the ordinary `/open?_file=` path. They ride along in the wheel and the DMG with
no build step. Two ship today: **Note taker** (HTML only, `readFile` /
`writeFile`) and **Pipeline Intelligence** (`runPython` + its own
`pyproject.toml`).

To add one: drop the `.fused` into that folder, give it a `title` and
`description` in `showcase/showcase.json`, and make sure it carries a
`pyproject.toml` (even with `dependencies = []`) and a `preview.png` —
`tests/test_showcase.py` checks both, and that it calls nothing Render App rejects.

Any app's `preview.png` (a member of the `.fused`, or one written into the
app's extract dir) also shows in the menu-bar dock: hovering the app's tile
opens the name bubble with the picture above the name
(`GET /api/dock/preview`; 8 MB cap, PNG only).

## Python environments

No packages are bundled. The DMG ships one CPython 3.12 (py2app's real
interpreter at `Contents/MacOS/python`, whole stdlib, self-locating through a
`Contents/lib` symlink — packaged exactly as fused-render's FusedRender.app).
Every environment is built on it. Each `.fused` app carries its own
`pyproject.toml`; on open, `uv sync --python <that interpreter>` builds a venv
for it under `~/.fused-render-app/venvs/` and `runPython` runs inside it. An app without a `pyproject.toml` runs in one
shared "legacy" venv holding fused-render's implicit set (numpy, pandas,
requests, httpx, pillow, openpyxl, python-pptx, msgpack, fpdf2, drain3;
not pyarrow, duckdb, botocore or google-auth), also built
on first use, so older `.fused` exports keep working.

`uv` ships inside the app (`Contents/Resources/bin/uv`, copied from the build
host exactly as fused-render does). Running from source, it is looked for at
`FUSED_RENDER_APP_UV`, beside the interpreter, in `~/.fused-render-app/bin/`
and on `PATH` (a uv older than 0.8 is skipped), and failing those is downloaded
once (pinned version, sha256-verified). The Apple-Intelligence helper
(`fused-apple-ai`) is compiled and bundled when the build host has the macOS 26
SDK; below that the apple tier reports itself unavailable.

Version, DMG/app size and the full supported/unsupported API table live in
[STATUS.md](STATUS.md).

## Run from source

```
pip install -e ".[dev]"
fused-render-app ~/Downloads/app.fused
pytest
```

Or `scripts/dev.sh`: bootstraps a Python 3.12 `.venv` with `[dev,app]`, runs the
server with auto-reload on `.py` edits, on a per-branch port and state dir so it
never collides with the installed app (see `.claude/skills/setting-up-dev-env`).

## Install (Homebrew)

```
brew install --cask fusedio/tap/render-app
```

Installs `RenderApp.app` (macOS 12+), signed + notarized. `brew update &&
brew upgrade --cask render-app` upgrades.

The app also updates itself: it checks a signed manifest on the CDN every
five minutes and, when a newer version is out, the launcher page (Home) shows
a banner with an **Update** button — download, verify, swap the bundle in
place, then **Restart Render App**. Only the launcher shows it; an open
`.fused` app's window is never interrupted. Nothing runs `brew`.
`FUSED_RENDER_APP_NO_AUTO_UPDATE=1` disables the background check;
`FUSED_RENDER_APP_UPDATE_DEV_MANAGER=1` lets a source run show the banner
(check-only, no bundle to swap).

## Build the macOS app

```
pip install ".[app]"            # rumps + pyobjc, for the menu-bar shell
bash scripts/build_dmg.sh       # dist/RenderApp-<version>.dmg
```

The DMG is ad-hoc signed by default (runs on the building machine; other
Macs need right-click → Open). `FUSED_RENDER_SIGN=1` or a
`FUSED_RENDER_CODESIGN_IDENTITY` switches to Developer ID signing with the
hardened runtime, and `FUSED_RENDER_NOTARY_PROFILE` (a `notarytool`
keychain profile) additionally notarizes and staples.

### Release pipeline (GitHub Actions)

Pushing a `v*` tag runs `.github/workflows/release.yml`, fusedio/fused-render's
macOS release job step for step:
`prepare-release` creates the GitHub Release, then on `macos-26` an ephemeral
keychain gets the Developer ID cert and an App Store Connect API key,
`build_dmg.sh` builds + signs + notarizes + staples, the ticket is verified, the
DMG is uploaded to the `fused-render` S3 bucket under `render-app-dmgs/` (served
by the same CloudFront distribution as fused-render, at
`https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/RenderApp-X.Y.Z.dmg`; the
CI assumes `github_render_app_role` via OIDC, which can only write that prefix),
the signed update manifest `render-app-dmgs/latest.json` is published next to
it (`scripts/generate_update_manifest.py`, key in the
`FUSED_RENDER_UPDATE_SIGNING_KEY` secret — skipped with a warning when unset),
the DMG + wheel land on the Release, and the Release notes get the CDN
download link as their first line (assets can't redirect, so the fast
CloudFront copy is linked from the notes; the attached DMG is the fallback).
`bump-homebrew` then rewrites
`Casks/render-app.rb` of [fusedio/homebrew-tap](https://github.com/fusedio/homebrew-tap)
to point at the CDN copy and pushes, so `brew upgrade --cask render-app` picks
the release up. To rebuild an existing tag:
`gh workflow run release --ref v0.6.0 -f tag=v0.6.0` (the run must build the
tag's own commit). `test.yml` runs the same ad-hoc DMG smoke build whenever
packaging files change. Signing needs these repository secrets (values are
write-only on GitHub; re-enter them from the originals):

| secret | what |
| --- | --- |
| `CODESIGN_CERT_P12` | base64 of the Developer ID Application `.p12` |
| `CODESIGN_CERT_PASSWORD` | its password |
| `CODESIGN_IDENTITY` | the cert's SHA-1 (`security find-identity -v -p codesigning`) |
| `KEYCHAIN_PASSWORD` | any string; unlocks the ephemeral keychain |
| `NOTARY_API_KEY_P8` | App Store Connect API key (`.p8` contents) |
| `NOTARY_API_KEY_ID` / `NOTARY_API_ISSUER_ID` | its key id and issuer id |
| `TAP_PUSH_TOKEN` | PAT with push to fusedio/homebrew-tap (same one fused-render uses); only the `bump-homebrew` job fails without it |

Without them the workflow still runs and publishes an ad-hoc-signed DMG (Render App-only fallback).

## Layout

```
fused_render_app/
  appfile.py      open a .fused (v2 container or legacy v1 zip) into ~/.fused-render-app/apps
  container.py    the FUSEDAPP v2 format (stdlib)
  env.py          uv lookup/download, per-app `uv sync`, running a .py in its venv
  server.py       the HTTP surface (http.server; binds 127.0.0.1)
  cli.py          `fused-render-app [file] [--port] [--no-browser]`
  macapp.py       macOS shell: server thread, menu-bar item, Finder open events -> windows
  mainwindow.py   the windows: NSWindow + WKWebView, delegates (popups, downloads, dialogs), main menu
  window_policy.py  pure-Python navigation/download decisions mainwindow.py enacts (tested)
  menubar_dock.py the menu-bar Dock tray (static/dock.html in a floating panel); dock_store.py its list
  launcher_panel.py  the ⌥Space launcher (static/launcher.html in a floating panel)
  launcher.py     launcher search over the Dock's apps + showcase; launcher.json (shortcut)
  hotkey.py       the global shortcut: Carbon RegisterEventHotKey via ctypes; spec parsing (tested)
  _child.py       worker: import the .py, call main(**params), print JSON
  capture/        fused.capture: ScreenCaptureKit / AVFoundation recorder (_darwin, _darwin_mux, _mixdown)
  routes/         Handler route groups: ai_routes, ai_relay, ai_metrics, capture
  static/         runtime.js, placeholder (index.html), open page (open.html), dock.html, launcher.html, settings.html
  showcase.py     lists showcase/*.fused for the placeholder; serves their preview.png
  showcase/       showcase .fused apps + showcase.json (title, description)
```

State lives in `~/.fused-render-app/` (override with `FUSED_RENDER_APP_HOME`).
