"""Open a ``.fused`` single-file app.

Two physical formats are accepted: the v2 opaque container (``container.py``,
magic ``FUSEDAPP``) and the legacy v1 zip (``manifest.json`` + ``files/``).
Both carry a manifest with ``name`` and ``entry`` (the payload-relative entry
page). Opening is extract-then-serve: the payload lands in
``~/.fused-render-app/apps/<slug>-<sha256 prefix>/`` keyed by the file's
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
from datetime import datetime, timezone

from fused_render_app import container, paths

PAYLOAD_DIR = "files"
MANIFEST_NAME = "manifest.json"

MAX_ENTRIES = 5000
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
_MANIFEST_CAP = 256 * 1024

_META_RE = re.compile(r"<meta\s[^>]*name\s*=\s*[\"']fused-app[\"']", re.I)

# ---- stable app identity (fused-render's app_id.py, D884) --------------------
#
# ``<meta name="fused-app-id" content="my-app-82de2580">`` in the entry page,
# minted once by fused-render on the app's first export and copied into the
# container index as ``app_id``. Same id across updates of one app, different
# ids for two apps that share a name. The value comes out of an untrusted
# file: it is validated by ``APP_ID_RE`` on every read and treated as absent
# when malformed. It IS used as a filename segment here (downloads keyed on
# it) — safe only because the regex admits nothing but ``[a-z0-9-]``.
APP_ID_META = "fused-app-id"
APP_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,47})-[0-9a-f]{8}$")
_APP_ID_SCAN_BYTES = 4096
_APP_ID_TAG_RE = re.compile(
    rb"<meta\s[^>]*name\s*=\s*[\"']?fused-app-id[\"']?[^>]*>", re.I)
_APP_ID_CONTENT_RE = re.compile(rb"content\s*=\s*[\"']([^\"']*)[\"']", re.I)


def is_app_id(value: object) -> bool:
    return isinstance(value, str) and APP_ID_RE.match(value) is not None


def app_id_from_text(head: bytes | str) -> str | None:
    """The declared app id in a page's head bytes, None when the tag is
    absent or malformed. Same two-step read as fused-render (find the tag,
    then its ``content``) so attribute order does not matter."""
    if isinstance(head, str):
        head = head.encode("utf-8", "ignore")
    tag = _APP_ID_TAG_RE.search(head[:_APP_ID_SCAN_BYTES])
    if not tag:
        return None
    m = _APP_ID_CONTENT_RE.search(tag.group(0))
    if not m:
        return None
    value = m.group(1).decode("utf-8", "ignore").strip()
    return value if is_app_id(value) else None


def app_id_of(fused_path: str, manifest: dict | None = None) -> str | None:
    """The .fused file's stable app id, or None for a file that predates it.

    The manifest's ``app_id`` first (one bounded index read, nothing
    extracted); else the entry page's head inside the container — an
    exporter that stamped the page but not its index still identifies the
    app. Never raises."""
    try:
        if manifest is None:
            manifest = read_manifest(fused_path)
        v = manifest.get("app_id")
        if is_app_id(v):
            return v
        entry = manifest.get("entry")
        if _entry_problem(entry):
            return None
        head = _shipped_member_bytes(fused_path, manifest, (entry,),
                                     lambda _n: _APP_ID_SCAN_BYTES, allow_over=True)
        return app_id_from_text(head) if head else None
    except (AppFileError, container.ContainerError, OSError, KeyError, ValueError,
            zipfile.BadZipFile):
        return None


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


def extract_dir_for(fused_path: str) -> str | None:
    """Where `open_app_file` extracts (or has extracted) ``fused_path`` —
    the same ``<apps_dir>/<slug>-<hash>`` it would answer as ``dir`` — WITHOUT
    extracting anything. None if the file cannot be read as a .fused.

    The reverse of the mapping a job row's ``page`` carries: an environment
    install names the extracted app folder it is building for, and a native
    notification click has to find the window showing the .fused that folder
    came from. Cheap on repeat calls (`_file_key` memoises on size+mtime)."""
    fused_path = os.path.abspath(fused_path)
    try:
        manifest = read_manifest(fused_path)
        name = manifest.get("name") if isinstance(manifest.get("name"), str) else "app"
        return os.path.join(paths.apps_dir(), _file_key(fused_path, name))
    except (AppFileError, OSError):
        return None


def ensure_dot_fused(app_dir: str) -> bool:
    """Materialise ``<app>/.fused/data``, ``.fused/cache`` and ``meta.json``.

    Same convention as fused-render's ``app_fused_dir.ensure``: the server
    creates the folders when an app is opened, so an app never has to
    ``mkdir`` before its first ``writeFile`` into ``.fused/data``. Best-effort:
    a failure here must not stop the app from opening.
    """
    try:
        dot = os.path.join(app_dir, ".fused")
        os.makedirs(os.path.join(dot, "data"), exist_ok=True)
        os.makedirs(os.path.join(dot, "cache"), exist_ok=True)
        meta = os.path.join(dot, "meta.json")
        if not os.path.exists(meta):
            payload = {"version": 1, "app_dir": app_dir,
                       "created_at": datetime.now(timezone.utc).isoformat(), "migrations": []}
            try:
                with open(meta, "x", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
            except FileExistsError:
                pass  # a concurrent open won the race with the same content
        return True
    except OSError:
        return False


def open_app_file(fused_path: str) -> dict:
    """Extract (or re-use) and answer ``{"dir", "entry", "name", "reused", "app_id"}``
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
            ensure_dot_fused(dest)
            return {"dir": dest, "entry": entry_abs, "name": name, "reused": True,
                    "app_id": app_id_of(fused_path, manifest)}
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
    ensure_dot_fused(dest)
    return {"dir": dest, "entry": entry_abs, "name": name, "reused": False,
            "app_id": app_id_of(fused_path, manifest)}


