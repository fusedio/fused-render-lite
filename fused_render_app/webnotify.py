"""Web Notifications for the app's WKWebViews: ``new Notification(...)`` →
a macOS notification.

WKWebView has no public notification API. The *WebKit C API* (the one
MiniBrowser and WebKitTestRunner use) does: ``WKContextGetNotificationManager``
on the process pool hands back a manager, and ``WKNotificationManagerSetProvider``
installs a struct of C callbacks that WebKit calls on the main thread
whenever a page shows, cancels or destroys a notification, and once per
process to ask which origins are already granted. Reached here through
``ctypes`` on the WebKit framework binary — no compiled helper.

Permission itself is a separate gate: WebKit asks the UI delegate through
the private selector ``_webView:requestNotificationPermissionForSecurityOrigin:
decisionHandler:`` (`mainwindow.py` answers: our own origin only). The
``notificationPermissions`` callback below then reports the app's own
origins as granted, so ``Notification.permission`` already reads
``"granted"`` on later loads instead of ``"default"``.

Display:

- Inside the bundle (a real ``Render App.app``): ``UNUserNotificationCenter``.
  Authorization is requested on first use; a click on the banner is routed
  back as ``WKNotificationManagerProviderDidClickNotification`` (the page's
  ``onclick``) and the app comes forward. ``UNUserNotificationCenter``
  *aborts the process* (uncaught ``NSInternalInconsistencyException``) when
  called from an unbundled binary, so it is never touched unless
  ``NSBundle.mainBundle().bundleIdentifier()`` is set.
- Unbundled (``python -m fused_render_app.macapp``): nothing on macOS will
  display for a bare interpreter — the legacy ``NSUserNotificationCenter``
  is nil there too (measured). The notification is logged at INFO and
  ``DidShowNotification`` is still reported, so the page's ``onshow`` fires
  and its logic proceeds exactly as in the bundle.

Second entry point — job banners. `macapp` installs a `jobs` transition
hook that runs each (prev, after) pair through `notify_policy.decide` and
calls `notify(identifier, title, body, sound=...)` here for the ones that
deserve a banner (model downloads starting, a job waiting on the user, a
job finishing). That path shares the display singleton with the WebKit
provider: one `UNUserNotificationCenter` delegate, one authorization
request, one click dispatcher. Clicks are routed by identifier prefix —
the provider owns ``web-`` (`IDENTIFIER_PREFIX`), `macapp` registers
`notify_policy.IDENTIFIER_PREFIX` — through `register_click_handler`.
`notify` / `remove` may be called from any thread (the jobs hook fires on
the producer's thread); they hop to the main thread and, if `install` never
ran (``fused-render serve`` on the CLI, Linux CI), build the display lazily
or, when AppKit is missing altogether, log once and do nothing.

Import of this module never loads WebKit, AppKit or ctypes symbols (CI runs
on Linux); everything native happens in `install`, `notify` and friends.
The pure helpers at the top are what the tests cover.
"""
from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

IDENTIFIER_PREFIX = "web-"

# UNNotificationPresentationOptions (macOS 11+): banner | list, with and
# without the sound bit. A banner posted with ``sound=False`` (a job merely
# starting) gets the silent set from `willPresentNotification`.
_UN_PRESENT_BANNER_LIST = (1 << 4) | (1 << 3)
_UN_PRESENT_BANNER_LIST_SOUND = _UN_PRESENT_BANNER_LIST | (1 << 1)
# UNAuthorizationOptions: badge | sound | alert.
_UN_AUTH_BADGE_SOUND_ALERT = 7


class NotificationsUnavailable(RuntimeError):
    """WebKit lacks a C symbol we need (or is not loadable here)."""


# ---- pure helpers (tested) ---------------------------------------------------

