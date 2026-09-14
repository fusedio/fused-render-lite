#!/usr/bin/env bash
# Build RenderLite.app + a DMG via py2app.
#
#   framework python -> wheel -> build venv (wheel[app] + py2app + dmgbuild)
#   -> icon -> py2app -> prune -> Contents/lib symlink -> sanity probes
#   -> bundle uv -> bundle the apple tier helper -> minos floor check
#   -> codesign -> dmgbuild -> [notarize] -> hygiene
#
# Same pipeline as fused-render's scripts/build_dmg.sh, step for step, minus
# what lite does not ship (rclone, the fused CLI, staged packages). The one
# deliberate difference: signing defaults to ad-hoc unless an identity is given
# or FUSED_RENDER_SIGN=1 asks for keychain auto-detection (main always
# auto-detects; that prompts on a dev keychain). CI always passes the identity.
#
# The Python packaging mirrors fused-render's build_dmg.sh exactly: py2app
# ships a REAL interpreter at Contents/MacOS/python (sys.executable in the
# running app), the whole stdlib (setup_py2app.py), and a relative
# Contents/lib -> Resources/lib symlink so that interpreter self-locates with
# NO environment variables. That last point is load-bearing: every venv the
# app builds is `uv sync --python Contents/MacOS/python` with PYTHONHOME
# scrubbed, and without the symlink that python resolves sys.prefix to the
# BUILD MACHINE's framework and dies with "No module named 'encodings'".
#
# Env:
#   FUSED_RENDER_FRAMEWORK_PYTHON  a framework-build python3 (py2app needs one)
#   FUSED_RENDER_APPLE_HELPER_SRC  prebuilt fused-apple-ai (default: built here when
#                                  the selected Xcode has the macOS 26 SDK)
#   FUSED_RENDER_MACOS_FLOOR       oldest macOS the bundle may require (default 14.0)
#   FUSED_RENDER_SIGN=1               Developer ID signing (auto-detect identity); default is ad-hoc
#   FUSED_RENDER_CODESIGN_IDENTITY  explicit identity ("-" = ad-hoc)
#   FUSED_RENDER_SKIP_CODESIGN=1   no signing at all (measurement builds)
#   FUSED_RENDER_NOTARY_PROFILE    notarytool keychain profile -> notarize + staple
set -euo pipefail

_build_failed() {
  local status=$?
  echo "" >&2
  echo "FATAL: build_dmg.sh failed at line ${BASH_LINENO[0]:-?} (exit $status)" >&2
  echo "       command: ${BASH_COMMAND}" >&2
  if [[ $status -gt 128 ]]; then
    echo "       exit > 128 means KILLED BY SIGNAL $((status - 128)) — most likely" >&2
    echo "       the OS reclaiming memory, not a bug in the command itself." >&2
  fi
  echo "       disk:" >&2
  df -h "${BUILD_DIR:-$PWD}" >&2 2>/dev/null || true
  exit "$status"
}
trap _build_failed ERR

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="RenderLite"
VERSION="$(python3 -c "
import re
print(re.search(r'(?m)^__version__\s*=\s*\"([^\"]+)\"', open('${REPO_ROOT}/fused_render_lite/__init__.py').read()).group(1))
")"
BUILD_DIR="$REPO_ROOT/build"
DIST_DIR="$REPO_ROOT/dist"
BUILD_VENV="$BUILD_DIR/py2app-venv"
PY2APP_DIST="$BUILD_DIR/py2app-dist"
ICNS_PATH="$BUILD_DIR/${APP_NAME}.icns"
APP_DIR="$PY2APP_DIST/${APP_NAME}.app"
DMG_PATH="$DIST_DIR/${APP_NAME}-${VERSION}.dmg"