# ---- icon --------------------------------------------------------------------

# The app icon names, in PRECEDENCE order (fused-render's app_listing.
# ICON_NAMES): `icon.svg` wins, `icon.png` stands in when there is no svg. An
# svg owns its own plate and is drawn as is; a png is a plain square raster the
# dock tile clips to its rounded corners (`.tile { overflow: hidden }` +
# `object-fit: cover` in dock.html).
ICON_NAMES = ("icon.svg", "icon.png")
# The svg — the name the picker writes on the fused-render side, and the one
# that outranks a png dropped in beside it.
ICON_NAME = ICON_NAMES[0]
ICON_MAX_BYTES = 64 * 1024
# A raster is bigger by nature (a 256px opaque png runs tens of KB), but the
# dock draws it at ~128px @2x at most: half a MB is generous, a photo is not
# an icon.
PNG_ICON_MAX_BYTES = 512 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def icon_cap(name: str) -> int:
    """The byte cap for an icon file by its name; over it counts as absent."""
    return PNG_ICON_MAX_BYTES if name.endswith(".png") else ICON_MAX_BYTES


def is_png(data: bytes) -> bool:
    return data.startswith(PNG_SIGNATURE)


def icon_override_paths(fused_path: str) -> list[str] | None:
    """Where an app's extract would hold a written icon, one path per
    ``ICON_NAMES`` in precedence order (whether or not any exists yet), or
    None if the file is unreadable. Cheap after the first call: ``_file_key``
    is memoised on the file's (size, mtime)."""
    try:
        fused_path = os.path.abspath(fused_path)
        if not os.path.isfile(fused_path):
            return None
        base = _extract_base(fused_path, read_manifest(fused_path))
        return [os.path.join(base, n) for n in ICON_NAMES]
    except (AppFileError, container.ContainerError, OSError, KeyError, zipfile.BadZipFile):
        return None


def _shipped_member_bytes(fused_path: str, manifest: dict, names, cap_for, *,
                          allow_over: bool = False) -> bytes | None:
    """The first of ``names`` packed inside the .fused itself (no extract
    lookup) that is there and within its cap (``cap_for(name)``). An
    over-cap member does not hide the next name. With ``allow_over`` the
    first ``cap`` bytes of an over-cap member are answered instead (a head
    read, e.g. the entry page's meta tags)."""
    v2 = manifest.get("fused_app_file") == container.VERSION
    for name in names:
        cap = cap_for(name)
        if v2:
            data = container.read_member(fused_path, manifest, name, cap)
        else:
            with zipfile.ZipFile(fused_path) as zf:
                try:
                    with zf.open(f"{PAYLOAD_DIR}/{name}") as f:
                        data = f.read(cap + 1)
                except KeyError:
                    data = None
        if data is None:
            continue
        if len(data) <= cap:
            return data
        if allow_over:
            return data[:cap]
    return None


def _shipped_icon_bytes(fused_path: str, manifest: dict) -> bytes | None:
    """The icon packed inside the .fused itself (no extract lookup): the
    first of ``ICON_NAMES`` that is there and within its cap. An over-cap
    svg does not hide a png beside it."""
    return _shipped_member_bytes(fused_path, manifest, ICON_NAMES, icon_cap)


def has_shipped_icon(fused_path: str) -> bool:
    """Whether the .fused packs an icon (``ICON_NAMES``; ignores any extract override)."""
    try:
        fused_path = os.path.abspath(fused_path)
        if not os.path.isfile(fused_path):
            return False
        return _shipped_icon_bytes(fused_path, read_manifest(fused_path)) is not None
    except (AppFileError, container.ContainerError, OSError, KeyError, zipfile.BadZipFile):
        return False


