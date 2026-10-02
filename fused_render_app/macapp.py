"""macOS app shell of FusedBot: the HTTP server on a thread, a menu-bar item,
and the app's own windows (mainwindow.py) — the bots page (``/``) and the
Tasks page in native windows, any number of them, all on the one server. The
default browser is only ever an explicit "Open in browser".

The menu-bar item opens a Dock-like tray (menubar_dock.py, the page at
``/dock``; its tiles are bots/dock.py's lists): a left click drops it, a right
click shows the utility menu (Open FusedBot, Tasks, Open in Browser, Open App
Logs, Quit). If the tray cannot be built, rumps's own menu with those same
items stays on the status item, so Quit is never lost.

Launch order matters: the AppKit run loop starts first and the server boots
in the background after it; the first window opens once the server answers.

Opening a ``.fused`` (Finder double-click, ``render-app://`` link, argv) is
no longer a feature: the document type and URL scheme stay registered for
now, and any such open just shows the FusedBot window.

A second launch (a CLI-style ``open -a`` while the app already runs) finds the
live server through the pidfile and hands over to the running app via
LaunchServices (a reopen event, or openFiles, which shows its window); only
if no such app is registered does it fall back to a browser tab on the live
port.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import urllib.request
import webbrowser

from fused_render_app import __version__, appfile, fetch, paths, server
from fused_render_app.bots import dock as bots_dock
from fused_render_app.cli import open_url
from fused_render_app.update import mac as mac_update

logger = logging.getLogger(__name__)

DEFAULT_PORT = 2777
BUNDLE_ID = "io.fused.render.app"  # must match scripts/setup_py2app.py
# How long quit waits for `capture.stop_all()`: enough for several recordings
# to finalise (~1.4 s each measured), short enough that logout does not show
# "app not responding" while ScreenCaptureKit stalls (up to ~135 s per stop).
QUIT_STOP_BUDGET_S = 20.0


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_running_server() -> int | None:
    try:
        with open(paths.pid_path(), "r", encoding="utf-8") as f:
            rec = json.load(f)
        pid, port = int(rec["pid"]), int(rec["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not _is_alive(pid):
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
            if json.load(r).get("ok"):
                return port
    except Exception:  # noqa: BLE001
        pass
    return None


def _write_pidfile(port: int) -> None:
    # server.make_server already wrote <home>/server.json ({pid, port, origin,
    # shared, ...}); nothing more to record here.
    return


def _remove_pidfile() -> None:
    try:
        os.remove(paths.pid_path())
    except OSError:
        pass


def pick_port(start: int = DEFAULT_PORT, tries: int = 20) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return 0  # let the OS pick


def _menubar_image(path: str):
    """The menu-bar template icon as ONE NSImage carrying both
    representations — ``menubar.png`` (36 px) and ``menubar@2x.png`` (72 px)
    — at rumps's 20×20 pt, so a Retina menu bar draws the 72 px one instead of
    upscaling the 36 (rumps itself loads only the single file it is given).
    None when neither file loads; the caller keeps rumps's own image then."""
    try:
        from AppKit import NSImage, NSImageRep

        image = NSImage.alloc().initWithSize_((20, 20))
        for p in (path, path[:-len(".png")] + "@2x.png"):
            if os.path.isfile(p):
                for rep in NSImageRep.imageRepsWithContentsOfFile_(p) or ():
                    rep.setSize_((20, 20))
                    image.addRepresentation_(rep)
        if not image.representations():
            return None
        image.setTemplate_(True)
        return image
    except Exception:  # noqa: BLE001 — cosmetic; rumps's single-file image stands
        logger.debug("menu-bar icon: @2x image not built", exc_info=True)
        return None


