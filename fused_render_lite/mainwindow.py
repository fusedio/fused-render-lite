"""Render Lite's windows — native NSWindows hosting the pages, not browser tabs.

Before this module the macOS app was a menu-bar process that pushed every
surface into the default browser: the placeholder, a Finder-opened .fused, a
Dock click. Now each opens (or focuses) a window of this app: an `NSWindow`
whose content view is a `WKWebView` pointed at the one in-process server.
Any number of windows, all on that one server, sharing one
`WKWebsiteDataStore` (an app's localStorage is one set across windows, and
`storage` events keep them in step the way browser tabs were) and one
`WKProcessPool`.

What the browser used to do for a page, the delegates here do instead
(`window_policy.py` holds the decisions; this module enacts them):

- `target=_blank`, `window.open`, ⌘-click / middle-click on an app link →
  a NEW WINDOW.
- an external http(s) link → the DEFAULT BROWSER, never a window of ours.
- `<a download>`, `Content-Disposition: attachment`, un-showable MIME types →
  saved into ~/Downloads under a Finder-style unique name.
- `alert()` / `confirm()` / `prompt()` → NSAlert; `<input type=file>` →
  NSOpenPanel; camera/mic requests from the app's own origin → granted (the
  system TCC prompt still gates the hardware); `requestFullscreen` enabled.

A main menu is installed too (rumps never builds one): without an Edit menu
a WKWebView has no ⌘C/⌘V/⌘X/⌘Z/⌘A, and there would be no ⌘W/⌘N/⌘R/⌘P/⌘M.

macOS-only. `macapp.py` imports this lazily inside `main()` and falls back
to `webbrowser.open` if construction fails — the app is never left without a
surface. Every method must run on the main thread; `macapp.py` hops with
`PyObjCTools.AppHelper.callAfter`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import urllib.parse
import webbrowser

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSApp,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRecessed,
    NSButton,
    NSControlSizeLarge,
    NSImage,
    NSLayoutAttributeTrailing,
    NSTitlebarAccessoryViewController,
    NSView,
    NSDistributedNotificationCenter,
    NSDownloadsDirectory,
    NSEventModifierFlagCommand,
    NSMakePoint,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSObject,
    NSOpenPanel,
    NSPrintInfo,
    NSSearchPathForDirectoriesInDomains,
    NSTextField,
    NSURL,
    NSUserDomainMask,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSKeyValueObservingOptionNew, NSURLRequest
from WebKit import (
    WKNavigationActionPolicyAllow,
    WKNavigationActionPolicyCancel,
    WKNavigationActionPolicyDownload,
    WKNavigationResponsePolicyAllow,
    WKNavigationResponsePolicyDownload,
    WKPermissionDecisionGrant,
    WKPermissionDecisionPrompt,
    WKProcessPool,
    WKWebsiteDataStore,
    WKWebView,
    WKWebViewConfiguration,
)

from fused_render_lite import __version__, paths, window_policy
from fused_render_lite.cli import open_url

logger = logging.getLogger(__name__)

APP_NAME = "Render Lite"
DEFAULT_SIZE = (1200, 800)
MIN_SIZE = (560, 360)
FRAME_AUTOSAVE_NAME = "RenderLiteWindow"
# Rides on WebKit's own UA so a page can tell "inside the app" from "a browser".
USER_AGENT_MARKER = f"RenderLite/{__version__}"

_SHIFT = 1 << 17
_CTRL = 1 << 18
_ALT = 1 << 19


def _nsurl(url: str):
    return NSURL.URLWithString_(url)


def app_file_of(url: str | None) -> str | None:
    """The absolute .fused path a window URL is showing (``/open?_file=…``),
    or None for the launcher and everything else. `/open` keeps `_file` in
    the address for the life of the app page (the app itself runs in an
    iframe below it), so this is a stable identity for the window — the menu
    bar Dock's running dot and focus-or-open key on it."""
    if not url:
        return None
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if parts.path != "/open":
        return None
    q = urllib.parse.parse_qs(parts.query)
    val = (q.get("_file") or q.get("file") or [None])[0]
    return os.path.abspath(val) if val else None


def _open_external(url: str) -> None:
    """Hand a URL to the default browser via LaunchServices."""
    if not NSWorkspace.sharedWorkspace().openURL_(_nsurl(url)):
        logger.warning("NSWorkspace refused %s; falling back to webbrowser", url)
        webbrowser.open(url)


