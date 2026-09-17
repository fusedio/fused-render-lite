"""``POST /api/fetch`` and ``fetch.download_app_file``: a public URL to a
.fused lands under ``<home>/downloads`` and opens like a local file."""
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fused_render_app import appfile, fetch
from fused_render_app.cli import open_url
from tests.conftest import CALC_PY, ENTRY_HTML


APP_ID = "demo-app-82de2580"
ID_ENTRY = ENTRY_HTML.replace('<meta name="fused-api-version" content="1" />',
                              '<meta name="fused-api-version" content="1" />\n'
                              f'<meta name="fused-app-id" content="{APP_ID}" />')


@pytest.fixture
def id_fused(tmp_path):
    """A v2 file exported by an id-aware fused-render: the tag in the entry
    page and ``app_id`` in the container index (PR 1186)."""
    from fused_render_app import container

    out = tmp_path / "with-id.fused"
    container.write(str(out), {"name": "demo", "entry": "index.html", "app_id": APP_ID},
                    [("index.html", ID_ENTRY.encode()), ("calc.py", CALC_PY.encode())])
    return str(out)


@pytest.fixture
def origin(v2_fused, id_fused):
    """A tiny HTTP origin serving the v2 fixture at /demo.fused, the id-bearing
    one at /a/… and /b/…, garbage at /bad.fused, a redirect to file:// at
    /evil, and 404 elsewhere."""
    body = open(v2_fused, "rb").read()
    id_body = open(id_fused, "rb").read()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/demo.fused"):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith(("/a/", "/b/")):
                self.send_response(200)
                self.send_header("Content-Length", str(len(id_body)))
                self.end_headers()
                self.wfile.write(id_body)
            elif self.path == "/named":
                self.send_response(200)
                self.send_header("Content-Disposition", 'attachment; filename="My App.fused"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/bad.fused":
                self.send_response(200)
                self.send_header("Content-Length", "7")
                self.end_headers()
                self.wfile.write(b"garbage")
            elif self.path == "/evil":
                self.send_response(302)
                self.send_header("Location", "file:///etc/passwd")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_download_keyed_on_app_id(origin, app_home):
    a = fetch.download_app_file(origin + "/a/first.fused")
    assert a == str(app_home / "downloads" / f"{APP_ID}.fused") and os.path.isfile(a)
    # A different URL serving the same app lands on the same file.
    assert fetch.download_app_file(origin + "/b/renamed.fused?v=2") == a
    assert appfile.open_app_file(a)["app_id"] == APP_ID
    assert not [n for n in os.listdir(app_home / "downloads") if n.startswith(".download-")]


def test_download_without_id_falls_back_to_url_key(origin, app_home):
    url = origin + "/demo.fused"
    saved = fetch.download_app_file(url)
    assert saved.startswith(str(app_home / "downloads")) and os.path.isfile(saved)
    assert os.path.basename(saved).startswith("demo-") and saved.endswith(".fused")
    assert fetch.download_app_file(url) == saved  # same URL -> same path, overwritten
    assert fetch.download_app_file(url + "?v=2") != saved  # different URL -> different path
    assert appfile.open_app_file(saved)["app_id"] is None


def test_app_id_of_reads_index_then_entry_page(tmp_path, v1_fused, id_fused):
    import zipfile
    from fused_render_app import container

    assert appfile.app_id_of(id_fused) == APP_ID
    # Page-only stamp (index without app_id): the entry head is sniffed.
    page_only = tmp_path / "page-only.fused"
    container.write(str(page_only), {"name": "demo", "entry": "index.html"},
                    [("index.html", ID_ENTRY.encode()), ("big.bin", b"x" * 100_000)])
    assert appfile.app_id_of(str(page_only)) == APP_ID
    # A v1 zip with the tag in its page.
    v1 = tmp_path / "v1-id.fused"
    with zipfile.ZipFile(v1, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"fused_app_file": 1, "name": "l", "entry": "index.html"}))
        zf.writestr("files/index.html", ID_ENTRY)
    assert appfile.app_id_of(str(v1)) == APP_ID
    # Malformed ids are absent, never a path segment.
    bad = tmp_path / "bad-id.fused"
    container.write(str(bad), {"name": "demo", "entry": "index.html", "app_id": "../x-82de2580"},
                    [("index.html", ENTRY_HTML.replace("fused-api-version", "fused-app-id\" content=\"Evil/../x").encode())])
    assert appfile.app_id_of(str(bad)) is None
    assert appfile.app_id_of(v1_fused) is None
    for ok in ("a-82de2580", "my-app-test-82de2580"):
        assert appfile.is_app_id(ok)
    for no in ("A-82de2580", "x-82DE2580", "-x-82de2580", "x-82de258", "x/y-82de2580", 5, None):
        assert not appfile.is_app_id(no)


