"""macOS in-app updater, ported from fused-render's `fused_render/update/mac.py`.

A silent background loop checks the signed manifest and surfaces a newer
version only through `GET /api/update` — the launcher page (static/index.html)
shows a banner. Nothing else in the app does: a .fused app's own window is
never interrupted. Downloading and installing happen solely on an explicit
`POST /api/update/install`.

ONE install path: download the signed DMG, verify it, and swap the .app
bundle in place. Replacing the bundle under a running process is the same
thing a manual DMG drag does; the running process keeps its open files on the
old inode, `status()` notices the bundle on disk is now the new version and
reports "installed", and the banner offers "Restart Render App"
(`POST /api/update/relaunch`), which quits through the normal teardown and
respawns from the bundle now on disk.

THE APP NEVER INVOKES BREW ON ITSELF. The `render-app` cask
(fusedio/homebrew-tap) carries `uninstall quit:`, so a `brew upgrade` started
from inside the app would quit the app mid-upgrade. A Homebrew-installed
bundle gets the same swap as any other; brew's receipt then lags until its
next run (a redundant reinstall of the same version, never a broken one).

Everything runs on worker threads and never raises out of the manager: a
failed check leaves state "idle"/"error", never a dead loop.
"""
from __future__ import annotations

import logging
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from fused_render_app import __version__, paths
from fused_render_app.update import common

logger = logging.getLogger("fused_render_app.update")

BUNDLE_ID = "io.fused.render.app"  # scripts/setup_py2app.py + macapp.BUNDLE_ID
# Published by release.yml next to the DMGs — the S3 role can only write under
# render-app-dmgs/, so the manifest lives there rather than under its own
# prefix. Overridable for staging/E2E tests; safe to expose because the
# manifest must still verify against the pinned key.
MANIFEST_URL = os.environ.get(
    "FUSED_RENDER_APP_UPDATE_MANIFEST_URL",
    "https://d2ic19jpchjovp.cloudfront.net/render-app-dmgs/latest.json")
# The first check runs right after boot so the launcher's banner appears on
# its first polls rather than minutes into the session; every check after it
# is common.CHECK_INTERVAL_S apart.
STARTUP_DELAY_S = 1.0
# A CHECK-ONLY MANAGER IN A DEV RUN. `start()` refuses to run outside a bundle
# (nothing to swap), which also means an unpackaged server never shows the
# banner. Set this to a non-empty value and `start()` builds a manager with no
# bundle: it fetches and verifies the real manifest and reports every state a
# packaged app would, but `install()` refuses and `status()` says so
# (`check_only`), so the banner hides its Update button. Never read by a
# packaged app.
DEV_MANAGER_ENV = "FUSED_RENDER_APP_UPDATE_DEV_MANAGER"
NO_AUTO_UPDATE_ENV = "FUSED_RENDER_APP_NO_AUTO_UPDATE"
# Floor between two checks that actually hit the network. The launcher checks
# on its own when the app comes back to the front, and a run of focus flips
# must not become a run of CDN fetches. Only the throttled path
# (POST /api/update/check) is affected — the auto loop passes force=True.
MIN_CHECK_GAP_S = 60.0
# The floor AFTER A FAILED fetch: a laptop that just came back online must be
# able to press "Check for updates" and get a real retry, but several windows
# each firing check-on-return during an outage must not each cost a 15-second
# fetch.
FAILED_CHECK_GAP_S = 5.0
_DOWNLOAD_PREFIX = "RenderApp-"
_DOWNLOAD_SUFFIX = ".dmg"
# The download and the staged .app copy coexist briefly during the swap.
_DISK_SPACE_FACTOR = 3


def bundle_path() -> str | None:
    """The .app bundle root when running packaged, None otherwise. py2app's
    bootstrap sets `sys.frozen = "macosx_app"` and the interpreter lives at
    <bundle>/Contents/MacOS/python."""
    if getattr(sys, "frozen", None) != "macosx_app":
        return None
    contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(contents)


def relaunch_script(bundle: str, pid: int) -> str:
    """The shell that restarts the app from the bundle now on disk: wait for
    THIS process to exit (so the pidfile is gone and `open` cannot hand the
    launch back to the dying instance), then open the bundle by path. Run
    detached by macapp's relaunch hook right before it quits."""
    return (f"while kill -0 {int(pid)} 2>/dev/null; do sleep 0.2; done; "
            f"/usr/bin/open -n {shlex.quote(bundle)}")


def _discard_old_bundle(old: str) -> None:
    try:
        shutil.rmtree(old, ignore_errors=True)
    except OSError:
        logger.debug("could not remove old bundle %s", old, exc_info=True)