def _downloads_dir() -> str:
    found = NSSearchPathForDirectoriesInDomains(NSDownloadsDirectory, NSUserDomainMask, True)
    return str(found[0]) if found else os.path.expanduser("~/Downloads")


class _WebDelegate(NSObject):
    """One per window: navigation + UI + download delegate of its web view,
    and the window's own delegate (close → forget the window). WebKit keeps
    only a WEAK reference to delegates, so `_Window` holds this strongly.

    Deliberately NOT declared with ``protocols=[WKNavigationDelegate, …]``:
    on PyObjC 12 that declaration makes the completion-handler blocks arrive
    WITHOUT a signature (``cannot call block without a signature`` on the
    first navigation), while a plain NSObject subclass picks up the block
    metadata PyObjC registers for these selectors on NSObject. Measured on
    the first launch, not guessed."""

    def initWithManager_window_(self, manager, window):
        self = objc.super(_WebDelegate, self).init()
        if self is None:
            return None
        self._manager = manager
        self._window = window  # the _Window record, not the NSWindow
        self._downloads = []   # strong refs: a WKDownload's delegate is weak too
        return self

    # ---- navigation policy -------------------------------------------------

    def webView_decidePolicyForNavigationAction_decisionHandler_(
            self, webview, action, decision):
        request = action.request()
        url = str(request.URL().absoluteString()) if request and request.URL() else None
        target = action.targetFrame()
        source = action.sourceFrame()
        is_main = bool(target.isMainFrame()) if target is not None else \
            (source is None or bool(source.isMainFrame()))
        flags = int(action.modifierFlags())
        verdict = window_policy.navigation_action(
            url, self._manager.port,
            is_main_frame=is_main,
            has_target_frame=target is not None,
            wants_download=bool(action.shouldPerformDownload()),
            new_window_modifier=bool(flags & NSEventModifierFlagCommand)
            or int(action.buttonNumber()) == 2,
        )
        logger.debug("navigation %s -> %s", url, verdict)
        if verdict == "allow":
            if is_main:
                # Plain Python attribute, main thread: the server thread reads
                # it (WindowManager.open_files) without touching WebKit.
                self._window.app_file = app_file_of(url)
            decision(WKNavigationActionPolicyAllow)
        elif verdict == "download":
            decision(WKNavigationActionPolicyDownload)
        elif verdict == "new_window":
            self._manager.open(url)
            decision(WKNavigationActionPolicyCancel)
        else:  # open_external
            _open_external(url)
            decision(WKNavigationActionPolicyCancel)

    def webView_decidePolicyForNavigationResponse_decisionHandler_(
            self, webview, nav_response, decision):
        response = nav_response.response()
        disposition = None
        if response.respondsToSelector_(b"allHeaderFields"):
            for key in response.allHeaderFields():
                if str(key).lower() == "content-disposition":
                    disposition = str(response.allHeaderFields()[key])
                    break
        verdict = window_policy.response_action(
            is_main_frame=bool(nav_response.isForMainFrame()),
            can_show_mime=bool(nav_response.canShowMIMEType()),
            content_disposition=disposition,
        )
        decision(WKNavigationResponsePolicyDownload if verdict == "download"
                 else WKNavigationResponsePolicyAllow)

    def webView_didFailProvisionalNavigation_withError_(self, webview, nav, error):
        # -999 is "cancelled": every navigation turned into a new window, a
        # browser hand-off or a download reports as one. Not a failure.
        if error is not None and int(error.code()) != -999:
            logger.warning("navigation failed: %s", error.localizedDescription())

    # ---- downloads (WKDownloadDelegate) ------------------------------------

    def webView_navigationAction_didBecomeDownload_(self, webview, action, download):
        self._adopt_download(download)

    def webView_navigationResponse_didBecomeDownload_(self, webview, response, download):
        self._adopt_download(download)

    def _adopt_download(self, download) -> None:
        self._downloads.append(download)
        download.setDelegate_(self)

    def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(
            self, download, response, suggested, completion):
        dest = window_policy.download_destination(_downloads_dir(), str(suggested or ""))
        logger.info("download -> %s", dest)
        completion(NSURL.fileURLWithPath_(dest))

    def downloadDidFinish_(self, download):
        self._downloads = [d for d in self._downloads if d is not download]
        # Bounce the Downloads stack in the Dock, the way Safari does.
        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_(
            "com.apple.DownloadFileFinished", None)

    def download_didFailWithError_resumeData_(self, download, error, resume_data):
        self._downloads = [d for d in self._downloads if d is not download]
        logger.warning("download failed: %s", error.localizedDescription() if error else "?")

    # ---- popups, dialogs, pickers, permissions (WKUIDelegate) --------------

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, webview, configuration, action, features):
        # `window.open(url)`: the policy delegate has not seen this URL yet.
        request = action.request()
        url = str(request.URL().absoluteString()) if request and request.URL() else None
        kind = window_policy.classify(url, self._manager.port)
        if kind == "app":
            self._manager.open(url)
        elif kind == "external":
            _open_external(url)
        # None: we made no view for it; the opener's `window.open` gets null.
        return None

    def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(
            self, webview, message, frame, completion):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(str(message))
        alert.addButtonWithTitle_("OK")
        alert.runModal()
        completion()

    def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
            self, webview, message, frame, completion):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(str(message))
        alert.addButtonWithTitle_("OK")
        alert.addButtonWithTitle_("Cancel")
        completion(alert.runModal() == NSAlertFirstButtonReturn)

    def webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_(
            self, webview, prompt, default_text, frame, completion):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(str(prompt))
        alert.addButtonWithTitle_("OK")
        alert.addButtonWithTitle_("Cancel")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 24))
        field.setStringValue_(str(default_text or ""))
        alert.setAccessoryView_(field)
        alert.window().setInitialFirstResponder_(field)
        if alert.runModal() == NSAlertFirstButtonReturn:
            completion(field.stringValue())
        else:
            completion(None)

    def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(
            self, webview, parameters, frame, completion):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(bool(parameters.allowsDirectories()))
        panel.setAllowsMultipleSelection_(bool(parameters.allowsMultipleSelection()))
        # NSModalResponseOK (1), not NSAlertFirstButtonReturn (1000): panels
        # and alerts answer on different scales.
        if panel.runModal() == NSModalResponseOK:
            completion(list(panel.URLs()))
        else:
            completion(None)

    def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(
            self, webview, origin, frame, capture_type, decision):
        # Our own page asked (a .fused app using the webcam or mic): grant —
        # the system TCC prompt still gates the hardware. A third-party
        # iframe inside an app gets WebKit's own prompt.
        own = str(origin.host()) in ("127.0.0.1", "localhost") and \
            int(origin.port()) == self._manager.port
        decision(WKPermissionDecisionGrant if own else WKPermissionDecisionPrompt)

    # ---- window title follows the page title (KVO) -------------------------

    def observeValueForKeyPath_ofObject_change_context_(self, key, obj, change, ctx):
        if key == "title":
            self._window.set_title(str(obj.title() or ""))

    # ---- NSWindowDelegate --------------------------------------------------

    def windowWillClose_(self, notification):
        self._manager._forget(self._window)


