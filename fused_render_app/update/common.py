"""Platform-neutral core of the self-updater: fetch + cryptographically
verify the signed update manifest, and stream-download the artifact it points
at while checking its SHA-256 against the signed value.

Ported from fused-render's `fused_render/update/common.py` with the
`cryptography` dependency replaced by `ed25519.py` (stdlib only — Render App
ships no third-party runtime packages). The signing scheme is otherwise the
same: an ed25519 signature over a domain-separated `version\\nsha256` line
(scripts/generate_update_manifest.py), so a CDN/bucket compromise cannot forge
a version or point the client at different bytes. The artifact URL itself is
not signed — its content is pinned by the signed sha256 — so downloads
additionally require HTTPS end to end.

The signing key and context are Render App's OWN (`render-app-update`, not
fused-render's `fused-render-update`), so a FusedRender manifest replayed at
this app's manifest URL cannot verify even if the keys were ever shared.
Version downgrade replays are rejected by is_newer().
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request

from fused_render_app.update import ed25519

# Render App's OWN key (generated 2026-09-17 with
# `scripts/generate_update_manifest.py keygen`), not fused-render's: release CI
# signs with this repo's FUSED_RENDER_UPDATE_SIGNING_KEY secret. Rotating it
# means a new seed in that secret AND a new value here, shipped in a release
# that the old key still signs.
PUBLIC_KEY = base64.b64decode("fmrKVBJRU4X/P/Eh1cEVgkM0AfxoxETX3YkH03BdunU=")
SIGNING_CONTEXT = "render-app-update"
FETCH_TIMEOUT_S = 15.0
DOWNLOAD_TIMEOUT_S = 300.0
# Every five minutes, as fused-render settled on (one ~300-byte signed GET on
# CloudFront; a release sitting unnoticed for part of a working day costs
# more than 288 of those). The launcher page polls /api/update on top, and
# checks again when the app comes back to the front.
CHECK_INTERVAL_S = 5 * 60
MAX_MANIFEST_BYTES = 64 * 1024
# The shipped DMG is ~42 MB (STATUS.md); 200 MB leaves room to grow several
# times over while still bounding what a bad manifest could make us write.
MAX_ARTIFACT_BYTES = 200 * 1024 * 1024
DOWNLOAD_CHUNK = 1024 * 1024


class UpdateCancelled(Exception):
    """The user asked for the in-flight update to stop. Its own type so the
    manager can tell a cancel (state goes back to "available") from a failure
    (state goes to "error"). Raised only by `download_verified`'s
    `should_abort` check, so the partial file is discarded by the same
    `finally` that handles a genuine failure."""


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """urlopen follows redirects by default; refuse any that leave HTTPS so a
    compromised CDN cannot 302 the download to http (integrity still rests on
    the signed sha256, but never ship bytes over cleartext)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise urllib.error.URLError("refusing non-https redirect during update")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(HttpsOnlyRedirect)


def urlopen(url: str, timeout: float):
    return _opener.open(url, timeout=timeout)


def signing_message(version: str, sha256: str) -> bytes:
    return f"{SIGNING_CONTEXT}\n{version}\n{sha256}\n".encode("utf-8")


def fetch_manifest(url: str, *, urlopen_fn=None, public_key: bytes | None = None) -> dict:
    """Fetch, validate, and cryptographically verify the manifest. The
    signature is checked here — before any caller trusts `version` to decide
    "up to date" or to prompt — so a CDN/bucket compromise cannot forge a
    version to suppress or fake an update."""
    if urlopen_fn is None:
        urlopen_fn = urlopen
    with urlopen_fn(url, FETCH_TIMEOUT_S) as resp:
        raw = resp.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("update manifest is too large")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or not all(
        isinstance(manifest.get(key), str)
        for key in ("version", "url", "sha256", "signature")
    ):
        raise ValueError("malformed update manifest")
    verify_signature(manifest["version"], manifest["sha256"], manifest["signature"],
                     public_key=public_key)
    return manifest


def verify_signature(version: str, sha256: str, signature: str, *,
                     public_key: bytes | None = None) -> None:
    # Resolved at call time, not bound as a default, so a test (or a staging
    # build) can repoint PUBLIC_KEY on the module.
    if public_key is None:
        public_key = PUBLIC_KEY
    try:
        ed25519.verify(public_key, base64.b64decode(signature),
                       signing_message(version, sha256))
    except (ed25519.BadSignature, ValueError) as error:
        raise ValueError("update manifest signature is invalid") from error


def is_newer(candidate: str, current: str) -> bool:
    def parts(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in version.split("."))

    return parts(candidate) > parts(current)


def download_verified(manifest: dict, *, dir: str | None = None,
                      prefix: str = "RenderApp-update-", suffix: str = "",
                      max_bytes: int = MAX_ARTIFACT_BYTES,
                      progress=None, should_abort=None, urlopen_fn=None) -> str:
    """Stream the artifact to a temp file (in `dir`, or the system temp dir)
    while hashing it, and confirm its SHA-256 matches the signed value. The
    URL is not signed, so require HTTPS. `progress` (optional) is called with
    (bytes so far, total bytes or None) after each chunk — the total comes
    from Content-Length, which the manifest itself does not carry.

    `should_abort` (optional) is consulted once per chunk, before the chunk is
    written; returning true raises `UpdateCancelled` and the partial file is
    discarded on the way out, exactly like a checksum mismatch."""
    if urlopen_fn is None:
        urlopen_fn = urlopen
    url = manifest["url"]
    if not url.startswith("https://"):
        raise ValueError("update manifest url is not https")
    sha256 = manifest["sha256"]
    digest = hashlib.sha256()
    done = 0
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=dir)
    ok = False
    try:
        with os.fdopen(fd, "wb") as out, urlopen_fn(url, DOWNLOAD_TIMEOUT_S) as resp:
            content_length = resp.getheader("Content-Length")
            try:
                size = int(content_length) if content_length is not None else None
            except ValueError:
                size = None
            while chunk := resp.read(DOWNLOAD_CHUNK):
                if should_abort is not None and should_abort():
                    raise UpdateCancelled("update download cancelled")
                done += len(chunk)
                if done > max_bytes:
                    raise ValueError("update download exceeds the size limit")
                digest.update(chunk)
                out.write(chunk)
                if progress is not None:
                    progress(done, size)
        if digest.hexdigest() != sha256:
            raise ValueError("downloaded file does not match the signed manifest")
        ok = True
        return path
    finally:
        if not ok:
            discard(path)


def discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
