"""The menu-bar Dock: a click on the status item drops a macOS-Dock-like tray
of the .fused apps you have opened — pinned ones first, then recent — each a
tile with the app's own ``icon.svg`` (or ``icon.png``), its name beneath, and a dot when it is
open in a window. A click focuses the app's window if there is one, else
opens it; right-click pins, reveals, forgets.

The tray is HTML (``static/dock.html``, served at ``/dock`` by the in-process
server) inside a transparent ``WKWebView``. It floats: the host is a
borderless, non-opaque ``NSPanel`` under the status item, and the glass is an
``NSVisualEffectView`` with rounded corners sized to the tray rect the page
reports — nothing else is drawn, so there is no popover box around the Dock
(an ``NSPopover`` always paints its own chrome; that is why it is not one).
The page reports its size and the tray rect through a script message and
the panel follows.

Status item: rumps attached an ``NSMenu`` to it, which AppKit opens on every
click without ever firing the button's action. The menu is taken off; left
click toggles the panel, right click (or ⌃-click) shows a small utility menu
(launcher, browser, logs, quit) so Quit stays one click away.

macOS-only, like rumps. ``macapp.py`` builds this lazily after the run loop is
up and keeps rumps' menu when construction fails — the app is never left
without a Quit.
"""
from __future__ import annotations

import logging
import math
import os
import subprocess

import AppKit
import objc
from AppKit import (
    NSAnimationContext,
    NSApp,
    NSColor,
    NSEvent,
    NSEventMaskLeftMouseDown,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseDown,
    NSEventMaskRightMouseUp,
    NSEventModifierFlagControl,
    NSEventTypeRightMouseUp,
    NSMakePoint,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSObject,
    NSPanel,
    NSStatusWindowLevel,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialPopover,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorTransient,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
    NSWorkspace,
    NSBackingStoreBuffered,
)
from Foundation import NSURL, NSURLRequest
from WebKit import (
    WKNavigationActionPolicyAllow,
    WKNavigationActionPolicyCancel,
    WKUserContentController,
    WKWebView,
    WKWebViewConfiguration,
)

from fused_render_app import dock_store, server
from fused_render_app.cli import open_url
from fused_render_app.mainwindow import USER_AGENT_MARKER, _open_external

logger = logging.getLogger(__name__)

# Before the page has reported a size: room for two utility tiles + hint.
INITIAL_SIZE = (360, 100)
MIN_SIZE = (120, 60)
MAX_SIZE = (1400, 420)
MESSAGE_NAME = "dock"
TRAY_RADIUS = 18.0
GAP_BELOW_MENU_BAR = 4.0

# Appear / dismiss: the panel drops from the menu bar (starts tucked up by
# APPEAR_OFFSET, fully transparent) and settles into place; dismissal is the
# reverse, quicker, since it follows a click elsewhere and must not feel
# laggy. The fade is decoupled from the motion and much shorter: the tray is
# fully visible while most of the travel still happens, so the movement
# reads (a fade as long as the slide hides the slide). The slide curve is a
# spring-like "expo out": fast start, long soft settle, no overshoot — window
# frames cannot take a CASpringAnimation, this is the closest bezier.
# Skipped when Reduce Motion is on.
APPEAR_OFFSET = 18.0
APPEAR_DURATION = 0.42
APPEAR_FADE = 0.14
DISMISS_OFFSET = 8.0
DISMISS_DURATION = 0.2
DISMISS_FADE = 0.16
EASE_SPRING = (0.16, 1.0, 0.3, 1.0)
EASE_IN = (0.4, 0.0, 1.0, 1.0)
EASE_LINEAR = (0.0, 0.0, 1.0, 1.0)


# QuartzCore class, reached through the runtime: it is already loaded by
# AppKit, and pyobjc-framework-Quartz is not a dependency.
CAMediaTimingFunction = objc.lookUpClass("CAMediaTimingFunction")


def _reduce_motion() -> bool:
    try:
        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:  # noqa: BLE001
        return False


