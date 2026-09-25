# fused-render-app status

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
| 0.10.0 | TBD (release build) | — | — | `fused.capture` native macOS capture (ScreenCaptureKit + AVFoundation) + pyobjc ScreenCaptureKit/AVFoundation frameworks in the `[app]` extra and py2app packages — first packaging change since 0.6.0; title-bar Edit button (editlink.py) |
| 0.9.5 | 43.43 MB (43,434,486 B) | −0.00 MB | 99 MB | legacy env drops pyarrow/duckdb; DoodleShooter + OpenRelax regain pyproject.toml (+361 B); no packaging change |
| 0.9.4 | 43.44 MB (43,438,706 B) | +0.41 MB | 99 MB | legacy env deps (pyarrow/duckdb/httpx); refreshed DoodleShooter + OpenRelax showcase files (OpenRelax ~130 KB → ~432 KB); no packaging change |
| 0.9.3 | 43.03 MB (43,029,693 B) | −0.00 MB | 99 MB | launcher polish (Render App row / ⌥0, frostier glass); no packaging change |
| 0.9.2 | 43.03 MB (43,032,592 B) | +0.01 MB | 99 MB | Spotlight-like app launcher (hotkey, launcher, launcher_panel, launcher.html, settings.html); no packaging change |
| 0.9.1 | 43.02 MB (43,021,381 B) | +0.29 MB | 99 MB | per-app window frame memory (window_policy + mainwindow); no packaging change |
| 0.9.0 | 42.74 MB (42,735,197 B) | +0.18 MB | 98 MB | OpenMail showcase demo (.fused ~166 KB) bundled in the wheel; no packaging change |
| 0.8.16 | 42.55 MB (42,553,189 B) | +0.00 MB | 98 MB | .fused state shared across every extract of one app via symlink to ~/.fused-render-app/fused_data/<app_id> (PR #32); no packaging change |
| 0.8.15 | 42.55 MB (42,553,006 B) | −0.00 MB | 98 MB | open .fused from URL without confirm, CDN link first in release notes, non-macOS code paths dropped (PRs #28, #29, #30); no packaging change |
| 0.8.14 | 42.56 MB (42,555,876 B) | +0.01 MB | 98 MB | in-app update banner, open .fused from URL, home page ordering (PRs #25, #26, #27); no packaging change |
| 0.8.13 | 42.54 MB (42,543,618 B) | +0.03 MB | 98 MB | menu-bar dock: own slide driver, fixed web view canvas, preview.png in hover bubble (PRs #22, #23); no packaging change |
| 0.8.12 | 42.52 MB (42,517,525 B) | −0.02 MB | 98 MB | native macOS notifications for model downloads, env installs and AI jobs (PR #21); no packaging change |
| 0.8.11 | 42.54 MB (42,538,988 B) | −0.01 MB | 98 MB | browser parity in windows: pointer lock, geolocation, notifications, popups (PR #20); no packaging change |
| 0.8.10 | 42.55 MB (42,550,462 B) | −0.00 MB | 98 MB | icon.png dock fallback; CDN upload landed (PR #18) then reverted (PR #19) pending AWS OIDC role fix, bump-homebrew disabled; no packaging change |
| 0.8.9 | 42.55 MB (42,551,093 B) | +0.00 MB | 98 MB | dock icons follow system theme, dock appear/dismiss animation, dev iframe releases link removed; no packaging change |
| 0.8.8 | 42.55 MB (42,547,067 B) | +0.05 MB | 98 MB | renamed to Render App: `RenderApp.app`, `RenderApp-<version>.dmg`, package `fused_render_app`, cask `render-app`; no packaging change |
| 0.8.7 | 42.50 MB (42,496,603 B) | −0.00 MB | 98 MB | dock tray resize via separator drag (tilesize persisted), web view destroyed on window close; no packaging change |
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
| 0.6.1 | 40.85 MB (40,848,435 B) | −0.07 MB | 95 MB | rename to Render App; no packaging change |
| 0.6.0 | 40.92 MB (40,915,928 B) | +16.52 MB | 95 MB | build/CI parity with fused-render: uv bundled in the app (`Resources/bin/uv`, ~+15 MB), apple tier helper compiled + bundled (macos-26 runner), Mach-O/minos probes, prepare-release job, DMG smoke build in test.yml |
| 0.5.8 | 24.39 MB (24,393,863 B) | −3.30 MB | 60 MB | size: drop Tcl/Tk, ncurses and CPython's `_test*` fixtures that the whole-stdlib copy dragged in (Render App-only trim on top of fused-render's list) |
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
| 0.1.0 | 17.72 MB (17,723,156 B) | — | 25 MB | first Render App build |

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

## 0.10.0

Minor: `fused.capture` supported — fused-render's native macOS capture
copied in (`capture/__init__.py`, `_darwin.py`, `_darwin_mux.py`,
`_mixdown.py`, runtime.js block), packaging change: the `[app]` extra gains
`pyobjc-framework-ScreenCaptureKit` + `pyobjc-framework-AVFoundation` (Quartz,
CoreMedia, CoreAudio as transitives) and the py2app packages list grows to
match. Verified end to end: screen `.mov`, mic `.m4a`, screenshot `.png`.

Render App-specific: macOS only — any other platform answers `unavailable`
(409); the browser MediaRecorder / WebSocket streaming transport fused-render
carries for Windows and Linux is dropped, so `sources()` never reports a
`client` recorder. Routes live on the stdlib `Handler` (`routes/capture.py`).
A preview (`_preview=1`) refuses `screen` / `audio` / `screenshot` with
`bad_request`; `sources` / `list` / `attach` are allowed there. A recording
is a job row `sys:capture:<id>` (origin Capture, cancellable): ✕ = stop +
delete, the `maxSeconds` cap (default 30 min) = stop + keep. Files land in
`~/.fused-render-app/recordings/` unless the page names a `path` (relative
resolves beside the page). Permissions: Screen Recording is a TCC grant in
System Settings (no plist key or entitlement); the mic reuses the existing
`NSMicrophoneUsageDescription` + `audio-input` entitlement.

| member | status | notes |
| --- | --- | --- |
| `fused.capture.screen(opts)` | ✅ new | `{display, rect, audio: false\|"mic"\|"system"\|"both", device, cursor, path, maxSeconds, title}` → handle `{id, jobId, path, url, state, stop(), cancel()}`; `.mov` |
| `fused.capture.audio(opts)` | ✅ new | `{source, path, maxSeconds, title}` → same handle; `.m4a`; a `device` is refused |
| `fused.capture.screenshot(opts)` | ✅ new | `{display, rect, cursor, path}` → `{path, url, width, height, bytes, mime}`; extension picks png / jpeg; no job row |
| `fused.capture.sources()` | ✅ new | `{video, audio, systemAudio, screenshot}` each `{available, granted, reason}` + `displays`, `microphones`; never prompts |
| `fused.capture.list()` | ✅ new | live recordings on this machine, any page's |
| `fused.capture.attach(id)` | ✅ new | handle for a live recording (reload finds its own) |

Server routes added: `GET /api/capture`, `POST /api/capture/start`,
`POST /api/capture/{id}/stop`, `POST /api/capture/{id}/cancel`,
`POST /api/capture/screenshot`.

Hardening after review:
- logout / shutdown end recordings via `applicationWillTerminate:` (rumps
  `before_quit`), with a bounded quit budget (`QUIT_STOP_BUDGET_S`, 20 s) so
  a stalled ScreenCaptureKit stop cannot beachball the menu bar.
- a refused Screen Recording grant is a 409, not a 500.
- a recording that dies mid-flight still answers the page's `stop()`.
- the 13–14 muxer (`_darwin_mux.py`) is never imported on 15+.
- a second `stop()` / `cancel()` during an in-flight stop waits for it
  instead of a 404.
- output paths refuse an existing file and a wrong container extension.
- mic access is requested before the first take (undetermined → prompt,
  denied → 409).
- the start-side TCC wait is bounded under the web view's 60 s fetch timeout.
- `sources()` degrades per part if one enumeration fails.
- a missing ScreenCaptureKit API on a future macOS is a 409.

Title-bar Edit button (`editlink.py`, `mainwindow.py`): a third button,
leftmost of Open in Browser and Home (View → Edit in fused-render, ⌘⇧E),
hands the window's `.fused` to fused-render as a
`fused-render://open?file=<path>` deep link (path percent-encoded once);
fused-render clones it into `~/Fused/local/<name>` (no-op when the copy
exists) and opens the copy. Disabled on Home; follows in-window navigation.
The scheme is probed with `NSWorkspace.URLForApplicationToOpenURL:`; when no
handler exists an alert offers **Download fused-render**
(`render.fused.io/latest.json` → `dmg_url`, 4 s timeout, download page as
fallback). Depends on the sibling fused-render PR accepting `file=`: the
installed FusedRender 0.5.86 lands the link on its clone page's
"unsupported fused-render link" error until that ships.

## 0.9.5

Patch: legacy env sheds pyarrow/duckdb; showcase containers regain their
`pyproject.toml`; no packaging change.

Showcase: `02_DoodleShooter.fused` and `03_OpenRelax.fused` were re-exported
from fused-render in 0.9.4 without a `pyproject.toml` (the exporter dropped
it; upstream follow-up). Since `env.ensure` runs on every open, both demos
pulled the full legacy venv on first click, and `test_showcase` had been red
on `main` since then (the release job does not gate on `test`). Re-added:
empty deps for DoodleShooter (pure three.js), `httpx` for OpenRelax
(`research.py`, `sounds.py` import it). All other members byte-identical.

Legacy env drops `pyarrow` and `duckdb`: 122 MB + 44 MB installed (58% of the
285 MB legacy venv; pyarrow alone is 3x pandas), both added in 0.9.4 only for
parity with fused-render's authoring skill. Removed from `LEGACY_DEPS`; a
`.fused` without a `pyproject.toml` that imports either now fails on open
(accepted). The generated legacy `pyproject.toml` header changes, so the legacy
venv rebuilds once on next open.

## 0.9.4

Patch: legacy env deps + refreshed showcase files, no packaging change.

Legacy env aligned with fused-render's authoring skill. `LEGACY_DEPS`
(`fused_render_app/env.py`) gains `pyarrow>=14`, `duckdb>=1.1` and `httpx`:
fused-render's skill promises them to apps without a `pyproject.toml` (they
ride along as core server deps there), so a `.fused` importing `httpx` worked
in fused-render and failed here. `botocore` / `google-auth` stay out; the skill
now says so. The generated legacy `pyproject.toml` header changes too, which
invalidates the existing legacy venv and triggers one rebuild on next open.

Showcase: `02_DoodleShooter.fused` and `03_OpenRelax.fused` updated (OpenRelax
grows ~130 KB → ~432 KB).

## 0.9.3

Patch: launcher polish, no packaging change.

The search always ends with a "Render App" row that opens the app's home
window; <modifier>+0 does the same, in the panel and globally (bound alongside
the pinned-app digits). macOS 26 backdrop: a HUD vibrancy material behind the
Liquid Glass plus a heavier, appearance-following tint, so text stays legible
over busy windows. Empty-state note sits above the list, not under the Render
App row.

## 0.9.2

Patch: Spotlight-like app launcher, no packaging change.

⌥Space (configurable) drops a non-activating search panel: empty, it lists the
apps pinned in the menu-bar Dock; typing searches every app Render App
remembers plus the shipped showcase. ↑/↓ select, ↩ opens, esc clears then
closes. ⌥1–9 are global shortcuts for the Nth pinned Dock app, resolved at
press time so pin/unpin/reorder need no rebind; while the panel is up the same
keys open the Nth row. One modifier setting drives both. Global hotkeys go
through Carbon `RegisterEventHotKey` via ctypes (`hotkey.py`: no Accessibility
grant, no new dependency; `HotKeySet` shares one process-wide handler and id
space). `launcher.py` holds the registry (dock_store ∪ showcase), ranked
search and settings in `<home>/launcher.json`; `launcher_panel.py` is the
NSPanel + WKWebView (Liquid Glass on macOS 26, flat before). New pages
`static/launcher.html` and `static/settings.html` (gear on Home); server
routes `/launcher`, `/settings`, `/api/launcher?q=`, `/api/launcher/settings`.
Menu-bar item right-click gains "Search Apps…".

## 0.9.1

Patch: per-app window frame memory, no packaging change.

Every window shared one NSWindow frame-autosave name, so the first window
restored wherever the last window of any app was left. Now each `.fused` app
owns its own saved frame, keyed on its stable app id (`fused-app-id` meta,
via `appfile.app_id_of`) so it survives updates, renames and moves; files
that predate ids are keyed on abspath. Home keeps the historical name so an
existing saved Home frame carries over. `window_policy.frame_autosave_name`
holds the decision (pure, tested); `mainwindow` enacts it. A second window of
an already-open app cascades from it instead of stacking, and does not take
the autosave name. Frame is saved explicitly on close. Also fixed: cascading
never worked (`cascadeTopLeftFromPoint:` was handed the front window's own
top-left), and `window.open` popups no longer write to any saved frame.

## 0.9.0

Minor bump: first showcase entry, no packaging change.

Add OpenMail as the first showcase item (`fused_render_app/showcase/00_OpenMail.fused`,
listed in `showcase.json`): a local-first multi-account Gmail client with
thread list, reading pane, compose, labels, Gmail search, catch-up briefings,
thread summaries and an approval-gated triage board. The bundled `.fused`
adds ~166 KB to the wheel and DMG.

## 0.8.16

One appfile change, no packaging change.

Share .fused state across every extract of one app (PR #32). An app's
`.fused/` (data, cache, meta.json) lived inside each extract dir, so every
re-export (new bytes, new `<slug>-<hash>` dir) started from empty state and
two versions of one app opened side by side could not see each other's
data. For a file stamped with `fused-app-id`, `.fused` is now a symlink to
`~/.fused-render-app/fused_data/<app_id>`; every iteration of the app reads
and writes that one dir. Id-less files keep a local `.fused` as before.

Migration on next open: an extract holding a real `.fused` seeds the shared
dir when it is empty or holds only a scaffold (empty data/cache +
meta.json); otherwise the local copy is deleted and the shared state wins.
A missing shared dir is recreated before the link is checked, so a swept
dir never leaves a dangling link.

## 0.8.15

Release plumbing and one launcher change, no packaging change.

Open .fused from a URL without the confirmation step (PR #30). A
`render-app://open?url=` link or an argv URL is already the user's gesture,
so the page shows "Downloading app…" at once, navigates to `/open?_file=`,
runs the environment-install poll, then opens the app. `confirmDownload()`
is gone from `open.html`. Trade-off: the port is fixed, so a web page can
point the browser at `/open?_url=` and have the app downloaded and run
without a click; a per-process nonce on the native deep-link handler would
close this.

Release notes link the CloudFront DMG first (PR #29). After the S3 and
asset uploads succeed the job prepends a "Download (CDN, fastest)" line to
the Release notes; the attached DMG stays as fallback. Idempotent on tag
rebuilds.

Non-macOS CI and platform branches dropped (PR #28). `env.py` always fetches
the darwin uv tarball; Windows paths and the `.cmd`-shim spawn path in
`claude_health.py` / `ai_relay.py` removed; `/api/dock/reveal` no longer
guards on `sys.platform`. `test-python` runs on `ubuntu-latest`,
`macos-desktop` is the native check.

## 0.8.14

Three launcher features, no packaging change.

In-app update banner (PR #26). A background loop fetches the signed
`render-app-dmgs/latest.json` manifest every 5 min; when it names a newer
version the launcher page shows a pill with an Update button that downloads
the DMG, verifies it (Ed25519 manifest signature + sha256 + bundle
version/id), swaps the .app in place and offers Restart. Only the launcher
shows it; open .fused windows are never interrupted. Ed25519 is a stdlib-only
implementation (RFC 8032 vectors tested) since the app ships zero runtime
deps. Render App pins its own public key (`update/common.py`); the seed is
the `FUSED_RENDER_UPDATE_SIGNING_KEY` secret and the release job publishes
the manifest, refusing to move `latest.json` backwards unless the live one
fails validation. Install errors hold through re-checks; `hdiutil` detach
failures are best-effort and attach failures always clean up their mount.

Open a .fused app from a public URL (PR #27). Paste an http(s) link on the
home page, load `/open?_url=`, pass it on argv, or click a
`render-app://open?url=` link. `fetch.py` streams into
`~/.fused-render-app/downloads/<app_id>.fused` keyed on the app's stable
`fused-app-id`, so repeat links update one file and one dock row. Non-http(s)
redirects refused, 1 GB cap, `/open` waits for a click before fetching so a
web page cannot trigger a download against the predictable localhost port.

Home page (PR #25): recent apps first, then unopened showcase; deleted files
leave the dock.

## 0.8.13

Menu-bar dock fixes (PRs #22, #23). The appear/dismiss slide is driven by
our own run-loop timer instead of `animator()` + `NSAnimationContext`, whose
completion semantics differ across macOS versions and made the panel jump
on macOS 15 (expo-out appear, cubic-in dismiss, explicit cancel, mid-slide
retarget and reversal; also removes the first-open snap). The web view is
never resized: a fixed 1400x420 canvas pinned in screen space, the panel a
viewport onto it, so a separator drag no longer flashes the tray or lands it
at screen centre on narrow displays (the page replays the native
centre-and-clamp from `dockAnchor`; web view offset and glass are set before
the panel frame so it all commits in one transaction). The tile hover bubble
shows the app's preview.png above its name when the app ships one
(`appfile.preview_bytes`, `/api/dock/preview` with immutable caching,
presence memoised in `dock_store` so the poll stays cheap; MAX_SIZE height
420 -> 520). No packaging change.

## 0.8.12

Native macOS notifications for background work (PR #21). Render App posts
its own `UNUserNotificationCenter` banners for model downloads, env
installs, renders and AI jobs, without adopting fused-render's in-app
notification panel. One stable identifier per job row: the silent
"started" banner is replaced in place by the outcome. `jobs.set_transition_hook`
fires once per row creation or state change (never on a progress tick);
`notify_policy` (pure) decides which transitions notify, mirroring
fused-render's tier semantics (silent/transient success stays quiet,
error/cancelled is always news, start banners only for model downloads and
env installs); `webnotify` gains module-level notify/remove/click dispatch by
identifier prefix; `jobnotify` is the glue macapp installs, routing clicks:
an install banner focuses the app whose env it built, a render banner
reveals the output file, everything else shows Home. Error banners show the
first line naming an exception or uv's `error:`, else the last line; an
approved compile silently replaces its "approve" banner. No packaging change.

## 0.8.11

Browser parity in app windows (PR #20). WKWebView asks the host before
granting several web APIs and an unanswered delegate is a deny, so `.fused`
apps that worked in a browser tab lost input paths in a Render App window.
Now: `requestPointerLock` granted (private `WKUIDelegatePrivate` selector);
`navigator.geolocation` granted for the app's own origin only, with
`NSLocation*UsageDescription` in the bundle plist; `Notification` granted
for own origin and delivered through a WebKit C API notification provider
(`webnotify.py`, ctypes) → `UNUserNotificationCenter`, with
onshow/onclick/onclose round-tripped; `window.open` returns a live popup
handle (opener, postMessage, `popup.close()`) and `window.close()` closes
the window. `window_policy.is_own_origin` is the single gate for
camera/mic, geolocation and notifications. No packaging change.

## 0.8.10

Dock accepts `icon.png` as a lower-priority fallback to `icon.svg` (PR #17).
The release pipeline briefly uploaded the DMG to S3/CloudFront
(`render-app-dmgs/`, PR #18) but the `github_render_app_role` OIDC assume
failed (`Not authorized to perform sts:AssumeRoleWithWebIdentity`), so it was
reverted (PR #19): DMG + wheel go to the GitHub Release only and the
`bump-homebrew` job is disabled. The tap cask (homebrew-tap #7) still points
at the CDN url and is broken until the CDN upload is re-landed. Tag was
force-moved to the revert commit. No packaging change.

## 0.8.9

Dock icons follow the system theme (port of fused-render's icon-color swap,
PR #15), including the `var(--fused-bg)` plate (fused-render #1159). Dock
popover appear/dismiss animation: drops from the menu bar with a spring-like
slide and a decoupled fast fade (PR #16). Dev iframe no longer links to
GitHub releases. No packaging change.

## 0.8.8

Renamed to **Render App** (PR #12): `RenderApp.app`, `RenderApp-<version>.dmg`,
menu-bar title, placeholder page, runtime error text, Python package
`fused_render_app`, CLI `fused-render-app`, `FUSED_RENDER_APP_*` env vars,
`~/.fused-render-app` state dir, bundle id `io.fused.render.app`, runtime flag
`fused.renderApp`. Homebrew cask token is `render-app` (`Casks/render-app.rb`,
tap PR #5); `bump-homebrew` now writes the whole cask file. First release
under the new name. No packaging change.

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
next to Open in Browser. GitHub links added to the dev and Render App UIs. No
packaging change.

## 0.8.5

Fix (dock): the tray Home tile and "Open in app" focus an open Home window
or open a new one, even while app windows are open; the macOS Dock-icon
reopen keeps its front-window behaviour. Hover magnification capped at 1.5.
Menu bar icon regenerated from the app glyph with the hollow centre.

## 0.8.4

Homebrew: `brew install --cask fusedio/tap/render-app` (PR #7). The
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
`~/.fused-render-app/venvs`. Now resolves the venv directory instead.

Fix: apps written to fused-render's background-apps contract could not find
the server — Render App never exported `FUSED_RENDER_HOME_DIR` nor wrote
`<home>/server.json`. `make_server` now does both (`{origin, port, pid,
shared, version, started}`), and `fused_render_app/shared/` ships
`appenv.py`, `fused_ai.py`, `background_app.py` verbatim from fused-render so
`sys.path.insert(0, info["shared"])` works the same way.

## 0.7.1

Rebuild of 0.7.0 from main head — confirms the DMG carries the native-window /
menu-bar work (PR #2) alongside `fused.daemon`. No code change.

## 0.7.0

`fused.daemon` supported — fused-render's background-apps feature copied in
(`background_apps.py`, `engine_host.py`, `engine_worker.py`,
`background_app.py`, runtime.js block verbatim). Render App-specific: routes ported
from FastAPI onto the stdlib `Handler` (`background_routes.py`), the proxy is
synchronous (`engine_forward.py`: same pool / at-most-once / 504-never-heals /
heal-then-retry-once rules, minus the browser-hangup 204 path), the daemon
runs on the venv `env.py` built for the app, macOS spawns through a
posix_spawn-safe bootstrap (no fork; setsid + chdir in the child), and the
cache lives under `~/.fused-render-app/engines/<id>/`.

| member | status | notes |
| --- | --- | --- |
| `fused.daemon.status / start / stop / restart / setAutostart` | ✅ new | `[tool.fused-render.app]` manifest in the app's pyproject.toml |
| `fused.daemon.run(params)` | ✅ new | `main =` apps; warm `engine_worker.py`, 60 s call budget, reaped after 15 min idle |
| `fused.daemon.call(path, body)` | ✅ new | `daemon =` apps' own HTTP routes, proxied |
| `fused.daemon.watch(cb)` | ✅ new | 5 s poll while visible |

Server routes added: `GET/POST /api/apps/background/{status,start,stop,restart,autostart,running}`,
`GET /api/engines/running`, `POST /api/engines/<id>/stop`, `ANY /api/engines/<id>/proxy/<path>`.

## 0.6.1

App renamed to **Render App**: `RenderApp.app`, `RenderApp-<version>.dmg`, menu-bar title,
placeholder page, runtime error text, Python package (`fused_render_app`), state dir
(`~/.fused-render-app`), `FUSED_RENDER_APP_*` env vars, bundle id `io.fused.render.app`
and the Homebrew cask token (`render-app`). Pre-release; no migration.

## 0.5.0

Changes from 0.4.0: **local inference**, by copying fused-render's AI subsystem
verbatim (`fused_render_app/ai/`: registry, catalog, fit, hw_detect, hub_cache,
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
`SSL_CERT_DIR` py2app exports is still dropped at startup (Render App-only).

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
under `~/.fused-render-app/ai/`.

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
`FUSED_RENDER_APP_LEGACY_DEPS` (comma list). Generated project lives at
`~/.fused-render-app/legacy/pyproject.toml`; a changed set invalidates the venv.

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
| `fused.autoReload(true)` | ❌ throws | live reload needs a file watcher Render App does not have; the app should know |
| `fused.env` / `fused.device` / `fused.renderApp` | ✅ | |
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
| `fused.env` / `fused.device` / `fused.renderApp` | ✅ | `"local"` / `"desktop"` / `true` |
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

"throws" = `Error("<name> is not supported on fused-render-app")`, `err.type === "unsupported"`, on property read for namespaces (Proxy) or on call.

Server routes: `GET /`, `/open?_file=`, `/render?path=`, `/api/health`, `/api/fs/raw`, `/api/fs/stat`, `/api/open/status`, `/api/ai/runtime`, `/api/ai/catalog`; `POST /api/open`, `/api/drop`, `/api/run`, `/api/fs/write`, `/api/ai` (JSON or chunked NDJSON), `/api/ai/cancel`; other `POST /api/ai/*` → 409 `unavailable`. POSTs need `X-Fused: 1`. 127.0.0.1 only.

Requires the `claude` CLI on the machine for the AI tier (`FUSED_RENDER_APP_CLAUDE_BIN`, PATH, `~/.claude/local`, `~/.local/bin`, `~/.bun/bin`, Homebrew).

---

## 0.1.0

| member | status | notes |
| --- | --- | --- |
| `fused.runPython` | ✅ | as above |
| `fused.params.*` | ✅ | as above |
| `fused.readFile` / `stat` / `writeFile` / `rawUrl` | ✅ | as above |
| `fused.env` / `fused.device` / `fused.renderApp` | ✅ | |
| `fused.ai.*` (all verbs, `models`, `cancel`) | ❌ throws | whole namespace a Proxy |
| `fused.capture.*`, `fused.fileIndex.*`, `fused.daemon.*` | ❌ throws | |
| `fused.trackJob`, `watchJob`, `uploadFile`, `mkdir`, `autoReload`, `snapshot` | ❌ throws | |

Server routes: as 0.2.0 minus every `/api/ai*` route.

---

## Removed from fused-render (never in Render App)

Explorer shell (React/Vite), ~50 preview templates and their vendored JS,
file index, LAN sharing, background daemons, jobs, capture, git snapshots,
Claude chat sidebar, local/Apple AI runners (mlx, llama.cpp, whisper, image
and video models), bookmarks, drafts, updater, Windows/Linux/iOS shells,
bundled data stack (numpy, pandas, pyarrow, duckdb, botocore, fused engine…).