class _Window:
    """One open window: the NSWindow, its WKWebView, and the strong delegate."""

    def __init__(self, manager: "WindowManager", url: str, configuration):
        self.manager = manager
        self.app_file: str | None = app_file_of(url)
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable)
        w, h = DEFAULT_SIZE
        self.ns = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
        # AppKit must not release the window on close while Python still
        # holds it (⌘W on a second window would crash). We own the lifetime.
        self.ns.setReleasedWhenClosed_(False)
        self.ns.setMinSize_(NSMakeSize(*MIN_SIZE))
        self.ns.setTitle_(APP_NAME)
        self.ns.setTabbingMode_(2)  # NSWindowTabbingModeDisallowed: windows, not tabs

        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, w, h), configuration)
        self.webview.setAllowsBackForwardNavigationGestures_(True)
        self.ns.setContentView_(self.webview)

        self.delegate = _WebDelegate.alloc().initWithManager_window_(manager, self)
        self.webview.setNavigationDelegate_(self.delegate)
        self.webview.setUIDelegate_(self.delegate)
        self.webview.addObserver_forKeyPath_options_context_(
            self.delegate, "title", NSKeyValueObservingOptionNew, None)
        self.ns.setDelegate_(self.delegate)
        self._add_titlebar_button()

        self._place()
        self.webview.loadRequest_(NSURLRequest.requestWithURL_(_nsurl(url)))

    def _add_titlebar_button(self) -> None:
        """An "Open in Browser" button at the right end of the title bar.

        A titlebar accessory keeps the standard titled window (title stays
        centred, traffic lights untouched) — no toolbar row, no
        full-size-content-view mask. Same action as the ⌘⇧L menu item.
        """
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "safari", "Open in Browser")
        button = NSButton.buttonWithImage_target_action_(
            image, self.manager._menu_target, b"openInBrowser:")
        button.setBezelStyle_(NSBezelStyleRecessed)
        button.setBordered_(False)
        button.setToolTip_("Open in Browser (⌘⇧L)")
        button.setControlSize_(NSControlSizeLarge)
        button.sizeToFit()
        bw = button.frame().size.width
        bh = button.frame().size.height
        pad = 10  # breathing room from the window's right edge
        # Title-bar height; the accessory is bottom-aligned, so a holder this
        # tall with the button centred lines it up with the title text.
        bar = self.ns.frame().size.height - self.ns.contentLayoutRect().size.height
        hh = max(bh, bar)
        holder = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, bw + pad, hh))
        button.setFrameOrigin_(NSMakePoint(0, round((hh - bh) / 2)))
        holder.addSubview_(button)

        vc = NSTitlebarAccessoryViewController.alloc().init()
        vc.setView_(holder)
        vc.setLayoutAttribute_(NSLayoutAttributeTrailing)
        self.ns.addTitlebarAccessoryViewController_(vc)

    def _place(self) -> None:
        # The first window restores where the user last left one; each
        # further window cascades from the front window.
        front = self.manager.front()
        if front is None:
            if not self.ns.setFrameUsingName_(FRAME_AUTOSAVE_NAME):
                self.ns.center()
        else:
            frame = front.ns.frame()
            self.ns.setFrame_display_(frame, False)
            self.ns.cascadeTopLeftFromPoint_(
                NSMakePoint(frame.origin.x, frame.origin.y + frame.size.height))
        self.ns.setFrameAutosaveName_(FRAME_AUTOSAVE_NAME)

    def set_title(self, title: str) -> None:
        self.ns.setTitle_(title or APP_NAME)

    def show(self) -> None:
        if self.ns.isMiniaturized():  # a Dock click restores a minimized window
            self.ns.deminiaturize_(None)
        self.ns.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

    def current_url(self) -> str | None:
        u = self.webview.URL()
        return str(u.absoluteString()) if u is not None else None

    def teardown(self) -> None:
        try:
            self.webview.removeObserver_forKeyPath_(self.delegate, "title")
        except Exception:  # noqa: BLE001 — already removed; nothing to undo
            pass
        self.webview.setNavigationDelegate_(None)
        self.webview.setUIDelegate_(None)
        self.ns.setDelegate_(None)