def _make_glass(w: float, h: float):
    """The tray's glass: Liquid Glass (``NSGlassEffectView``, macOS 26) with a
    light tint, or the pre-26 ``NSVisualEffectView`` popover material. A
    sibling of the web view, not its container: magnified tiles and the name
    bubble hang outside the glass, and a container would clip them."""
    GlassView = getattr(AppKit, "NSGlassEffectView", None)
    if GlassView is not None:
        glass = GlassView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        if glass.respondsToSelector_(b"setCornerRadius:"):
            glass.setCornerRadius_(TRAY_RADIUS)
            glass.setTintColor_(NSColor.colorWithWhite_alpha_(0.5, 0.12))
            logger.info("dock glass: NSGlassEffectView")
            return glass
    glass = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    glass.setMaterial_(NSVisualEffectMaterialPopover)
    glass.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
    glass.setState_(NSVisualEffectStateActive)
    glass.setWantsLayer_(True)
    glass.layer().setCornerRadius_(TRAY_RADIUS)
    glass.layer().setMasksToBounds_(True)
    if glass.layer().respondsToSelector_(b"setCornerCurve:"):
        glass.layer().setCornerCurve_("continuous")  # squircle, like the Dock
    logger.info("dock glass: NSVisualEffectView")
    return glass


class _Target(NSObject):
    """Action target for the status button and the utility menu."""

    def initWithController_(self, controller):
        self = objc.super(_Target, self).init()
        if self is None:
            return None
        self._c = controller
        return self

    def statusItemClicked_(self, _sender):
        self._c.status_item_clicked()

    def openLauncher_(self, _s):
        self._c.actions["show_home"]()

    def openBrowser_(self, _s):
        self._c.actions["open_browser"]()

    def openLogs_(self, _s):
        self._c.actions["open_logs"]()

    def quitApp_(self, _s):
        self._c.actions["quit"]()

    # ---- per-tile menu (representedObject = the .fused path) -------------------

    def itemOpen_(self, sender):
        self._c.item_open(sender.representedObject())

    def itemPin_(self, sender):
        self._c.item_pin(sender.representedObject(), True)

    def itemUnpin_(self, sender):
        self._c.item_pin(sender.representedObject(), False)

    def itemReveal_(self, sender):
        self._c.item_reveal(sender.representedObject())

    def itemBrowser_(self, sender):
        self._c.item_browser(sender.representedObject())

    def itemForget_(self, sender):
        self._c.item_forget(sender.representedObject())


class _ScriptHandler(NSObject):
    """``window.webkit.messageHandlers.dock.postMessage({...})`` lands here.
    Plain NSObject subclass, not declared with the protocol — same PyObjC
    reason as ``mainwindow._WebDelegate``."""

    def initWithController_(self, controller):
        self = objc.super(_ScriptHandler, self).init()
        if self is None:
            return None
        self._c = controller
        return self

    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        try:
            body = message.body()
            data = dict(body) if body is not None else {}
        except Exception:  # noqa: BLE001 — a page can post anything
            return
        kind = data.get("type")
        tray = data.get("tray")
        tray = dict(tray) if tray is not None else None
        if kind == "size":
            self._c.resize_to(data.get("width"), data.get("height"), tray)
        elif kind == "tray":  # per-frame while the fisheye is live: glass only
            self._c.set_tray(tray)
        elif kind == "resize":  # separator drag started / ended
            self._c.set_resizing(bool(data.get("active")))
        elif kind == "menu":
            self._c.show_item_menu(data)


class _DockWebDelegate(NSObject):
    """Navigation + UI delegate of the tray's web view. The tray page is the
    only thing this view ever shows: any other main-frame navigation, and any
    ``target=_blank`` / ``window.open`` (the context menu's "Open in
    Browser"), goes to the default browser. Without a UI delegate WebKit
    silently drops ``_blank`` clicks — same reason the app windows have one."""

    def initWithController_(self, controller):
        self = objc.super(_DockWebDelegate, self).init()
        if self is None:
            return None
        self._c = controller
        return self

    def webView_decidePolicyForNavigationAction_decisionHandler_(self, webview, action, decision):
        request = action.request()
        url = str(request.URL().absoluteString()) if request and request.URL() else ""
        target = action.targetFrame()
        is_main = target is None or bool(target.isMainFrame())
        if not is_main or url == self._c._url() or url.startswith("about:"):
            decision(WKNavigationActionPolicyAllow)
            return
        decision(WKNavigationActionPolicyCancel)
        if url:
            self._c.close()
            _open_external(url)

    def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
            self, webview, config, action, features):
        request = action.request()
        url = str(request.URL().absoluteString()) if request and request.URL() else ""
        if url:
            self._c.close()
            _open_external(url)
        return None


