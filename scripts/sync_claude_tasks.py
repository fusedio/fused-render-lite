#!/usr/bin/env python3
"""Re-sync the Claude sessions / tasks cluster from a fused-render checkout.

    python scripts/sync_claude_tasks.py /path/to/fused-render [--runtime]

Copies the chat engine (`templates/claude`, `templates/shared` helpers), the
task / schedule / queue / drafts modules and their routers into
`fused_render_app/`, rewriting `fused_render` imports to `fused_render_app`
and FastAPI / pydantic imports to the `_web` shim, then re-applies the two
Render App patches (the runs dir name in agent.py, the LaunchAgent label).
With `--runtime` it also splices fused-render's `fused.tasks` block
(runtime.js, between `// --- fused.tasks` and `// --- fused.capture`) into
Render App's runtime.js between the `fused-tasks:begin` / `fused-tasks:end`
markers. Lite-only modules (the stubs in `current_apps`, `app_listing`,
`index_ignore`, `shell/mounts`, `claude_config/`, and the pages) are never
touched. Review `git diff` afterwards; run `pytest`.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(os.path.dirname(HERE), "fused_render_app")

MODULES = [
    "schedule.py", "cron.py", "recur.py", "schedule_wake.py", "drafts.py",
    "project_queue.py", "queue_manager.py", "tasks_store.py", "tasks_watch.py",
    "session_liveness.py", "claude_spawn.py", "claude_session_move.py",
    "claude_artifacts.py",
]
ROUTERS = ["tasks.py", "claude_sessions.py", "queue_events.py", "schedule.py",
           "drafts.py", "claude_artifacts.py"]
SERVER_MODULES = ["image_convert.py"]
SHARED = ["app_entry.py", "appenv.py", "file_history.py", "private_dir.py", "procutil.py"]

REWRITES = [
    (r"from fused_render\.server\.common import", "from fused_render_app.routes.common import"),
    (r"from fused_render\.server\.routers\.tasks import", "from fused_render_app.routes.tasks import"),
    (r"from fused_render\.server\.routers import", "from fused_render_app.routes import"),
    (r"from fused_render\.server import image_convert", "from fused_render_app.routes import image_convert"),
    (r"from fused_render\.index\.ignore import MountGuard", "from fused_render_app.index_ignore import MountGuard"),
    (r"from fused_render\.shell\.mounts\.access import is_mount_backed", "from fused_render_app.shell.mounts import is_mount_backed"),
    (r"from fused_render\.shell\.mounts import is_mount_backed", "from fused_render_app.shell.mounts import is_mount_backed"),
    (r"from fastapi\.concurrency import run_in_threadpool", "from fused_render_app._web import run_in_threadpool"),
    (r"from fastapi\.responses import JSONResponse", "from fused_render_app._web import JSONResponse"),
    (r"from fastapi import", "from fused_render_app._web import"),
    (r"from pydantic import BaseModel", "from fused_render_app._web import BaseModel"),
    (r"from fused_render import", "from fused_render_app import"),
    (r"from fused_render\.", "from fused_render_app."),
]

# Render App's own patches on the verbatim copy.
PATCHES = {
    os.path.join("templates", "claude", "agent.py"): [
        ('"fused_render_claude" + suffix, "runs")', '"fused_render_app_claude" + suffix, "runs")'),
    ],
    "schedule_wake.py": [
        ('LABEL = "io.fused.render.schedule-wake"', 'LABEL = "io.fused.render.app.schedule-wake"'),
    ],
}

# claude_spawn.agent_path points at the package, not a staged core dir.
SPAWN_OLD = re.compile(
    r"def agent_path\(\) -> str:\n(?:    .*\n|\n)*?    from fused_render_app\.core_templates import ensure_core_templates\n\n"
    r"    return os\.path\.join\(ensure_core_templates\(\), \"claude\", \"agent\.py\"\)\n")
SPAWN_NEW = '''def templates_dir() -> str:
    """Render App ships the chat engine inside the package
    (`fused_render_app/templates/`); fused-render stages a copy into a core dir
    (`core_templates.ensure_core_templates`). Same layout underneath —
    `claude/agent.py` beside `shared/`."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def agent_path() -> str:
    """The claude template backend (agent.py) — the packaged copy, the same
    file the chat page executes through `/api/run`, so the runs dir and
    permission_server path stay in step with what the page polls."""
    return os.path.join(templates_dir(), "claude", "agent.py")