echo "==> fused-render-lite ${VERSION} -> ${APP_NAME}.app -> ${DMG_PATH##*/}"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# --- 1. a framework-build python ------------------------------------------
# py2app copies the interpreter's Python.framework into the bundle; a
# non-framework build (uv/pyenv install_only, plain --enable-shared) has none.
# The python.org-style install at /Library/Frameworks is preferred: built
# against an old deployment target, so the bundle runs on older macOS too.
# Homebrew's python@3.12 is a framework build as well but a per-OS bottle.
PORTABLE="/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
BREW="/opt/homebrew/opt/python@3.12/bin/python3.12"
if [[ -n "${FUSED_RENDER_FRAMEWORK_PYTHON:-}" ]]; then
  FRAMEWORK_PYTHON="$FUSED_RENDER_FRAMEWORK_PYTHON"
elif [[ -x "$PORTABLE" ]]; then
  FRAMEWORK_PYTHON="$PORTABLE"
elif [[ -x "$BREW" ]]; then
  FRAMEWORK_PYTHON="$BREW"
  if [[ -n "${FUSED_RENDER_CODESIGN_IDENTITY:-}" && "${FUSED_RENDER_CODESIGN_IDENTITY}" != "-" ]]; then
    echo "FATAL: a release build must not use Homebrew's per-OS python bottle; install python.org 3.12" >&2
    exit 1
  fi
else
  echo "==> installing python@3.12 via Homebrew (dev fallback)"
  brew install python@3.12
  FRAMEWORK_PYTHON="$BREW"
fi
# Resolve a toolcache symlink (setup-python's) to the framework it points into,
# so py2app copies the real Python.framework tree rather than a link farm.
FRAMEWORK_PYTHON="$("$FRAMEWORK_PYTHON" -c "import os, sys; print(os.path.realpath(sys.executable))")"
if [[ -z "$("$FRAMEWORK_PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("PYTHONFRAMEWORK") or "")')" ]]; then
  echo "FATAL: $FRAMEWORK_PYTHON is not a framework build" >&2
  exit 1
fi
echo "    python: $FRAMEWORK_PYTHON ($("$FRAMEWORK_PYTHON" --version))"

# --- 2. build venv + wheel -------------------------------------------------
if [[ ! -x "$BUILD_VENV/bin/python" ]]; then
  echo "==> creating build venv"
  "$FRAMEWORK_PYTHON" -m venv "$BUILD_VENV"
fi
"$BUILD_VENV/bin/pip" install --quiet --upgrade pip
echo "==> building wheel"
rm -f "$DIST_DIR"/*.whl
"$BUILD_VENV/bin/pip" install --quiet --upgrade build
"$BUILD_VENV/bin/python" -m build --quiet --wheel --outdir "$DIST_DIR" "$REPO_ROOT"
WHEEL_PATH="$(ls "$DIST_DIR"/*.whl)"
echo "==> installing ${WHEEL_PATH##*/} [app] + py2app + dmgbuild + pillow into the build venv"
"$BUILD_VENV/bin/pip" install --quiet "${WHEEL_PATH}[app]" py2app dmgbuild pillow
# The build venv is reused across builds; make sure THIS wheel's code is what
# py2app copies, not a cached earlier install of the same version number.
"$BUILD_VENV/bin/pip" install --quiet --force-reinstall --no-deps --no-cache-dir "${WHEEL_PATH}"

# --- 3. icon -------------------------------------------------------------
echo "==> generating app icon"
ICONSET_DIR="$BUILD_DIR/${APP_NAME}.iconset"
rm -rf "$ICONSET_DIR" "$ICNS_PATH"
mkdir -p "$ICONSET_DIR"
"$BUILD_VENV/bin/python" - "$ICONSET_DIR" <<'PYEOF'
import math, sys
from PIL import Image, ImageDraw
out = sys.argv[1]
C = 1024 * 4
bg = Image.new("RGBA", (C, C), (0, 0, 0, 0))
d = ImageDraw.Draw(bg)
m = C * 0.06
d.rounded_rectangle([m, m, C - m, C - m], radius=C * 0.22, fill=(27, 29, 33, 255))
cx = cy = C / 2
tips = [(-90, C * 0.34), (0, C * 0.34), (90, C * 0.34), (180, C * 0.34)]
waists = [(-45, C * 0.09), (45, C * 0.09), (135, C * 0.09), (-135, C * 0.09)]
def pt(a, r):
    a = math.radians(a); return (cx + r * math.cos(a), cy + r * math.sin(a))
