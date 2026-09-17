"""The self-updater (fused_render_app/update/): the stdlib Ed25519, the signed
manifest, the UpdateManager state machine, and the /api/update routes."""
import base64
import hashlib
import io
import json
import os
import plistlib

import pytest

from fused_render_app import server
from fused_render_app.update import common, ed25519
from fused_render_app.update import mac as mac_update

# RFC 8032 section 7.1, TEST 1 and TEST 2.
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]

SEED = bytes(range(32))
PUBLIC = ed25519.public_key(SEED)


@pytest.mark.parametrize("seed,pk,msg,sig", RFC_VECTORS)
def test_ed25519_rfc8032_vectors(seed, pk, msg, sig):
    seed, pk, msg, sig = (bytes.fromhex(x) for x in (seed, pk, msg, sig))
    assert ed25519.public_key(seed) == pk
    assert ed25519.sign(seed, msg) == sig
    ed25519.verify(pk, sig, msg)
    with pytest.raises(ed25519.BadSignature):
        ed25519.verify(pk, sig, msg + b"x")
    with pytest.raises(ed25519.BadSignature):
        ed25519.verify(pk, sig[:-1] + bytes([sig[-1] ^ 1]), msg)
    # Non-canonical S (>= L) is rejected even if it would otherwise reduce.
    s = int.from_bytes(sig[32:], "little") + ed25519.L
    with pytest.raises(ed25519.BadSignature):
        ed25519.verify(pk, sig[:32] + s.to_bytes(32, "little"), msg)


def _manifest(version="9.9.9", sha256="ab" * 32, url="https://cdn.example/RenderApp-9.9.9.dmg",
              seed=SEED, **extra):
    sig = ed25519.sign(seed, common.signing_message(version, sha256))
    body = {"schema": 1, "version": version, "url": url, "sha256": sha256,
            "signature": base64.b64encode(sig).decode()}
    body.update(extra)
    return body


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self._headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def getheader(self, name):
        return self._headers.get(name)


def _serve(payload):
    data = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    return lambda url, timeout: _Resp(data)


def test_fetch_manifest_verifies_signature():
    m = common.fetch_manifest("https://x/latest.json", urlopen_fn=_serve(_manifest()),
                              public_key=PUBLIC)
    assert m["version"] == "9.9.9"
    # Tampered version under a valid-looking signature: rejected.
    bad = _manifest()
    bad["version"] = "10.0.0"
    with pytest.raises(ValueError, match="signature is invalid"):
        common.fetch_manifest("https://x", urlopen_fn=_serve(bad), public_key=PUBLIC)
    # Wrong key: rejected.
    with pytest.raises(ValueError, match="signature is invalid"):
        common.fetch_manifest("https://x", urlopen_fn=_serve(_manifest(seed=bytes(32))),
                              public_key=PUBLIC)
    # fused-render's own signing context does not carry over to this app.
    fr = _manifest()
    fr["signature"] = base64.b64encode(ed25519.sign(
        SEED, f"fused-render-update\n9.9.9\n{'ab' * 32}\n".encode())).decode()
    with pytest.raises(ValueError, match="signature is invalid"):
        common.fetch_manifest("https://x", urlopen_fn=_serve(fr), public_key=PUBLIC)
    # Malformed shapes.
    for junk in ({"schema": 2}, {"schema": 1, "version": 1, "url": "", "sha256": "", "signature": ""}, []):
        with pytest.raises(ValueError):
            common.fetch_manifest("https://x", urlopen_fn=_serve(junk), public_key=PUBLIC)


def test_is_newer():
    assert common.is_newer("0.8.14", "0.8.13")
    assert common.is_newer("0.9.0", "0.8.13")
    assert not common.is_newer("0.8.13", "0.8.13")
    assert not common.is_newer("0.8.2", "0.8.13")


def test_download_verified_checks_hash_https_and_cancel(tmp_path):
    payload = b"dmg bytes " * 1000
    good = _manifest(sha256=hashlib.sha256(payload).hexdigest())
    serve = lambda url, timeout: _Resp(payload, {"Content-Length": str(len(payload))})  # noqa: E731
    seen = []
    out = common.download_verified(good, dir=str(tmp_path), urlopen_fn=serve,
                                   progress=lambda d, t: seen.append((d, t)))
    assert open(out, "rb").read() == payload and seen[-1] == (len(payload), len(payload))
    with pytest.raises(ValueError, match="does not match"):
        common.download_verified(_manifest(sha256="00" * 32), dir=str(tmp_path), urlopen_fn=serve)
    with pytest.raises(ValueError, match="not https"):
        common.download_verified(_manifest(url="http://cdn/x.dmg"), dir=str(tmp_path), urlopen_fn=serve)
    with pytest.raises(common.UpdateCancelled):
        common.download_verified(good, dir=str(tmp_path), urlopen_fn=serve, should_abort=lambda: True)
    # Every failure discards its partial file.
    assert os.listdir(tmp_path) == [os.path.basename(out)]


