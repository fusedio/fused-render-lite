"""py2app setup for FusedRenderLite.app.

Invoked by build_dmg.sh with FUSED_RENDER_ICNS set. Packaged the same way as
fused-render's FusedRender.app: the bundle carries the shell's own imports
(fused_render_lite, rumps, pyobjc Cocoa) plus the WHOLE standard library, not
the subset modulegraph happens to trace. The bundled `Contents/MacOS/python`
is the base interpreter every venv is built on (`uv sync --python <it>`), and
a venv inherits its stdlib — a traced subset surfaces as a missing `venv`,
`sqlite3` or `unittest` inside whichever third-party package imports it.
"""
import os
import re
import sys

from setuptools import setup

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

with open(os.path.join(REPO_ROOT, "fused_render_lite", "__init__.py")) as f:
    VERSION = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', f.read()).group(1)

ICONFILE = os.environ.get("FUSED_RENDER_ICNS")
if "py2app" in sys.argv and not (ICONFILE and os.path.isfile(ICONFILE)):
    sys.exit("FUSED_RENDER_ICNS must point at the generated .icns (build_dmg.sh sets it)")

APP = [os.path.join(SCRIPT_DIR, "app_entry.py")]

# Stdlib modules deliberately NOT shipped (name -> why), copied from fused-render.
STDLIB_EXCLUDED = {
    "tkinter": "the GUI is rumps/pyobjc, and build_dmg.sh prunes Tcl/Tk",
    "idlelib": "the bundled IDE; nothing in the app runs it",
    "turtle": "imports tkinter at module level, so it cannot work without it",
    "turtledemo": "demo suite for turtle",
    "ensurepip": "no pip in the bundle by design; uv builds every venv",
    "lib2to3": "2-to-3 dev tooling, removed upstream in 3.13",
    "antigravity": "opens a web browser at import time",
    "this": "an easter egg; the Zen of Python is not a dependency",
}


def _stdlib_split():
    """`(packages, includes)` covering the importable stdlib on this host."""
    import importlib.util

    packages, includes = [], []
    for name in sorted(sys.stdlib_module_names):
        if name in STDLIB_EXCLUDED or name.startswith("__"):
            continue
        if name in sys.builtin_module_names:
            continue  # compiled in; there is no file to carry
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue  # unresolvable here (a platform module for another OS)
        if spec is None:
            continue
        if spec.submodule_search_locations is not None:
            packages.append(name)
        else:
            includes.append(name)
    return packages, includes


STDLIB_PACKAGES, STDLIB_INCLUDES = _stdlib_split()

OPTIONS = {
    "argv_emulation": False,  # macapp.py owns AppKit file-open handling directly
    "iconfile": ICONFILE,
    "packages": ["fused_render_lite", "rumps"] + STDLIB_PACKAGES,
    "includes": STDLIB_INCLUDES,
    "resources": [os.path.join(REPO_ROOT, "fused_render_lite", "static")],
    # Third-party only: the stdlib is shipped whole (STDLIB_EXCLUDED is the
    # only list that trims it). PIL is imported lazily by runner-side modules
    # that only ever run inside a runner's own venv; the build venv has pillow
    # for the icon, and without this exclude py2app follows those imports and
    # ships 17 MB of pillow + libjpeg/libtiff/liblzma (which also fails strict
    # codesign).
    "excludes": ["setuptools", "pip", "PIL", "pillow", "packaging"],
    "no_report_missing_conditional_import": True,
    "plist": {
        "CFBundleIdentifier": "io.fused.render.lite",
        "CFBundleName": "FusedRenderLite",
        "CFBundleDisplayName": "FusedRenderLite",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0",
        "NSHighResolutionCapable": True,
        # Regular app: Dock icon + menu-bar item, so Finder can hand it files.
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Fused app",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Owner",
                "CFBundleTypeExtensions": ["fused"],
                "LSItemContentTypes": ["io.fused.render.app"],
            }
        ],
        # The .fused UTI, exported here so the Owner rank binds reliably.
        # Same identifier as full fused-render: they describe one format.
        "UTExportedTypeDeclarations": [
            {
                "UTTypeIdentifier": "io.fused.render.app",
                "UTTypeDescription": "Fused app",
                "UTTypeConformsTo": ["public.data"],
                "UTTypeTagSpecification": {"public.filename-extension": ["fused"]},
            }
        ],
        "NSDesktopFolderUsageDescription": "FusedRenderLite opens .fused apps from your Desktop.",
        "NSDocumentsFolderUsageDescription": "FusedRenderLite opens .fused apps from your Documents folder.",
        "NSDownloadsFolderUsageDescription": "FusedRenderLite opens .fused apps from your Downloads folder.",
    },
}

if __name__ == "__main__":
    setup(
        app=APP,
        name="FusedRenderLite",
        version=VERSION,
        options={"py2app": OPTIONS},
        setup_requires=["py2app"],
    )