poly = []
for i in range(4):
    t0, t1, c = pt(*tips[i]), pt(*tips[(i + 1) % 4]), pt(*waists[i])
    poly.append(t0)
    for k in range(1, 12):
        t = k / 12
        poly.append(((1-t)**2*t0[0] + 2*(1-t)*t*c[0] + t**2*t1[0],
                     (1-t)**2*t0[1] + 2*(1-t)*t*c[1] + t**2*t1[1]))
d.polygon(poly, fill=(229, 255, 68, 255))
# "lite": hollow the glyph's centre
d.ellipse([cx - C*0.075, cy - C*0.075, cx + C*0.075, cy + C*0.075], fill=(27, 29, 33, 255))
for s in (16, 32, 128, 256, 512):
    bg.resize((s, s), Image.LANCZOS).save(f"{out}/icon_{s}x{s}.png")
    bg.resize((s*2, s*2), Image.LANCZOS).save(f"{out}/icon_{s}x{s}@2x.png")
PYEOF
iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"

# --- 4. py2app -------------------------------------------------------------
echo "==> running py2app"
rm -rf "$PY2APP_DIST" "$BUILD_DIR/py2app-build"
(cd "$BUILD_DIR" && FUSED_RENDER_ICNS="$ICNS_PATH" "$BUILD_VENV/bin/python" "$REPO_ROOT/scripts/setup_py2app.py" py2app \
    --dist-dir "$PY2APP_DIST" --bdist-base "$BUILD_DIR/py2app-build" >"$BUILD_DIR/py2app.log" 2>&1) \
  || { tail -40 "$BUILD_DIR/py2app.log" >&2; exit 1; }
test -d "$APP_DIR"

# --- 4a. prune dead weight (same sweep as fused-render) ----------------------
echo "==> pruning bundle dead weight"
PRUNE_PYLIB="$APP_DIR/Contents/Resources/lib/python3.12"
PRUNE_FRAMEWORK="$APP_DIR/Contents/Frameworks/Python.framework"
find "$PRUNE_PYLIB" -type d \( -name tests -o -name test \) -prune -exec rm -rf {} +
find "$APP_DIR/Contents/Resources/lib" -type d -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$PRUNE_PYLIB/pip" "$PRUNE_PYLIB/setuptools" "$PRUNE_PYLIB/wheel" \
       "$PRUNE_PYLIB/pkg_resources" "$PRUNE_PYLIB/PyObjCTest" \
       "$PRUNE_PYLIB/_distutils_hack" "$PRUNE_PYLIB/distutils-precedence.pth"
FW_LIB="$PRUNE_FRAMEWORK/Versions/3.12/lib/python3.12"
rm -rf "$FW_LIB/test" "$FW_LIB/idlelib" "$FW_LIB/ensurepip" \
       "$FW_LIB/lib2to3" "$FW_LIB/tkinter" \
       "$FW_LIB/site-packages/pip" "$FW_LIB/site-packages/setuptools" \
       "$FW_LIB/site-packages/wheel" \
       "$FW_LIB/site-packages/_distutils_hack" \
       "$FW_LIB/site-packages/distutils-precedence.pth"
rm -rf "$PRUNE_FRAMEWORK/Versions/3.12/include" \
       "$PRUNE_FRAMEWORK/Versions/3.12/Headers" \
       "$PRUNE_FRAMEWORK/Versions/3.12/share" \
       "$PRUNE_FRAMEWORK/Headers"