def _bundle(tmp_path, version="0.8.13", ident=mac_update.BUNDLE_ID, name="RenderApp.app"):
    app = tmp_path / name
    (app / "Contents").mkdir(parents=True)
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version, "CFBundleIdentifier": ident}, f)
    return str(app)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "PUBLIC_KEY", PUBLIC)
    bundle = _bundle(tmp_path)
    m = mac_update.UpdateManager(manifest_url="https://x/latest.json", bundle=bundle,
                                 current_version="0.8.13")
    return m


def _point(monkeypatch, payload):
    monkeypatch.setattr(common, "urlopen", _serve(payload))


def test_check_finds_update_and_settles(manager, monkeypatch):
    _point(monkeypatch, _manifest(version="0.8.14"))
    st = manager.check(force=True)
    assert st["state"] == "available" and st["latest_version"] == "0.8.14"
    assert st["current_version"] == "0.8.13" and st["check_error"] is None
    # Same version as running: nothing to offer.
    _point(monkeypatch, _manifest(version="0.8.13"))
    assert manager.check(force=True)["state"] == "idle"


def test_check_failure_keeps_known_update_and_reports(manager, monkeypatch):
    _point(monkeypatch, _manifest(version="0.8.14"))
    manager.check(force=True)

    def boom(url, timeout):
        raise OSError("offline")
    monkeypatch.setattr(common, "urlopen", boom)
    st = manager.check(force=True)
    assert st["state"] == "available" and st["latest_version"] == "0.8.14"
    assert "offline" in st["check_error"]


def test_check_is_throttled_unless_forced(manager, monkeypatch):
    calls = []

    def counting(url, timeout):
        calls.append(url)
        return _Resp(json.dumps(_manifest(version="0.8.13")).encode())
    monkeypatch.setattr(common, "urlopen", counting)
    manager.check()
    manager.check()
    assert len(calls) == 1
    manager.check(force=True)
    assert len(calls) == 2
    # A found update is an answer: a non-forced check does not re-ask.
    monkeypatch.setattr(common, "urlopen", _serve(_manifest(version="0.8.14")))
    manager.check(force=True)
    manager._last_check_at = None
    monkeypatch.setattr(common, "urlopen", counting)
    manager.check()
    assert len(calls) == 2


def test_disk_version_decides_installed(manager, monkeypatch, tmp_path):
    _point(monkeypatch, _manifest(version="0.8.14"))
    manager.check(force=True)
    # Something else (a manual DMG drag) put 0.8.14 on disk: status flips to
    # installed without waiting for a tick, and a re-check agrees.
    with open(os.path.join(manager._bundle, "Contents", "Info.plist"), "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": "0.8.14",
                       "CFBundleIdentifier": mac_update.BUNDLE_ID}, f)
    assert manager.status()["state"] == "installed"
    assert manager.check(force=True)["state"] == "installed"


def test_install_defers_on_version_mismatch_and_refuses_check_only(manager, monkeypatch):
    _point(monkeypatch, _manifest(version="0.8.14"))
    manager.check(force=True)
    # The button said 0.8.14 but 0.8.15 is current by the time it is pressed.
    _point(monkeypatch, _manifest(version="0.8.15"))
    st = manager.install(expected_version="0.8.14")
    assert st["state"] == "available" and st["latest_version"] == "0.8.15"
    # Nothing to install from idle either.
    _point(monkeypatch, _manifest(version="0.8.13"))
    assert manager.install()["state"] == "idle"

    dev = mac_update.UpdateManager(manifest_url="https://x", bundle=None, check_only=True,
                                   current_version="0.8.13")
    _point(monkeypatch, _manifest(version="0.8.14"))
    st = dev.check(force=True)
    assert st["state"] == "available" and st["check_only"] is True
    assert dev.install(expected_version="0.8.14")["state"] == "available"


def test_install_runs_the_dmg_path_and_reports_errors(manager, monkeypatch):
    _point(monkeypatch, _manifest(version="0.8.14"))
    manager.check(force=True)
    done = {}

    def fake_dmg(manifest):
        done["v"] = manifest["version"]
    monkeypatch.setattr(manager, "_install_dmg", fake_dmg)
    manager.install(expected_version="0.8.14")
    manager._install_thread.join(5)
    assert done == {"v": "0.8.14"}
    # The bundle on disk did not actually change in this fake, so status
    # re-derives "available"... unless install() said installed — it did.
    assert manager._state == "installed"

    manager._state = "available"

    def failing(manifest):
        raise RuntimeError("no disk")
    monkeypatch.setattr(manager, "_install_dmg", failing)
    manager.install(expected_version="0.8.14")
    manager._install_thread.join(5)
    st = manager.status()
    assert st["state"] == "error" and st["error"] == "no disk"

    def cancelled(manifest):
        raise common.UpdateCancelled("stop")
    monkeypatch.setattr(manager, "_install_dmg", cancelled)
    manager.install(expected_version="0.8.14")
    manager._install_thread.join(5)
    assert manager.status()["state"] == "available"


