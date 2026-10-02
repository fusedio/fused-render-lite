# Working on FusedBot

FusedBot is a macOS app that runs browser bots: each bot drives its own
Chrome window and thinks with Claude Code (`claude -p` over MCP) or a local
Gemma model. It grew out of Render App (fused-render-lite), which is why the
Python package is still `fused_render_app`, the app home is
`~/.fused-render-app` and half the tree is copied from fused-render. This
file is the one-screen orientation for an agent; the design lives in
`docs/BOT-APP.md` (architecture + wire contract) and `README.md` (user-facing
behaviour, layout, release pipeline). Read those before changing behaviour;
read this before running anything.

## Commands

```
scripts/dev.sh                           # venv (3.12, [dev,app]) + server with reload, per-branch port + home
scripts/dev.sh --no-browser
scripts/build_shell.sh                   # React pages (bun) -> fused_render_app/static/shell-dist/  (gitignored)
cd frontend && bun run watch             # rebuild on edit
.venv/bin/python -m pytest -q            # backend (~100 s, ~1300 tests)
cd frontend && bun run build && bun test # tsc + boundaries + vite, then the pure-lib tests
bash scripts/build_dmg.sh                # FusedBot-<ver>.dmg, ad-hoc signed (see "DMG" below)
```

Always `.venv/bin/python`, never a bare `python3`: the venv is pinned to the
3.12 the packaged app bundles (`.claude/skills/setting-up-dev-env`).

## Shape of the tree