rm -rf "$PRUNE_FRAMEWORK"/Versions/3.12/lib/tcl* "$PRUNE_FRAMEWORK"/Versions/3.12/lib/tk* \
       "$FW_LIB"/lib-dynload/_tkinter*
find "$PRUNE_FRAMEWORK" -type d -name __pycache__ -prune -exec rm -rf {} +
# py2app copies lib-dynload wholesale — setup_py2app.STDLIB_EXCLUDED does not
# reach these .so files — and then walks their dylib deps into Contents/Frameworks.
# Lite-only trim (measured on the 0.5.6 DMG: Tcl/Tk 6 MB, ncurses 1.4 MB,
# CPython test fixtures 1.3 MB): GUI, terminal UI and test modules nothing in a
# .app or a venv built from it can use.
DYNLOAD="$PRUNE_PYLIB/lib-dynload"
rm -f "$DYNLOAD"/_tkinter* "$DYNLOAD"/_curses* "$DYNLOAD"/_test* "$DYNLOAD"/_xxtestfuzz* \
      "$DYNLOAD"/_ctypes_test* "$DYNLOAD"/xxlimited* "$DYNLOAD"/xxsubtype*
rm -f "$APP_DIR"/Contents/Frameworks/libtcl* "$APP_DIR"/Contents/Frameworks/libtk* \
      "$APP_DIR"/Contents/Frameworks/libncurses* "$APP_DIR"/Contents/Frameworks/libpanel* \
      "$APP_DIR"/Contents/Frameworks/libformw* "$APP_DIR"/Contents/Frameworks/libmenuw*

echo "==> stripping debug symbols from bundled dylibs"
find "$APP_DIR" -type f \( -name '*.so' -o -name '*.dylib' \) \
  -exec sh -c 'for f do strip -S -x "$f" 2>/dev/null || true; done' _ {} +

# --- 4a-ter. self-locating interpreter: Contents/lib -> Resources/lib ---------
# py2app puts the runtime under Contents/Resources/lib but the interpreter at
# Contents/MacOS/python; CPython's prefix search looks for <prefix>/lib/python3.12
# next to the executable's parent, misses, and falls back to the prefix compiled
# into the binary — the build machine's framework. One RELATIVE symlink makes
# the landmark resolve inside the .app. Before signing, so it is sealed in.
echo "==> making the bundled interpreter self-locating (Contents/lib -> Resources/lib)"
test -d "$APP_DIR/Contents/Resources/lib" || { echo "FATAL: py2app layout changed; no Contents/Resources/lib" >&2; exit 1; }
ln -sfn "Resources/lib" "$APP_DIR/Contents/lib"

# --- 4b. sanity probes (the regression guards fused-render runs) -------------
echo "==> bundle sanity: interpreter self-locates with PYTHONHOME stripped"
SELFLOC_OUT="$(env -u PYTHONHOME -u PYTHONPATH -u VIRTUAL_ENV \
  "$APP_DIR/Contents/MacOS/python" -c '
import sys
import fused_render_lite
print("prefix", sys.prefix)
print("selflocating OK", fused_render_lite.__version__)
' 2>&1 || true)"
if ! echo "$SELFLOC_OUT" | grep -q "^selflocating OK"; then
  echo "FATAL: the bundled interpreter cannot run without PYTHONHOME:" >&2
  echo "$SELFLOC_OUT" >&2
  exit 1
fi
SELFLOC_PREFIX="$(echo "$SELFLOC_OUT" | sed -n 's/^prefix //p')"
if [[ "$SELFLOC_PREFIX" != "$APP_DIR"* ]]; then
  echo "FATAL: the bundled interpreter's sys.prefix is OUTSIDE the app: $SELFLOC_PREFIX" >&2
  echo "       (that is the BUILD MACHINE's python; every venv built from it would be dead on arrival)" >&2
  exit 1