def granted_origins(port: int | None) -> set[str]:
    """The security origins `notificationPermissions` reports as granted:
    the app's own server on every loopback spelling a page may load under.
    Serialised the way ``WKSecurityOriginCopyToString`` does
    (``scheme://host:port``, no trailing slash). Empty until the server has
    bound (``port`` None/0)."""
    if not port:
        return set()
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def identifier_for(notification_id: int) -> str:
    """UNNotificationRequest identifier for a WebKit notification id."""
    return f"{IDENTIFIER_PREFIX}{int(notification_id)}"


def notification_id_from(identifier: str | None) -> int | None:
    """Inverse of `identifier_for`; None for identifiers that are not ours."""
    if not identifier or not str(identifier).startswith(IDENTIFIER_PREFIX):
        return None
    try:
        return int(str(identifier)[len(IDENTIFIER_PREFIX):])
    except ValueError:
        return None


# ---- module-level display + click registry --------------------------------------

# The one display for the process (`_UNDisplay` or `_LogOnlyDisplay`).
# Built by `install` (the WebKit provider) or lazily by the first `notify`
# on the main thread — whichever comes first; both then share it. Stays
# None where no display can exist (no AppKit).
_display = None
_no_display_logged = False

# Click handlers by UN identifier prefix. A banner's identifier says who
# posted it (``web-<id>`` from a page, ``job:<id>`` from `notify_policy`);
# the longest registered prefix that matches wins, so a more specific
# prefix can shadow a general one. Last registration per prefix wins.
_click_handlers: dict[str, Callable[[str], None]] = {}


def _handler_for(identifier: str | None) -> Callable[[str], None] | None:
    """Pure: the registered handler whose prefix is the longest one that
    ``identifier`` starts with, or None when nobody claims it."""
    if not identifier:
        return None
    best: str | None = None
    for prefix in _click_handlers:
        if identifier.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return None if best is None else _click_handlers[best]


def register_click_handler(prefix: str, handler: Callable[[str], None]) -> None:
    """A banner whose UN identifier starts with ``prefix`` was clicked:
    ``handler(identifier)`` runs on the MAIN thread after the app has been
    activated (``NSApp.activateIgnoringOtherApps_(True)``). Last registration
    per prefix wins."""
    _click_handlers[prefix] = handler


def _call_after(fn: Callable, *args) -> None:
    """Hop to the main thread. The one place `AppHelper` is touched, so a
    test can make it synchronous and a build without PyObjC fails here with
    ImportError (callers catch it)."""
    from PyObjCTools import AppHelper

    AppHelper.callAfter(fn, *args)


def _log_no_display_once() -> None:
    global _no_display_logged
    if not _no_display_logged:
        _no_display_logged = True
        logger.info("native notifications unavailable here (no AppKit / display); "
                    "banners are dropped")


def _ensure_display():
    """Main thread. The shared display, built on first need. Unlike
    `install`, a failure here (no PyObjC, no Foundation) is not an error:
    ``fused-render serve`` has jobs too and must simply not notify. The
    failure is not cached so a later `install` in a real app still builds."""
    global _display
    if _display is None:
        try:
            _display = _make_display(_dispatch_click)
        except Exception:  # noqa: BLE001 — ImportError mostly; never raise to the caller
            logger.debug("building the notification display failed", exc_info=True)
            _log_no_display_once()
            return None
    return _display


def notify(identifier: str, title: str, body: str, *, sound: bool = True) -> None:
    """Post/replace a macOS notification (same identifier → UN replaces the
    banner in place). Callable from ANY thread; hops to the main thread via
    `AppHelper.callAfter`. No-op with an INFO log if no display exists and
    one cannot be built (no AppKit, e.g. ``fused-render serve``)."""
    try:
        _call_after(_notify_main, str(identifier), title or "", body or "", bool(sound))
    except Exception:  # noqa: BLE001 — no PyObjC at all
        _log_no_display_once()


