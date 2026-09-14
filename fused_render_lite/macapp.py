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

from fused_render_lite import __version__, paths, server
from fused_render_lite.cli import open_url

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8765
BUNDLE_ID = "io.fused.render.lite"  # must match scripts/setup_py2app.py


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
    with open(paths.pid_path(), "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "port": port, "version": __version__}, f)


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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        handlers=[logging.FileHandler(paths.log_path(), encoding="utf-8")],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("fused-render-lite %s starting (pid %s)", __version__, os.getpid())

    # Files handed on argv (open -a … file, or a CLI-style launch).
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
        return

    import rumps  # macOS only

    port = pick_port()
    state = {"ready": False, "docs": False, "pending": [], "server": None, "windows": None}

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
        """Focus the front window, or open a placeholder window if none."""
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

    def application_openURLs_(self, _app, urls):
        for u in urls:
            raw = str(u.absoluteString())
            if raw.startswith("file://"):
                import urllib.parse

                open_file(urllib.parse.unquote(urllib.parse.urlsplit(raw).path))

    rumps.rumps.NSApp.application_openURLs_ = application_openURLs_

    # Dock click / Finder double-click on the running app: bring the front
    # window forward, or open the placeholder if every window was closed.
    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        target = open_url(port, None)
        if state["ready"]:
            show_home()
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
        # argv files join the queue BEFORE the ready flip so they dedupe
        # against the openFiles event AppKit already delivered for them.
        for f in argv_files:
            open_file(f)
        state["ready"] = True
        logger.info("server ready on port %s", actual)
        pending, state["pending"] = state["pending"], []
        for target in pending:
            show(target)
        # The placeholder window, unless this launch was a document open.
        # FUSED_RENDER_LITE_NO_BROWSER keeps its name: "open no surface at
        # startup", whatever the surface is.
        if not state["docs"] and not os.environ.get("FUSED_RENDER_LITE_NO_BROWSER"):
            show(open_url(actual, None))

    def quit_app(_sender) -> None:
        logger.info("quitting")
        server.stop_ai()  # evict resident models (kills worker processes), stop the warm claude
        srv = state.get("server")
        if srv is not None:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        _remove_pidfile()
        os._exit(0)

    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "menubar.png")

    class App(rumps.App):
        def __init__(self):
            super().__init__("Render Lite", icon=icon if os.path.isfile(icon) else None,
                             template=True, quit_button=None)
            self.menu = ["Show window", "Open in browser", "Open app logs", "Quit"]

        @rumps.clicked("Show window")
        def show_window(self, _sender):
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
            from fused_render_lite.mainwindow import WindowManager

            state["windows"] = WindowManager(port, quit=lambda: quit_app(None))
        except Exception:  # noqa: BLE001 — logged; browser fallback is the design
            logger.exception("windows unavailable; falling back to browser tabs")
        threading.Thread(target=bootstrap, daemon=True).start()

    # Held on `app`: an unreferenced rumps.Timer is collected before it fires.
    app.boot_timer = rumps.Timer(kickoff, 0.1)
    app.boot_timer.start()
    app.run()


if __name__ == "__main__":
    main()
