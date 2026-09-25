"""The Edit button's AppKit-free half (fused_render_app/editlink.py): the
deep link it builds and the download URL it falls back to."""
import io
import json
from contextlib import contextmanager
from urllib.parse import unquote

from fused_render_app import editlink


def test_edit_url_encodes_the_path_once():
    url = editlink.edit_url("/Users/me/My Apps/a&b #1 100%.fused")
    assert url.startswith("fused-render://open?file=")
    encoded = url[len("fused-render://open?file="):]
    # Nothing a query parser could split on survives unescaped…
    assert not any(c in encoded for c in "&#? /")
    # …and one unquote (fused-render's side of the contract) restores the path.
    assert unquote(encoded) == "/Users/me/My Apps/a&b #1 100%.fused"


def test_download_url_from_manifest_prefers_the_dmg():
    url = editlink.download_url_from(
        {"version": "0.5.86", "dmg_url": "https://cdn.example/FusedRender-0.5.86.dmg"})
    assert url == "https://cdn.example/FusedRender-0.5.86.dmg"


def test_download_url_from_bad_manifest_is_the_page():
    assert editlink.download_url_from({}) == editlink.DOWNLOAD_PAGE
    assert editlink.download_url_from({"dmg_url": "http://insecure/x.dmg"}) == editlink.DOWNLOAD_PAGE
    assert editlink.download_url_from("nonsense") == editlink.DOWNLOAD_PAGE


def _fake_urlopen(body: bytes | None):
    calls = []

    @contextmanager
    def urlopen(url, timeout):
        calls.append((url, timeout))
        if body is None:
            raise OSError("offline")
        yield io.BytesIO(body)

    return urlopen, calls


def test_download_url_fetches_the_manifest():
    urlopen, calls = _fake_urlopen(json.dumps(
        {"dmg_url": "https://cdn.example/FusedRender-1.0.dmg"}).encode())
    assert editlink.download_url(urlopen_fn=urlopen) == "https://cdn.example/FusedRender-1.0.dmg"
    assert calls == [(editlink.MANIFEST_URL, editlink.FETCH_TIMEOUT_S)]


def test_download_url_offline_falls_back_to_the_page():
    urlopen, _ = _fake_urlopen(None)
    assert editlink.download_url(urlopen_fn=urlopen) == editlink.DOWNLOAD_PAGE


def test_download_url_garbage_falls_back_to_the_page():
    urlopen, _ = _fake_urlopen(b"<html>not json</html>")
    assert editlink.download_url(urlopen_fn=urlopen) == editlink.DOWNLOAD_PAGE