def _notify_main(identifier: str, title: str, body: str, sound: bool) -> None:
    display = _ensure_display()
    if display is None:
        return
    try:
        display.show(identifier, title, body, sound)
    except Exception:  # noqa: BLE001 — a banner is never worth breaking the caller
        logger.exception("notification %s: display failed", identifier)


def remove(identifier: str) -> None:
    """Take a delivered/pending banner down. Any thread."""
    try:
        _call_after(_remove_main, str(identifier))
    except Exception:  # noqa: BLE001
        _log_no_display_once()


def _remove_main(identifier: str) -> None:
    # Nothing to remove if no display was ever built — and building one
    # (requestAuthorization) just to take nothing down would be wrong.
    if _display is None:
        return
    try:
        _display.remove([identifier])
    except Exception:  # noqa: BLE001
        logger.exception("notification %s: remove failed", identifier)


def _dispatch_click(identifier: str) -> None:
    """The display's click callback. Any thread (UN delivers on a
    background queue); activation and the handler run on the main thread."""
    try:
        _call_after(_dispatch_click_main, str(identifier))
    except Exception:  # noqa: BLE001
        logger.exception("notification click %s: dispatch failed", identifier)


def _activate_app() -> None:
    """Bring the app forward. Own function so a test can stub it (calling
    the real thing under pytest would spawn an NSApplication and steal
    focus from the terminal)."""
    from AppKit import NSApp

    NSApp.activateIgnoringOtherApps_(True)


def _dispatch_click_main(identifier: str) -> None:
    # Activate once for every prefix (the handlers no longer do it), even
    # when nobody claims the identifier: the user clicked *our* banner.
    try:
        _activate_app()
    except Exception:  # noqa: BLE001 — still deliver the click
        logger.exception("notification click %s: activating failed", identifier)
    handler = _handler_for(identifier)
    if handler is None:
        logger.info("notification %s clicked: no handler registered", identifier)
        return
    try:
        handler(identifier)
    except Exception:  # noqa: BLE001
        logger.exception("notification click %s: handler failed", identifier)


# ---- WebKit C API bindings ---------------------------------------------------

_WEBKIT = "/System/Library/Frameworks/WebKit.framework/WebKit"

_c_void_p = ctypes.c_void_p
_c_uint64 = ctypes.c_uint64

ShowCB = ctypes.CFUNCTYPE(None, _c_void_p, _c_void_p, _c_void_p)
CancelCB = ctypes.CFUNCTYPE(None, _c_void_p, _c_void_p)
DestroyCB = ctypes.CFUNCTYPE(None, _c_void_p, _c_void_p)
ManagerCB = ctypes.CFUNCTYPE(None, _c_void_p, _c_void_p)
PermissionsCB = ctypes.CFUNCTYPE(_c_void_p, _c_void_p)
ClearCB = ctypes.CFUNCTYPE(None, _c_void_p, _c_void_p)


class WKNotificationProviderBase(ctypes.Structure):
    _fields_ = [("version", ctypes.c_int), ("clientInfo", _c_void_p)]


class WKNotificationProviderV0(ctypes.Structure):
    _fields_ = [
        ("base", WKNotificationProviderBase),
        ("show", ShowCB),
        ("cancel", CancelCB),
        ("didDestroyNotification", DestroyCB),
        ("addNotificationManager", ManagerCB),
        ("removeNotificationManager", ManagerCB),
        ("notificationPermissions", PermissionsCB),
        ("clearNotifications", ClearCB),
    ]


