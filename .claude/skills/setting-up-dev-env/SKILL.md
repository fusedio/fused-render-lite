---
name: setting-up-dev-env
description: Use when setting up a fused-render-app (Render App) checkout or git worktree for the first time — before running pytest, the dev server, or build_dmg.sh — so tests and the server run on the pinned Python 3.12 venv instead of whatever python3 is on PATH.
---

# Setting Up the Dev Env

## Overview

A fresh checkout/worktree needs one thing: a **Python 3.12 venv** with the
`[dev,app]` extras. It is gitignored (`.venv/`), so it does not carry into a
worktree. There is no frontend build — the placeholder page and `runtime.js`
are static files served per request.

## Running the Dev Server

Use `scripts/dev.sh` — never `python -m fused_render_app.cli` directly. It
bootstraps `.venv` (3.12, `[dev,app]`) if missing, isolates this checkout
from the installed Render App.app and from other worktrees, and runs the
server under `watchfiles` so every `fused_render_app/**/*.py` edit restarts it.

```bash
scripts/dev.sh                      # server on the branch's port, opens a tab
scripts/dev.sh --no-browser
scripts/dev.sh ~/Downloads/x.fused  # extra args pass through to the CLI
scripts/dev.sh --port 9000
```

Isolation defaults (respected when already set):

| Env | dev.sh default | Why |
|-----|----------------|-----|
| `FUSED_RENDER_APP_PORT` | 2778 on main, `2779 + crc32(branch) % 1000` elsewhere | the installed app owns 2777; each worktree gets its own port |
| `FUSED_RENDER_APP_HOME` | `~/.fused-render-app-dev/<branch>` | the installed app owns `~/.fused-render-app`; app venvs/state never mix |
| `FUSED_RENDER_NO_RELOAD=1` | unset | single launch without watchfiles |

Static edits (`static/*.html`, `runtime.js`) need only a browser refresh.

## Setup for Tests

`dev.sh` already installs the `dev` extra (pytest), so after one `dev.sh`
run the venv is complete. To build it by hand:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,app]"
.venv/bin/python -m pytest -q      # ~31 pass
```

## Building the DMG locally

```bash
FUSED_RENDER_MACOS_FLOOR=26.0 bash scripts/build_dmg.sh
```

The floor override is LOCAL ONLY: Homebrew's python@3.12 is a per-OS bottle
(minos 26), so the bundle minos check fails against the shipped floor of 14.0.
CI uses python.org's framework build (minos 11.0) and needs no override. Never
raise the floor in the script or the workflow.

## Reference

| Item | Why |
|------|-----|
| Python 3.12 | **pinned**: the packaged app builds every app venv on its own bundled 3.12 (`env.base_python()` is `sys.executable` when frozen), so this version decides which wheels app venvs resolve. `dev.sh` rebuilds a `.venv` on anything else |
| `dev` extra | pytest |
| `app` extra | rumps + pyobjc, the menu-bar shell (`macapp.py`); only the packaged .app installs it, but the dev venv carries it so `macapp` imports in tests |
| uv | the server shells out to uv for app venvs; `env.uv_bin()` accepts the bundled copy, a uv ≥ 0.8 on PATH, or downloads a pinned one into `~/.fused-render-app/bin` (dev: under `FUSED_RENDER_APP_HOME`) |
| No frontend | nothing to `npm install`; `runtime.js` is hand-written |

Remote layout: `origin` is fusedio/fused-render-lite; `main` is the release branch.
The `origin` remote is fusedio/fused-render — never push there.
