"""py2app setup for RenderApp.app.

Invoked by build_dmg.sh with FUSED_RENDER_ICNS set. Packaged the same way as
fused-render's FusedRender.app: the bundle carries the shell's own imports
(fused_render_app, rumps, pyobjc Cocoa) plus the WHOLE standard library, not
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

with open(os.path.join(REPO_ROOT, "fused_render_app", "__init__.py")) as f:
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
    # Render App-only trims on top of fused-render's list (measured on the 0.5.6 DMG):
    "_tkinter": "the C half of tkinter; shipping it drags libtcl+libtk (6 MB) into Frameworks",
    "curses": "terminal UI; nothing in a .app has a terminal",
    "_curses": "C half of curses, pulls libncurses+libpanel (1.4 MB)",
    "_curses_panel": "see _curses",
    "_testcapi": "CPython's own C-API test fixture",
    "_testinternalcapi": "CPython's own C-API test fixture",
    "_testbuffer": "CPython test fixture",
    "_testclinic": "CPython test fixture",
    "_testimportmultiple": "CPython test fixture",
    "_testmultiphase": "CPython test fixture",
    "_testsinglephase": "CPython test fixture",
    "_xxtestfuzz": "CPython test fixture",
    "_ctypes_test": "ctypes' test fixture",
    "xxlimited": "limited-API example module",
    "xxlimited_35": "limited-API example module",
    "xxsubtype": "example module",
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


# --- the fused engine ([fused] extra) --------------------------------------
# Derived from the build venv the way fused-render's setup derives [bundled]:
# the transitive closure of the extra's distributions, split by what each
# top-level import name IS on disk. py2app treats the two differently and
# getting it wrong is silent: a C extension forced via `packages` is copied as
# `lib/python3.12/<name>.py`, which shadows the real thing (`_duckdb` is the
# canonical case). Empty lists when the extra is not installed, so a build
# venv without it still packages a fused-less app.

# Top-level names that must never be forced via `packages`: PEP 420 namespace
# packages (no `__init__.py`) break py2app's package bootstrap.
NEVER_FORCE_AS_PACKAGE = {"__pycache__"}
# Bare C extensions that belong in `includes`, never `packages`.
FORCE_AS_INCLUDE = {"_duckdb", "_cffi_backend"}


def _norm_dist(name):
    return name.lower().replace("_", "-")


def _req_name(requirement):
    name = requirement.split("[")[0]
    for sep in ("<", ">", "=", "!", "~", " ", "(", ";"):
        name = name.split(sep)[0]
    return _norm_dist(name.strip())


def _fused_distributions():
    """The distributions `[fused]` declares (names only)."""
    import tomllib

    with open(os.path.join(REPO_ROOT, "pyproject.toml"), "rb") as fh:
        pyproject = tomllib.load(fh)
    declared = pyproject["project"]["optional-dependencies"].get("fused", [])
    return [_req_name(d) for d in declared]


def _runtime_requires(dist):
    """Runtime deps of `dist`, skipping extras and unsatisfied markers."""
    out = []
    for raw in dist.requires or []:
        spec = raw
        if ";" in raw:
            head, marker = raw.split(";", 1)
            if "extra" in marker:
                continue  # optional extra: not installed, not needed
            try:
                from packaging.markers import Marker

                if not Marker(marker.strip()).evaluate():
                    continue
            except Exception:
                pass
            spec = head
        name = _req_name(spec)
        if name:
            out.append(name)
    return out


def fused_force_lists():
    """`(packages, includes)` contributed by `[fused]`, derived from the venv."""
    import importlib.metadata as importlib_metadata
    import sysconfig

    _paths = sysconfig.get_paths()
    site_dirs = []
    for scheme in ("purelib", "platlib"):
        d = _paths.get(scheme)
        if d and d not in site_dirs:
            site_dirs.append(d)
    installed = {}
    for dist in importlib_metadata.distributions():
        name = dist.metadata["Name"] if dist.metadata else None
        if name:
            installed[_norm_dist(name)] = dist

    seen, stack = set(), list(_fused_distributions())
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        dist = installed.get(name)
        if dist is None:
            continue  # not in the build venv: marker-gated, extra-only, or [fused] absent
        seen.add(name)
        stack.extend(_runtime_requires(dist))

    top_level = {}
    for import_name, dist_names in importlib_metadata.packages_distributions().items():
        for dist_name in dist_names:
            top_level.setdefault(_norm_dist(dist_name), set()).add(import_name)

    packages, includes = set(), set()
    for name in seen:
        for import_name in top_level.get(name, ()):
            if import_name in NEVER_FORCE_AS_PACKAGE:
                continue
            if import_name in FORCE_AS_INCLUDE:
                includes.add(import_name)
                continue
            path = next(
                (p for p in (os.path.join(s, import_name) for s in site_dirs)
                 if os.path.isdir(p)),
                None,
            )
            if path is not None:
                if os.path.exists(os.path.join(path, "__init__.py")):
                    packages.add(import_name)
                # else: a namespace package -- skip rather than break the bootstrap
            else:
                includes.add(import_name)
    return sorted(packages), sorted(includes)


FUSED_PACKAGES, FUSED_INCLUDES = fused_force_lists()

OPTIONS = {
    "argv_emulation": False,  # macapp.py owns AppKit file-open handling directly
    "iconfile": ICONFILE,
    # WebKit named explicitly: pyobjc framework packages are half C
    # extension, half `_metadata.py`, and a traced import can leave the
    # metadata behind — which surfaces as delegate blocks arriving without a
    # signature inside the built app only.
    "packages": ["fused_render_app", "rumps", "WebKit"] + STDLIB_PACKAGES + FUSED_PACKAGES,
    "includes": STDLIB_INCLUDES + FUSED_INCLUDES,
    "resources": [os.path.join(REPO_ROOT, "fused_render_app", "static")],
    # Third-party only: the stdlib is shipped whole (STDLIB_EXCLUDED is the
    # only list that trims it). PIL is imported lazily by runner-side modules
    # that only ever run inside a runner's own venv; the build venv has pillow
    # for the icon, and without this exclude py2app follows those imports and
    # ships 17 MB of pillow + libjpeg/libtiff/liblzma (which also fails strict
    # codesign). `packaging` is no longer excluded: the fused engine requires
    # it (and the derivation above lists it when [fused] is installed).
    "excludes": ["setuptools", "pip", "PIL", "pillow"],
    "no_report_missing_conditional_import": True,
    "plist": {
        "CFBundleIdentifier": "io.fused.render.app",
        "CFBundleName": "RenderApp",
        "CFBundleDisplayName": "Render App",
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
        # "Open in Render App" web links: render-app://open?url=<http(s) link
        # to a .fused>. Delivered to application:openURLs: (macapp.py), which
        # hands the http(s) target to the open page; fetch.py downloads it.
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "Render App link",
                "CFBundleURLSchemes": ["render-app"],
                "CFBundleTypeRole": "Viewer",
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
        "NSDesktopFolderUsageDescription": "Render App opens .fused apps from your Desktop.",
        "NSDocumentsFolderUsageDescription": "Render App opens .fused apps from your Documents folder.",
        "NSDownloadsFolderUsageDescription": "Render App opens .fused apps from your Downloads folder.",
        # Pages run inside the app's own WKWebView (mainwindow.py), so a
        # .fused app's getUserMedia is THIS process asking for the camera or
        # microphone. Without the usage string the OS kills the app instead
        # of prompting.
        "NSCameraUsageDescription": "Render App uses the camera when a .fused app you opened asks for it.",
        "NSMicrophoneUsageDescription": "Render App uses the microphone when a .fused app you opened asks for it.",
        # Same for navigator.geolocation: mainwindow.py grants our own pages,
        # then CoreLocation asks the OS, and without these strings that
        # second prompt never appears (the request just fails).
        "NSLocationUsageDescription": "Render App uses your location when a .fused app you opened asks for it.",
        "NSLocationWhenInUseUsageDescription": "Render App uses your location when a .fused app you opened asks for it.",
    },
}

if __name__ == "__main__":
    setup(
        app=APP,
        name="RenderApp",
        version=VERSION,
        options={"py2app": OPTIONS},
        setup_requires=["py2app"],
    )