fi
echo "    $(echo "$SELFLOC_OUT" | tail -1) (prefix $SELFLOC_PREFIX)"

echo "==> bundle sanity: the bundled stdlib is complete"
STDLIB_EXPECTED="$("$BUILD_VENV/bin/python" -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import setup_py2app as s
print(','.join(sorted(set(s.STDLIB_PACKAGES) | set(s.STDLIB_INCLUDES))))
")"
[[ -n "$STDLIB_EXPECTED" ]] || { echo "FATAL: setup_py2app.py named no stdlib modules to ship" >&2; exit 1; }
STDLIB_CHECK="$BUILD_DIR/stdlib_check.py"
cat > "$STDLIB_CHECK" <<'STDLIBEOF'
import importlib
import os
import sys

names = os.environ["STDLIB_EXPECTED"].split(",")
missing = []
for name in names:
    try:
        importlib.import_module(name)
    except BaseException as exc:  # noqa: BLE001 - the report IS the product
        missing.append("%s: %s: %s" % (name, exc.__class__.__name__, exc))
print("checked %d, missing %d, prefix %s" % (len(names), len(missing), sys.prefix))
for line in missing[:25]:
    print("   ", line)
STDLIBEOF
for STDLIB_WHO in bundled venv; do
  if [[ "$STDLIB_WHO" == "bundled" ]]; then
    STDLIB_PY="$APP_DIR/Contents/MacOS/python"
  else
    rm -rf "$BUILD_DIR/stdlib-venv"
    if ! env -u PYTHONHOME -u PYTHONPATH -u VIRTUAL_ENV \
        "$APP_DIR/Contents/MacOS/python" -m venv --without-pip "$BUILD_DIR/stdlib-venv" >/dev/null 2>&1; then
      echo "FATAL: the bundled interpreter cannot create a venv at all — every app env is built exactly that way." >&2
      exit 1
    fi
    STDLIB_PY="$BUILD_DIR/stdlib-venv/bin/python"
  fi
  STDLIB_OUT="$(env -u PYTHONHOME -u PYTHONPATH -u VIRTUAL_ENV STDLIB_EXPECTED="$STDLIB_EXPECTED" \
    "$STDLIB_PY" "$STDLIB_CHECK" 2>&1 || true)"
  if ! echo "$STDLIB_OUT" | grep -q ", missing 0,"; then
    echo "FATAL: the $STDLIB_WHO interpreter is missing stdlib modules:" >&2
    echo "$STDLIB_OUT" >&2
    exit 1
  fi
  echo "    $STDLIB_WHO: $(echo "$STDLIB_OUT" | head -1)"
done
rm -rf "$BUILD_DIR/stdlib-venv" "$STDLIB_CHECK"

# --- 4c. Mach-O-as-.py check (py2app mis-copies a bare C extension as .py) ----
echo "==> bundle sanity: Mach-O-as-.py check"
APP_PYLIB="$APP_DIR/Contents/Resources/lib/python3.12"
BAD_PY=""
while IFS= read -r -d '' f; do
  BAD_PY+="$f"$'\n'
done < <(
  find "$APP_PYLIB" -name '*.py' -size +1M -exec sh -c '
    for f do
      case "$(head -c4 "$f" | xxd -p)" in
        cffaedfe|cafebabe|feedfacf) printf "%s\0" "$f" ;;
      esac
    done
  ' _ {} +
)
if [[ -n "$BAD_PY" ]]; then
  echo "FATAL: Mach-O binary shipped as .py (would shadow the real extension):" >&2
  echo "$BAD_PY" >&2
  exit 1
fi

