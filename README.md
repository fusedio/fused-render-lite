# fused-render-app

Ships as **FusedBot** (`FusedBot.app`, `FusedBot-<version>.dmg`; the package,
bundle id `io.fused.render.app`, `render-app://` scheme and `FUSED_RENDER_*`
settings keep their Render App names). Since 0.11.0 the app is autonomous
browsing bots that run on your own Mac. Each bot gets its own headless Chrome (private profile, cookies and
history), a chat thread where you give it tasks, and an agent loop that reads
the page, clicks, types and reports back. You can pause, resume or stop a bot,
take over its browser by hand, give it routines on a schedule, and keep notes
and reusable skills between tasks.

It is the OpenBot fused-render app (`~/Fused/sandbox/Showcase Drafts/OpenBot`)
rebuilt behaviour for behaviour: the UI in React + shadcn
(`frontend/src/apps/bots/`), the backend in the Render App server process
(`fused_render_app/bots/`). `docs/BOT-APP.md` is the architecture and wire
contract. Everything runs locally; the server binds 127.0.0.1 only.

## The page

`/` serves the FusedBot page in a native window. Three columns:

- **Bots** (left): every bot, pinned first, then the ones waiting on you with
  unread messages, then by your last message. "New bot" opens the bot dialog
  (name, avatar, model, effort, standing instructions, approvals, browser
  profile, encryption).
- **Thread** (middle): the chat with the selected bot. Its thoughts, actions
  (with a step thumbnail), questions, approval cards, offers and final answers
  arrive as messages. Reply to a message, react to it, attach files (paste or
  drop), dictate, search the thread (⌘F).
- **Preview** (right): the bot's latest screenshot, its Inbox, routines, a
  usage strip, and a side app when the bot shows one. Click the screenshot for
  the full-screen **live view**: a CDP screencast of the bot's browser with a
  tab strip. **Take over** forwards your mouse and keyboard to the page; hand
  back and the bot carries on.

Column widths and collapse state are remembered per browser.

## Bots

- **Tasks.** Whatever you send becomes the bot's task. Mid-task messages reach
  it as instructions that override the task. Stop ends the task at once.
- **Approvals.** With "Ask before irreversible actions" (the default) the bot
  pauses on actions you cannot take back (buying, paying, sending, posting,
  deleting, booking, uploading) and shows an approval card. "Never ask" turns the gate off.
- **Questions.** A bot can ask you something (optionally with choices) and
  waits; "log in" questions pop its browser window so you can sign in.
- **Routines.** Recurring tasks: every N minutes, daily at a time, on given
  weekdays, or once at a time. Three failures in a row disable a routine.
- **Skills.** Reusable playbooks (name, trigger, text) the bot loads when a
  task matches; it can learn one from a finished task.
- **Memory.** Notes the bot keeps between tasks (capped at 200), readable and
  editable in the bot's settings.
- **Browser profile.** Import one of your own Chrome profiles (logins,
  cookies, extensions) into a bot's browser, and optionally encrypt the
  profile at rest (AES-256 while Chrome is closed, key in the macOS Keychain).
- **Presets.** A new bot can start from a site preset (LinkedIn, YouTube, X,
  Reddit, Gmail, GitHub, Google Docs, Apple Notes and more): the site's brand
  mark as its avatar, read-only standing instructions, and four to six
  playbooks copied into its own Skills. Presets ship in
  `fused_render_app/bots/presets/<key>/`; add a folder to add one.

## Inbox, Apps, Builds

**Inbox.** What a bot produces for you lives in `~/Fused/bots/<bot name>/`,
one subfolder per task: files it saves, downloads that arrived during the
task, and a `README.md` with the task and its final answer. The preview
column lists the newest items with a download link each. Deleting a bot
leaves its Inbox alone.

**Apps.** The Apps panel lists every fused app under `~/Fused/app`
(`FUSED_RENDER_DIR` overrides `~/Fused`). Thumbnails and the viewer frame each
app through `/embed`. "Upload app" (or a drop onto the panel) unpacks a
`.fused` export (v1 zip or v2 container) or a zipped app folder into a new
folder there; nothing is overwritten.