'''


def rewrite(text: str) -> str:
    for pat, rep in REWRITES:
        text = re.sub(pat, rep, text)
    left = [l for l in text.splitlines()
            if re.match(r"\s*(from|import) fused_render(\.|\s)", l) or re.match(r"\s*from (fastapi|pydantic)", l)]
    if left:
        sys.exit("unrewritten imports: %r" % left)
    return text


def copy_py(src: str, rel: str) -> None:
    with open(src, encoding="utf-8") as f:
        text = rewrite(f.read())
    for old, new in PATCHES.get(rel, []):
        if old not in text:
            sys.exit(f"patch anchor missing in {rel}: {old!r}")
        text = text.replace(old, new)
    if rel == "claude_spawn.py":
        text, n = SPAWN_OLD.subn(SPAWN_NEW, text)
        if n != 1:
            sys.exit("claude_spawn.agent_path anchor missing")
    dst = os.path.join(DST, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    print("py ", rel)


def sync(src_root: str) -> None:
    src = os.path.join(src_root, "fused_render")
    for name in MODULES:
        copy_py(os.path.join(src, name), name)
    for name in ROUTERS:
        copy_py(os.path.join(src, "server", "routers", name), os.path.join("routes", name))
    for name in SERVER_MODULES:
        copy_py(os.path.join(src, "server", name), os.path.join("routes", name))
    tdst = os.path.join(DST, "templates", "claude")
    if os.path.isdir(tdst):
        shutil.rmtree(tdst)
    shutil.copytree(os.path.join(src, "templates", "claude"), tdst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print("dir templates/claude")
    rel = os.path.join("templates", "claude", "agent.py")
    with open(os.path.join(DST, rel), encoding="utf-8") as f:
        text = f.read()
    for old, new in PATCHES[rel]:
        if old not in text:
            sys.exit(f"patch anchor missing in {rel}: {old!r}")
        text = text.replace(old, new)
    with open(os.path.join(DST, rel), "w", encoding="utf-8") as f:
        f.write(text)
    os.makedirs(os.path.join(DST, "templates", "shared"), exist_ok=True)
    for name in SHARED:
        shutil.copy2(os.path.join(src, "templates", "shared", name),
                     os.path.join(DST, "templates", "shared", name))
        print("tpl templates/shared/" + name)


def sync_runtime(src_root: str) -> None:
    with open(os.path.join(src_root, "fused_render", "static", "runtime.js"), encoding="utf-8") as f:
        upstream = f.read()
    start = upstream.index("  // ------------------------------------------------------------- fused.tasks")
    end = upstream.index("  // ----------------------------------------------------------- fused.capture", start)
    block = upstream[start:end].rstrip() + "\n"
    path = os.path.join(DST, "static", "runtime.js")
    with open(path, encoding="utf-8") as f:
        ours = f.read()
    begin = "  // fused-tasks:begin (copied from fused-render runtime.js, D890)\n"
    finish = "  // fused-tasks:end\n"
    a, b = ours.index(begin) + len(begin), ours.index(finish)
    ours = ours[:a] + block + ours[b:]
    with open(path, "w", encoding="utf-8") as f:
        f.write(ours)
    print("js  static/runtime.js (fused.tasks block, %d lines)" % block.count("\n"))


#: Render App's own files in `frontend/`: never overwritten by a sync.
FRONTEND_OWN = ("lite.html", "src/lite.tsx", "src/LiteApp.tsx", "vite.config.js")

#: Render App's patches on the verbatim frontend copy, re-applied after a sync.
FRONTEND_PATCHES = {
    # fused-render #1344 hides the "Open in Explorer" door inside a framed
    # `/tasks?embed=1`; Render App has no Explorer to open, so the door is
    # off everywhere (the row's press still opens the peek; ⌘-click still
    # lands on the chat).
    os.path.join("src", "shell", "tasks-lib.ts"): [
        ("export const SHOW_PAGE_DOOR = !IS_QUERY_EMBED;",
         "export const SHOW_PAGE_DOOR = false; // Render App: no Explorer to open (fused-render #1344)"),
    ],
}


def sync_frontend(src_root: str) -> None:
    """Copy fused-render's `frontend/` verbatim (its React shell: the Tasks
    page, the native Claude chat and everything they import), keeping Render
    App's own entry files (`FRONTEND_OWN`). Then `scripts/build_shell.sh`."""
    import subprocess

    src = os.path.join(src_root, "frontend") + os.sep
    dst = os.path.join(os.path.dirname(DST), "frontend") + os.sep
    cmd = ["rsync", "-a", "--delete", "--exclude", "node_modules", "--exclude", ".vite"]
    for own in FRONTEND_OWN:
        cmd += ["--exclude", own]
    subprocess.run(cmd + [src, dst], check=True)
    print("dir frontend/ (kept: %s)" % ", ".join(FRONTEND_OWN))
    for rel, patches in FRONTEND_PATCHES.items():
        path = os.path.join(dst, rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for old, new in patches:
            if new in text:
                continue
            if old not in text:
                sys.exit(f"frontend patch anchor missing in {rel}: {old!r}")
            text = text.replace(old, new)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("patched frontend/" + rel)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fused_render", help="path to a fused-render checkout (its repo root)")
    ap.add_argument("--runtime", action="store_true", help="also re-splice the fused.tasks runtime.js block")
    ap.add_argument("--frontend", action="store_true",
                    help="also re-copy frontend/ (fused-render's React shell), keeping Render App's entry files")
    args = ap.parse_args()
    sync(os.path.abspath(args.fused_render))
    if args.runtime:
        sync_runtime(os.path.abspath(args.fused_render))
    if args.frontend:
        sync_frontend(os.path.abspath(args.fused_render))
