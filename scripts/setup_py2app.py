"""py2app setup for FusedRenderLite.app.

Invoked by build_dmg.sh with FUSED_RENDER_ICNS set. The bundle carries only
what the shell imports (modulegraph walks it): fused_render_lite, rumps,
pyobjc Cocoa, and the stdlib modules those reach. No data stack, no forced
whole-stdlib copy — app code never runs on this interpreter (env.py runs it
on a uv-managed CPython inside the app's own venv).
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

OPTIONS = {
    "iconfile": ICONFILE,
    "packages": ["fused_render_lite", "rumps"],
    "resources": [os.path.join(REPO_ROOT, "fused_render_lite", "static")],
    "excludes": ["tkinter", "idlelib", "turtle", "turtledemo", "test", "unittest",
                 "pydoc_data", "ensurepip", "lib2to3", "distutils", "setuptools", "pip"],
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