class _MenuTarget(NSObject):
    """Receiver of the main menu's app-specific items. The Edit menu's items
    target nil and reach the web view through the responder chain."""

    def initWithManager_(self, manager):
        self = objc.super(_MenuTarget, self).init()
        if self is None:
            return None
        self._m = manager
        return self

    def newWindow_(self, _s):
        self._m.open(self._m.home_url)

    def openDocument_(self, _s):
        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(True)
        panel.setAllowedFileTypes_(["fused"])
        panel.setTitle_(f"Open in {APP_NAME}")
        if panel.runModal() == NSModalResponseOK:
            for u in panel.URLs():
                self._m.open_file(str(u.path()))

    def reload_(self, _s):
        if (w := self._m.key()) is not None:
            w.webview.reload()

    def goBack_(self, _s):
        if (w := self._m.key()) is not None:
            w.webview.goBack()

    def goForward_(self, _s):
        if (w := self._m.key()) is not None:
            w.webview.goForward()

    def goHome_(self, _s):
        if (w := self._m.key()) is not None:
            w.webview.loadRequest_(NSURLRequest.requestWithURL_(_nsurl(self._m.home_url)))
        else:
            self._m.open(self._m.home_url)

    def openInBrowser_(self, _s):
        w = self._m.key()
        webbrowser.open((w and w.current_url()) or self._m.home_url)

    def copyUrl_(self, _s):
        w = self._m.key()
        url = (w and w.current_url()) or self._m.home_url
        subprocess.run(["pbcopy"], input=url.encode(), check=False)

    def printDocument_(self, _s):
        if (w := self._m.key()) is None:
            return
        op = w.webview.printOperationWithPrintInfo_(NSPrintInfo.sharedPrintInfo())
        op.setShowsPrintPanel_(True)
        op.runOperationModalForWindow_delegate_didRunSelector_contextInfo_(
            w.ns, None, None, None)

    def showLogs_(self, _s):
        subprocess.run(["open", "-R", paths.log_path()], check=False)

    def quitApp_(self, _s):
        self._m.quit()