class _WK:
    """The WebKit C functions we call, resolved once. Attribute lookup on
    the CDLL raises AttributeError for a symbol this WebKit lacks; that is
    turned into `NotificationsUnavailable` so the caller degrades cleanly."""

    _SIGNATURES = {
        "WKContextGetNotificationManager": (_c_void_p, [_c_void_p]),
        "WKNotificationManagerSetProvider": (None, [_c_void_p, _c_void_p]),
        "WKNotificationManagerProviderDidShowNotification": (None, [_c_void_p, _c_uint64]),
        "WKNotificationManagerProviderDidClickNotification": (None, [_c_void_p, _c_uint64]),
        "WKNotificationManagerProviderDidCloseNotifications": (None, [_c_void_p, _c_void_p]),
        "WKNotificationGetID": (_c_uint64, [_c_void_p]),
        "WKNotificationCopyTitle": (_c_void_p, [_c_void_p]),
        "WKNotificationCopyBody": (_c_void_p, [_c_void_p]),
        "WKNotificationCopyTag": (_c_void_p, [_c_void_p]),
        "WKNotificationGetSecurityOrigin": (_c_void_p, [_c_void_p]),
        "WKSecurityOriginCopyToString": (_c_void_p, [_c_void_p]),
        "WKStringGetMaximumUTF8CStringSize": (ctypes.c_size_t, [_c_void_p]),
        "WKStringGetUTF8CString": (ctypes.c_size_t, [_c_void_p, ctypes.c_char_p, ctypes.c_size_t]),
        "WKStringCreateWithUTF8CString": (_c_void_p, [ctypes.c_char_p]),
        "WKMutableDictionaryCreate": (_c_void_p, []),
        "WKDictionarySetItem": (ctypes.c_bool, [_c_void_p, _c_void_p, _c_void_p]),
        "WKBooleanCreate": (_c_void_p, [ctypes.c_bool]),
        "WKMutableArrayCreate": (_c_void_p, []),
        "WKArrayAppendItem": (None, [_c_void_p, _c_void_p]),
        "WKArrayGetSize": (ctypes.c_size_t, [_c_void_p]),
        "WKArrayGetItemAtIndex": (_c_void_p, [_c_void_p, ctypes.c_size_t]),
        "WKUInt64Create": (_c_void_p, [_c_uint64]),
        "WKUInt64GetValue": (_c_uint64, [_c_void_p]),
        "WKRelease": (None, [_c_void_p]),
    }

    def __init__(self) -> None:
        try:
            lib = ctypes.CDLL(_WEBKIT)
        except OSError as exc:
            raise NotificationsUnavailable(f"cannot load WebKit: {exc}") from exc
        for name, (restype, argtypes) in self._SIGNATURES.items():
            try:
                fn = getattr(lib, name)
            except AttributeError as exc:
                raise NotificationsUnavailable(f"WebKit lacks {name}") from exc
            fn.restype = restype
            fn.argtypes = argtypes
            setattr(self, name, fn)
        self._lib = lib

    def string(self, ref) -> str | None:
        """Python str from a WKStringRef *we own* (a Copy* result); releases it."""
        if not ref:
            return None
        try:
            n = self.WKStringGetMaximumUTF8CStringSize(ref)
            buf = ctypes.create_string_buffer(n)
            self.WKStringGetUTF8CString(ref, buf, n)
            return buf.value.decode("utf-8", "replace")
        finally:
            self.WKRelease(ref)


# ---- provider ------------------------------------------------------------------