**Starter apps.** Some apps ship with the package, ready to install rather
than build: Google Docs Tabs, Google Sheets Tabs and Apple Notes, each
exposing its actions as tools. They live in `fused_render_app/bots/starters/`;
installing copies one into `~/Fused/app/<key>` and never overwrites an
existing folder. A bot made from the matching preset installs its own. The
starter's `*_status` tool tells whether it still needs setup, and Update
replaces its files with a newer shipped version while keeping its `.fused/`.

**Builds.** A bot builds or updates an app with its `build` action: a Claude
Code task (the Tasks engine below) that writes the app under `~/Fused/app`.
The Builds panel shows those tasks. A bot also **offers** apps on its own: one
that already fits the task ("Use it" / "Not now"), or a new one when a task
looks like something you will repeat ("Build it"). One offer per task, none on
routines, and "Not now" keeps an app out of offers for a week.

**App tools and skills.** Any fused app on this Mac that exposes MCP tools
(an `mcp.toml`) is available to every bot through its `tool` action; reading
tools run at once, tools that change something wait for approval. A `.py`
beside an app's page is callable through the `py` action when the app's
`SKILL.md` documents it; the call goes through the same `POST /api/run` the
app's own page uses.

**iMessage.** Give a bot a phone number or Apple ID (bot settings › Advanced ›
iMessage) and texts from that sender become its tasks; answers, questions and
errors are texted back. The bridge reads `~/Library/Messages/chat.db` and
sends through Messages.app, so Messages must be signed in and Render App needs
Full Disk Access. A bot can also text the contacts you list for it (its
`text` action, approval-gated) and read their replies (`texts`).

**botsend.** Other local scripts drop a task into a bot's inbox folder:

```
python -m fused_render_app.bots.botsend <bot name or id> "<task text>"
python -m fused_render_app.bots.botsend --list
```

The scheduler picks it up within about 20 s and runs it as if typed in the
chat.

## Engines

- **Claude Code** (`haiku`, `sonnet`, `opus`, `fable`): one `claude -p`
  process per task, the bot's actions served to it as MCP tools by
  `bots/botmcp.py`. Needs the `claude` CLI installed and logged in.
- **Local** (`local-4b`, `local-9b`: Gemma 4B and 12B, MLX 4-bit): the
  OpenBot JSON-action loop (`bots/steps_engine.py`) over `fused.ai`'s local
  tier. Also used for any model when no `claude` CLI is found.

## State

Bot state lives under `~/.fused-render-app/bots/` (override the root with
`FUSED_RENDER_APP_HOME`):

```
bots/data/<id>/      bot.json, events.jsonl, memory, skills, Chrome profile, downloads, files, inbox/ (botsend)
bots/cache/<id>/     latest screenshot, step thumbnails; deletable any time
bots/usage.jsonl     one line per model call (the usage dialog)
bots/builds.json     the Builds panel's list
```

The Inbox (`~/Fused/bots/`) and the apps (`~/Fused/app/`) stay in the Fused
workspace where you can see them.

## Windows

The macOS app (`macapp.py` + `mainwindow.py`) is a regular app: Dock icon,
menu-bar item, a main menu, native windows (`NSWindow` + `WKWebView`) all on
the one local server.

| in a window | what happens |
| --- | --- |
| New Window (⌘N), Dock click | a new Browser Bots window (a Dock click focuses the front one if any) |
| Home (title-bar house, ⌘⇧H) | this window goes to the bots page (`/`); nothing happens if it is already there |
| `target=_blank`, `window.open`, ⌘-click / middle-click on an app link | a new window (`window.open` returns a live handle) |
| a link to another site | the default browser |
| `<a download>`, `Content-Disposition: attachment`, a type WebKit can't show | saved to `~/Downloads` (Finder-style `name 2` on collision) |
| `alert` / `confirm` / `prompt`, `<input type=file>` | native panels |
| `getUserMedia`, `navigator.geolocation`, `Notification` from the app's own page | granted; the system prompts still apply |
| ⌘C/⌘V/⌘X/⌘Z/⌘A, ⌘W, ⌘R, ⌘[ ⌘], ⌘P, ⌘M | the Edit / File / View / Window menus |