class WindowManager:
    """All open windows of this app, one server behind them.

    ``quit`` is `macapp.py`'s quit action, so ⌘Q from our menu funnels through
    the same shutdown as the menu-bar Quit.
    """

    def __init__(self, port: int, quit) -> None:
        self.port = port
        self.home_url = open_url(port, None)
        self.quit = quit
        self._windows: list[_Window] = []

        # One data store and one process pool for every window: an app's
        # localStorage is a single set, and `storage` events reach the other
        # windows exactly as they reached other tabs of one browser.
        self._pool = WKProcessPool.alloc().init()
        self._configuration = self._make_configuration()

        # A `.venv/bin/python -m fused_render_lite.macapp` dev run is not a
        # bundle, and AppKit then defaults to a policy under which windows
        # never take focus. A regular app either way.
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        self._menu_target = _MenuTarget.alloc().initWithManager_(self)
        NSApp.setMainMenu_(_build_main_menu(self._menu_target))

    def _make_configuration(self):
        config = WKWebViewConfiguration.alloc().init()
        config.setProcessPool_(self._pool)
        config.setWebsiteDataStore_(WKWebsiteDataStore.defaultDataStore())
        config.setApplicationNameForUserAgent_(USER_AGENT_MARKER)
        prefs = config.preferences()
        if prefs.respondsToSelector_(b"setElementFullscreenEnabled:"):
            prefs.setElementFullscreenEnabled_(True)
        # Right-click → Inspect Element: a .fused app is somebody's HTML, and
        # the inspector is the debugging surface a browser tab used to give.
        try:
            prefs.setValue_forKey_(True, "developerExtrasEnabled")
        except Exception:  # noqa: BLE001 — a WebKit without the private key
            logger.debug("developerExtrasEnabled not settable", exc_info=True)
        # Autoplaying media did not need a click in a browser tab either.
        config.setMediaTypesRequiringUserActionForPlayback_(0)
        return config

    # ---- what macapp.py calls -----------------------------------------------

    def set_port(self, port: int) -> None:
        """The port the server actually bound (it can differ from the one
        picked before binding). Any thread; plain attribute writes."""
        self.port = port
        self.home_url = open_url(port, None)

    def open(self, url: str) -> _Window:
        """Open ``url`` in a NEW window and bring it to the front."""
        win = _Window(self, url, self._configuration)
        self._windows.append(win)
        win.show()
        return win

    def open_file(self, fs_path: str) -> _Window:
        return self.open(open_url(self.port, fs_path))

    def reopen(self) -> None:
        """A macOS Dock-icon click on the running app: the front window if
        there is one (whatever it shows — the user put it there), else a
        fresh Home window."""
        front = self.front()
        if front is not None:
            front.show()
        else:
            self.open(self.home_url)

    def show_home(self) -> None:
        """Dock semantics for the Home tile: a window already showing Home
        (no .fused file) comes to the front — the key/front one if several —
        otherwise a fresh Home window opens, even if app windows are open."""
        homes = [w for w in self._windows if not w.app_file]
        if homes:
            win = homes[-1]
            for w in reversed(homes):  # prefer the key/front one
                if w is self.key() or w is self.front():
                    win = w
                    break
            win.show()
        else:
            self.open(self.home_url)

    def has_windows(self) -> bool:
        return bool(self._windows)

    # ---- what the menu-bar Dock asks (see menubar_dock.py, server.native_hooks)

    def open_files(self) -> set[str]:
        """The .fused files currently showing in a window. Safe from ANY
        thread: reads Python attributes only, never WebKit."""
        return {w.app_file for w in list(self._windows) if w.app_file}

    def window_for(self, fs_path: str) -> _Window | None:
        fs_path = os.path.abspath(fs_path)
        for w in self._windows:
            if w.app_file == fs_path:
                return w
        return None

    def focus_or_open(self, fs_path: str) -> _Window:
        """Dock semantics: an app already open comes to the front (its most
        recently used window if several), otherwise it opens fresh."""
        win = self.window_for(fs_path)
        if win is not None:
            for w in reversed(self._windows):  # prefer the key/front one
                if w.app_file == win.app_file and (w is self.key() or w is self.front()):
                    win = w
                    break
            win.show()
            return win
        return self.open_file(fs_path)

    def choose_file(self) -> None:
        """The Dock's "Open…" slot: pick .fused files. The tray is a
        non-activating panel, so this app may not be active when the click
        lands — activate first or the modal panel opens behind other apps."""
        NSApp.activateIgnoringOtherApps_(True)
        self._menu_target.openDocument_(None)

    def front(self) -> _Window | None:
        return self.key() or (self._windows[-1] if self._windows else None)

    def key(self) -> _Window | None:
        kw = NSApp.keyWindow()
        if kw is None:
            return None
        for w in self._windows:
            if w.ns.isEqual_(kw):
                return w
        return None

    def _forget(self, win: _Window) -> None:
        if win in self._windows:
            self._windows.remove(win)
        win.teardown()