class NotificationProvider:
    """The one provider for the shared process pool. Holds the ctypes
    callbacks and the struct for the life of the process — WebKit keeps the
    struct pointer, not a copy of the Python objects, so dropping any of
    them would be a use-after-free the next time a page notifies.

    Every WebKit callback arrives on the main thread. UN completion handlers
    do not; they hop to the main thread before touching WebKit or AppKit.
    """

    def __init__(self, wk: _WK, pool, port_getter: Callable[[], int | None],
                 on_click: Callable[[str | None], None] | None) -> None:
        import objc

        self._wk = wk
        self._port = port_getter
        self._on_click = on_click
        self._origins: dict[int, str | None] = {}  # live notification id → origin
        self._callbacks = (
            ShowCB(self._show), CancelCB(self._cancel), DestroyCB(self._destroy),
            ManagerCB(self._add_manager), ManagerCB(self._remove_manager),
            PermissionsCB(self._permissions), ClearCB(self._clear),
        )
        self._struct = WKNotificationProviderV0(
            WKNotificationProviderBase(0, None), *self._callbacks)
        self.manager = wk.WKContextGetNotificationManager(objc.pyobjc_id(pool))
        if not self.manager:
            raise NotificationsUnavailable("WKContextGetNotificationManager returned NULL")
        # Clicks on ``web-*`` banners come back here through the shared
        # dispatcher; the display itself is shared with `notify` and may
        # already exist if a job banner was posted before the first window.
        register_click_handler(IDENTIFIER_PREFIX, self._clicked)
        global _display
        if _display is None:
            _display = _make_display(_dispatch_click)
        self._display = _display
        wk.WKNotificationManagerSetProvider(self.manager, ctypes.byref(self._struct))
        logger.info("web notification provider installed (%s)", self._display.describe())

    # ---- WebKit → us (main thread) ----------------------------------------

    def _show(self, page, notif, _info) -> None:
        wk = self._wk
        try:
            nid = wk.WKNotificationGetID(notif)
            title = wk.string(wk.WKNotificationCopyTitle(notif))
            body = wk.string(wk.WKNotificationCopyBody(notif))
            tag = wk.string(wk.WKNotificationCopyTag(notif))
            origin_ref = wk.WKNotificationGetSecurityOrigin(notif)  # Get: not ours
            origin = wk.string(wk.WKSecurityOriginCopyToString(origin_ref)) if origin_ref else None
        except Exception:  # noqa: BLE001 — never let an exception cross into C
            logger.exception("web notification: reading WKNotification failed")
            return
        self._origins[nid] = origin
        logger.info("web notification #%d from %s: %r / %r%s", nid, origin, title, body,
                    f" tag={tag!r}" if tag else "")
        try:
            self._display.show(identifier_for(nid), title or "", body or "", True)
        except Exception:  # noqa: BLE001 — report shown anyway; the page must not hang
            logger.exception("web notification #%d: display failed", nid)
        wk.WKNotificationManagerProviderDidShowNotification(self.manager, nid)

    def _cancel(self, notif, _info) -> None:
        # `notification.close()` from the page.
        try:
            nid = self._wk.WKNotificationGetID(notif)
        except Exception:  # noqa: BLE001
            logger.exception("web notification: cancel failed")
            return
        logger.debug("web notification #%d cancelled by page", nid)
        self._remove_ids([nid])

    def _destroy(self, notif, _info) -> None:
        try:
            self._origins.pop(self._wk.WKNotificationGetID(notif), None)
        except Exception:  # noqa: BLE001
            logger.exception("web notification: destroy failed")

    def _add_manager(self, manager, _info) -> None:
        logger.debug("web notification manager added 0x%x", manager or 0)

    def _remove_manager(self, manager, _info) -> None:
        logger.debug("web notification manager removed 0x%x", manager or 0)

    def _permissions(self, _info):
        # WebKit adopts the returned dictionary (WebNotificationProvider.cpp:
        # adoptRef(toImpl(...))); the dict retains keys/values we set, so
        # our own refs are released right after.
        wk = self._wk
        origins = granted_origins(self._port())
        d = wk.WKMutableDictionaryCreate()
        try:
            for origin in sorted(origins):
                key = wk.WKStringCreateWithUTF8CString(origin.encode("utf-8"))
                val = wk.WKBooleanCreate(True)
                wk.WKDictionarySetItem(d, key, val)
                wk.WKRelease(key)
                wk.WKRelease(val)
        except Exception:  # noqa: BLE001 — still return a valid (maybe partial) dict
            logger.exception("web notification: building permissions failed")
        logger.debug("web notification permissions -> %s", sorted(origins))
        return d

    def _clear(self, ids_ref, _info) -> None:
        # Page/process going away: WebKit lists the ids it wants gone.
        wk = self._wk
        try:
            n = wk.WKArrayGetSize(ids_ref) if ids_ref else 0
            ids = [wk.WKUInt64GetValue(wk.WKArrayGetItemAtIndex(ids_ref, i)) for i in range(n)]
        except Exception:  # noqa: BLE001
            logger.exception("web notification: clear failed")
            return
        logger.debug("web notifications cleared: %s", ids)
        for nid in ids:
            self._origins.pop(nid, None)
        try:
            self._display.remove([identifier_for(i) for i in ids])
        except Exception:  # noqa: BLE001
            logger.exception("web notification: removing delivered failed")

    # ---- helpers -------------------------------------------------------------

    def _remove_ids(self, ids: list[int]) -> None:
        """Take the banners down and tell WebKit they are closed (the page's
        ``onclose``). Reported *synchronously*, as WebKitTestRunner's
        provider does: WebKit follows `cancel` with `didDestroyNotification`
        and drops the record, so a deferred DidClose would find nothing and
        ``onclose`` would never fire (measured)."""
        try:
            self._display.remove([identifier_for(i) for i in ids])
        except Exception:  # noqa: BLE001
            logger.exception("web notification: removing delivered failed")
        self._report_closed(ids)

    def _report_closed(self, ids: list[int]) -> None:
        wk = self._wk
        arr = wk.WKMutableArrayCreate()
        try:
            for nid in ids:
                item = wk.WKUInt64Create(nid)
                wk.WKArrayAppendItem(arr, item)
                wk.WKRelease(item)
            wk.WKNotificationManagerProviderDidCloseNotifications(self.manager, arr)
        finally:
            wk.WKRelease(arr)

    def _clicked(self, identifier: str) -> None:
        """A ``web-*`` banner was clicked; called by `_dispatch_click_main`
        on the main thread with the app already activated. Tolerates any
        thread anyway (hops), since the WebKit call must be on main."""
        nid = notification_id_from(identifier)
        if nid is None:
            return
        _call_after(self._clicked_main, nid)

    def _clicked_main(self, nid: int) -> None:
        origin = self._origins.get(nid)
        logger.info("web notification #%d clicked", nid)
        try:
            if self._on_click is not None:
                self._on_click(origin)
        except Exception:  # noqa: BLE001 — still deliver the click to the page
            logger.exception("web notification: focusing failed")
        self._wk.WKNotificationManagerProviderDidClickNotification(self.manager, nid)