# --- 4d. bundle uv (as fused-render does) ----------------------------------
# NOT a convenience: the bundle has no pip/ensurepip, so every environment the
# app builds (app pyproject, legacy set, AI runners) is `uv sync`. env.uv_bin
# looks here first; the download path is only the from-source fallback.
echo "==> bundling uv"
UV_SRC="$(command -v uv || true)"
if [[ -z "$UV_SRC" ]]; then
  echo "FATAL: uv not found on PATH, but the bundle needs it: the app cannot build" >&2
  echo "       a venv without it (no venv/ensurepip/pip in this bundle)." >&2
  echo "       Install uv (https://docs.astral.sh/uv/) and re-run." >&2
  exit 1
fi
UV_DEST="$APP_DIR/Contents/Resources/bin/uv"
mkdir -p "$(dirname "$UV_DEST")"
cp "$UV_SRC" "$UV_DEST"
chmod +x "$UV_DEST"
UV_SMOKE_OUT="$("$UV_DEST" --version || true)"
if ! echo "$UV_SMOKE_OUT" | grep -q "^uv "; then
  echo "FATAL: bundled uv failed to report its version:" >&2
  echo "$UV_SMOKE_OUT" >&2
  exit 1
fi
echo "    $UV_SMOKE_OUT"

# --- 4e. the apple tier's Swift helper (fused_render_lite/ai/apple/) ---------
# Lands in Contents/MacOS beside the interpreter, where ai/apple/host.py looks.
echo "==> bundling the apple tier helper"
APPLE_HELPER_DEST="$APP_DIR/Contents/MacOS/fused-apple-ai"
APPLE_HELPER_SRC="${FUSED_RENDER_APPLE_HELPER_SRC:-}"
if [[ -z "$APPLE_HELPER_SRC" ]]; then
  APPLE_SDK_MAJOR="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null | cut -d. -f1 || echo 0)"
  if [[ "${APPLE_SDK_MAJOR:-0}" -ge 26 ]]; then
    APPLE_HELPER_SRC="$BUILD_DIR/fused-apple-ai"
    bash "$REPO_ROOT/scripts/build_apple_helper.sh" "$APPLE_HELPER_SRC"
  fi
fi
if [[ -n "$APPLE_HELPER_SRC" && -f "$APPLE_HELPER_SRC" ]]; then
  cp "$APPLE_HELPER_SRC" "$APPLE_HELPER_DEST"
  chmod +x "$APPLE_HELPER_DEST"
  echo "    $(file -b "$APPLE_HELPER_DEST" | cut -c1-80)"
elif [[ -n "${FUSED_RENDER_CODESIGN_IDENTITY:-}" && "${FUSED_RENDER_CODESIGN_IDENTITY}" != "-" ]]; then
  echo "FATAL: a release build needs the apple tier helper; select an Xcode 26 (xcode-select -s)" >&2
  echo "       or set FUSED_RENDER_APPLE_HELPER_SRC to a binary built by scripts/build_apple_helper.sh" >&2
  exit 1
else
  echo "    skipped: no prebuilt helper and no macOS 26 SDK here (the apple tier will report itself unavailable)"
fi

# --- 4f. every Mach-O must load on the oldest macOS the bundle claims ---------
# A per-OS Homebrew python bottle or a too-new wheel tag shows up here, not on
# a user's older Mac. The helper is exempt: it targets macOS 26 by design and
# host.py reports the tier unavailable below that.
MINOS_FLOOR="${FUSED_RENDER_MACOS_FLOOR:-14.0}"
MINOS_EXEMPT=("Contents/MacOS/fused-apple-ai")
echo "==> bundle sanity: no Mach-O requires a macOS newer than ${MINOS_FLOOR}"
for rel in "${MINOS_EXEMPT[@]}"; do
  if [[ -f "$APP_DIR/$rel" ]]; then
    v="$(otool -l "$APP_DIR/$rel" 2>/dev/null | awk '/LC_BUILD_VERSION/{b=1} b&&/minos/{print $2; exit}')"
    echo "    exempt: $rel (minos ${v:-?})"
  fi