| Path | What | Origin |
| --- | --- | --- |
| `fused_render_app/bots/` | the product: `bot.py` (Bot, models, `_engine_for`), `agent_engine.py` (Claude Code harness), `steps_engine.py` (local models), `browser.py` (Chrome over CDP), `routes.py` (`/api/bots/*`), `presets/`, `starters/`, `dock*.py` | OpenBot port |
| `fused_render_app/server.py` | the whole HTTP surface on `http.server`; routers mount through `_web.APIRouter` (a FastAPI-shaped shim, not FastAPI) | Render App |
| `fused_render_app/onboarding.py`, `claude_health.py`, `claude_install.py`, `claude_login.py`, `routes/claude_health.py` | first-run wizard flag/stages, Claude Code health + repair | ported from fused-render 2026-10 |
| `fused_render_app/ai/`, `routes/ai_*.py`, `routes/tasks.py`, `claude_*`, `tasks_store.py`, `schedule.py`, … | local AI runtime, Claude sessions / tasks | **copied verbatim** from fused-render — `scripts/sync_claude_tasks.py` re-syncs; fix upstream, then sync |
| `fused_render_app/skills/` | the fused-render skills shipped to the bots' Claude sessions | synced copy (`sync_claude_tasks.py --skills`); never edit here |
| `frontend/src/apps/bots/` | the bots page (React port of OpenBot's page) + `onboarding/` (the setup wizard) | ours |
| `frontend/src/platform/`, `frontend/src/shell/`, `frontend/src/apps/claude/` | shadcn primitives, API client, Tasks page, native Claude chat | copied verbatim from fused-render; `scripts/check-boundaries.mjs` enforces the layering |
| `frontend/bots.html` → `src/bots.tsx` | the page `/` serves (and `/onboarding`) | |
| `frontend/lite.html` → `src/lite.tsx` | `/tasks`, `/chat`, `/explorer/*` (Builds iframe, task peek) | |
| `fused_render_app/macapp.py`, `mainwindow.py`, `menubar_dock.py`, `window_policy.py` | the AppKit shell: windows, menu-bar dock tray, navigation policy (pure Python, tested) | |
| `tests/` | pytest; `conftest.py` isolates HOME, `~/.claude`, the app home, the CLI (`FUSED_RENDER_CLAUDE_BIN` → missing path) and the onboarding redirect (`FUSED_RENDER_ONBOARDING=0`) per test | |

## Rules that are not obvious from the code

- **Frontend changes are invisible until `scripts/build_shell.sh` runs.** The
  server serves `static/shell-dist/`, which is gitignored and built, not
  watched by the Python reloader. A "nothing changed" symptom after a `.tsx`
  edit is a stale build.
- **Copied code is not forked.** Anything under the "copied verbatim" rows
  above gets fixed in fused-render and re-synced. A local patch there is lost
  on the next sync and drifts the two apps.
- **`_web`, not FastAPI.** Routers use `from fused_render_app._web import
  APIRouter, Body, Header, JSONResponse, run_in_threadpool`. The shim covers
  what the copied routers use; add to it, do not guess at it.
- **Mutating POSTs need `X-Fused: 1`** (`routes.common._require_fused`). The
  test `client` fixture adds it; its calls return `(status, headers, body)`.
- **Bots pick the ENGINE, never the MODEL, on their own.** `bot.py::
  _engine_for` falls back to the steps engine when no `claude` is runnable,
  but a bot's model stays what its Settings say. A preset bot (Claude model)
  on a Mac without Claude Code fails its first task; the onboarding wizard
  says so and tells the user to change the Model by hand. Owner's call
  (2026-10-02): do not add an automatic model switch.
- **`claude_health.runnable()` vs `resolve()`.** `resolve()` reports a
  non-executable user override as `(path, "override")` so the UI can say so;
  code that means "is there a CLI to run" calls `runnable()`.
- **The bots page's CSS beats Tailwind.** `apps/bots/styles/bots.css` embeds
  OpenBot's `app.css` unlayered (`button { background: var(--raised) … }`),
  and unlayered CSS wins over `@layer utilities` whatever the class list. A
  shadcn `<Button variant="accent">` renders grey there unless an unlayered
  rule restores it (`apps/bots/onboarding/onboarding.css` is the pattern).
  Check computed styles before blaming a token.
- **`/` redirects to `/onboarding` on a never-seen install** (server-side,
  `onboarding.should_auto_show`). Tests force it off; a dev server with a
  fresh `FUSED_RENDER_APP_HOME` shows it; `FUSED_RENDER_ONBOARDING=1` forces
  it on, `=0` off. Wizard exits await their complete/dismiss write before
  navigating, or the redirect bounces them back in.
- **Upgrade edge.** `onboarding.seed_for_existing_users` marks an install
  with bots as completed at startup. Keep that true for any new first-run
  surface: 0.11.x is shipped.
- **Worktrees share one `build/`.** Never run two `build_dmg.sh` in the same
  checkout; `rm -rf build dist` between a Homebrew-python build and a
  framework-python one (`build/py2app-venv` is reused otherwise).

## Seeing it run

Isolated server so the real app home is untouched and the wizard auto-shows:

```
FUSED_RENDER_APP_HOME=/tmp/fb-home FUSED_RENDER_DIR=/tmp/fb-ws \
  .venv/bin/python -m fused_render_app.cli --port 2995 --no-browser
open http://127.0.0.1:2995/
```

Driving it from an agent: Chrome with `--remote-debugging-port=9222` and
Argent (`list-devices` → `describe` → `gesture-tap` / `screenshot`;
`debugger-evaluate` for computed styles). Launch Chrome WITHOUT a URL and open
the tab with `curl -X PUT "http://127.0.0.1:9222/json/new?<url>"` — `open -na
"Google Chrome" --args … <url>` hands the URL to the user's running Chrome
instead, and a stray visit there stamps first-run flags in the wrong profile.
The AppKit side (windows, dock tray, navigation policy) is not reachable this
way; `window_policy.py` is pure and tested, `mainwindow.py` is checked by
launching the built app.

## DMG

`bash scripts/build_dmg.sh` is ad-hoc signed and only runs on the macOS it was
built on (Homebrew python is a per-OS bottle). For a build that runs on older
macOS and can be shared: python.org's 3.12 framework (installable per-user
with `installer -pkg … -target CurrentUserHomeDirectory`), run under
`/opt/homebrew/bin/bash` (SIP strips `DYLD_*` under `/bin/bash`), with

```
FUSED_RENDER_FRAMEWORK_PYTHON=~/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
DYLD_FRAMEWORK_PATH=~/Library/Frameworks  DYLD_LIBRARY_PATH=$FW/lib  TCL_LIBRARY=$FW/lib/tcl8.6  TK_LIBRARY=$FW/lib/tk8.6
FUSED_RENDER_SIGN=1                       # Developer ID + hardened runtime; codesign may ask for the keychain — click Always Allow
FUSED_RENDER_NOTARY_PROFILE=<profile>     # notarytool keychain profile -> submit, wait, staple
```

The notary profile is `xcrun notarytool store-credentials <name> --key
AuthKey_<id>.p8 --key-id <id> --issuer <issuer-uuid>` once per machine; CI
builds it from the repo secrets (`.github/workflows/release.yml`). Releases
proper go through `.claude/skills/making-a-release` (tag → CI → GitHub
Release + Homebrew cask); do not use the release workflow for test builds.

## Where decisions are recorded

- `docs/BOT-APP.md` — architecture, wire shapes, HTTP API, what the backend
  keeps from OpenBot, the agent engine, server/shell changes, status.
- `docs/AUDIT-onboarding.md` — why the setup wizard exists and what was
  brought from fused-render.
- `STATUS.md` — per-version DMG size and what changed.
- Module docstrings carry the reasoning for anything non-obvious; keep that
  habit (a decision in a chat is lost, a decision above the code is not).
