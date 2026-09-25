"""macOS app shell: the HTTP server on a thread, a menu-bar item, and the
app's own windows (mainwindow.py) — every .fused opens in a native window of
this app, any number of them, all on the one server. The default browser is
only ever an explicit "Open in browser".

Launch order matters: the AppKit run loop starts first and the server boots
in the background after it, because ``application:openFiles:`` (a Finder
double-click on a .fused) is delivered once the run loop is up while the
server takes a moment. Files that arrive before readiness are queued.

A second launch (a CLI-style ``open -a`` while the app already runs) finds the
live server through the pidfile and asks the running app, via LaunchServices,
to open the files in its windows; only if no such app is registered does it
fall back to a browser tab on the live port.
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

from fused_render_app import __version__, fetch, paths, server
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


def _install_dock_hooks(state: dict) -> None:
    """What the server's /api/dock/* routes call, from the HTTP thread: every
    hook hops to the main thread itself and returns at once, except
    ``open_files`` which only reads plain attributes (WindowManager keeps it
    thread-safe on purpose)."""
    from PyObjCTools import AppHelper

    manager = state["windows"]
    dock = state["dock"]

    def focus_or_open(fs_path: str) -> None:
        def run():
            dock.close_popover()
            manager.focus_or_open(fs_path)
        AppHelper.callAfter(run)

    def choose_file() -> None:
        def run():
            dock.close_popover()
            manager.choose_file()
        # One extra tick: started from inside the popover's event handling
        # the modal panel gets its clicks eaten; a clean run-loop pass fixes it.
        AppHelper.callAfter(lambda: AppHelper.callAfter(run))

    def show_home() -> None:
        def run():
            dock.close_popover()
            manager.show_home()
        AppHelper.callAfter(run)

    server.native_hooks.update({
        "open_files": manager.open_files,
        "focus_or_open": focus_or_open,
        "choose_file": choose_file,
        "show_home": show_home,
    })


def _install_launcher_hooks(state: dict) -> None:
    """What ``POST /api/launcher/hotkey`` and the footer's status read call,
    from the HTTP thread: rebinding hops to the main thread (Carbon and the
    page live there); the bound flag is a plain attribute read."""
    from PyObjCTools import AppHelper

    launcher = state["launcher"]

    def rebind(spec) -> None:
        if spec:
            AppHelper.callAfter(launcher.bind_hotkey, spec)
        else:  # another setting changed; only the page needs telling
            AppHelper.callAfter(launcher.push_settings)

    server.native_hooks.update({
        "launcher_rebind": rebind,
        "launcher_hotkey_bound": launcher.hotkey_bound,
        "launcher_pinned_bound": launcher.pinned_bound,
    })


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(paths.log_path(), encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("fused-render-app %s starting (pid %s)", __version__, os.getpid())

    # Files handed on argv (open -a … file, or a CLI-style launch), plus
    # http(s) or render-app:// links to a .fused (downloaded by the open
    # page, fetch.py).
    argv_urls = [u for u in (fetch.url_from_link(a) for a in sys.argv[1:]) if u]
    argv_files = [a for a in sys.argv[1:] if a.lower().endswith(".fused") and os.path.isfile(a)]

    existing = find_running_server()
    if existing is not None:
        # Hand the files to the RUNNING app: LaunchServices delivers them to
        # its application:openFiles: (or, with no files, a reopen event), and
        # that instance opens them in its own windows. Only from a SOURCE run:
        # inside the bundle this branch means another process owns the
        # pidfile (a source run, a second copy of the app), and `open -b`
        # would resolve to the registered bundle — this very process, about
        # to exit — dropping the files or relaunching in a loop. There, and
        # when no bundle with our id is registered, a browser tab on the live
        # port is the fallback that keeps the files openable.
        logger.info("live server on port %s; handing over and exiting", existing)
        handed = None
        if not getattr(sys, "frozen", False):  # py2app sets sys.frozen
            handed = subprocess.run(["open", "-b", BUNDLE_ID, *argv_files],
                                    check=False, capture_output=True)
        if handed is None or handed.returncode != 0:
            for f in argv_files or [None]:
                webbrowser.open(open_url(existing, f))
        # `open -b` only carries files; a URL goes to the live port directly.
        for u in argv_urls:
            webbrowser.open(open_url(existing, u))
        return

    import rumps  # macOS only

    port = pick_port()
    state = {"ready": False, "docs": False, "pending": [], "server": None, "windows": None,
             "dock": None, "launcher": None}

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
        """Focus a Home window, or open a new one if none is open."""
        manager = state["windows"]
        if manager is None:
            webbrowser.open(open_url(port, None))
            return
        from PyObjCTools import AppHelper

        AppHelper.callAfter(manager.show_home)

    def open_file(fs_path: str) -> None:
        target = open_url(port, fs_path)
        state["docs"] = True
        if state["ready"]:
            logger.info("opening %s", target)
            show(target)
        elif target in state["pending"]:
            # A source run gets a launch file twice: once as argv, once as
            # the openFiles event AppKit synthesises from it. One window.
            logger.info("already queued %s", target)
        else:
            logger.info("queueing %s until the server is ready", target)
            state["pending"].append(target)

    # Finder "Open with" / double-click: AppKit calls application:openFiles:
    # on the delegate. rumps's delegate lacks it; adding the method to the
    # class is enough — pyobjc registers the selector.
    def application_openFiles_(self, _app, filenames):
        names = [str(n) for n in filenames]
        logger.info("Finder open-files event: %s", names)
        for name in names:
            open_file(name)

    rumps.rumps.NSApp.application_openFiles_ = application_openFiles_

    # URL scheme (render-app://open?url=…, "Open in Render App" web links),
    # a bare http(s) link, or a file:// URL. The http(s) target is downloaded
    # by the open page (fetch.py); nothing is fetched here.
    def application_openURLs_(self, _app, urls):
        for u in urls:
            raw = str(u.absoluteString())
            logger.info("open-URLs event: %s", raw)
            if raw.startswith("file://"):
                import urllib.parse

                open_file(urllib.parse.unquote(urllib.parse.urlsplit(raw).path))
                continue
            link = fetch.url_from_link(raw)
            if link:
                open_file(link)
            else:
                logger.warning("ignoring URL %s", raw)

    rumps.rumps.NSApp.application_openURLs_ = application_openURLs_

    # Dock click / Finder double-click on the running app: bring the front
    # window forward, or open the placeholder if every window was closed.
    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        target = open_url(port, None)
        if state["ready"]:
            manager = state["windows"]
            if manager is None:
                webbrowser.open(target)
            else:
                from PyObjCTools import AppHelper

                AppHelper.callAfter(manager.reopen)
        elif target not in state["pending"]:
            state["pending"].append(target)
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
        if state["windows"] is not None:
            state["windows"].set_port(actual)
        if state["dock"] is not None:
            state["dock"].set_port(actual)
        if state["launcher"] is not None:
            state["launcher"].set_port(actual)
        # argv files join the queue BEFORE the ready flip so they dedupe
        # against the openFiles event AppKit already delivered for them.
        for f in argv_files + argv_urls:
            open_file(f)
        state["ready"] = True
        logger.info("server ready on port %s", actual)
        if state["dock"] is not None:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(state["dock"].server_ready)
            if os.environ.get("FUSED_RENDER_APP_DOCK_SHOW"):  # dev: screenshot the tray
                AppHelper.callAfter(state["dock"].show_popover)
        if state["launcher"] is not None:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(state["launcher"].server_ready)
            AppHelper.callAfter(state["launcher"].bind_hotkey)
            AppHelper.callAfter(state["launcher"].bind_pinned)
            if os.environ.get("FUSED_RENDER_APP_LAUNCHER_SHOW"):  # dev: show without the shortcut
                AppHelper.callAfter(state["launcher"].show)
        # The in-app updater (update/mac.py): a background manifest check
        # whose only surface is the launcher page's banner. No-op outside a
        # bundle, and guarded like everything else — no updates is a lesser
        # outcome than no app.
        try:
            mac_update.start()
        except Exception:  # noqa: BLE001
            logger.exception("update manager unavailable")
        pending, state["pending"] = state["pending"], []
        for target in pending:
            show(target)
        # The placeholder window, unless this launch was a document open.
        # FUSED_RENDER_APP_NO_BROWSER keeps its name: "open no surface at
        # startup", whatever the surface is.
        if not state["docs"] and not os.environ.get("FUSED_RENDER_APP_NO_BROWSER"):
            show(open_url(actual, None))

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

    class App(rumps.App):
        def __init__(self):
            super().__init__("Render App", icon=icon if os.path.isfile(icon) else None,
                             template=True, quit_button=None)
            self.menu = ["Open in app", "Open in browser", "Open app logs", "Quit"]

        @rumps.clicked("Open in app")
        def open_in_app(self, _sender):
            show_home()

        @rumps.clicked("Open in browser")
        def open_browser(self, _sender):
            webbrowser.open(open_url(port, None))

        @rumps.clicked("Open app logs")
        def open_logs(self, _sender):
            subprocess.run(["open", "-R", paths.log_path()], check=False)

        @rumps.clicked("Quit")
        def quit(self, _sender):
            quit_app(_sender)

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
                from fused_render_app import dock_store, jobnotify

                jobnotify.install(
                    state["windows"], paths.apps_dir(),
                    remembered_files=lambda: [a["file"] for a in dock_store.list_apps()])
            except Exception:  # noqa: BLE001
                logger.exception("job notifications unavailable")
        # The menu-bar Dock (menubar_dock.py) replaces rumps' menu with a
        # popover tray of pinned + recent apps; right-click keeps the old
        # entries. Needs the window manager (focus-or-open is its point).
        # Guarded: without it the rumps menu above stays as it was.
        if state["windows"] is not None:
            try:
                from fused_render_app.menubar_dock import DockController

                state["dock"] = DockController(
                    app._nsapp.nsstatusitem, port,
                    actions={"show_home": show_home,
                             # The launcher is built after the Dock: resolve at click time.
                             "show_launcher": lambda: state["launcher"] and state["launcher"].show(),
                             "open_browser": lambda: webbrowser.open(open_url(port, None)),
                             "open_logs": lambda: subprocess.run(
                                 ["open", "-R", paths.log_path()], check=False),
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
        # The launcher (launcher_panel.py): a Spotlight-like panel on a
        # global shortcut (⌥Space by default) that opens any known app.
        # Guarded like the Dock — no launcher is a lesser outcome than no app.
        if state["windows"] is not None:
            try:
                from fused_render_app.launcher_panel import LauncherController

                manager = state["windows"]

                def open_from_launcher(fs_path: str) -> None:
                    # Dock semantics; the panel is non-activating, so bring
                    # this app forward or the window opens behind the caller.
                    from AppKit import NSApp

                    NSApp.activateIgnoringOtherApps_(True)
                    manager.focus_or_open(fs_path)

                def home_from_launcher() -> None:
                    from AppKit import NSApp

                    NSApp.activateIgnoringOtherApps_(True)
                    manager.show_home()

                state["launcher"] = LauncherController(port, open_from_launcher, home_from_launcher)
                _install_launcher_hooks(state)
                if os.environ.get("FUSED_RENDER_APP_LAUNCHER_SHOW"):
                    # Dev only: SIGUSR2 toggles the launcher (SIGUSR1 is the
                    # Dock's); the breathing timer below keeps signals landing.
                    import signal

                    from PyObjCTools import AppHelper

                    signal.signal(signal.SIGUSR2, lambda *_: AppHelper.callAfter(
                        state["launcher"].toggle))
                    if not hasattr(app, "dock_dev_tick"):
                        app.dock_dev_tick = rumps.Timer(lambda _t: None, 0.5)
                        app.dock_dev_tick.start()
            except Exception:  # noqa: BLE001
                logger.exception("launcher unavailable")
                state["launcher"] = None
        threading.Thread(target=bootstrap, daemon=True).start()

    # Held on `app`: an unreferenced rumps.Timer is collected before it fires.
    app.boot_timer = rumps.Timer(kickoff, 0.1)
    app.boot_timer.start()
    app.run()


if __name__ == "__main__":
    main()