def _override_bytes(candidates: list[str], cap_for) -> bytes | None:
    """The first extract-dir file that exists and is within its cap."""
    for p in candidates:
        cap = cap_for(p)
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= cap:
                with open(p, "rb") as f:
                    return f.read(cap)
        except OSError:
            continue
    return None


def _override_icon_bytes(candidates: list[str]) -> bytes | None:
    """The first extract-dir icon that exists and is within its cap."""
    return _override_bytes(candidates, icon_cap)


def _extract_base(fused_path: str, manifest: dict) -> str:
    """Where ``fused_path``'s extract lives (whether or not it exists yet)."""
    name = manifest.get("name") if isinstance(manifest.get("name"), str) else "app"
    return os.path.join(paths.apps_dir(), _file_key(fused_path, name))


def icon_bytes(fused_path: str) -> bytes | None:
    """The app's icon (``icon.svg``, else ``icon.png``) for the menu-bar
    dock, or None. Never raises. `is_png` tells the two apart.

    Looked up in this order, cheapest-first for the common case and so an
    app that WRITES its own icon into its extract wins over the shipped one:
    the extracted dir (if any; svg then png), then the container member /
    the v1 zip's ``files/`` (svg then png). Anything over its cap
    (`icon_cap`) counts as absent and the walk goes on — the dock polls this
    for every card and a file that size is not an icon. `dock_store._card_info`
    walks the same candidates, so ``hasIcon`` and this route agree.
    """
    try:
        fused_path = os.path.abspath(fused_path)
        if not os.path.isfile(fused_path):
            return None
        manifest = read_manifest(fused_path)
        base = _extract_base(fused_path, manifest)
        data = _override_icon_bytes([os.path.join(base, n) for n in ICON_NAMES])
        if data is not None:
            return data
        return _shipped_icon_bytes(fused_path, manifest)
    except (AppFileError, container.ContainerError, OSError, KeyError, zipfile.BadZipFile):
        return None


# ---- preview -----------------------------------------------------------------

# The app's screenshot, ``preview.png`` (the showcase cards' image, and the
# picture the menu-bar dock shows in a tile's hover bubble). One name, one
# cap, shared with showcase.py. A png only: the dock's <img> is fed it as is.
PREVIEW_NAME = "preview.png"
PREVIEW_MAX_BYTES = 8 * 1024 * 1024


def preview_cap(_name: str) -> int:
    return PREVIEW_MAX_BYTES


def preview_override_path(fused_path: str) -> str | None:
    """Where an app's extract would hold a written ``preview.png`` (whether
    or not it exists yet), or None if the file is unreadable."""
    try:
        fused_path = os.path.abspath(fused_path)
        if not os.path.isfile(fused_path):
            return None
        return os.path.join(_extract_base(fused_path, read_manifest(fused_path)), PREVIEW_NAME)
    except (AppFileError, container.ContainerError, OSError, KeyError, zipfile.BadZipFile):
        return None


def has_shipped_preview(fused_path: str) -> bool:
    """Whether the .fused packs a ``preview.png`` within its cap (ignores
    any extract override). Reads only the index for a v2 container / the
    zip directory for v1 — the dock polls this for every card."""
    try:
        fused_path = os.path.abspath(fused_path)
        if not os.path.isfile(fused_path):
            return False
        manifest = read_manifest(fused_path)
        if manifest.get("fused_app_file") == container.VERSION:
            entry = container.find(manifest, PREVIEW_NAME)
            return entry is not None and int(entry.get("size", 0)) <= PREVIEW_MAX_BYTES
        with zipfile.ZipFile(fused_path) as zf:
            try:
                info = zf.getinfo(f"{PAYLOAD_DIR}/{PREVIEW_NAME}")
            except KeyError:
                return False
            return info.file_size <= PREVIEW_MAX_BYTES
    except (AppFileError, container.ContainerError, OSError, KeyError, ValueError, TypeError,
            zipfile.BadZipFile):
        return False


def preview_bytes(fused_path: str) -> bytes | None:
    """The app's ``preview.png`` for the dock's hover bubble, or None. Never
    raises. Same walk as `icon_bytes`: the extract dir first (an app that
    writes its own preview wins), then the shipped member / the v1 zip's
    ``files/``; anything over `PREVIEW_MAX_BYTES` counts as absent.
    `dock_store._card_info` walks the same candidates, so ``hasPreview`` and
    ``/api/dock/preview`` agree.
    """
    try:
        fused_path = os.path.abspath(fused_path)
        if not os.path.isfile(fused_path):
            return None
        manifest = read_manifest(fused_path)
        base = _extract_base(fused_path, manifest)
        data = _override_bytes([os.path.join(base, PREVIEW_NAME)], preview_cap)
        if data is not None:
            return data
        return _shipped_member_bytes(fused_path, manifest, (PREVIEW_NAME,), preview_cap)
    except (AppFileError, container.ContainerError, OSError, KeyError, zipfile.BadZipFile):
        return None
