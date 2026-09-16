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

Import of this module never loads WebKit or ctypes symbols (CI runs on
Linux); everything native happens in `install`. The pure helpers at the top
are what the tests cover.
"""
from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

IDENTIFIER_PREFIX = "web-"

# UNNotificationPresentationOptions (macOS 11+): banner | list | sound.
_UN_PRESENT_BANNER_LIST_SOUND = (1 << 4) | (1 << 3) | (1 << 1)
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
        self._display = _make_display(self._clicked)
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
            self._display.show(nid, title or "", body or "")
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
        """A banner was clicked. Any thread (UN delivers on a background
        queue); the WebKit and AppKit work hops to the main thread."""
        nid = notification_id_from(identifier)
        if nid is None:
            return
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._clicked_main, nid)

    def _clicked_main(self, nid: int) -> None:
        origin = self._origins.get(nid)
        logger.info("web notification #%d clicked", nid)
        try:
            from AppKit import NSApp

            NSApp.activateIgnoringOtherApps_(True)
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

    def show(self, nid: int, title: str, body: str) -> None:
        logger.info("web notification #%d not displayed (unbundled run): %s — %s",
                    nid, title, body)

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
        self._center = objc.lookUpClass("UNUserNotificationCenter").currentNotificationCenter()
        self._delegate = _UNDelegate.alloc().initWithCallback_(on_click)
        self._center.setDelegate_(self._delegate)
        self._authorized: bool | None = None
        self._pending: list[tuple[int, str, str]] = []
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
        for nid, title, body in pending:
            self.show(nid, title, body)

    def show(self, nid: int, title: str, body: str) -> None:
        if self._authorized is None:
            self._pending.append((nid, title, body))  # answer still in flight
            return
        if not self._authorized:
            logger.info("web notification #%d suppressed: notifications not authorized", nid)
            return
        content = self._Content.alloc().init()
        content.setTitle_(title)
        content.setBody_(body)
        request = self._Request.requestWithIdentifier_content_trigger_(
            identifier_for(nid), content, None)

        def done(error):
            if error is not None:
                logger.warning("web notification #%d: %s", nid, error.localizedDescription())

        self._center.addNotificationRequest_withCompletionHandler_(request, done)

    def remove(self, identifiers: list[str]) -> None:
        if identifiers:
            self._center.removeDeliveredNotificationsWithIdentifiers_(identifiers)
            self._center.removePendingNotificationRequestsWithIdentifiers_(identifiers)


def _un_delegate_class():
    from Foundation import NSObject
    import objc

    class _UNDelegate(NSObject):
        def initWithCallback_(self, callback):
            self = objc.super(_UNDelegate, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                self, center, notification, completion):
            # Show the banner even while we are the frontmost app — a browser
            # tab's notification shows regardless of focus too.
            completion(_UN_PRESENT_BANNER_LIST_SOUND)

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
