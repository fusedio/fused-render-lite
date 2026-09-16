"""The pure part of `webnotify`: which origins are reported granted, how
WebKit notification ids map to UNNotification identifiers, how banner clicks
are routed by identifier prefix, and that `notify()` degrades to a log line
where no display can exist. Importing the module must not load WebKit (CI
has none); every native import lives inside a function."""
import logging

import pytest

from fused_render_app import webnotify


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    # The display singleton, the click registry and the log-once flag are
    # module globals; tests must not leak them into each other.
    monkeypatch.setattr(webnotify, "_display", None)
    monkeypatch.setattr(webnotify, "_click_handlers", {})
    monkeypatch.setattr(webnotify, "_no_display_logged", False)


def test_granted_origins_are_own_loopback_origins_only():
    assert webnotify.granted_origins(3123) == {
        "http://127.0.0.1:3123", "http://localhost:3123"}


def test_granted_origins_empty_before_server_binds():
    assert webnotify.granted_origins(None) == set()
    assert webnotify.granted_origins(0) == set()


def test_identifier_round_trip():
    assert webnotify.identifier_for(7) == "web-7"
    assert webnotify.notification_id_from("web-7") == 7
    assert webnotify.notification_id_from("web-7") == webnotify.notification_id_from(
        webnotify.identifier_for(7))


def test_foreign_identifiers_are_not_ours():
    assert webnotify.notification_id_from(None) is None
    assert webnotify.notification_id_from("") is None
    assert webnotify.notification_id_from("other-3") is None
    assert webnotify.notification_id_from("web-x") is None


# ---- click registry ----------------------------------------------------------

def test_handler_for_picks_longest_matching_prefix():
    general = lambda i: None  # noqa: E731
    specific = lambda i: None  # noqa: E731
    web = lambda i: None  # noqa: E731
    webnotify.register_click_handler("job:", general)
    webnotify.register_click_handler("job:sys:ai-model:", specific)
    webnotify.register_click_handler(webnotify.IDENTIFIER_PREFIX, web)

    assert webnotify._handler_for("job:sys:ai-model:llama") is specific
    assert webnotify._handler_for("job:sys:env-install:x") is general
    assert webnotify._handler_for("web-7") is web


def test_handler_for_unknown_identifier_is_none():
    webnotify.register_click_handler("job:", lambda i: None)
    assert webnotify._handler_for("other-3") is None
    assert webnotify._handler_for("") is None
    assert webnotify._handler_for(None) is None
    # Registry empty → nothing matches either.
    webnotify._click_handlers.clear()
    assert webnotify._handler_for("job:x") is None


def test_register_twice_replaces_handler():
    first = lambda i: None  # noqa: E731
    second = lambda i: None  # noqa: E731
    webnotify.register_click_handler("job:", first)
    webnotify.register_click_handler("job:", second)
    assert webnotify._handler_for("job:x") is second
    assert len(webnotify._click_handlers) == 1


def test_dispatch_click_activates_then_calls_handler_on_main(monkeypatch):
    # Drive the main-thread hop synchronously and stub the activation (the
    # real one would spawn an NSApplication inside pytest and steal focus).
    monkeypatch.setattr(webnotify, "_call_after", lambda fn, *a: fn(*a))
    order: list[str] = []
    monkeypatch.setattr(webnotify, "_activate_app", lambda: order.append("activate"))
    webnotify.register_click_handler("job:", lambda i: order.append(i))
    webnotify._dispatch_click("job:sys:ai-model:llama")
    assert order == ["activate", "job:sys:ai-model:llama"]


def test_dispatch_click_survives_activation_failure(monkeypatch):
    # No AppKit (Linux CI): the click still reaches the handler.
    monkeypatch.setattr(webnotify, "_call_after", lambda fn, *a: fn(*a))
    monkeypatch.setattr(webnotify, "_activate_app", _raise_import_error)
    seen: list[str] = []
    webnotify.register_click_handler("job:", seen.append)
    webnotify._dispatch_click("job:x")
    assert seen == ["job:x"]


def test_dispatch_click_without_handler_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(webnotify, "_call_after", lambda fn, *a: fn(*a))
    monkeypatch.setattr(webnotify, "_activate_app", lambda: None)
    with caplog.at_level(logging.INFO, logger=webnotify.logger.name):
        webnotify._dispatch_click("nobody-1")
    assert any("no handler" in r.getMessage() for r in caplog.records)


# ---- notify() without AppKit ---------------------------------------------------

def _raise_import_error(*_a, **_k):
    raise ImportError("No module named 'Foundation'")


def test_notify_without_display_logs_once_and_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(webnotify, "_call_after", lambda fn, *a: fn(*a))
    monkeypatch.setattr(webnotify, "_make_display", _raise_import_error)
    with caplog.at_level(logging.INFO, logger=webnotify.logger.name):
        webnotify.notify("job:a", "Title", "Body", sound=False)
        webnotify.notify("job:b", "Title", "Body")
        webnotify.remove("job:a")
    infos = [r for r in caplog.records
             if r.levelno == logging.INFO and "unavailable" in r.getMessage()]
    assert len(infos) == 1
    assert webnotify._display is None


def test_notify_without_pyobjc_at_all_does_not_raise(monkeypatch, caplog):
    # `_call_after` itself fails (no PyObjCTools): still a logged no-op.
    monkeypatch.setattr(webnotify, "_call_after", _raise_import_error)
    with caplog.at_level(logging.INFO, logger=webnotify.logger.name):
        webnotify.notify("job:a", "Title", "Body")
        webnotify.remove("job:a")
    assert any("unavailable" in r.getMessage() for r in caplog.records)


def test_notify_uses_lazily_built_display(monkeypatch):
    shown: list[tuple] = []
    removed: list[list[str]] = []

    class FakeDisplay:
        def show(self, identifier, title, body, sound):
            shown.append((identifier, title, body, sound))

        def remove(self, identifiers):
            removed.append(identifiers)

    built: list[object] = []

    def make(on_click):
        built.append(on_click)
        return FakeDisplay()

    monkeypatch.setattr(webnotify, "_call_after", lambda fn, *a: fn(*a))
    monkeypatch.setattr(webnotify, "_make_display", make)
    webnotify.notify("job:a", "T", "B", sound=False)
    webnotify.notify("job:a", "T2", "B2")
    webnotify.remove("job:a")
    assert shown == [("job:a", "T", "B", False), ("job:a", "T2", "B2", True)]
    assert removed == [["job:a"]]
    # Built once, wired to the module dispatcher, and kept for `install`.
    assert built == [webnotify._dispatch_click]
    assert isinstance(webnotify._display, FakeDisplay)


def test_remove_without_display_does_not_build_one(monkeypatch):
    monkeypatch.setattr(webnotify, "_call_after", lambda fn, *a: fn(*a))
    monkeypatch.setattr(webnotify, "_make_display", _raise_import_error)
    webnotify.remove("job:a")
    assert webnotify._display is None
