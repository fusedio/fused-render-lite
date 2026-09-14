#!/usr/bin/env bash
# Dev loop for fused-render-lite (Render Lite): venv bootstrap + server with
# Python auto-reload, one command. The lite counterpart of fused-render's
# scripts/dev.sh minus everything lite has no need for (there is no frontend
# build: the placeholder page and runtime.js are static files read per request).
#
#   scripts/dev.sh [fused-render-lite args…]     e.g. scripts/dev.sh --port 9000
#                                                     scripts/dev.sh ~/x.fused
#
# What it does:
#   1. Picks a Python 3.12 venv: the active $VIRTUAL_ENV, else a repo-local
#      .venv, created on first run with the [dev,app] extras. 3.12 is PINNED,
#      not a preference: the packaged app builds every app venv on its own
#      bundled 3.12 (env.base_python() == sys.executable), so a dev server on
#      another version would resolve different wheels than the shipped app.
#   2. Isolates this checkout/worktree: port and state dir derive from the git
#      branch, so a dev server never fights the installed Render Lite.app on
#      8765 / ~/.fused-render-lite, and two worktrees never share venvs.
#   3. Runs `python -m fused_render_lite.cli` under watchfiles: an edit to any
#      fused_render_lite/**/*.py restarts the server (SIGINT, wait, relaunch).
#      Static files need no restart — refresh the browser.
#   4. Opens the browser once, when the port answers (unless --no-browser).
#
# Knobs (respected when already set):
#   FUSED_RENDER_LITE_PORT   port (default: 8766 on main, 8766 + hash(branch) elsewhere)
#   FUSED_RENDER_LITE_HOME   state dir (default: ~/.fused-render-lite-dev/<branch>)
#   FUSED_RENDER_NO_RELOAD=1 single launch, no watchfiles
set -euo pipefail

REPO_ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && cd -P .. && pwd -P)"
PKG="fused_render_lite"
DEV_PYTHON_VERSION="3.12"

# ---------------------------------------------------------------- isolation
BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
SAFE_BRANCH="$(printf '%s' "$BRANCH" | tr -c 'A-Za-z0-9._-' '_')"
if [[ -z "${FUSED_RENDER_LITE_PORT:-}" ]]; then
  if [[ "$BRANCH" == "main" || "$BRANCH" == "master" || "$BRANCH" == "HEAD" || "$BRANCH" == "lite-main" ]]; then
    FUSED_RENDER_LITE_PORT=8766
  else
    # deterministic per branch, 8767..9766; python is on every dev box already
    FUSED_RENDER_LITE_PORT="$(python3 -c 'import sys,zlib; print(8767 + zlib.crc32(sys.argv[1].encode()) % 1000)' "$BRANCH")"
  fi
fi
export FUSED_RENDER_LITE_PORT
export FUSED_RENDER_LITE_HOME="${FUSED_RENDER_LITE_HOME:-$HOME/.fused-render-lite-dev/$SAFE_BRANCH}"
mkdir -p "$FUSED_RENDER_LITE_HOME"

# --port on the command line wins over the derived port (cli.py parses it).
PORT="$FUSED_RENDER_LITE_PORT"
NO_BROWSER=0
prev=""
for a in "$@"; do
  case "$a" in
    --no-browser) NO_BROWSER=1 ;;
    --port=*) PORT="${a#--port=}" ;;
  esac
  [[ "$prev" == "--port" ]] && PORT="$a"
  prev="$a"
done

# --------------------------------------------------------------------- venv
venv_is_pinned_version() {
  local found
  found="$("$1/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
  [[ "$found" == "$DEV_PYTHON_VERSION" ]]
}
install_python_deps() {
  if command -v uv >/dev/null 2>&1; then
    (cd "$REPO_ROOT" && uv pip install --python "$1/bin/python" -e ".[dev,app]")
  else
    (cd "$REPO_ROOT" && "$1/bin/python" -m pip install -e ".[dev,app]")
  fi
}

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  VENV_DIR="$VIRTUAL_ENV"
  if ! venv_is_pinned_version "$VENV_DIR"; then
    echo "==> NOTE: the active venv is not Python $DEV_PYTHON_VERSION; the packaged app" >&2
    echo "    builds app venvs on 3.12, so wheel resolution here may differ from it." >&2
    echo "    Deactivate to use $REPO_ROOT/.venv instead." >&2
  fi
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]] && venv_is_pinned_version "$REPO_ROOT/.venv"; then
  VENV_DIR="$REPO_ROOT/.venv"
