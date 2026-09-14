"""``fused-render-lite [FILE.fused] [--port N] [--no-browser]``

Runs the server in the foreground (dev, Linux/Windows). The macOS .app uses
``macapp.py`` instead, which owns a run loop for Finder open events.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import urllib.parse
import webbrowser

from fused_render_lite import __version__, paths, server


def open_url(port: int, file: str | None) -> str:
    base = f"http://127.0.0.1:{port}"
    if not file:
        return base + "/"
    return base + "/open?_file=" + urllib.parse.quote(os.path.abspath(file), safe="/")


def setup_logging(to_file: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if to_file:
        handlers.append(logging.FileHandler(paths.log_path(), encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fused-render-lite",
                                 description="Open a .fused single-file app.")
    ap.add_argument("file", nargs="?", help="a .fused file to open")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FUSED_RENDER_LITE_PORT", "2777")))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    setup_logging(to_file=False)

    srv, _thread = server.serve_in_thread(args.port)
    port = srv.server_address[1]
    url = open_url(port, args.file)
    print(f"fused-render-lite {__version__} at {url}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    stop = threading.Event()
    # SIGTERM (a plain `kill`, launchd, a supervisor) must evict resident model
    # workers like Ctrl-C does — otherwise they outlive the server.
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        while not stop.is_set() and _thread.is_alive():
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop_ai()
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