def install(pool, port_getter: Callable[[], int | None],
            on_click: Callable[[str | None], None] | None = None) -> NotificationProvider:
    """Install the provider on ``pool`` (a WKProcessPool). Call once, before
    the first web view is created on that pool, and keep the result alive.
    Raises `NotificationsUnavailable` if WebKit lacks the C API."""
    return NotificationProvider(_WK(), pool, port_getter, on_click)


# ---- macOS display back-ends ----------------------------------------------------

class _LogOnlyDisplay:
    """Unbundled dev run: macOS has no notification surface for a bare
    interpreter (UNUserNotificationCenter aborts; NSUserNotificationCenter is
    nil). Log and move on — `onshow` still fires on the page."""

    def describe(self) -> str:
        return "unbundled: notifications are logged, not displayed"

    def show(self, identifier: str, title: str, body: str, sound: bool) -> None:
        logger.info("notification %s not displayed (unbundled run): %s — %s",
                    identifier, title, body)

    def remove(self, identifiers: list[str]) -> None:
        pass


_UN_SEL_WILL_PRESENT = b"userNotificationCenter:willPresentNotification:withCompletionHandler:"
_UN_SEL_DID_RECEIVE = b"userNotificationCenter:didReceiveNotificationResponse:withCompletionHandler:"