def test_swap_survives_a_detach_failure(manager, monkeypatch, tmp_path):
    """Cleanup after the swap is best-effort: a hung `hdiutil detach` must not
    report a finished install as an error (bugbot, PR #26)."""
    import shutil
    import subprocess

    _point(monkeypatch, _manifest(version="0.8.14"))
    manager.check(force=True)
    mount = tmp_path / "mount"
    source = _bundle(mount, version="0.8.14")
    dmg = tmp_path / "dl.dmg"
    dmg.write_bytes(b"x")
    monkeypatch.setattr(common, "download_verified", lambda *a, **k: str(dmg))
    monkeypatch.setattr(manager, "_check_disk_space", lambda updates: None)
    monkeypatch.setattr(manager, "_attach", lambda d: str(mount))
    ran = []

    def fake_run(argv, **kw):
        ran.append(argv[1])
        if argv[1] == "detach":
            raise subprocess.TimeoutExpired(argv, 60)
        assert argv[0] == "/usr/bin/ditto"
        shutil.copytree(argv[1], argv[2])
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(mac_update.subprocess, "run", fake_run)

    manager.install(expected_version="0.8.14")
    manager._install_thread.join(5)
    assert manager.status()["state"] == "installed"
    assert manager._disk_version() == "0.8.14"
    assert "detach" in ran and not dmg.exists()


def test_verify_app_checks_version_and_bundle_id(manager, tmp_path):
    manager._verify_app(_bundle(tmp_path / "ok", version="0.8.14"), "0.8.14")
    with pytest.raises(RuntimeError, match="contains version 0.8.13"):
        manager._verify_app(_bundle(tmp_path / "old", version="0.8.13"), "0.8.14")
    # A FusedRender bundle signed with the shared key is not this app.
    with pytest.raises(RuntimeError, match="io.fused.render.app"):
        manager._verify_app(_bundle(tmp_path / "other", version="0.8.14",
                                    ident="io.fused.render"), "0.8.14")
    with pytest.raises(RuntimeError, match="no readable Info.plist"):
        manager._verify_app(str(tmp_path / "missing.app"), "0.8.14")


def test_cancel_only_while_downloading(manager):
    assert manager.cancel()["state"] == "idle" and manager._cancel is False
    manager._state, manager._phase = "installing", "downloading"
    manager.cancel()
    assert manager._cancel is True
    manager._cancel, manager._phase = False, "installing"
    manager.cancel()
    assert manager._cancel is False


def test_relaunch_script_waits_for_pid_then_opens_bundle():
    script = mac_update.relaunch_script("/Applications/Render App.app", 4242)
    assert "kill -0 4242" in script
    assert script.endswith("/usr/bin/open -n '/Applications/Render App.app'")


def test_start_is_a_no_op_outside_a_bundle(monkeypatch):
    mac_update.reset_for_tests()
    monkeypatch.delenv(mac_update.DEV_MANAGER_ENV, raising=False)
    monkeypatch.setenv(mac_update.NO_AUTO_UPDATE_ENV, "1")
    assert mac_update.start() is None and mac_update.manager() is None
    monkeypatch.setenv(mac_update.DEV_MANAGER_ENV, "1")
    m = mac_update.start()
    assert m is not None and m.status()["check_only"] is True
    assert mac_update.start() is m
    mac_update.reset_for_tests()


# ---- routes -----------------------------------------------------------------

def test_update_routes_without_a_manager(client):
    mac_update.reset_for_tests()
    status, _, body = client.get("/api/update")
    assert status == 200 and json.loads(body) == {"update": None}
    for action in ("check", "install", "cancel", "relaunch"):
        status, _, _ = client.post(f"/api/update/{action}", {}, headers={"X-Fused": "0"})
        assert status == 403, action
        status, _, _ = client.post(f"/api/update/{action}", {})
        assert status == 404, action
    status, _, _ = client.post("/api/update/nope", {})
    assert status == 404


def test_update_routes_with_a_manager(client, tmp_path, monkeypatch):
    monkeypatch.setattr(common, "PUBLIC_KEY", PUBLIC)
    _point(monkeypatch, _manifest(version="0.8.14"))
    m = mac_update.UpdateManager(manifest_url="https://x", bundle=_bundle(tmp_path),
                                 current_version="0.8.13")
    monkeypatch.setattr(mac_update, "_manager", m)
    try:
        status, _, body = client.post("/api/update/check", {})
        assert status == 200 and json.loads(body)["state"] == "available"
        status, _, body = client.get("/api/update")
        assert json.loads(body)["update"]["latest_version"] == "0.8.14"
        # Deferred: the client saw a version that is no longer current.
        _point(monkeypatch, _manifest(version="0.8.15"))
        status, _, body = client.post("/api/update/install", {"expected_version": "0.8.14"})
        assert status == 200 and json.loads(body)["state"] == "available"
        # Relaunch only from "installed", and only with the native shell.
        status, _, _ = client.post("/api/update/relaunch", {})
        assert status == 409
        m._state = "installed"
        status, _, _ = client.post("/api/update/relaunch", {})
        assert status == 404
        fired = []
        monkeypatch.setitem(server.native_hooks, "relaunch", lambda: fired.append(1))
        status, _, body = client.post("/api/update/relaunch", {})
        assert status == 200 and json.loads(body) == {"relaunching": True} and fired == [1]
    finally:
        mac_update.reset_for_tests()
