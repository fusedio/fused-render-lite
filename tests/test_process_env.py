"""paths.fix_process_env() and the updater's opener: TLS inside the .app bundle.

Seen live in the packaged 0.11.0 app (python.org framework build): every
update check failed with CERTIFICATE_VERIFY_FAILED although the environment
had been repaired to /etc/ssl/cert.pem. Python 3.12's HTTPSHandler loads the
CA bundle when an opener is BUILT, so (a) the updater's import-time opener and
(b) urllib's global opener, built by the launcher's http health probe before
the repair, each froze an empty certificate store for the life of the process.
"""
import os
import urllib.request

import pytest

from fused_render_app import paths
from fused_render_app.update import common


def test_fix_process_env_drops_dangling_cert_paths_and_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "openssl.ca" / "no-such-file"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "openssl.ca" / "no-such-file"))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(tmp_path / "nope.pem"))
    paths.fix_process_env()
    assert "SSL_CERT_DIR" not in os.environ
    assert "REQUESTS_CA_BUNDLE" not in os.environ
    cert = os.environ.get("SSL_CERT_FILE")
    # the system bundle when this host has one, else no dangling value at all
    assert cert is None or (os.path.exists(cert) and cert in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"))


def test_fix_process_env_keeps_a_real_cert_file(monkeypatch, tmp_path):
    pem = tmp_path / "ca.pem"
    pem.write_text("not really a cert, but it exists")
    monkeypatch.setenv("SSL_CERT_FILE", str(pem))
    paths.fix_process_env()
    assert os.environ["SSL_CERT_FILE"] == str(pem)


def test_fix_process_env_resets_urllibs_global_opener(monkeypatch):
    """The launcher's http probe builds urllib's default opener before the
    repair; it must be rebuilt afterwards or its frozen (empty) CA store
    fails every HTTPS call for the rest of the process."""
    sentinel = object()
    monkeypatch.setattr(urllib.request, "_opener", sentinel)
    paths.fix_process_env()
    assert urllib.request._opener is None


def test_updater_builds_a_fresh_opener_per_call(monkeypatch):
    built = []

    class FakeOpener:
        def open(self, url, timeout=None):
            return ("opened", url, timeout)

    def build_opener(*handlers):
        built.append(handlers)
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    assert common.urlopen("https://a.test/x", 3) == ("opened", "https://a.test/x", 3)
    assert common.urlopen("https://a.test/y", 4) == ("opened", "https://a.test/y", 4)
    assert len(built) == 2
    assert all(common.HttpsOnlyRedirect in h for h in built)
    assert not hasattr(common, "_opener"), "no module-level opener may freeze a CA store at import"


@pytest.mark.skipif(not os.path.exists("/etc/ssl/cert.pem"), reason="needs the macOS system CA bundle")
def test_updater_opener_sees_the_repaired_bundle(monkeypatch, tmp_path):
    """End to end in-process: a dangling SSL_CERT_FILE, then the repair, then
    an opener whose HTTPSHandler context holds CA certificates."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "no-such-file"))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    paths.fix_process_env()
    opener = urllib.request.build_opener(common.HttpsOnlyRedirect)
    https = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
    assert https._context.cert_store_stats()["x509_ca"] > 0