def _register_un_metadata() -> None:
    """The venv ships no pyobjc-framework-UserNotifications, so the block
    arguments (delegate selectors in, completion handlers out) need
    hand-written metadata (same failure
    mode `mainwindow.py` documents: ``cannot call block without a
    signature``). ``void (^)(UNNotificationPresentationOptions)`` is an
    NSUInteger (``Q``); ``void (^)(void)`` takes nothing."""
    import objc

    objc.registerMetaDataForSelector(b"NSObject", _UN_SEL_WILL_PRESENT, {
        "required": False, "retval": {"type": b"v"},
        "arguments": {
            2: {"type": b"@"}, 3: {"type": b"@"},
            4: {"callable": {"retval": {"type": b"v"},
                             "arguments": {0: {"type": b"^v"}, 1: {"type": b"Q"}}},
                "type": b"@?"},
        },
    })
    objc.registerMetaDataForSelector(b"NSObject", _UN_SEL_DID_RECEIVE, {
        "required": False, "retval": {"type": b"v"},
        "arguments": {
            2: {"type": b"@"}, 3: {"type": b"@"},
            4: {"callable": {"retval": {"type": b"v"}, "arguments": {0: {"type": b"^v"}}},
                "type": b"@?"},
        },
    })
    # Outgoing calls whose completion handler is a Python callable: PyObjC
    # must know the block shape to build one.
    objc.registerMetaDataForSelector(
        b"UNUserNotificationCenter", b"requestAuthorizationWithOptions:completionHandler:", {
            "arguments": {
                2: {"type": b"Q"},
                3: {"callable": {"retval": {"type": b"v"},
                                 "arguments": {0: {"type": b"^v"}, 1: {"type": b"Z"},
                                               2: {"type": b"@"}}},
                    "type": b"@?"},
            },
        })
    objc.registerMetaDataForSelector(
        b"UNUserNotificationCenter", b"addNotificationRequest:withCompletionHandler:", {
            "arguments": {
                2: {"type": b"@"},
                3: {"callable": {"retval": {"type": b"v"},
                                 "arguments": {0: {"type": b"^v"}, 1: {"type": b"@"}}},
                    "type": b"@?"},
            },
        })


def _make_display(on_click: Callable[[str], None]):
    from Foundation import NSBundle

    if NSBundle.mainBundle().bundleIdentifier() is None:
        return _LogOnlyDisplay()
    try:
        return _UNDisplay(on_click)
    except Exception:  # noqa: BLE001 — a bundle whose UN setup fails still runs
        logger.exception("UNUserNotificationCenter unavailable; logging notifications")
        return _LogOnlyDisplay()


