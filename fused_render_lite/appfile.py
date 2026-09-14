"""Open a ``.fused`` single-file app.

Two physical formats are accepted: the v2 opaque container (``container.py``,
magic ``FUSEDAPP``) and the legacy v1 zip (``manifest.json`` + ``files/``).
Both carry a manifest with ``name`` and ``entry`` (the payload-relative entry
page). Opening is extract-then-serve: the payload lands in
``~/.fused-render-lite/apps/<slug>-<sha256 prefix>/`` keyed by the file's
bytes, so re-opening the same file re-uses the extract and a changed file
gets a fresh dir. The extract is WRITABLE — ``fused.writeFile`` is a
supported API here, and an app that saves state next to itself is the point.

No trust gate: opening a .fused runs its Python. Same posture as opening any
folder of pages someone sent you.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat as stat_mod
import tempfile
import zipfile

from fused_render_lite import container, paths

PAYLOAD_DIR = "files"
MANIFEST_NAME = "manifest.json"

MAX_ENTRIES = 5000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_MANIFEST_CAP = 256 * 1024

_META_RE = re.compile(r"<meta\s[^>]*name\s*=\s*[\"']fused-app[\"']", re.I)


class AppFileError(Exception):
    pass


def has_fused_meta(html_path: str) -> bool:
    try:
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            return bool(_META_RE.search(f.read(64 * 1024)))
    except OSError:
        return False


def _entry_problem(entry: object) -> bool:
    return (
        not isinstance(entry, str)
        or not entry
        or entry.startswith("/")
        or ".." in entry.split("/")
        or not entry.lower().endswith((".html", ".htm"))
    )


def read_manifest(fused_path: str) -> dict:
    """Validated manifest: ``{fused_app_file, name, entry, ...}``; v2 also
    carries ``files``. Nothing is extracted."""
    if container.is_container(fused_path):
        try:
            index = container.read_index(
                fused_path,
                max_entries=MAX_ENTRIES,
                max_entry_bytes=MAX_ENTRY_BYTES,
                max_total_bytes=MAX_TOTAL_BYTES,
            )
        except container.ContainerError as exc:
            raise AppFileError(str(exc))
        if _entry_problem(index.get("entry")):
            raise AppFileError(f"invalid entry path in manifest: {index.get('entry')!r}")
        return index
    return _read_zip_manifest(fused_path)


def _read_zip_manifest(fused_path: str) -> dict:
    try:
        with zipfile.ZipFile(fused_path) as zf:
            with zf.open(MANIFEST_NAME) as f:
                raw = f.read(_MANIFEST_CAP + 1)
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise AppFileError(f"not a readable .fused file: {exc}")
    if len(raw) > _MANIFEST_CAP:
        raise AppFileError("manifest.json is too large — not a fused app file")
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise AppFileError(f"invalid manifest.json in .fused file: {exc}")
    if not isinstance(manifest, dict) or manifest.get("fused_app_file") != 1:
        raise AppFileError("not a fused app file (manifest carries no fused_app_file: 1)")
    if _entry_problem(manifest.get("entry")):
        raise AppFileError(f"invalid entry path in manifest: {manifest.get('entry')!r}")
    return manifest


def _extract_zip(fused_path: str, staging: str) -> None:
    """Hardened v1 extraction: no absolute/.. paths, no symlinks, capped sizes."""
    total = 0
    count = 0
    with zipfile.ZipFile(fused_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            count += 1
            if count > MAX_ENTRIES:
                raise AppFileError(f"archive has more than {MAX_ENTRIES} entries")
            parts = name.split("/")
            if name.startswith(("/", "\\")) or ".." in parts or "" in parts or ":" in name:
                raise AppFileError(f"unsafe path in archive: {name!r}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat_mod.S_ISLNK(mode):
                raise AppFileError(f"symlink in archive: {name!r}")
            dest = os.path.join(staging, *parts)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            written = 0
            with zf.open(info) as src, open(dest, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > MAX_ENTRY_BYTES or total > MAX_TOTAL_BYTES:
                        raise AppFileError("archive contents exceed the size cap")
                    dst.write(chunk)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "app")[:40]


_key_cache: dict[tuple, str] = {}


def _file_key(fused_path: str, name: str) -> str:
    """``<slug>-<sha256 prefix>`` of the file's bytes, memoised on
    (path, size, mtime): the open page polls status every ~700 ms while an
    environment installs, and re-hashing a large .fused each time is waste."""
    st = os.stat(fused_path)
    cache_key = (fused_path, st.st_size, st.st_mtime_ns, name)
    hit = _key_cache.get(cache_key)
    if hit is not None:
        return hit
    h = hashlib.sha256()
    with open(fused_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    key = f"{_slug(name)}-{h.hexdigest()[:16]}"
    if len(_key_cache) > 256:
        _key_cache.clear()
    _key_cache[cache_key] = key
    return key


def open_app_file(fused_path: str) -> dict:
    """Extract (or re-use) and answer ``{"dir", "entry", "name", "reused"}``
    with absolute paths."""
    fused_path = os.path.abspath(fused_path)
    if not os.path.isfile(fused_path):
        raise AppFileError(f"no such file: {fused_path}")
    manifest = read_manifest(fused_path)
    name = manifest.get("name") if isinstance(manifest.get("name"), str) else "app"
    root = paths.apps_dir()
    dest = os.path.join(root, _file_key(fused_path, name))
    entry_rel = manifest["entry"]
    entry_abs = os.path.join(dest, *entry_rel.split("/"))

    if os.path.isdir(dest):
        if os.path.isfile(entry_abs) and has_fused_meta(entry_abs):
            return {"dir": dest, "entry": entry_abs, "name": name, "reused": True}
        shutil.rmtree(dest, ignore_errors=True)  # half-extracted: rebuild

    staging = tempfile.mkdtemp(prefix="open-", dir=root)
    try:
        payload = os.path.join(staging, PAYLOAD_DIR)
        if manifest.get("fused_app_file") == container.VERSION:
            try:
                container.extract(fused_path, manifest, payload)
            except container.ContainerError as exc:
                raise AppFileError(str(exc))
        else:
            try:
                _extract_zip(fused_path, staging)
            except zipfile.BadZipFile as exc:
                raise AppFileError(str(exc))
        if not os.path.isdir(payload):
            raise AppFileError(f"the .fused file has no {PAYLOAD_DIR}/ payload directory")
        staged_entry = os.path.join(payload, *entry_rel.split("/"))
        if not os.path.isfile(staged_entry):
            raise AppFileError(f"entry page {entry_rel!r} is missing from the .fused payload")
        if not has_fused_meta(staged_entry):
            raise AppFileError(
                f"entry page {entry_rel!r} does not carry <meta name=\"fused-app\"> — not a fused app"
            )
        # Writable extract: the container marks nothing, the zip may carry
        # read-only bits from the exporter. Normalise so writeFile works.
        for dirpath, _dirs, files in os.walk(payload):
            os.chmod(dirpath, 0o755)
            for fn in files:
                p = os.path.join(dirpath, fn)
                os.chmod(p, (os.stat(p).st_mode | 0o600) & 0o777)
        try:
            os.rename(payload, dest)
        except OSError:
            if not (os.path.isfile(entry_abs) and has_fused_meta(entry_abs)):
                raise AppFileError(f"could not place the extracted app at {dest}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"dir": dest, "entry": entry_abs, "name": name, "reused": False}
