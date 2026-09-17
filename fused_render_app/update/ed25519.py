"""Ed25519 (RFC 8032) on the standard library alone.

Render App ships zero runtime dependencies (pyproject.toml), so the signed
update manifest cannot be checked with `cryptography` the way fused-render's
updater does. A verify is one hash and two fixed-base-free scalar
multiplications; in extended twisted-Edwards coordinates (no modular inverse
per step) that is a few tens of milliseconds of Python on a background thread
once every five minutes — cheap enough to earn the missing dependency's place.

`sign` is here for the manifest generator (scripts/generate_update_manifest.py)
and the tests; it runs in release CI or a test process, never in the app, so
its constant-time properties are not a concern. Nothing else in the app calls
it.

Checked against the RFC 8032 section 7.1 test vectors in tests/test_update.py.
"""
from __future__ import annotations

import hashlib
import os

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

_BY = (4 * pow(5, P - 2, P)) % P
_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
# Extended coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z, T = XY/Z.
_B = (_BX, _BY, 1, (_BX * _BY) % P)
_IDENTITY = (0, 1, 1, 0)


class BadSignature(ValueError):
    """The signature does not verify (or key/signature bytes are malformed)."""


def _add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % P
    b = ((y1 + x1) * (y2 + x2)) % P
    c = (2 * t1 * t2 * D) % P
    d = (2 * z1 * z2) % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P


def _mul(point, scalar: int):
    result = _IDENTITY
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _encode(point) -> bytes:
    x, y, z, _ = point
    inv = pow(z, P - 2, P)
    x, y = (x * inv) % P, (y * inv) % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(raw: bytes):
    if len(raw) != 32:
        raise BadSignature("point is not 32 bytes")
    value = int.from_bytes(raw, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= P:
        raise BadSignature("point y out of range")
    y2 = (y * y) % P
    u, v = (y2 - 1) % P, (D * y2 + 1) % P
    x2 = (u * pow(v, P - 2, P)) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x) % P != x2:
        x = (x * SQRT_M1) % P
    if (x * x) % P != x2:
        raise BadSignature("point is not on the curve")
    if x == 0 and sign:
        raise BadSignature("point has invalid sign")
    if (x & 1) != sign:
        x = P - x
    return x, y, 1, (x * y) % P


def _hash_int(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def verify(public_key: bytes, signature: bytes, message: bytes) -> None:
    """Raise BadSignature unless `signature` is a valid Ed25519 signature of
    `message` under `public_key`. Rejects non-canonical S (S >= L)."""
    if len(signature) != 64:
        raise BadSignature("signature is not 64 bytes")
    a = _decode(public_key)
    r = _decode(signature[:32])
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        raise BadSignature("signature scalar out of range")
    k = _hash_int(signature[:32], public_key, message) % L
    if _encode(_mul(_B, s)) != _encode(_add(r, _mul(a, k))):
        raise BadSignature("signature does not verify")


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("seed is not 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(seed: bytes) -> bytes:
    a, _ = _expand(seed)
    return _encode(_mul(_B, a))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    pk = _encode(_mul(_B, a))
    r = _hash_int(prefix, message) % L
    rb = _encode(_mul(_B, r))
    k = _hash_int(rb, pk, message) % L
    s = (r + k * a) % L
    return rb + s.to_bytes(32, "little")


def generate_seed() -> bytes:
    return os.urandom(32)
