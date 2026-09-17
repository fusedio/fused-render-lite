import os

import pytest

from fused_render_app import appfile, container


def test_open_v2_extracts_and_reuses(v2_fused, app_home):
    first = appfile.open_app_file(v2_fused)
    assert first["name"] == "demo"
    assert first["reused"] is False
    assert os.path.isfile(first["entry"])
    assert open(os.path.join(first["dir"], "data", "note.txt")).read() == "hello"
    assert first["dir"].startswith(str(app_home / "apps"))
    # writable extract: writeFile is a supported API
    assert os.access(first["entry"], os.W_OK)
    second = appfile.open_app_file(v2_fused)
    assert second["reused"] is True
    assert second["dir"] == first["dir"]


def test_open_v1_zip(v1_fused):
    result = appfile.open_app_file(v1_fused)
    assert result["name"] == "legacy"
    assert os.path.isfile(os.path.join(result["dir"], "calc.py"))


def test_changed_bytes_get_a_fresh_dir(v2_fused, tmp_path):
    a = appfile.open_app_file(v2_fused)
    other = tmp_path / "demo2.fused"
    container.write(str(other), {"name": "demo", "entry": "index.html"},
                    [("index.html", open(a["entry"], "rb").read() + b"<!-- v2 -->")])
    b = appfile.open_app_file(str(other))
    assert a["dir"] != b["dir"]


def test_missing_marker_rejected(tmp_path):
    out = tmp_path / "bad.fused"
    container.write(str(out), {"name": "bad", "entry": "index.html"},
                    [("index.html", b"<html><body>no marker</body></html>")])
    with pytest.raises(appfile.AppFileError, match="fused-app"):
        appfile.open_app_file(str(out))


def test_bad_entry_rejected(tmp_path):
    out = tmp_path / "bad.fused"
    container.write(str(out), {"name": "bad", "entry": "../x.html"}, [("x.html", b"")])
    with pytest.raises(appfile.AppFileError, match="entry"):
        appfile.read_manifest(str(out))


def test_not_a_fused_file(tmp_path):
    p = tmp_path / "x.fused"
    p.write_bytes(b"not really")
    with pytest.raises(appfile.AppFileError):
        appfile.read_manifest(str(p))


def test_open_materialises_dot_fused(v2_fused):
    """Apps write state into .fused/data without mkdir first (fused-render
    convention); the opener must create the folders on every open."""
    import json
    import shutil

    result = appfile.open_app_file(v2_fused)
    dot = os.path.join(result["dir"], ".fused")
    assert os.path.isdir(os.path.join(dot, "data"))
    assert os.path.isdir(os.path.join(dot, "cache"))
    meta = json.load(open(os.path.join(dot, "meta.json")))
    assert meta["version"] == 1 and meta["app_dir"] == result["dir"]
    # a cache sweep between opens is recovered on the reused path too
    shutil.rmtree(dot)
    again = appfile.open_app_file(v2_fused)
    assert again["reused"] is True
    assert os.path.isdir(os.path.join(dot, "data"))


# ---- shared .fused state per app id ------------------------------------------

APP_ID = "demo-app-82de2580"


def _id_fused(tmp_path, name, payload_extra=b"v1"):
    """A v2 file stamped with ``APP_ID``; ``payload_extra`` changes the bytes
    so two files land in two extract dirs (a re-export of one app)."""
    from tests.conftest import CALC_PY, ENTRY_HTML

    entry = ENTRY_HTML.replace(
        '<meta name="fused-api-version" content="1" />',
        '<meta name="fused-api-version" content="1" />\n'
        f'<meta name="fused-app-id" content="{APP_ID}" />')
    out = tmp_path / name
    container.write(str(out), {"name": "demo", "entry": "index.html", "app_id": APP_ID},
                    [("index.html", entry.encode()), ("calc.py", CALC_PY.encode()),
                     ("v.txt", payload_extra)])
    return str(out)


