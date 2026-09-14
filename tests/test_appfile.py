import os

import pytest

from fused_render_lite import appfile, container


def test_open_v2_extracts_and_reuses(v2_fused, lite_home):
    first = appfile.open_app_file(v2_fused)
    assert first["name"] == "demo"
    assert first["reused"] is False
    assert os.path.isfile(first["entry"])
    assert open(os.path.join(first["dir"], "data", "note.txt")).read() == "hello"
    assert first["dir"].startswith(str(lite_home / "apps"))
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