done
MINOS_REPORT="$(find "$APP_DIR" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -u+x \) \
    ! -path "$APP_DIR/Contents/MacOS/fused-apple-ai" -print0 \
  | xargs -0 -n 64 sh -c 'for f do
      case "$(head -c 4 "$f" | od -An -tx1 | tr -d " \n")" in
        cffaedfe|cafebabe|feedfacf) ;;
        *) continue ;;
      esac
      v="$(otool -l "$f" 2>/dev/null | awk "/LC_BUILD_VERSION/{b=1} b&&/minos/{print \$2; exit} /LC_VERSION_MIN_MACOSX/{m=1} m&&/version/{print \$2; exit}")"
      [ -n "$v" ] && printf "%s %s\n" "$v" "$f"
    done' _ | sort -t. -k1,1n -k2,2n | tail -5)"
MINOS_MAX="$(echo "$MINOS_REPORT" | tail -1 | cut -d" " -f1)"
echo "    highest minos in the bundle: ${MINOS_MAX:-none} (floor ${MINOS_FLOOR})"
if [[ -n "$MINOS_MAX" ]] && [[ "$(printf '%s\n%s\n' "$MINOS_FLOOR" "$MINOS_MAX" | sort -t. -k1,1n -k2,2n | tail -1)" != "$MINOS_FLOOR" ]]; then
  echo "FATAL: the bundle carries code that will not load below macOS ${MINOS_MAX}:" >&2
  echo "$MINOS_REPORT" | sed "s|$APP_DIR/||; s|^|       |" >&2
  echo "       Either the interpreter is a per-OS bottle (see step 1) or pip picked a newer" >&2
  echo "       wheel tag on this host. Fix the source, do not raise the floor." >&2
  exit 1
fi

echo "==> app size: $(du -sh "$APP_DIR" | cut -f1)"

# --- 5. codesign -----------------------------------------------------------
# Default is ad-hoc (local use). Set FUSED_RENDER_SIGN=1 (or a
# FUSED_RENDER_CODESIGN_IDENTITY) for Developer ID + hardened runtime.
SIGN_IDENTITY=""
if [[ "${FUSED_RENDER_SKIP_CODESIGN:-}" == "1" ]]; then
  echo "==> FUSED_RENDER_SKIP_CODESIGN set -> unsigned"
elif [[ "${FUSED_RENDER_SIGN:-}" != "1" && -z "${FUSED_RENDER_CODESIGN_IDENTITY:-}" ]]; then
  echo "==> ad-hoc signing (set FUSED_RENDER_SIGN=1 for Developer ID)"
  codesign --force --deep -s - "$APP_DIR"
else
  KC_OPT=""
  [[ -n "${FUSED_RENDER_CODESIGN_KEYCHAIN:-}" ]] && KC_OPT="--keychain $FUSED_RENDER_CODESIGN_KEYCHAIN"
  if [[ "${FUSED_RENDER_CODESIGN_IDENTITY:-}" == "-" ]]; then
    SIGN_IDENTITY=""  # explicit ad-hoc: skips keychain lookup (and its UI prompt)
  elif [[ -n "${FUSED_RENDER_CODESIGN_IDENTITY:-}" ]]; then
    SIGN_IDENTITY="$FUSED_RENDER_CODESIGN_IDENTITY"
  else
    LINES="$(security find-identity -v -p codesigning ${FUSED_RENDER_CODESIGN_KEYCHAIN:-} 2>/dev/null | grep 'Developer ID Application' || true)"
    COUNT="$(printf '%s' "$LINES" | grep -c 'Developer ID Application' || true)"
    if [[ "$COUNT" -eq 1 ]]; then
      SIGN_IDENTITY="$(printf '%s\n' "$LINES" | sed -E 's/^ *[0-9]+\) +([0-9A-Fa-f]+) .*/\1/')"
    elif [[ "$COUNT" -gt 1 ]]; then
      echo "FATAL: several Developer ID identities; set FUSED_RENDER_CODESIGN_IDENTITY" >&2; exit 1
    fi
  fi
  if [[ -n "$SIGN_IDENTITY" ]]; then
    echo "==> Developer ID signing (hardened runtime): $SIGN_IDENTITY"
    ENTITLEMENTS="$BUILD_DIR/entitlements.plist"
    cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