The title bar ends in Open in Browser (View → Open in Browser ⌘⇧L) and
Home (View → Home ⌘⇧H), which takes that window back to the bots page.
Window → Tasks (⌘⇧T) opens the Tasks page. Closing the last window does
not quit. A click on the menu-bar item drops a Dock-like glass tray of
tiles: Home, pinned bots and pinned apps, then up to three recently used bots
and three recently changed apps. A bot tile selects that bot in a FusedBot
window; an app tile opens the app in its own window. Right-click a tile to
keep it in or remove it from the tray, show an app in Finder, or open either
in the browser; drag the separator to resize the tiles. Right-click the
menu-bar item for "Open FusedBot", "Tasks…", "Open in Browser", "Open App
Logs" and "Quit FusedBot". Bots can also be pinned from the sidebar, apps
from the app viewer's ⋯ menu ("Pin to menu bar").
`FUSED_RENDER_APP_NO_BROWSER=1` suppresses the startup window.

The app posts macOS notifications for background work (model downloads,
environment installs, AI jobs): one banner per job, replaced in place by the
outcome (`jobnotify.py`, `notify_policy.py`).

Opening a `.fused` from Finder or a `render-app://` link is no longer a
feature. The document type and URL scheme stay registered for now, and such
an open shows the Browser Bots window.

## Claude tasks

Window → Tasks (⌘⇧T) opens fused-render's Tasks page, the same React page:
`frontend/` is the slice of fused-render's frontend it imports (copied
verbatim), built into `static/shell-dist/` with Render App's entries
(`lite.html` for `/tasks` and `/chat`, `bots.html` for `/`). A task is one
`claude` session run by fused-render's chat engine, copied into the package
(`fused_render_app/templates/claude/`). Bot builds are such tasks. State lives
under `~/.fused-render-app/claude-sessions/`, apart from fused-render's own.
Re-sync from a fused-render checkout with
`scripts/sync_claude_tasks.py <path> --runtime --frontend --skills`.

Every session Render App spawns is handed fused-render's skills: the packaged
copy under `fused_render_app/skills/` is assembled into
`~/.fused-render-app/skill-plugin/` at startup and passed as
`claude --plugin-dir` (`skill_plugin.py`). For the user's own `claude`, the
published `fusedio/fused-render` plugin is installed or refreshed in their
Claude config (`user_plugin.py`); `"fused-render@fused-render": false` under
`enabledPlugins` in Claude's `settings.json` turns that off.

## App runtime

Apps the bots build or show run through `/render` and `/embed`, with
`runtime.js` injected. The page runtime exposes these `fused.*` members:

| API | Notes |
| --- | --- |
| `fused.runPython(py, params, opts?)` | runs `main(**params)` from the app's own venv, 600 s cap |
| `fused.params.get/getAll/set/onChange` | URL-backed state, same semantics as fused-render |
| `fused.readFile` / `stat` / `writeFile` / `rawUrl` / `uploadFile` / `mkdir` | files, as in fused-render |
| `fused.ai.text / image / video / transcribe / embed`, `fused.ai.models.*`, `fused.ai.cancel` | fused-render's AI subsystem (see AI below) |
| `fused.trackJob(spec)` / `fused.watchJob(id)` | in-process job rows; survive a reload, cancellable |
| `fused.tasks.*` | Claude tasks (fused-render D890) |
| `fused.daemon.*` | the app's own long-running daemon (`[tool.fused-render.app]` in its `pyproject.toml`, fused-render's contract) |
| `fused.capture.*` | native macOS screen / microphone / still capture (ScreenCaptureKit + AVFoundation, macOS 13+) |
| `fused.autoReload(false)` | accepted, no-op; `autoReload(true)` throws |

`fused.fileIndex` and `fused.snapshot` are **not supported**: calling one
throws `<name> is not supported on Render App`.

## AI

`fused.ai.*` is fused-render's AI subsystem, copied in. Two tiers:

- **Claude** (`haiku`/`sonnet`/`opus`/`fable`, the default): runs `claude -p`
  from Claude Code. One warm process, reset between calls.
- **Local** (a Hugging Face repo id or `.gguf`, or `provider: "local"`): each
  backend is a runner folder under `fused_render_app/ai/runners/` with its own
  `pyproject.toml`; the first call builds its venv with `uv sync`, downloads
  the model into the Hugging Face cache and spawns a worker process. Nothing
  ML ships in the DMG.

The Apple-Intelligence tier needs a Swift helper compiled only on a macOS 26
SDK build host; elsewhere it answers `unavailable`.

## Python environments

No packages are bundled. The DMG ships one CPython 3.12 (py2app's real
interpreter at `Contents/MacOS/python`, whole stdlib, packaged exactly as
fused-render's FusedRender.app). Every environment is built on it: an app with
a `pyproject.toml` gets its own venv under `~/.fused-render-app/venvs/`
(`uv sync`); an app without one runs in a shared "legacy" venv holding
fused-render's implicit set. `uv` ships inside the app
(`Contents/Resources/bin/uv`); from source it is looked for at
`FUSED_RENDER_APP_UV`, beside the interpreter, in `~/.fused-render-app/bin/`
and on `PATH`, and failing those is downloaded once (pinned, sha256-verified).

Version, DMG size and per-version notes live in [STATUS.md](STATUS.md).

## Run from source

```
scripts/dev.sh
```

`dev.sh` bootstraps a Python 3.12 `.venv` with `[dev,app]`, builds the React
pages when `static/shell-dist/` is missing, and runs the server with auto-reload on `.py` edits, on a
per-branch port and state dir so it never collides with the installed app
(see `.claude/skills/setting-up-dev-env`). The frontend needs
[bun](https://bun.sh): `scripts/build_shell.sh` rebuilds it,
`cd frontend && bun run watch` rebuilds on edit. Tests:

```
.venv/bin/python -m pytest -q
cd frontend && bun run build && bun test
```

## Install (Homebrew)

```
brew install --cask fusedio/tap/render-app
```

Installs `RenderApp.app` (macOS 12+), signed and notarized.
`brew upgrade --cask render-app` upgrades. The app also checks a signed update
manifest on the CDN every five minutes and can download, verify and swap its
own bundle (`update/mac.py`, `GET/POST /api/update*`). The update banner lived
on the old home page; no page in the Browser Bots app shows it yet.
`FUSED_RENDER_APP_NO_AUTO_UPDATE=1` disables the background check.

## Build the macOS app

```
pip install ".[app]"            # rumps + pyobjc, for the menu-bar shell
bash scripts/build_dmg.sh       # dist/RenderApp-<version>.dmg
```

The DMG is ad-hoc signed by default (runs on the building machine; other Macs
need right-click → Open). `FUSED_RENDER_SIGN=1` or a
`FUSED_RENDER_CODESIGN_IDENTITY` switches to Developer ID signing with the
hardened runtime, and `FUSED_RENDER_NOTARY_PROFILE` (a `notarytool` keychain
profile) additionally notarizes and staples. The build needs bun for the
React pages.

### Release pipeline (GitHub Actions)

Pushing a `v*` tag runs `.github/workflows/release.yml`: `prepare-release`
creates the GitHub Release, then on `macos-26` an ephemeral keychain gets the
Developer ID cert and an App Store Connect API key, `build_dmg.sh` builds,
signs, notarizes and staples, and the DMG is uploaded to the `fused-render` S3
bucket under `render-app-dmgs/` (served at
`https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/RenderApp-X.Y.Z.dmg`;
CI assumes `github_render_app_role` via OIDC). The signed update manifest
`render-app-dmgs/latest.json` is published next to it
(`scripts/generate_update_manifest.py`, key in the
`FUSED_RENDER_UPDATE_SIGNING_KEY` secret, skipped with a warning when unset).
The DMG and wheel land on the Release, whose notes lead with the CDN link.
`bump-homebrew` then rewrites `Casks/render-app.rb` in
[fusedio/homebrew-tap](https://github.com/fusedio/homebrew-tap). To rebuild an
existing tag: `gh workflow run release --ref v0.6.0 -f tag=v0.6.0`. `test.yml`
runs an ad-hoc DMG smoke build whenever packaging files change. Signing needs
these repository secrets:

| secret | what |
| --- | --- |
| `CODESIGN_CERT_P12` | base64 of the Developer ID Application `.p12` |
| `CODESIGN_CERT_PASSWORD` | its password |
| `CODESIGN_IDENTITY` | the cert's SHA-1 (`security find-identity -v -p codesigning`) |
| `KEYCHAIN_PASSWORD` | any string; unlocks the ephemeral keychain |
| `NOTARY_API_KEY_P8` | App Store Connect API key (`.p8` contents) |
| `NOTARY_API_KEY_ID` / `NOTARY_API_ISSUER_ID` | its key id and issuer id |
| `TAP_PUSH_TOKEN` | PAT with push to fusedio/homebrew-tap; only the `bump-homebrew` job fails without it |

Without them the workflow still publishes an ad-hoc-signed DMG.

## Layout

```
fused_render_app/
  bots/           the Browser Bots backend (docs/BOT-APP.md §1)
    bot.py          one bot: lifecycle, memory, skills, Inbox, routines, offers, builds, take over
    agent_engine.py the Claude Code engine: one `claude -p` per task, tools over MCP
    steps_engine.py the OpenBot JSON-action loop (local models, no-CLI fallback)
    tools.py        the tool table both engines share; the approval risk rule
    botmcp.py       stdio MCP server `claude` spawns; forwards to POST /api/bots/<id>/tool
    browser.py      per-bot Chrome over CDP, accessibility-tree snapshot, screenshots
    apptools.py     APPS / APP TOOLS / app SKILL.md for the prompt
    imessage.py     the iMessage bridge
    apps.py         list / import / mkdir / reveal under ~/Fused/app
    presets.py      site presets (presets/<key>/: preset.json + playbooks) applied at create
    starters.py     starter apps (starters/<key>/) installed into ~/Fused/app
    registry.py     the bot registry, scheduler (routines, file inbox), iMessage thread
    routes.py       /api/bots/*, /api/apps/*
    store.py, paths.py  bot.json, events.jsonl, the usage ledger; state roots
    botsend.py      CLI: drop a task into a bot's inbox
  server.py       the HTTP surface (http.server; binds 127.0.0.1)
  cli.py          `fused-render-app [--port] [--no-browser]` (dev server)
  macapp.py       macOS shell: server thread, menu-bar item, windows
  mainwindow.py   the windows: NSWindow + WKWebView, delegates (popups, downloads, dialogs), main menu
  menubar_dock.py the menu-bar tray: glass NSPanel + WKWebView on /dock, tile and utility menus
  window_policy.py  pure-Python navigation/download decisions mainwindow.py enacts
  appfile.py, container.py, localapps.py  .fused (v2 container, v1 zip) and folder apps, for POST /api/open
  env.py          uv lookup/download, per-app `uv sync`, running a .py in its venv
  ai/, routes/    the AI subsystem and the routers copied from fused-render
  capture/        fused.capture (ScreenCaptureKit / AVFoundation)
  templates/claude/  fused-render's Claude chat engine (Tasks, bot builds)
  skills/         fused-render's skills, synced verbatim
  update/         the in-app updater
  static/         runtime.js, the FusedBot icon (fusedbot-icon.svg → fusedbot-icon-1024.png / -64.png,
                  menubar.svg → menubar.png / menubar@2x.png; scripts/render_icons.py), shell-dist/ (built: bots.html, lite.html)
frontend/
  bots.html, lite.html   the two Vite entries
  src/apps/bots/  the FusedBot React app: components/, dialogs/, apps/, builds/, state/, lib/, styles/
  src/            fused-render's frontend slice (Tasks page, Claude chat), copied verbatim
docs/BOT-APP.md   architecture and wire contract
```

State lives in `~/.fused-render-app/` (override with `FUSED_RENDER_APP_HOME`).