def _install_window_hooks(manager) -> None:
    """`server.native_hooks` for the window manager, callable from the HTTP
    thread: each hook hops to the main thread itself and returns at once,
    except ``open_files`` which only reads plain attributes (WindowManager
    keeps it thread-safe on purpose)."""
    from PyObjCTools import AppHelper

    server.native_hooks.update({
        "open_files": manager.open_files,
        "focus_or_open": lambda fs_path: AppHelper.callAfter(manager.focus_or_open, fs_path),
        "show_home": lambda: AppHelper.callAfter(manager.show_home),
    })


def _install_dock_hooks(state: dict) -> None:
    """What the tray's ``/api/dock/*`` routes (and its own tile menu) call,
    from any thread: each hook hops to the main thread, closes the tray first,
    then shows the window. Installed after `_install_window_hooks`, whose
    ``show_home`` it replaces with the tray-closing one."""
    from PyObjCTools import AppHelper

    manager = state["windows"]
    dock = state["dock"]

    def dock_open(kind: str, key: str) -> None:
        def run():
            dock.close_popover()
            if kind == "bot":
                manager.show_bot(key)
            elif kind == "app":
                manager.show_url(bots_dock.app_render_path(key))
        AppHelper.callAfter(run)

    def show_home() -> None:
        def run():
            dock.close_popover()
            manager.show_home()
        AppHelper.callAfter(run)

    server.native_hooks.update({"dock_open": dock_open, "show_home": show_home})


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(paths.log_path(), encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("fused-render-app %s starting (pid %s)", __version__, os.getpid())

    # Files or links handed on argv (open -a … file, a CLI-style launch).
    # Opening them is no longer a feature (module docstring): they are only
    # logged, and the launch shows the FusedBot window like any other.
    argv_ignored = [a for a in sys.argv[1:]
                    if fetch.url_from_link(a)
                    or (a.lower().endswith(".fused") and os.path.isfile(a)) or appfile.is_app_dir(a)]
    if argv_ignored:
        logger.info("opening .fused files is no longer a feature; ignoring %s", argv_ignored)

    # Before the first urlopen (find_running_server's health probe): py2app's
    # bootstrap leaves SSL_CERT_FILE pointing at a file that does not exist,
    # and urllib freezes its CA bundle into the first opener it builds. The
    # server's own make_server() repeats this; running it here first keeps the
    # launcher's probe from poisoning every later HTTPS call in the process.
    paths.fix_process_env()
    existing = find_running_server()
    if existing is not None:
        # Hand over to the RUNNING app: LaunchServices delivers a reopen
        # event and that instance shows its window. Only from a SOURCE run:
        # inside the bundle this branch means another process owns the
        # pidfile (a source run, a second copy of the app), and `open -b`
        # would resolve to the registered bundle — this very process, about
        # to exit — relaunching in a loop. There, and when no bundle with our
        # id is registered, a browser tab on the live port is the fallback.
        logger.info("live server on port %s; handing over and exiting", existing)
        handed = None
        if not getattr(sys, "frozen", False):  # py2app sets sys.frozen
            handed = subprocess.run(["open", "-b", BUNDLE_ID],
                                    check=False, capture_output=True)
        if handed is None or handed.returncode != 0:
            webbrowser.open(open_url(existing, None))
        return

    import rumps  # macOS only

    port = pick_port()
    state = {"ready": False, "server": None, "windows": None, "dock": None}

    def show(target: str) -> None:
        """Open ``target`` in a new window of this app. Callable from any
        thread. A browser tab only if the window manager failed to build —
        the app is never left without a surface."""
        manager = state["windows"]
        if manager is None:
            webbrowser.open(target)
            return
        from PyObjCTools import AppHelper

        AppHelper.callAfter(manager.open, target)

    def show_home() -> None:
        """Focus a FusedBot window, or open a new one if none is open."""
        manager = state["windows"]
        if manager is None:
            webbrowser.open(open_url(port, None))
            return
        from PyObjCTools import AppHelper

        AppHelper.callAfter(manager.show_home)

    def open_file(what: str) -> None:
        """A Finder / URL-scheme open of a .fused, a folder app or a link.
        Opening those is no longer a feature: log it and show the FusedBot
        window. Before readiness, nothing — the startup window opens once
        the server answers."""
        logger.info("opening .fused files is no longer a feature; showing FusedBot for %s", what)
        if state["ready"]:
            show_home()

    # Finder "Open with" / double-click: AppKit calls application:openFiles:
    # on the delegate. rumps's delegate lacks it; adding the method to the
    # class is enough — pyobjc registers the selector. The .fused document
    # type stays registered for now (scripts/setup_py2app.py).
    def application_openFiles_(self, _app, filenames):
        names = [str(n) for n in filenames]
        logger.info("Finder open-files event: %s", names)
        open_file(", ".join(names))

    rumps.rumps.NSApp.application_openFiles_ = application_openFiles_

    # URL scheme (render-app://open?url=…), a bare http(s) link, or a
    # file:// URL: same as a file open.
    def application_openURLs_(self, _app, urls):
        raws = [str(u.absoluteString()) for u in urls]
        logger.info("open-URLs event: %s", raws)
        open_file(", ".join(raws))

    rumps.rumps.NSApp.application_openURLs_ = application_openURLs_

    # Dock click / Finder double-click on the running app: bring the front
    # window forward, or open FusedBot if every window was closed. Before
    # readiness, nothing: the startup window is on its way.
    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        if state["ready"]:
            manager = state["windows"]
            if manager is None:
                webbrowser.open(open_url(port, None))
            else:
                from PyObjCTools import AppHelper

                AppHelper.callAfter(manager.reopen)
        return True

    rumps.rumps.NSApp.applicationShouldHandleReopen_hasVisibleWindows_ = (
        applicationShouldHandleReopen_hasVisibleWindows_
    )

    def bootstrap() -> None:
        srv, _thread = server.serve_in_thread(port)
        state["server"] = srv
        actual = srv.server_address[1]
        if not server.wait_ready(actual, 15.0):
            logger.error("server did not become ready on port %s", actual)
            quit_app(None)
            return
        _write_pidfile(actual)
        state["port"] = actual
        if state["windows"] is not None:
            state["windows"].set_port(actual)
        if state["dock"] is not None:
            state["dock"].set_port(actual)
        logger.info("server ready on port %s", actual)
        # The tray's page loads now the server answers (the panel was built
        # at kickoff, before the port was bound). No first-launch show: the
        # FusedBot window below is the startup surface.
        if state["dock"] is not None:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(state["dock"].server_ready)
            if os.environ.get("FUSED_RENDER_APP_DOCK_SHOW"):  # dev: screenshot the tray
                AppHelper.callAfter(state["dock"].show_popover)
        # The in-app updater (update/mac.py): a background manifest check
        # read through GET /api/update. No-op outside a bundle, and guarded
        # like everything else — no updates is a lesser outcome than no app.
        try:
            mac_update.start()
        except Exception:  # noqa: BLE001
            logger.exception("update manager unavailable")
        # The FusedBot window (`/`). Queued BEFORE the ready flip: an
        # open or reopen event landing right after it then finds this window
        # (both hop to the main thread in order) instead of opening a second.
        # FUSED_RENDER_APP_NO_BROWSER keeps its name: "open no surface at
        # startup", whatever the surface is.
        if not os.environ.get("FUSED_RENDER_APP_NO_BROWSER"):
            show(open_url(actual, None))
        state["ready"] = True

    def relaunch() -> None:
        """POST /api/update/relaunch (HTTP thread): the bundle on disk is a
        newer version than this process runs. Spawn a detached shell that
        waits for this pid to exit and then opens the bundle by path, and quit
        through the normal teardown a moment later — after the HTTP reply has
        gone out and off the request thread, since close_all() wants the main
        thread."""
        bundle = mac_update.bundle_path()
        if bundle is None:
            logger.warning("relaunch requested outside a bundle; quitting only")
        else:
            logger.info("relaunching from %s", bundle)
            subprocess.Popen(
                ["/bin/sh", "-c", mac_update.relaunch_script(bundle, os.getpid())],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
        from PyObjCTools import AppHelper

        threading.Timer(0.3, lambda: AppHelper.callAfter(quit_app, None)).start()

    server.native_hooks["relaunch"] = relaunch

    def _stop_captures() -> None:
        """End every recording BEFORE the process goes: each one has a file
        open and a native stream running, and a .mov only gets its moov atom
        on stop. Lazy import so a bundle missing ScreenCaptureKit still quits
        cleanly. Idempotent — `stop_all` on an empty registry is a no-op.

        Runs `stop_all` on a daemon thread and waits at most
        `QUIT_STOP_BUDGET_S`: the stops are sequential and one stalled
        ScreenCaptureKit stream can hold a stop for minutes, and this is the
        main thread — both `quit_app` and `applicationWillTerminate:` — so an
        unbounded wait beachballs the menu bar for the whole time. Past the
        budget the process goes anyway (daemon thread, `os._exit` / AppKit's
        `exit()`); the recordings still live at that point are logged."""
        try:
            from fused_render_app import capture

            def _run() -> None:
                try:
                    capture.stop_all()
                except Exception:  # noqa: BLE001 — quitting regardless
                    logger.debug("capture.stop_all failed during quit",
                                 exc_info=True)

            t = threading.Thread(target=_run, name="capture-stop-all",
                                 daemon=True)
            t.start()
            t.join(QUIT_STOP_BUDGET_S)
            if t.is_alive():
                logger.warning(
                    "capture.stop_all did not finish within %.0fs; "
                    "%d recording(s) still live at quit",
                    QUIT_STOP_BUDGET_S, len(capture.active()) + capture.ending_count())
        except Exception:  # noqa: BLE001 — quitting regardless
            logger.debug("capture.stop_all failed during quit", exc_info=True)

    # Logout, shutdown and any `NSApp.terminate:` never reach `quit_app`:
    # AppKit exits through C `exit()`, which runs no Python `atexit` handler.
    # rumps emits `before_quit` from `applicationWillTerminate:`, synchronously
    # and before that exit, so this is the one hook that covers those paths.
    rumps.events.before_quit.register(lambda: _stop_captures())

    def quit_app(_sender) -> None:
        logger.info("quitting")
        # Unload every page and destroy its web view first, so media stops
        # and WebKit closes its pages before the process goes away.
        wins = state.get("windows")
        if wins is not None:
            try:
                wins.close_all()
            except Exception:  # noqa: BLE001 — quitting regardless
                logger.debug("close_all failed during quit", exc_info=True)
        server.stop_ai()  # evict resident models (kills worker processes), stop the warm claude
        _stop_captures()
        srv = state.get("server")
        if srv is not None:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        _remove_pidfile()
        os._exit(0)

    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "menubar.png")

    def live_port() -> int:
        return state.get("port") or port

    def open_tasks() -> None:
        manager = state.get("windows")
        if manager is not None:
            manager.show_tasks()
        else:
            webbrowser.open(f"http://127.0.0.1:{live_port()}/tasks")

    def open_browser() -> None:
        webbrowser.open(open_url(live_port(), None))

    def open_logs() -> None:
        subprocess.run(["open", "-R", paths.log_path()], check=False)

    class App(rumps.App):
        """The menu-bar item. This plain menu is the fallback only: the tray
        (menubar_dock.py, built at kickoff) takes it off the status item and
        shows the same entries on a right click. Every item has its own
        callback — not `@rumps.clicked`."""

        def __init__(self):
            super().__init__("FusedBot", icon=icon if os.path.isfile(icon) else None,
                             template=True, quit_button=None)
            if os.path.isfile(icon):
                # Read by rumps at run() (its delegate sees this __dict__).
                self._icon_nsimage = _menubar_image(icon) or self._icon_nsimage
            self.menu = [
                rumps.MenuItem("Open FusedBot", callback=lambda _s: show_home()),
                rumps.MenuItem("Tasks", callback=lambda _s: open_tasks()),
                rumps.MenuItem("Open in browser", callback=lambda _s: open_browser()),
                rumps.MenuItem("Open app logs", callback=lambda _s: open_logs()),
                rumps.MenuItem("Quit", callback=quit_app),
            ]

    app = App()

    def kickoff(timer):
        timer.stop()
        # The window manager needs the AppKit run loop (it installs the main
        # menu and sets the activation policy), so it is built here, on the
        # first timer tick, and never at import time. Guarded: without it the
        # app runs the old way, every surface a browser tab.
        try:
            from fused_render_app.mainwindow import WindowManager

            state["windows"] = WindowManager(port, quit=lambda: quit_app(None))
        except Exception:  # noqa: BLE001 — logged; browser fallback is the design
            logger.exception("windows unavailable; falling back to browser tabs")
        # Model downloads, environment installs and AI jobs announce their
        # start/finish as native notifications (jobnotify.py). Needs the
        # window manager for what a click does; guarded like everything else
        # here — no banners is a lesser outcome than no app.
        if state["windows"] is not None:
            try:
                from fused_render_app import jobnotify

                jobnotify.install(state["windows"], paths.apps_dir())
            except Exception:  # noqa: BLE001
                logger.exception("job notifications unavailable")
            _install_window_hooks(state["windows"])
        # The menu-bar Dock (menubar_dock.py) replaces rumps' menu with a
        # floating tray of bots and apps; right-click keeps the menu's
        # entries. Needs the window manager (showing a bot or an app is its
        # point). Guarded: without it the rumps menu above stays as it was.
        if state["windows"] is not None:
            try:
                from fused_render_app.menubar_dock import DockController

                state["dock"] = DockController(
                    app._nsapp.nsstatusitem, live_port(),
                    actions={"show_home": show_home,
                             "show_tasks": open_tasks,
                             "open_browser": open_browser,
                             "open_logs": open_logs,
                             "quit": lambda: quit_app(None)})
                _install_dock_hooks(state)
                if os.environ.get("FUSED_RENDER_APP_DOCK_SHOW"):
                    # Dev only: SIGUSR1 shows the tray, so a script can
                    # screenshot it without Accessibility access to click.
                    # Main thread here — signal.signal insists on it.
                    import signal

                    from PyObjCTools import AppHelper

                    signal.signal(signal.SIGUSR1, lambda *_: AppHelper.callAfter(
                        state["dock"].show_popover))
                    # Python signal handlers run only between bytecodes; an
                    # idle AppKit run loop executes none. A no-op tick keeps
                    # the interpreter breathing so the signal lands.
                    app.dock_dev_tick = rumps.Timer(lambda _t: None, 0.5)
                    app.dock_dev_tick.start()
            except Exception:  # noqa: BLE001
                logger.exception("menu-bar Dock unavailable; keeping the status-item menu")
                state["dock"] = None
                server.native_hooks.pop("dock_open", None)
                _install_window_hooks(state["windows"])  # the plain show_home again
                try:  # a half-built Dock may have taken the menu off already
                    app._nsapp.nsstatusitem.setMenu_(app._menu._menu)
                except Exception:  # noqa: BLE001
                    logger.debug("status-item menu not restored", exc_info=True)
        threading.Thread(target=bootstrap, daemon=True).start()

    # Held on `app`: an unreferenced rumps.Timer is collected before it fires.
    app.boot_timer = rumps.Timer(kickoff, 0.1)
    app.boot_timer.start()
    app.run()


if __name__ == "__main__":
    main()