def _build_main_menu(target) -> NSMenu:
    def item(title, action, key="", mods=None, tgt=target):
        it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        if mods is not None:
            it.setKeyEquivalentModifierMask_(mods)
        if tgt is not None:
            it.setTarget_(tgt)
        return it

    def submenu(title, items, main):
        menu = NSMenu.alloc().initWithTitle_(title)
        for it in items:
            menu.addItem_(it)
        holder = NSMenuItem.alloc().init()
        holder.setSubmenu_(menu)
        main.addItem_(holder)
        return menu

    CMD = NSEventModifierFlagCommand
    sep = NSMenuItem.separatorItem
    main = NSMenu.alloc().init()

    submenu(APP_NAME, [
        item(f"About {APP_NAME}", b"orderFrontStandardAboutPanel:", tgt=None),
        sep(),
        item(f"Hide {APP_NAME}", b"hide:", "h", tgt=None),
        item("Hide Others", b"hideOtherApplications:", "h", CMD | _ALT, tgt=None),
        item("Show All", b"unhideAllApplications:", tgt=None),
        sep(),
        item(f"Quit {APP_NAME}", b"quitApp:", "q"),
    ], main)

    submenu("File", [
        item("New Window", b"newWindow:", "n"),
        item("Open…", b"openDocument:", "o"),
        sep(),
        item("Close Window", b"performClose:", "w", tgt=None),
        sep(),
        item("Print…", b"printDocument:", "p"),
    ], main)

    # Standard selectors, nil target: the responder chain delivers them to
    # the web view, which is what gives a WKWebView its ⌘C/⌘V/⌘X/⌘Z/⌘A.
    submenu("Edit", [
        item("Undo", b"undo:", "z", tgt=None),
        item("Redo", b"redo:", "z", CMD | _SHIFT, tgt=None),
        sep(),
        item("Cut", b"cut:", "x", tgt=None),
        item("Copy", b"copy:", "c", tgt=None),
        item("Paste", b"paste:", "v", tgt=None),
        item("Delete", b"delete:", tgt=None),
        item("Select All", b"selectAll:", "a", tgt=None),
    ], main)

    submenu("View", [
        item("Reload Page", b"reload:", "r"),
        item("Back", b"goBack:", "["),
        item("Forward", b"goForward:", "]"),
        item("Home", b"goHome:", "H", CMD | _SHIFT),
        sep(),
        item("Open in Browser", b"openInBrowser:", "L", CMD | _SHIFT),
        item("Copy URL", b"copyUrl:", "C", CMD | _SHIFT),
        sep(),
        item("Enter Full Screen", b"toggleFullScreen:", "f", CMD | _CTRL, tgt=None),
    ], main)

    window_menu = submenu("Window", [
        item("Minimize", b"performMiniaturize:", "m", tgt=None),
        item("Zoom", b"performZoom:", tgt=None),
        sep(),
        item("Bring All to Front", b"arrangeInFront:", tgt=None),
    ], main)
    NSApp.setWindowsMenu_(window_menu)

    help_menu = submenu("Help", [
        item("Show App Logs in Finder", b"showLogs:"),
    ], main)
    NSApp.setHelpMenu_(help_menu)

    return main
