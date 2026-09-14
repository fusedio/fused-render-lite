"""macOS app shell: a menu-bar item, the HTTP server on a thread, and Finder
document opens routed to the browser.

Launch order matters: the AppKit run loop starts first and the server boots
in the background after it, because ``application:openFiles:`` (a Finder
double-click on a .fused) is delivered once the run loop is up while the
server takes a moment. Files that arrive before readiness are queued.

A second launch (Finder opening a file while the app already runs) finds the
live server through ``~/.fused-render-lite/server.json``, hands it the file
via the browser and exits.
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
        logger.info("live server on port %s; forwarding and exiting", existing)
        for f in argv_files or [None]:
            webbrowser.open(open_url(existing, f))
        return

    import rumps  # macOS only

    port = pick_port()
    state = {"ready": False, "docs": False, "pending": [], "server": None}

    def open_file(fs_path: str) -> None:
        target = open_url(port, fs_path)
        state["docs"] = True
        if state["ready"]:
            logger.info("opening %s", target)
            webbrowser.open(target)
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

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        target = open_url(port, None)
        if state["ready"]:
            webbrowser.open(target)
        else:
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
        state["ready"] = True
        logger.info("server ready on port %s", actual)
        for f in argv_files:
            open_file(f)
        pending, state["pending"] = state["pending"], []
        for target in pending:
            webbrowser.open(target)
        if not state["docs"] and not os.environ.get("FUSED_RENDER_LITE_NO_BROWSER"):
            webbrowser.open(open_url(actual, None))

    def quit_app(_sender) -> None:
        logger.info("quitting")
        srv = state.get("server")
        if srv is not None:
            threading.Thread(target=srv.shutdown, daemon=True).start()
        _remove_pidfile()
        os._exit(0)

    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "menubar.png")

    class App(rumps.App):
        def __init__(self):
            super().__init__("fused-render-lite", icon=icon if os.path.isfile(icon) else None,
                             template=True, quit_button=None)
            self.menu = ["Open in browser", "Open app logs", "Quit"]

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
        threading.Thread(target=bootstrap, daemon=True).start()

    # Held on `app`: an unreferenced rumps.Timer is collected before it fires.
    app.boot_timer = rumps.Timer(kickoff, 0.1)
    app.boot_timer.start()
    app.run()


if __name__ == "__main__":
    main()