def test_dot_fused_is_shared_across_iterations_of_one_app(tmp_path, app_home):
    """Two exports of one app (same id, different bytes) extract to two dirs
    but read and write ONE ``.fused``: ``<home>/fused_data/<app_id>``."""
    import json
    import shutil

    a = appfile.open_app_file(_id_fused(tmp_path, "a.fused", b"one"))
    b = appfile.open_app_file(_id_fused(tmp_path, "b.fused", b"two"))
    assert a["dir"] != b["dir"] and a["app_id"] == b["app_id"] == APP_ID

    shared = str(app_home / "fused_data" / APP_ID)
    for r in (a, b):
        dot = os.path.join(r["dir"], ".fused")
        assert os.path.islink(dot) and os.readlink(dot) == shared
        assert os.path.isdir(os.path.join(dot, "data"))
        assert os.path.isdir(os.path.join(dot, "cache"))
    assert appfile.shared_dot_fused_dir(APP_ID) == shared

    with open(os.path.join(a["dir"], ".fused", "data", "state.json"), "w") as f:
        json.dump({"n": 1}, f)
    assert json.load(open(os.path.join(b["dir"], ".fused", "data", "state.json"))) == {"n": 1}
    assert os.path.isfile(os.path.join(shared, "data", "state.json"))
    # meta.json is created once, in the shared dir
    assert os.path.isfile(os.path.join(shared, "meta.json"))

    # re-open (reused path) keeps the link; a swept shared dir is re-materialised
    shutil.rmtree(shared)
    again = appfile.open_app_file(_id_fused(tmp_path, "a.fused", b"one"))
    assert again["reused"] is True
    assert os.path.islink(os.path.join(again["dir"], ".fused"))
    assert os.path.isdir(os.path.join(shared, "data"))


def test_dot_fused_migrates_a_local_dir_into_the_shared_one(tmp_path, app_home):
    """An extract made before linking existed holds a REAL ``.fused``: its
    contents move into the shared dir and a link takes its place."""
    import shutil

    path = _id_fused(tmp_path, "old.fused")
    dest = appfile.open_app_file(path)["dir"]
    shared = appfile.shared_dot_fused_dir(APP_ID)
    # rewind to the pre-linking layout: a real .fused, no shared dir yet
    os.unlink(os.path.join(dest, ".fused"))
    shutil.rmtree(shared)
    os.makedirs(os.path.join(dest, ".fused", "data"))
    with open(os.path.join(dest, ".fused", "data", "x.txt"), "w") as f:
        f.write("kept")

    r = appfile.open_app_file(path)
    assert r["dir"] == dest and r["reused"] is True
    assert os.path.islink(os.path.join(dest, ".fused"))
    assert open(os.path.join(shared, "data", "x.txt")).read() == "kept"


def test_dot_fused_deletes_a_conflicting_local_dir(tmp_path, app_home):
    """Shared state already exists AND the extract has its own real
    ``.fused``: the shared one wins and the local copy is removed."""
    first = appfile.open_app_file(_id_fused(tmp_path, "one.fused", b"one"))
    with open(os.path.join(first["dir"], ".fused", "data", "s.txt"), "w") as f:
        f.write("shared")

    path = _id_fused(tmp_path, "two.fused", b"two")
    dest = appfile.open_app_file(path)["dir"]
    os.unlink(os.path.join(dest, ".fused"))  # pre-linking layout: a real .fused
    os.makedirs(os.path.join(dest, ".fused", "data"))
    with open(os.path.join(dest, ".fused", "data", "s.txt"), "w") as f:
        f.write("local")

    r = appfile.open_app_file(path)
    dot = os.path.join(r["dir"], ".fused")
    assert os.path.islink(dot)
    assert open(os.path.join(dot, "data", "s.txt")).read() == "shared"
    assert not os.path.lexists(dot + ".local")


def test_dot_fused_stays_local_without_an_app_id(v2_fused, app_home):
    """A file that predates app ids keeps a real, per-extract ``.fused``."""
    r = appfile.open_app_file(v2_fused)
    dot = os.path.join(r["dir"], ".fused")
    assert os.path.isdir(dot) and not os.path.islink(dot)
    fd = str(app_home / "fused_data")
    assert not os.path.exists(fd) or not os.listdir(fd)