else
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    echo "==> $REPO_ROOT/.venv is not Python $DEV_PYTHON_VERSION — rebuilding it"
    rm -rf "$REPO_ROOT/.venv"
  else
    echo "==> no venv found — creating $REPO_ROOT/.venv with the [dev,app] extras"
  fi
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$DEV_PYTHON_VERSION" "$REPO_ROOT/.venv"
  else
    BASE_PY="$(command -v "python$DEV_PYTHON_VERSION" || true)"
    if [[ -z "$BASE_PY" ]]; then
      echo "FATAL: need Python $DEV_PYTHON_VERSION for $REPO_ROOT/.venv and neither uv nor" >&2
      echo "       python$DEV_PYTHON_VERSION is on PATH. Install uv (https://docs.astral.sh/uv/)." >&2
      exit 1
    fi
    "$BASE_PY" -m venv "$REPO_ROOT/.venv"
    "$REPO_ROOT/.venv/bin/python" -m pip install --upgrade pip
  fi
  VENV_DIR="$REPO_ROOT/.venv"
  install_python_deps "$VENV_DIR"
  touch "$VENV_DIR/.fused-render-lite-deps"
fi
PY="$VENV_DIR/bin/python"

DEPS_STAMP="$VENV_DIR/.fused-render-lite-deps"
if [[ ! -e "$DEPS_STAMP" ]]; then
  echo "==> syncing python deps into $VENV_DIR (no install stamp yet)"
  install_python_deps "$VENV_DIR"; touch "$DEPS_STAMP"
elif [[ "$REPO_ROOT/pyproject.toml" -nt "$DEPS_STAMP" ]]; then
  echo "==> syncing python deps into $VENV_DIR (pyproject.toml changed since last install)"
  install_python_deps "$VENV_DIR"; touch "$DEPS_STAMP"
fi
"$PY" -c "import $PKG" 2>/dev/null || {
  echo "$PKG not importable from $PY — run: uv pip install --python $PY -e \".[dev,app]\"" >&2
  exit 1
}

# ------------------------------------------------------------------- reload
RELOAD=1
[[ -n "${FUSED_RENDER_NO_RELOAD:-}" ]] && RELOAD=0
if [[ "$RELOAD" -eq 1 ]] && ! "$PY" -c 'import watchfiles' 2>/dev/null; then
  echo "==> installing watchfiles into the venv (for Python auto-reload)"
  if command -v uv >/dev/null 2>&1; then uv pip install --python "$PY" watchfiles || true
  else "$PY" -m pip install watchfiles || true; fi
  "$PY" -c 'import watchfiles' 2>/dev/null || {
    echo "==> WARNING: watchfiles unavailable — single launch, no Python auto-reload"; RELOAD=0; }
fi

SERVER_PID=""; OPENER_PID=""
dev_shutdown() {
  [[ -n "$OPENER_PID" ]] && kill "$OPENER_PID" 2>/dev/null || true
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    # TERM, not INT: watchfiles stops its child (SIGINT, wait, SIGKILL) and
    # exits on either, but a dev.sh started as a background job inherits
    # SIGINT ignored and passes that on, so INT would be dropped there.
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    for _ in $(seq 1 80); do kill -0 "$SERVER_PID" 2>/dev/null || break; sleep 0.25; done
    kill -9 "$SERVER_PID" 2>/dev/null || true
  fi
}
trap 'dev_shutdown' EXIT
trap 'dev_shutdown; exit 130' INT
trap 'dev_shutdown; exit 143' TERM

echo "==> $PKG dev server: branch=$BRANCH port=$PORT home=$FUSED_RENDER_LITE_HOME python=$PY"
if [[ "$RELOAD" -eq 1 ]]; then
  if [[ "$NO_BROWSER" -eq 0 ]]; then
    (
      for _ in $(seq 1 120); do
        if "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT))==0 else 1)" 2>/dev/null; then
          "$PY" -c "import sys, webbrowser; webbrowser.open(sys.argv[1])" "http://127.0.0.1:$PORT/" >/dev/null 2>&1 || true
          exit 0
        fi
        sleep 0.5
      done
    ) &
    OPENER_PID=$!
  fi
  CMD="$(printf '%q' "$PY") -m $PKG.cli"
  for a in "$@"; do CMD+=" $(printf '%q' "$a")"; done
  # the reloader relaunches on every edit; dev.sh opens the tab once
  [[ "$NO_BROWSER" -eq 0 ]] && CMD+=" --no-browser"
  echo "==> watching $REPO_ROOT/$PKG/**/*.py (Ctrl-C stops the server)"
  "$PY" -m watchfiles --filter python "$CMD" "$REPO_ROOT/$PKG" &
else
  "$PY" -m "$PKG.cli" "$@" &
fi
SERVER_PID=$!
set +e; wait "$SERVER_PID"; STATUS=$?; set -e
SERVER_PID=""
exit "$STATUS"