class UpdateManager:
    """State machine behind GET /api/update.

    states: idle -> checking -> (idle | available) -> installing(progress)
            -> installed | error(message)
    "installed" means the bundle on disk is the new version; the banner's
    Restart button drives the relaunch from there."""

    def __init__(self, *, manifest_url: str = MANIFEST_URL, bundle: str | None = None,
                 check_only: bool = False, current_version: str = __version__):
        # RLock: the early-return paths in check()/install() read status()
        # while already holding the lock.
        self._lock = threading.RLock()
        self._manifest_url = manifest_url
        self._bundle = bundle if bundle is not None else bundle_path()
        self._check_only = check_only
        self._current = current_version
        # Why the LAST CHECK could not answer (network, a manifest that did not
        # verify), or None when it did. Distinct from `_error`, an install's.
        self._check_error: str | None = None
        self._state = "idle"
        self._latest: dict | None = None
        self._error: str | None = None
        self._progress: float | None = None
        self._progress_total: float | None = None
        self._phase: str | None = None
        self._install_thread: threading.Thread | None = None
        # monotonic() of the last check that actually fetched — the throttle's
        # only state. Monotonic so a clock change cannot open or close the gap.
        self._last_check_at: float | None = None
        self._cancel = False

    # -- status ---------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            # An update can also land from outside this process (a `brew
            # upgrade`, a manual DMG drag), so "available" re-checks the bundle
            # on disk on every read rather than waiting for the next tick.
            if self._state == "available" and self._latest:
                disk = self._disk_version()
                if disk is not None and not common.is_newer(
                        self._latest["version"], disk):
                    self._state = "installed"
            return {
                "state": self._state,
                "current_version": self._current,
                "latest_version": self._latest["version"] if self._latest else None,
                "progress": self._progress,
                "progress_total": self._progress_total,
                "error": self._error,
                # "downloading" while the DMG streams, "installing" from the
                # mount to the swap. None outside "installing".
                "phase": self._phase if self._state == "installing" else None,
                # True only for the dev-run manager (DEV_MANAGER_ENV).
                "check_only": self._check_only,
                # The last check's failure, if it failed — what lets a manual
                # check say "Couldn't check" rather than "Up to date".
                "check_error": self._check_error,
            }

    # -- checking -------------------------------------------------------------

    def start_auto_checks(self) -> None:
        """Background check loop (startup delay, then every
        common.CHECK_INTERVAL_S). Silent: a newer version only flips state to
        "available". Set NO_AUTO_UPDATE_ENV to a non-empty value to disable."""
        if os.environ.get(NO_AUTO_UPDATE_ENV):
            return

        def loop():
            time.sleep(STARTUP_DELAY_S)
            swept = False
            while True:
                try:
                    # Once per process, and NEVER from the check-only manager:
                    # the updates dir is shared with the packaged app's
                    # manager, and a dev run must not delete a DMG the real
                    # app just downloaded.
                    if not swept and not self._check_only:
                        self._sweep_stale_downloads()
                        swept = True
                    # force: this tick IS the cadence; it must never be
                    # swallowed by MIN_CHECK_GAP_S because a focus flip fetched
                    # a minute ago.
                    self.check(force=True)
                except Exception:  # noqa: BLE001 - a tick must never kill the loop
                    logger.exception("auto update tick failed")
                time.sleep(common.CHECK_INTERVAL_S)

        threading.Thread(target=loop, daemon=True, name="render-app-update-auto").start()

    def check(self, force: bool = False) -> dict:
        """Fetch + verify the manifest and update state. Never touches state
        while an install is running. Returns status().

        Throttled by default (MIN_CHECK_GAP_S); `force=True` (the auto loop)
        always fetches."""
        with self._lock:
            if self._state == "installing":
                return self.status()
            # A fetch already in flight owns the answer: "checking" is set only
            # here and every exit from that fetch resolves it.
            if self._state == "checking":
                return self.status()
            # A non-forced check only ever looks from "idle": an update already
            # offered, installed or failed is an answer.
            if not force and self._state != "idle":
                return self.status()
            if not force and self._last_check_at is not None and (
                    time.monotonic() - self._last_check_at < MIN_CHECK_GAP_S):
                return self.status()
            self._last_check_at = time.monotonic()
            # Only an idle manager says "checking": a forced re-check from
            # "available" keeps that on the wire so the banner does not blink
            # and install() is not refused for the length of the fetch.
            during = "checking" if self._state == "idle" else self._state
            self._state = during
        try:
            manifest = common.fetch_manifest(self._manifest_url)
            newer = common.is_newer(manifest["version"], self._current)
        except Exception as error:  # noqa: BLE001 - network/manifest failures are routine
            logger.info("update check failed: %s", error)
            with self._lock:
                self._check_error = str(error) or error.__class__.__name__
                # A failure arms the SHORT floor (FAILED_CHECK_GAP_S), expressed
                # as a back-dated timestamp so the one comparison above stays
                # the only throttle logic.
                self._last_check_at = time.monotonic() - (MIN_CHECK_GAP_S - FAILED_CHECK_GAP_S)
            # Keep a previously-found update visible over a transient failure,
            # re-deriving WHICH state from the bundle on disk: a network blip
            # after a completed install must not resurface the install button.
            disk = self._disk_version()
            with self._lock:
                if self._state == during:
                    self._settle(self._latest, disk)
            return self.status()
        # The bundle on disk, not the running version, decides "already
        # installed": after a swap this process still runs the old code.
        disk = self._disk_version()
        with self._lock:
            self._check_error = None
            # Untouched if anything else moved the state while the fetch was
            # out (an install that began from "available").
            if self._state == during:
                self._settle(manifest if newer else None, disk)
        return self.status()

    def _settle(self, latest: dict | None, disk: str | None) -> None:
        """Set `_latest` and the resting state after a check, given what the
        manifest offers (`latest`, None when nothing newer) and what is on
        disk. Under the lock.

        An install's "error" is HELD, not cleared (bugbot, PR #26): the auto
        loop re-checks every five minutes, and a failed install's reason and
        its Try again must survive those ticks — the user has not seen them
        yet. Only two things end it: the bundle on disk is now the version we
        failed on (someone installed it another way), or the manifest no
        longer offers that version (a pulled release, or a newer one — the
        retry would be a different install, so it starts clean)."""
        previous = self._latest["version"] if self._latest else None
        self._latest = latest
        if self._state == "error" and latest is not None and latest["version"] == previous:
            if disk is not None and not common.is_newer(latest["version"], disk):
                self._state, self._error = "installed", None
            return
        self._error = None
        if latest is not None and disk is not None and not common.is_newer(latest["version"], disk):
            self._state = "installed"
        elif latest is not None:
            self._state = "available"
        else:
            self._state = "idle"

    def _disk_version(self) -> str | None:
        """CFBundleShortVersionString of the bundle on disk — what would
        launch next time."""
        if self._bundle is None:
            return None
        try:
            with open(os.path.join(self._bundle, "Contents", "Info.plist"), "rb") as f:
                return plistlib.load(f).get("CFBundleShortVersionString")
        except (OSError, plistlib.InvalidFileException):
            return None

    # -- installing -----------------------------------------------------------

    def install(self, expected_version: str | None = None) -> dict:
        """Kick the install on a worker thread. One at a time; allowed from
        "available" and from "error" (retry).

        `expected_version` is what the CALLER had on screen. The background
        loop can move `_latest` on its own cadence before the click lands, so
        a fresh check runs first and, if what is current no longer matches
        what the button said, nothing is installed: the banner now shows the
        current version and a second click commits to it. None (a caller with
        no such field) trusts `_latest` as-is."""
        with self._lock:
            worth_rechecking = (self._latest is not None
                               and self._state in ("available", "error"))
        if worth_rechecking:
            self.check(force=True)
        with self._lock:
            if self._state == "installing":
                return self.status()
            if self._latest is None or self._state not in ("available", "error"):
                return self.status()
            if (expected_version is not None
                    and self._latest["version"] != expected_version):
                logger.info("update install deferred: v%s is current, not the v%s "
                            "the caller had on screen",
                            self._latest["version"], expected_version)
                return self.status()
            # The dev-run manager has no bundle to swap. Refused here so the
            # state stays "available" and honest instead of "error".
            if self._check_only:
                logger.info("update install refused: check-only manager (%s)", DEV_MANAGER_ENV)
                return self.status()
            manifest = self._latest
            self._state = "installing"
            self._error = None
            self._progress = 0.0
            self._progress_total = None
            self._phase = "downloading"
            self._cancel = False
            thread = threading.Thread(
                target=self._install, args=(manifest,), daemon=True,
                name="render-app-update-install")
            self._install_thread = thread
        thread.start()
        return self.status()

    def cancel(self) -> dict:
        """Ask the in-flight download to stop. Honoured only while
        downloading; once the bundle swap starts there is no safe point to
        stop at, and the flag is simply ignored."""
        with self._lock:
            if self._state == "installing" and self._phase == "downloading":
                self._cancel = True
            return self.status()

    def _cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel

    def _install(self, manifest: dict) -> None:
        try:
            if self._bundle is None:
                raise RuntimeError("not running from an installed bundle")
            self._install_dmg(manifest)
        except common.UpdateCancelled:
            # Not a failure: the update is still there to install, so the
            # manager goes back to exactly where the cancel came from.
            logger.info("update install cancelled")
            with self._lock:
                self._state = "available"
                self._error = None
                self._progress = None
                self._progress_total = None
            return
        except Exception as error:  # noqa: BLE001 - reported through state, never raised
            logger.exception("update install failed")
            with self._lock:
                self._state = "error"
                self._error = str(error)
            return
        with self._lock:
            self._state = "installed"
            self._progress = None
            self._progress_total = None

    # -- dmg path -------------------------------------------------------------

    def _updates_dir(self) -> str:
        path = os.path.join(paths.home(), "updates")
        os.makedirs(path, exist_ok=True)
        return path

    def _sweep_stale_downloads(self) -> None:
        """Best-effort cleanup of DMGs and staged bundles a previous session
        left behind (install failed, or the process died mid-download)."""
        try:
            updates = self._updates_dir()
            for name in os.listdir(updates):
                full = os.path.join(updates, name)
                try:
                    if os.path.isdir(full):
                        shutil.rmtree(full)
                    else:
                        os.unlink(full)
                except OSError:
                    pass
        except OSError:
            pass

    def _install_dmg(self, manifest: dict) -> None:
        if self._bundle is None:
            raise RuntimeError("not running from an installed bundle")
        # realpath: a bundle reached through a symlink (an /Applications link
        # into a Caskroom artifact) would otherwise have its LINK renamed by
        # the swap, leaving the real bundle untouched.
        bundle = os.path.realpath(self._bundle)
        parent = os.path.dirname(bundle)
        if not os.access(parent, os.W_OK):
            raise RuntimeError(
                f"cannot write to {parent} — update by downloading the DMG manually")

        updates = self._updates_dir()
        self._check_disk_space(updates)

        def on_bytes(done: int, size: int | None) -> None:
            with self._lock:
                self._progress = float(done)
                self._progress_total = float(size) if size is not None else None

        dmg = common.download_verified(
            manifest, dir=updates, prefix=_DOWNLOAD_PREFIX,
            suffix=_DOWNLOAD_SUFFIX, progress=on_bytes,
            should_abort=self._cancel_requested)
        # A cancel that arrived with the final chunk has no next chunk to be
        # honoured on; the bundle is still untouched here, so honour it.
        if self._cancel_requested():
            common.discard(dmg)
            raise common.UpdateCancelled("cancelled after download")
        with self._lock:
            self._phase = "installing"
            self._progress = None
            self._progress_total = None
        mount = None
        old = None
        swap_in = os.path.join(parent, ".RenderApp-update.app")
        try:
            mount = self._attach(dmg)
            source = self._find_app(mount)
            self._verify_app(source, manifest["version"])
            if os.path.exists(swap_in):
                shutil.rmtree(swap_in)
            # ditto straight from the mounted image into the bundle's own
            # parent dir: it preserves the code signature, resource forks and
            # xattrs a plain copy can drop (a stripped signature would leave a
            # bundle Gatekeeper refuses to launch), and landing on the target
            # volume makes the swap below two same-volume renames.
            subprocess.run(["/usr/bin/ditto", source, swap_in],
                           check=True, capture_output=True, timeout=600)
            # Both renames happen inside `parent`, so each is atomic on the
            # volume; the running process keeps its open files on the old
            # inode.
            old = os.path.join(parent, f".RenderApp-old-{os.getpid()}.app")
            os.rename(bundle, old)
            try:
                os.rename(swap_in, bundle)
            except OSError:
                os.rename(old, bundle)  # roll back — never leave no app at all
                raise
        finally:
            # Cleanup is best-effort and must not turn a finished swap into
            # an "error": a detach that hangs (TimeoutExpired) or fails is a
            # stale mount to tidy later, not a failed update (bugbot, PR #26).
            if mount is not None:
                try:
                    subprocess.run(["/usr/bin/hdiutil", "detach", mount, "-quiet"],
                                   check=False, capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    logger.warning("could not detach update image %s", mount, exc_info=True)
            common.discard(dmg)
            if os.path.exists(swap_in):
                shutil.rmtree(swap_in, ignore_errors=True)
        # Old bundle: best-effort removal on a worker; open files keep working
        # on the unlinked inodes until this process exits.
        if old is not None:
            threading.Thread(target=_discard_old_bundle, args=(old,), daemon=True).start()

    def _check_disk_space(self, updates: str) -> None:
        stat = os.statvfs(updates)
        free = stat.f_bavail * stat.f_frsize
        if free < _DISK_SPACE_FACTOR * common.MAX_ARTIFACT_BYTES // 2:
            raise RuntimeError("not enough free disk space to download the update")

    def _attach(self, dmg: str) -> str:
        """Mount the DMG and return its mount point. Every failure path after
        `hdiutil attach` may have done its work (a timeout, an unparsable
        plist, a non-zero status with a mount already made, a mount with no
        volume) detaches whatever got attached for this image before raising,
        so `_install_dmg`'s cleanup never has to guess (bugbot, PR #26)."""
        try:
            result = subprocess.run(
                ["/usr/bin/hdiutil", "attach", dmg, "-nobrowse", "-readonly",
                 "-plist", "-mountrandom", tempfile.gettempdir()],
                capture_output=True, timeout=120)
        except subprocess.SubprocessError:
            self._detach_image(dmg)
            raise RuntimeError("could not open the downloaded update image")
        try:
            if result.returncode != 0:
                raise RuntimeError("could not open the downloaded update image")
            for entity in plistlib.loads(result.stdout).get("system-entities", []):
                if entity.get("mount-point"):
                    return entity["mount-point"]
            raise RuntimeError("update image mounted with no volume")
        except (plistlib.InvalidFileException, ValueError, TypeError) as error:
            raise RuntimeError("could not read the update image's mount table") from error
        finally:
            # Only reached without a mount point to hand back.
            if sys.exc_info()[0] is not None:
                self._detach_image(dmg)

    def _detach_image(self, dmg: str) -> None:
        """Best-effort: detach any attached image whose backing file is `dmg`
        (`hdiutil info -plist` lists them by image-path), so a failed attach
        leaves no stale volume behind and the DMG can be deleted."""
        try:
            info = subprocess.run(["/usr/bin/hdiutil", "info", "-plist"],
                                  capture_output=True, timeout=60)
            if info.returncode != 0:
                return
            target = os.path.realpath(dmg)
            for image in plistlib.loads(info.stdout).get("images", []):
                if os.path.realpath(str(image.get("image-path", ""))) != target:
                    continue
                for entity in image.get("system-entities", []):
                    dev = entity.get("dev-entry")
                    if dev:
                        subprocess.run(["/usr/bin/hdiutil", "detach", dev, "-force", "-quiet"],
                                       check=False, capture_output=True, timeout=60)
                        break
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException,
                ValueError, TypeError):
            logger.warning("could not detach stale update image %s", dmg, exc_info=True)

    def _find_app(self, mount: str) -> str:
        for name in sorted(os.listdir(mount)):
            if name.endswith(".app"):
                return os.path.join(mount, name)
        raise RuntimeError("update image contains no app bundle")

    def _verify_app(self, app: str, version: str) -> None:
        """The DMG's integrity is already pinned by the signed sha256; this
        guards against a mispublished manifest (right signature, wrong file)
        swapping in an unexpected version — or a different app altogether
        (a FusedRender DMG published under this manifest by mistake)."""
        try:
            with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
                info = plistlib.load(f)
        except (OSError, plistlib.InvalidFileException) as error:
            raise RuntimeError("update app bundle has no readable Info.plist") from error
        found = info.get("CFBundleShortVersionString")
        if found != version:
            raise RuntimeError(
                f"update image contains version {found}, expected {version}")
        ident = info.get("CFBundleIdentifier")
        if ident != BUNDLE_ID:
            raise RuntimeError(
                f"update image contains {ident or 'an unknown app'}, expected {BUNDLE_ID}")


_manager: UpdateManager | None = None
_manager_lock = threading.Lock()


def manager() -> UpdateManager | None:
    """The process-wide manager, or None when start() was never called (dev
    server, CLI, tests) — GET /api/update answers `{"update": null}` then."""
    return _manager


def start() -> UpdateManager | None:
    """Create the singleton and start its background checks. Called once from
    macapp's server bootstrap; idempotent. No-op (returns None) when not
    running from a bundle, unless DEV_MANAGER_ENV asks for a check-only
    manager."""
    global _manager
    with _manager_lock:
        if _manager is None:
            if bundle_path() is None:
                if not os.environ.get(DEV_MANAGER_ENV):
                    return None
                _manager = UpdateManager(bundle=None, check_only=True)
            else:
                _manager = UpdateManager()
            _manager.start_auto_checks()
        return _manager


def reset_for_tests() -> None:
    global _manager
    with _manager_lock:
        _manager = None
