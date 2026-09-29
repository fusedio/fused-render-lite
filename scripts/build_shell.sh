#!/usr/bin/env bash
# Build the React shell (fused-render's Tasks page + native Claude chat,
# frontend/ copied verbatim from fused-render, plus Render App's lite entry)
# into fused_render_app/static/shell-dist/. Needs bun (https://bun.sh).
# Called by scripts/dev.sh when the build is missing and by
# scripts/build_dmg.sh before py2app, which ships static/ whole.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/frontend"
if ! command -v bun >/dev/null 2>&1; then
  echo "build_shell.sh: bun not found — install it (brew install oven-sh/bun/bun) to build the React shell" >&2
  exit 1
fi
bun install --frozen-lockfile
bun run build
