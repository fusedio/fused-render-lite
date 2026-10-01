"""Job rows → native macOS notifications: the glue `macapp.py` installs.

Three parts that live elsewhere, joined here:

- `jobs.set_transition_hook` fires once per row creation or state change, on
  whatever thread reported it (a bring-up thread, a worker's HTTP tick).
- `notify_policy.decide` turns that transition into a `Banner` or nothing —
  pure, and the whole of the *what to notify* question (see its docstring).
- `webnotify.notify` posts the banner through the same UNUserNotificationCenter
  path the web views' `new Notification()` already uses, hopping to the main
  thread itself.

The only logic of our own is what a banner CLICK does. A row's `page` is one
of three things, and each has one sensible answer:

1. An extracted app folder (`envinstall` names the folder whose environment
   it is building): bring that app's window forward if one is open, else
   Home. The folder does not record its source file, so the reverse lookup
   runs the open windows' files through `appfile.extract_dir_for`.
2. A file the job produced (an image or video render, a transcript): reveal
   it in Finder — there is no window to show a PNG in.
3. A shell route with no page in this app (`/ai-models/local` is
   `supervisor._job_page`'s answer, and Render App has no such page), or an
   empty page: activate the app and show Home. Activation itself is done by
   `webnotify` before any handler runs.

`page_target` is the pure decision so it can be tested without AppKit; the
handler installed by `install` does the AppKit side.
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterable

from fused_render_app import jobs, notify_policy, webnotify

logger = logging.getLogger(__name__)


def page_target(page: str, apps_root: str,
                candidates: Iterable[tuple[str, str | None]]) -> tuple[str, str]:
    """What a click on a banner for a row whose `page` is ``page`` should do.

    ``candidates`` are ``(fused_path, extract_dir)`` pairs — the open
    windows' app files — and the first whose
    ``extract_dir`` is ``page`` (or contains it) wins. Returns one of
    ``("window", fused_path)``, ``("reveal", fs_path)``, ``("home", "")``.
    """
    if not page or not os.path.isabs(page):
        return ("home", "")
    page_abs = os.path.normpath(page)
    page_real = os.path.realpath(page_abs)
    # Candidates are matched wherever the page lives: a folder app's
    # extract dir is the folder itself, outside ``apps_root``. Both sides
    # are compared realpath'd too, so a symlinked ~/Fused still matches.
    for fused_path, extract_dir in candidates:
        if not extract_dir:
            continue
        d = os.path.normpath(extract_dir)
        d_real = os.path.realpath(d)
        if (page_abs == d or page_abs.startswith(d + os.sep)
                or page_real == d_real or page_real.startswith(d_real + os.sep)):
            return ("window", fused_path)
    if os.path.isfile(page_abs):
        return ("reveal", page_abs)
    return ("home", "")


def install(manager, apps_root: str) -> None:
    """Wire jobs → policy → banners, and banner clicks → ``manager``
    (a `mainwindow.WindowManager`). Call once, from the main thread, after
    the window manager exists."""
    from fused_render_app import appfile

    # identifier → the `page` its last banner carried. A click can land long
    # after the row was dismissed or swept, when `jobs` no longer knows it;
    # what the banner pointed at is remembered here so the click still
    # goes there. Written on the reporting thread, read on the main thread:
    # a single dict assignment either way, atomic under the GIL.
    pages: dict[str, str] = {}

    def on_transition(prev: dict | None, after: dict) -> None:
        banner = notify_policy.decide(prev, after)
        if banner is None:
            return
        pages[banner.identifier] = banner.page
        logger.info("job banner %s: %s — %s", banner.identifier, banner.title, banner.body)
        webnotify.notify(banner.identifier, banner.title, banner.body, sound=banner.sound)

    def on_click(identifier: str) -> None:
        # Main thread, app already activated (webnotify's dispatcher).
        page = pages.get(identifier) or ""
        if not page:
            job_id = notify_policy.job_id_from(identifier)
            record = _record_for(job_id) if job_id else None
            page = (record or {}).get("page") or ""
        files: list[str] = []
        for w in list(getattr(manager, "_windows", [])):
            if w.app_file and w.app_file not in files:
                files.append(w.app_file)
        candidates = ((f, _extract_dir(appfile, f)) for f in files)
        kind, arg = page_target(page, apps_root, candidates)
        logger.info("job banner %s clicked → %s %s", identifier, kind, arg)
        try:
            if kind == "window":
                manager.focus_or_open(arg)
            elif kind == "reveal":
                subprocess.run(["open", "-R", arg], check=False)
            else:
                manager.show_home()
        except Exception:  # noqa: BLE001 — a click must never take the app down
            logger.exception("job banner click failed")

    jobs.set_transition_hook(on_transition)
    webnotify.register_click_handler(notify_policy.IDENTIFIER_PREFIX, on_click)
    logger.info("job notifications installed")


def _extract_dir(appfile, fused_path: str) -> str | None:
    try:
        return appfile.extract_dir_for(fused_path)
    except Exception:  # noqa: BLE001 — one unreadable file must not lose the click
        logger.exception("job banner click: extract dir for %s", fused_path)
        return None


def _record_for(job_id: str) -> dict | None:
    for record in jobs.list_jobs():
        if record.get("id") == job_id:
            return record
    return None