</dict>
</plist>
PLIST
    while IFS= read -r -d '' macho; do
      codesign --force --options runtime --timestamp --entitlements "$ENTITLEMENTS" $KC_OPT -s "$SIGN_IDENTITY" "$macho"
    done < <(find "$APP_DIR" -type f -exec sh -c '
      for f do case "$(head -c4 "$f" | xxd -p)" in
        cffaedfe|cafebabe|feedfacf|feedface|cefaedfe|bebafeca) printf "%s\0" "$f" ;; esac; done' _ {} +)
    codesign --force --options runtime --timestamp --entitlements "$ENTITLEMENTS" $KC_OPT -s "$SIGN_IDENTITY" "$APP_DIR"
    codesign --verify --strict --verbose=2 "$APP_DIR"
  else
    echo "==> no Developer ID identity -> ad-hoc signing (local use only)"
    codesign --force --deep -s - "$APP_DIR"
  fi
fi

# --- 6. dmg ----------------------------------------------------------------
echo "==> building dmg"
SETTINGS="$BUILD_DIR/dmgbuild_settings.py"
cat > "$SETTINGS" <<'PYEOF'
import os
application = defines.get("app")  # noqa: F821
files = [application]
symlinks = {"Applications": "/Applications"}
format = "ULFO"
PYEOF
rm -f "$DMG_PATH"
"$BUILD_VENV/bin/dmgbuild" -s "$SETTINGS" -D app="$APP_DIR" "$APP_NAME" "$DMG_PATH"

if [[ -n "${FUSED_RENDER_NOTARY_PROFILE:-}" ]]; then
  if [[ "${FUSED_RENDER_SKIP_CODESIGN:-}" == "1" ]]; then
    echo "FATAL: FUSED_RENDER_NOTARY_PROFILE and FUSED_RENDER_SKIP_CODESIGN are both set —" >&2
    echo "       the app is completely unsigned; there is nothing to notarize." >&2
    exit 1
  fi
  if [[ -z "$SIGN_IDENTITY" ]]; then
    echo "FATAL: FUSED_RENDER_NOTARY_PROFILE is set but the app was signed ad-hoc." >&2
    echo "       Notarization requires a Developer ID signature — configure FUSED_RENDER_CODESIGN_IDENTITY." >&2
    exit 1
  fi
  echo "==> notarizing $DMG_PATH (profile: $FUSED_RENDER_NOTARY_PROFILE)"
  NOTARY_KC=()
  [[ -n "${FUSED_RENDER_CODESIGN_KEYCHAIN:-}" ]] && NOTARY_KC=(--keychain "$FUSED_RENDER_CODESIGN_KEYCHAIN")
  # --wait blocks until Apple answers (minutes); a rejection prints the log id,
  # fetch it with `xcrun notarytool log <id> --keychain-profile ...`.
  xcrun notarytool submit "$DMG_PATH" --keychain-profile "$FUSED_RENDER_NOTARY_PROFILE" \
    "${NOTARY_KC[@]}" --wait
  echo "==> stapling notarization ticket"
  xcrun stapler staple "$DMG_PATH"
  xcrun stapler validate "$DMG_PATH"
else
  echo "==> skipping notarization (FUSED_RENDER_NOTARY_PROFILE unset)"
fi

# --- 7. hygiene: the .app is sealed in the DMG; drop the loose copies ---------
rm -rf "$APP_DIR" "$ICONSET_DIR"

echo "==> done: $DMG_PATH ($(du -h "$DMG_PATH" | cut -f1))"