class _UNDisplay:
    """Bundled app: UNUserNotificationCenter. Only ever constructed when
    ``bundleIdentifier`` is set (see `_make_display`)."""

    def __init__(self, on_click: Callable[[str], None]) -> None:
        import objc

        objc.loadBundle("UserNotifications", {},
                        bundle_path="/System/Library/Frameworks/UserNotifications.framework")
        _register_un_metadata()
        self._Content = objc.lookUpClass("UNMutableNotificationContent")
        self._Request = objc.lookUpClass("UNNotificationRequest")
        try:
            self._Sound = objc.lookUpClass("UNNotificationSound")
        except objc.nosuchclass_error:  # a WebKit-only future without it: banners stay mute
            self._Sound = None
        self._center = objc.lookUpClass("UNUserNotificationCenter").currentNotificationCenter()
        self._delegate = _UNDelegate.alloc().initWithCallback_options_(
            on_click, self.options_for)
        self._center.setDelegate_(self._delegate)
        self._authorized: bool | None = None
        self._pending: list[tuple[str, str, str, bool]] = []
        # Identifiers posted with sound=False; `willPresentNotification`
        # asks `options_for` and leaves the sound bit off for these.
        self._silent: set[str] = set()
        self._center.requestAuthorizationWithOptions_completionHandler_(
            _UN_AUTH_BADGE_SOUND_ALERT, self._authorized_cb)

    def describe(self) -> str:
        return "UNUserNotificationCenter"

    def _authorized_cb(self, granted, error) -> None:
        # Background queue → main thread before touching anything shared.
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._authorized_main, bool(granted), error)

    def _authorized_main(self, granted: bool, error) -> None:
        self._authorized = granted
        logger.info("notification authorization %s%s", "granted" if granted else "denied",
                    f" ({error.localizedDescription()})" if error is not None else "")
        pending, self._pending = self._pending, []
        for identifier, title, body, sound in pending:
            self.show(identifier, title, body, sound)

    def options_for(self, identifier: str) -> int:
        """Presentation options for a banner about to be shown in the
        foreground: no sound bit for one posted with ``sound=False``."""
        if identifier in self._silent:
            return _UN_PRESENT_BANNER_LIST
        return _UN_PRESENT_BANNER_LIST_SOUND

    def show(self, identifier: str, title: str, body: str, sound: bool) -> None:
        # Recorded before the authorization gate so the answer is right when
        # the queued banner is replayed too (replay re-records it anyway).
        if sound:
            self._silent.discard(identifier)
        else:
            self._silent.add(identifier)
        if self._authorized is None:
            self._pending.append((identifier, title, body, sound))  # answer still in flight
            return
        if not self._authorized:
            logger.info("notification %s suppressed: notifications not authorized", identifier)
            return
        content = self._Content.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        if sound and self._Sound is not None:
            # `willPresentNotification` only decides the FOREGROUND case; a
            # banner delivered while another app is frontmost plays whatever
            # the content carries, and a content with no sound is mute. A
            # terminal "done"/"failed" is the one the user walked away from,
            # so it is exactly the one that has to be audible.
            content.setSound_(self._Sound.defaultSound())
        request = self._Request.requestWithIdentifier_content_trigger_(
            identifier, content, None)

        def done(error):
            if error is not None:
                logger.warning("notification %s: %s", identifier, error.localizedDescription())

        self._center.addNotificationRequest_withCompletionHandler_(request, done)

    def remove(self, identifiers: list[str]) -> None:
        if not identifiers:
            return
        # Closed while the authorization answer was still in flight: drop it
        # from the queue too, or it would be posted once the answer lands
        # (a banner for a notification the page already closed).
        gone = set(identifiers)
        self._pending = [p for p in self._pending if p[0] not in gone]
        self._silent -= gone
        self._center.removeDeliveredNotificationsWithIdentifiers_(identifiers)
        self._center.removePendingNotificationRequestsWithIdentifiers_(identifiers)


def _un_delegate_class():
    from Foundation import NSObject
    import objc

    class _UNDelegate(NSObject):
        def initWithCallback_options_(self, callback, options_for):
            self = objc.super(_UNDelegate, self).init()
            if self is None:
                return None
            self._callback = callback
            self._options = options_for
            return self

        def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                self, center, notification, completion):
            # Show the banner even while we are the frontmost app — a browser
            # tab's notification shows regardless of focus too. Sound bit per
            # banner (a job merely starting is silent).
            try:
                options = self._options(str(notification.request().identifier()))
            except Exception:  # noqa: BLE001 — never leave the block uncalled
                logger.exception("notification: presentation options failed")
                options = _UN_PRESENT_BANNER_LIST_SOUND
            completion(options)

        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                self, center, response, completion):
            try:
                self._callback(str(response.notification().request().identifier()))
            finally:
                completion()

    return _UNDelegate


class _LazyUNDelegate:
    """`_UNDelegate` is defined at first use, so importing this module needs
    no Foundation (CI)."""

    _cls = None

    def alloc(self):
        if _LazyUNDelegate._cls is None:
            _LazyUNDelegate._cls = _un_delegate_class()
        return _LazyUNDelegate._cls.alloc()


_UNDelegate = _LazyUNDelegate()
