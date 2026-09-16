"""The pure part of `webnotify`: which origins are reported granted and how
WebKit notification ids map to UNNotification identifiers. Importing the
module must not load WebKit (CI has none)."""
from fused_render_app import webnotify


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