def test_content_disposition_names_the_file(origin, app_home):
    saved = fetch.download_app_file(origin + "/named")
    assert os.path.basename(saved).startswith("My App-")


def test_rejects_garbage_missing_and_bad_schemes(origin, app_home):
    with pytest.raises(fetch.FetchError, match="not a fused app file|not a readable"):
        fetch.download_app_file(origin + "/bad.fused")
    with pytest.raises(fetch.FetchError, match="HTTP 404"):
        fetch.download_app_file(origin + "/nope.fused")
    with pytest.raises(fetch.FetchError, match="non-http"):
        fetch.download_app_file(origin + "/evil")
    for bad in ("file:///etc/passwd", "ftp://x/y.fused", "", "/abs/path.fused"):
        with pytest.raises(fetch.FetchError, match="only http"):
            fetch.download_app_file(bad)
    assert os.listdir(app_home / "downloads") == []  # nothing left behind


def test_api_fetch_then_open(client, origin, app_home):
    url = origin + "/demo.fused"
    status, _, body = client.post("/api/fetch", {"url": url}, headers={"X-Fused": ""})
    assert status == 403
    status, _, body = client.post("/api/fetch", {"url": "file:///etc/passwd"})
    assert status == 400
    status, _, body = client.post("/api/fetch", {"url": origin + "/bad.fused"})
    assert status == 400 and "fused" in json.loads(body)["error"]
    status, _, body = client.post("/api/fetch", {"url": url})
    assert status == 200, body
    saved = json.loads(body)["file"]
    assert saved.startswith(str(app_home / "downloads"))
    status, _, body = client.post("/api/open", {"file": saved})
    assert status == 200, body
    assert json.loads(body)["name"] == "demo"


def test_open_page_accepts_url(client):
    status, _, body = client.get("/open?_url=" + urllib.parse.quote("https://example.com/a.fused", safe=""))
    assert status == 200
    text = body.decode()
    assert '"https://example.com/a.fused"' in text and "/api/fetch" in text
    # A URL open is gated behind a click (any page can point the browser
    # here) and, once downloaded, opens by a real navigation to /open?_file=
    # so the native window learns which .fused it shows.
    assert 'go.id = "confirm"' in text and "confirmDownload()" in text
    assert 'location.replace("/open?_file="' in text and "replaceState" not in text
    # A query value must not be able to close the inline <script> block.
    evil = "https://x/</script><script>alert(1)</script>"
    status, _, body = client.get("/open?_url=" + urllib.parse.quote(evil, safe=""))
    assert status == 200
    line = [l for l in body.decode().splitlines() if "const URL_ =" in l][0]
    assert "<" not in line and "\\u003c/script" in line


def test_url_from_link_scheme_and_bare_http():
    link = "https://x.io/a.fused?v=1"
    enc = urllib.parse.quote(link, safe="")
    assert fetch.url_from_link(f"render-app://open?url={enc}") == link
    assert fetch.url_from_link(f"RENDER-APP://open/?url={enc}") == link
    assert fetch.url_from_link(link) == link
    for bad in ("render-app://open", "render-app://open?url=file:///etc/passwd",
                "render-app://other?url=" + enc, "render-app://open/x?url=" + enc,
                "file:///tmp/a.fused", "mailto:a@b", ""):
        assert fetch.url_from_link(bad) is None, bad


def test_plist_registers_the_scheme():
    import ast
    src = open(os.path.join(os.path.dirname(fetch.__file__), "..", "scripts", "setup_py2app.py")).read()
    assert f'"CFBundleURLSchemes": ["{fetch.SCHEME}"]' in src
    ast.parse(src)


def test_cli_open_url_routes_links():
    assert open_url(1234, "https://x.io/a.fused?v=1") == \
        "http://127.0.0.1:1234/open?_url=https%3A%2F%2Fx.io%2Fa.fused%3Fv%3D1"
    assert open_url(1234, "/tmp/a.fused").startswith("http://127.0.0.1:1234/open?_file=")