class _DockPanel(NSPanel):
    """Borderless panels refuse key status by default; the tray needs it so
    a click elsewhere resigns it (→ close) and ⎋ reaches the page."""

    def canBecomeKeyWindow(self):
        return True


class _MenuDelegate(NSObject):
    """Tells the page when the tile menu closes, so it can thaw the fisheye
    it froze on that tile while the menu was up."""

    def initWithController_(self, controller):
        self = objc.super(_MenuDelegate, self).init()
        if self is None:
            return None
        self._c = controller
        return self

    def menuDidClose_(self, _menu):
        self._c._webview.evaluateJavaScript_completionHandler_(
            "window.dockMenuClosed && window.dockMenuClosed();", None)


class _PanelDelegate(NSObject):
    def initWithController_(self, controller):
        self = objc.super(_PanelDelegate, self).init()
        if self is None:
            return None
        self._c = controller
        return self

    def windowDidResignKey_(self, _n):
        self._c.close()


class DockController:
    """Owns the status-item click, the floating panel and its web view.

    ``actions``: app-level callbacks for the right-click menu —
    ``show_home`` / ``open_browser`` / ``open_logs`` / ``quit``.
    Main thread only, like everything AppKit.
    """

    def __init__(self, statusitem, port: int, actions: dict) -> None:
        self._statusitem = statusitem
        self._port = port
        self.actions = actions
        self._target = _Target.alloc().initWithController_(self)
        self._handler = _ScriptHandler.alloc().initWithController_(self)
        self._panel_delegate = _PanelDelegate.alloc().initWithController_(self)
        self._web_delegate = _DockWebDelegate.alloc().initWithController_(self)
        self._menu_delegate = _MenuDelegate.alloc().initWithController_(self)
        self._loaded = False
        self._monitor = None
        self._size = INITIAL_SIZE
        self._tray = None  # (x, y, w, h) in page coordinates, top-left origin
        self._resizing = False  # separator drag in progress (resize cursor held)
        self._closing = False  # dismiss animation running (panel still ordered in)
        self._animating_in = False  # appear animation running: _place must not snap
        self._anim_gen = 0  # bumps on every show/close; stale completions bail
        self._build_panel()
        self._take_over_status_item()

    # ---- public ----------------------------------------------------------------

    def set_port(self, port: int) -> None:
        self._port = port

    def server_ready(self) -> None:
        """Load the tray page once the server answers (the panel may be built
        before the port is bound)."""
        self._load()

    def is_shown(self) -> bool:
        # A panel mid-dismiss counts as hidden: a status-item click while it
        # fades out reopens it instead of doing nothing.
        return bool(self._panel.isVisible()) and not self._closing

    def toggle_popover(self) -> None:
        if self.is_shown():
            self.close()
        else:
            self.show_popover()

    def close_popover(self) -> None:
        self.close()

    def close(self) -> None:
        if self._monitor is not None:
            NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
        if not self._panel.isVisible() or self._closing:
            return
        self._anim_gen += 1
        gen = self._anim_gen
        self._animating_in = False
        if _reduce_motion():
            self._panel.orderOut_(None)
            return
        self._closing = True
        frame = self._panel.frame()
        target = NSMakeRect(frame.origin.x, frame.origin.y + DISMISS_OFFSET,
                            frame.size.width, frame.size.height)

        def done():
            if gen != self._anim_gen:
                return  # re-shown mid-fade: leave it up
            self._closing = False
            self._panel.orderOut_(None)
            self._panel.setAlphaValue_(1.0)

        self._animate(target, DISMISS_DURATION, EASE_IN, 0.0, DISMISS_FADE, done)

    def show_popover(self) -> None:
        if not self._loaded:
            self._load()
        self._anim_gen += 1
        gen = self._anim_gen
        self._closing = False
        self._place()
        if _reduce_motion():
            self._panel.setAlphaValue_(1.0)
            self._panel.makeKeyAndOrderFront_(None)
        else:
            rest = self._panel.frame()
            start = NSMakeRect(rest.origin.x, rest.origin.y + APPEAR_OFFSET,
                               rest.size.width, rest.size.height)
            self._panel.setFrame_display_(start, False)
            self._panel.setAlphaValue_(0.0)
            self._panel.makeKeyAndOrderFront_(None)
            self._animating_in = True

            def done():
                if gen == self._anim_gen:
                    self._animating_in = False

            self._animate(rest, APPEAR_DURATION, EASE_SPRING, 1.0, APPEAR_FADE, done)
        # A click anywhere outside the panel — in another app, on the desktop,
        # on the menu bar — dismisses it, like the Dock's own menus. The
        # non-activating panel does not make us the active app, so
        # resignKey alone would not fire for clicks in whatever app is
        # frontmost; the global monitor covers that case.
        if self._monitor is None:
            mask = (NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown)
            self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                mask, lambda _e: self.close())
        self._webview.evaluateJavaScript_completionHandler_(
            "window.dockShown && window.dockShown();", None)

    def _animate(self, frame, duration, ease, alpha, fade, completion) -> None:
        """Slide the panel to ``frame`` over ``duration`` and fade it to
        ``alpha`` over ``fade`` — two groups, so the fade can be short while
        the slide is long. ``completion`` runs when the slide ends."""
        def group(secs, curve, done, apply):
            NSAnimationContext.beginGrouping()
            ctx = NSAnimationContext.currentContext()
            ctx.setDuration_(secs)
            ctx.setTimingFunction_(CAMediaTimingFunction.functionWithControlPoints____(*curve))
            if done is not None:
                ctx.setCompletionHandler_(done)
            apply()
            NSAnimationContext.endGrouping()

        group(fade, EASE_LINEAR, None, lambda: self._panel.animator().setAlphaValue_(alpha))
        group(duration, ease, completion,
              lambda: self._panel.animator().setFrame_display_(frame, True))

    def set_tray(self, tray: dict | None) -> None:
        """Move the glass to a new tray rect without touching the panel."""
        if not tray:
            return
        try:
            self._tray = tuple(float(tray[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            return
        self._layout_glass()
        if self._resizing:
            # A view frame change makes AppKit re-resolve the cursor (to the
            # arrow): keep the resize cursor up for the whole drag.
            AppKit.NSCursor.resizeUpDownCursor().set()

    def set_resizing(self, active: bool) -> None:
        """The page is dragging the separator (Dock-style resize): hold the
        up/down resize cursor natively, since the web view's CSS cursor does
        not survive the panel and glass frame changes made during the drag."""
        if active == self._resizing:
            return
        self._resizing = active
        if active:
            AppKit.NSCursor.resizeUpDownCursor().push()
        else:
            AppKit.NSCursor.pop()

    def _layout_glass(self) -> None:
        if self._tray is None:
            self._glass.setHidden_(True)
            return
        _w, h = self._size
        tx, ty, tw, th = self._tray
        self._glass.setFrame_(NSMakeRect(tx, h - ty - th, tw, th))
        self._glass.setHidden_(False)

    def resize_to(self, width, height, tray: dict | None = None) -> None:
        try:
            w = float(width)
            h = float(height)
        except (TypeError, ValueError):
            return
        if not (math.isfinite(w) and math.isfinite(h)):
            return
        w = min(max(math.ceil(w), MIN_SIZE[0]), MAX_SIZE[0])
        h = min(max(math.ceil(h), MIN_SIZE[1]), MAX_SIZE[1])
        self._size = (w, h)
        if tray:
            try:
                self._tray = tuple(float(tray[k]) for k in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError):
                self._tray = None
        self._layout()
        # Not while dropping in: _place would snap the panel to rest and cut
        # the slide short (dockShown → refresh → report lands right here).
        if self.is_shown() and not self._animating_in:
            self._place()
        if self._resizing:  # frame changes reset the cursor: see set_resizing
            AppKit.NSCursor.resizeUpDownCursor().set()

    # ---- status item -------------------------------------------------------------

    def status_item_clicked(self) -> None:
        event = NSApp.currentEvent()
        right = False
        if event is not None:
            right = int(event.type()) == NSEventTypeRightMouseUp or bool(
                int(event.modifierFlags()) & NSEventModifierFlagControl)
        if right:
            self.close()
            self._show_utility_menu()
        else:
            self.toggle_popover()

    def _take_over_status_item(self) -> None:
        self._statusitem.setMenu_(None)
        button = self._statusitem.button()
        button.setTarget_(self._target)
        button.setAction_(b"statusItemClicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        logger.info("status item: menu removed, Dock panel on click")

    def _show_utility_menu(self) -> None:
        menu = NSMenu.alloc().initWithTitle_("Render App")

        def item(title, action, key=""):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            it.setTarget_(self._target)
            menu.addItem_(it)

        item("Open Launcher", b"openLauncher:")
        item("Open in Browser", b"openBrowser:")
        menu.addItem_(NSMenuItem.separatorItem())
        item("Open App Logs", b"openLogs:")
        menu.addItem_(NSMenuItem.separatorItem())
        item("Quit Render App", b"quitApp:", "q")
        # With a menu set, a click on the button opens it and fires no
        # action; set it just for this synthetic click, then take it off so
        # the next real click reaches statusItemClicked: again.
        self._statusitem.setMenu_(menu)
        self._statusitem.button().performClick_(None)
        self._statusitem.setMenu_(None)

    # ---- per-tile context menu: a real NSMenu, laid out like the Dock's ------------

    def show_item_menu(self, data: dict) -> None:
        file = str(data.get("file") or "")
        if not file:
            return
        name = str(data.get("name") or os.path.basename(file))
        pinned = bool(data.get("pinned"))
        running = bool(data.get("running"))
        exists = bool(data.get("exists", True))
        menu = NSMenu.alloc().initWithTitle_(name)
        menu.setAutoenablesItems_(False)
        menu.setDelegate_(self._menu_delegate)

        def add(m, title, action=None, key="", enabled=True, state=False, indent=0):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            it.setTarget_(self._target if action else None)
            it.setRepresentedObject_(file)
            it.setEnabled_(enabled and action is not None)
            it.setState_(1 if state else 0)
            it.setIndentationLevel_(indent)
            m.addItem_(it)
            return it

        # Header, like the Dock's window list: the app's name, checked when open.
        add(menu, name, None, state=running, enabled=False)
        menu.addItem_(NSMenuItem.separatorItem())
        if not running:  # the Dock offers Open only for apps that are not running
            add(menu, "Open", b"itemOpen:", enabled=exists)
            menu.addItem_(NSMenuItem.separatorItem())

        options = NSMenu.alloc().initWithTitle_("Options")
        options.setAutoenablesItems_(False)
        if pinned:
            add(options, "Remove from Dock", b"itemUnpin:")
        else:
            add(options, "Keep in Dock", b"itemPin:")
        options.addItem_(NSMenuItem.separatorItem())
        add(options, "Show in Finder", b"itemReveal:", enabled=exists)
        add(options, "Open in Browser", b"itemBrowser:", enabled=exists)
        opt_item = add(menu, "Options", None)
        opt_item.setEnabled_(True)
        menu.setSubmenu_forItem_(options, opt_item)

        if not pinned:
            menu.addItem_(NSMenuItem.separatorItem())
            add(menu, "Forget", b"itemForget:")

        try:
            x = float(data.get("x", 0))
            y = float(data.get("y", 0))
        except (TypeError, ValueError):
            x = y = 0.0
        # WKWebView is flipped, so page coordinates are view coordinates.
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(x, y), self._webview)

    def _refresh_page(self) -> None:
        self._webview.evaluateJavaScript_completionHandler_(
            "window.dockShown && window.dockShown();", None)

    def item_open(self, file: str) -> None:
        hook = server.native_hooks.get("focus_or_open")
        if hook is not None:
            hook(file)
        else:
            self.close()

    def item_pin(self, file: str, pinned: bool) -> None:
        dock_store.set_pinned(file, pinned)
        self._refresh_page()

    def item_forget(self, file: str) -> None:
        dock_store.remove(file)
        self._refresh_page()

    def item_reveal(self, file: str) -> None:
        subprocess.Popen(["open", "-R", file])

    def item_browser(self, file: str) -> None:
        self.close()
        _open_external(open_url(self._port, file))

    # ---- panel -------------------------------------------------------------------

    def _build_panel(self) -> None:
        w, h = INITIAL_SIZE
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = _DockPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
        panel.setReleasedWhenClosed_(False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        # No window shadow: on a non-opaque window AppKit traces every opaque
        # pixel (glass AND tiles) with a dark hairline. The Dock has none.
        panel.setHasShadow_(False)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setHidesOnDeactivate_(False)
        panel.setMovableByWindowBackground_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorTransient)
        panel.setDelegate_(self._panel_delegate)

        root = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        root.setWantsLayer_(True)

        self._glass = _make_glass(w, h)
        root.addSubview_(self._glass)

        config = WKWebViewConfiguration.alloc().init()
        ucc = WKUserContentController.alloc().init()
        ucc.addScriptMessageHandler_name_(self._handler, MESSAGE_NAME)
        config.setUserContentController_(ucc)
        # Same marker as the app windows: the page skips its dev backdrop and
        # its own glass when it sees "RenderApp/" in the UA.
        config.setApplicationNameForUserAgent_(f"{USER_AGENT_MARKER} RenderAppDock")
        try:
            config.preferences().setValue_forKey_(True, "developerExtrasEnabled")
        except Exception:  # noqa: BLE001
            pass

        self._webview = WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, w, h), config)
        # Transparent: only the tiles paint; the glass beneath is native.
        try:
            self._webview.setValue_forKey_(False, "drawsBackground")
        except Exception:  # noqa: BLE001 — opaque page then; still works
            logger.debug("drawsBackground not settable", exc_info=True)
        try:
            self._webview.setUnderPageBackgroundColor_(NSColor.clearColor())
        except Exception:  # noqa: BLE001 — older macOS
            pass
        self._webview.setNavigationDelegate_(self._web_delegate)
        self._webview.setUIDelegate_(self._web_delegate)
        root.addSubview_(self._webview)

        panel.setContentView_(root)
        self._panel = panel
        self._layout()

    def _layout(self) -> None:
        """Panel = page size; glass = the tray rect (flipped into AppKit's
        bottom-left coordinates); webview = whole panel."""
        w, h = self._size
        frame = self._panel.frame()
        if (w, h) != (frame.size.width, frame.size.height):
            # A direct setFrame cancels a running animator slide where it
            # stands. Only touch the frame when the size really changed —
            # the page reports its (unchanged) size on every show.
            self._animating_in = False
            self._panel.setFrame_display_(
                NSMakeRect(frame.origin.x, frame.origin.y + frame.size.height - h, w, h), True)
        self._panel.contentView().setFrame_(NSMakeRect(0, 0, w, h))
        self._webview.setFrame_(NSMakeRect(0, 0, w, h))
        self._layout_glass()

    def _place(self) -> None:
        """Centered under the status item, hanging from the menu bar."""
        button = self._statusitem.button()
        bwin = button.window()
        if bwin is None:
            return
        brect = bwin.convertRectToScreen_(button.convertRect_toView_(button.bounds(), None))
        w, h = self._size
        x = brect.origin.x + brect.size.width / 2 - w / 2
        y = brect.origin.y - GAP_BELOW_MENU_BAR - h
        screen = bwin.screen()
        if screen is not None:
            vis = screen.visibleFrame()
            x = max(vis.origin.x + 4, min(x, vis.origin.x + vis.size.width - w - 4))
        self._panel.setFrameOrigin_((x, y))

    def _url(self) -> str:
        return f"http://127.0.0.1:{self._port}/dock"

    def _load(self) -> None:
        url = self._url()
        self._webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
        self._loaded = True
        logger.info("dock panel loading %s", url)
