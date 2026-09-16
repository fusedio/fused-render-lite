"""The native windows' navigation/download policy (fused_render_app/window_policy.py).

`mainwindow.py` is AppKit and never imports in CI; every decision it acts on
lives in `window_policy.py` so it can be pinned here on any platform.
"""
import pytest

from fused_render_app import window_policy as wp

PORT = 2777
APP = f"http://127.0.0.1:{PORT}"


@pytest.mark.parametrize(
    "url, kind",
    [
        (f"{APP}/", "app"),
        (f"{APP}/open?_file=/Users/me/a.fused&n=80", "app"),
        (f"http://localhost:{PORT}/render", "app"),
        (f"http://127.0.0.1:{PORT + 1}/", "external"),  # some other local server
        ("http://127.0.0.1/", "external"),  # port 80 is not our server
        ("https://huggingface.co/models", "external"),
        ("about:blank", "other"),
        (f"blob:{APP}/abc", "other"),
        ("data:text/plain,hi", "other"),
        ("javascript:void(0)", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_classify(url, kind):
    assert wp.classify(url, PORT) == kind


@pytest.mark.parametrize(
    "host, port, own",
    [
        ("127.0.0.1", PORT, True),
        ("localhost", PORT, True),
        ("LOCALHOST", PORT, True),
        ("[::1]", PORT, True),
        ("127.0.0.1", PORT + 1, False),  # some other local server
        ("127.0.0.1", 0, False),          # WKSecurityOrigin of an opaque origin
        ("127.0.0.1", None, False),
        ("huggingface.co", PORT, False),  # a foreign host on "our" port number
        ("", PORT, False),
        (None, PORT, False),
    ],
)
def test_is_own_origin(host, port, own):
    # Media-capture and geolocation grants hinge on this: our pages yes,
    # a third-party iframe inside an app no.
    assert wp.is_own_origin(host, port, PORT) is own


def _nav(url, **kw):
    base = dict(is_main_frame=True, has_target_frame=True,
                wants_download=False, new_window_modifier=False)
    base.update(kw)
    return wp.navigation_action(url, PORT, **base)


def test_plain_in_app_navigation_is_allowed():
    assert _nav(f"{APP}/open?_file=/x.fused") == "allow"


def test_target_blank_to_the_app_opens_a_new_window():
    # window.open / target=_blank arrive with no target frame.
    assert _nav(f"{APP}/open?_file=/y.fused", has_target_frame=False) == "new_window"


def test_cmd_click_on_an_app_link_opens_a_new_window():
    assert _nav(f"{APP}/", new_window_modifier=True) == "new_window"


def test_cmd_click_inside_a_subframe_stays_put():
    # The .fused app's own iframe navigating itself.
    assert _nav(f"{APP}/render?x=1", is_main_frame=False,
                new_window_modifier=True) == "allow"


def test_external_main_frame_and_popups_go_to_the_default_browser():
    assert _nav("https://huggingface.co/x") == "open_external"
    assert _nav("https://github.com/", has_target_frame=False) == "open_external"


def test_external_subframe_loads_stay_inside_the_page():
    # A map's tile server / a third-party embed in an iframe.
    assert _nav("https://tiles.example.com/1/2/3.png", is_main_frame=False) == "allow"


def test_download_attribute_wins_over_everything():
    assert _nav(f"{APP}/api/fs/raw?path=a.parquet", wants_download=True) == "download"
    assert _nav(f"blob:{APP}/abc", has_target_frame=False, wants_download=True) == "download"


def test_other_schemes_are_left_to_webkit():
    assert _nav("about:blank", has_target_frame=False) == "allow"
    assert _nav(f"blob:{APP}/x") == "allow"


def _resp(**kw):
    base = dict(is_main_frame=True, can_show_mime=True, content_disposition=None)
    base.update(kw)
    return wp.response_action(**base)


def test_attachment_disposition_downloads():
    assert _resp(content_disposition='attachment; filename="a.fused"') == "download"
    assert _resp(content_disposition="ATTACHMENT") == "download"
    assert _resp(content_disposition='inline; filename="a.png"') == "allow"


def test_unshowable_mime_downloads():
    assert _resp(can_show_mime=False) == "download"


def test_subframe_responses_are_never_downloads():
    assert _resp(is_main_frame=False, can_show_mime=False,
                 content_disposition="attachment") == "allow"


def test_download_destination_dedupes_like_finder():
    taken = {"/dl/a.parquet", "/dl/a 2.parquet"}
    assert wp.download_destination("/dl", "a.parquet", exists=taken.__contains__) == "/dl/a 3.parquet"
    assert wp.download_destination("/dl", "b.zip", exists=taken.__contains__) == "/dl/b.zip"


def test_download_destination_sanitises_the_name():
    never = lambda _p: False  # noqa: E731
    assert wp.download_destination("/dl", "../../etc/passwd", exists=never) == "/dl/.._.._etc_passwd"
    assert wp.download_destination("/dl", "", exists=never) == "/dl/download"
    assert wp.download_destination("/dl", "..", exists=never) == "/dl/download"
    assert wp.download_destination("/dl", ".bashrc", exists=lambda p: p == "/dl/.bashrc") == "/dl/.bashrc 2"
