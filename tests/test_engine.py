"""The fused engine (engine.py) versus the built-in worker (_child.py).

Both engines must produce one wire shape for one script, so the tests run the
same file through each and compare. The fused half skips when the package is
not importable (a plain `pip install -e .[dev]`); CI installs `[dev,fused]`.
"""
import json
import os
import sys

import pytest

from fused_render_app import engine, env

HAS_FUSED = engine.fused_available()
needs_fused = pytest.mark.skipif(not HAS_FUSED, reason="fused package not installed")

CALC = """import json, os
def main(n: int = 1, label: str = "x") -> dict:
    print("hello from calc")
    with open("note.txt") as f:  # relative to the .py, under either engine
        note = f.read()
    return {"double": n * 2, "label": label, "note": note,
            "cwd_is_dir": os.path.basename(os.getcwd()), "file": os.path.basename(__file__),
            "name": __name__}
"""

RESULT_ONLY = "result = {'answer': 42}\n"
NO_ENTRY = "x = 1\n"
RAISES = "def main():\n    raise ValueError('boom')\n"
NOT_JSON = "def main():\n    return object()\n"


@pytest.fixture
def app(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    (d / "note.txt").write_text("hi")
    return d


def _write(app, name, src):
    p = app / name
    p.write_text(src)
    return str(p)


def _run(monkeypatch, which, path, params):
    """`env.run_python` with the engine forced to `which`, on this interpreter."""
    monkeypatch.setenv(engine.ENGINE_ENV, which)
    monkeypatch.setattr(env, "ensure", lambda app_dir: None)
    monkeypatch.setattr(env, "interpreter_for", lambda app_dir: sys.executable)
    return env.run_python(path, params, os.path.dirname(path))


def test_forced_override_parsing(monkeypatch):
    monkeypatch.delenv(engine.ENGINE_ENV, raising=False)
    assert engine.forced_override() is None
    monkeypatch.setenv(engine.ENGINE_ENV, "child")
    assert engine.forced_override() == "child" and engine.active() is False
    monkeypatch.setenv(engine.ENGINE_ENV, "bogus")
    assert engine.forced_override() is None


def test_forced_fused_without_package_is_a_wire_error(monkeypatch, app):
    monkeypatch.setattr(engine, "fused_available", lambda: False)
    path = _write(app, "calc.py", CALC)
    r = _run(monkeypatch, "fused", path, {})
    assert r["ok"] is False and r["error"]["type"] == "EngineError"
    assert "fused-render-app[fused]" in r["error"]["message"]


def test_loader_probe_survives_without_fused(monkeypatch):
    """`available()` is what the AI supervisor gates venv builds on; it must
    not flip false just because the engine is absent."""
    monkeypatch.setattr(engine, "fused_available", lambda: False)
    engine.reset_backend()
    assert engine.available() is True
    assert type(engine.get_backend()).__name__ == "_NoBackend"
    engine.reset_backend()


def test_build_code_embeds_binding_and_user_source():
    code = engine.build_code("def main(): pass\n", "/some/dir", "/some/dir/s.py")
    assert "def bind_params" in code  # _binding.py travels inside the wrapper
    assert "'/some/dir'" in code and "'/some/dir/s.py'" in code
    assert '__name__ = "__fused_module__"' in code


def test_split_error_maps_backend_timeout_to_worker_type():
    assert engine._split_error("Execution timed out after 600s") == (
        "TimeoutError", "Execution timed out after 600s")
    assert engine._split_error("Traceback...\nValueError: boom\n") == ("ValueError", "boom")
    assert engine._split_error("something odd") == ("Error", "something odd")


def test_child_engine_baseline(monkeypatch, app):
    path = _write(app, "calc.py", CALC)
    r = _run(monkeypatch, "child", path, {"n": "21"})
    assert r["ok"] is True, r
    assert r["result"]["double"] == 42 and r["result"]["note"] == "hi"
    assert r["result"]["cwd_is_dir"] == "app" and r["result"]["file"] == "calc.py"
    assert r["result"]["name"] == "__fused_module__"
    assert "hello from calc" in r["stdout"]


@needs_fused
def test_backend_is_constructed_with_the_loader_interpreter(monkeypatch):
    from fused_render_app import envinstall

    monkeypatch.delenv(engine.ENGINE_ENV, raising=False)
    envinstall.reset_script_python_cache()  # also drops the backend singleton
    b = engine.get_backend()
    assert type(b).__name__ == "LocalPythonComputeBackend"
    assert b._python_executable == envinstall.script_python()
    assert engine.get_backend() is b  # singleton
    envinstall.reset_script_python_cache()
    assert engine.get_backend() is not b


@needs_fused
@pytest.mark.parametrize("params", [{"n": "21"}, {"n": 3, "label": "y"}, {}])
def test_parity_success(monkeypatch, app, params):
    path = _write(app, "calc.py", CALC)
    child = _run(monkeypatch, "child", path, dict(params))
    fused = _run(monkeypatch, "fused", path, dict(params))
    assert fused["ok"] is True, fused
    assert fused["result"] == child["result"]
    assert fused["stdout"] == child["stdout"]
    assert isinstance(fused["duration_ms"], int)


@needs_fused
@pytest.mark.parametrize("src,params,err_type", [
    (CALC, {"n": "x"}, "ParamError"),
    (RAISES, {}, "ValueError"),
    (NO_ENTRY, {}, "AttributeError"),
    (NOT_JSON, {}, "TypeError"),
    ("import nosuchmodule_xyz\n", {}, "ModuleNotFoundError"),
])
def test_parity_errors(monkeypatch, app, src, params, err_type):
    path = _write(app, "s.py", src)
    child = _run(monkeypatch, "child", path, dict(params))
    fused = _run(monkeypatch, "fused", path, dict(params))
    assert child["ok"] is False and fused["ok"] is False
    assert child["error"]["type"] == err_type
    assert fused["error"]["type"] == err_type, fused["error"]
    # The no-entrypoint message is the worker's, extended with the fused
    # contract's alternatives; every other message is identical.
    assert fused["error"]["message"].startswith(child["error"]["message"])


@needs_fused
def test_fused_traceback_starts_at_user_file(monkeypatch, app):
    path = _write(app, "s.py", RAISES)
    r = _run(monkeypatch, "fused", path, {})
    tb = r["error"]["traceback"]
    assert "<lambda_exec>" not in tb and "_runner.py" not in tb
    assert f'File "{path}", line 2' in tb


@needs_fused
def test_fused_accepts_result_variable(monkeypatch, app):
    """The fused contract's `result = ...` form (the worker rejects it: no
    main). Not a parity case by design."""
    path = _write(app, "s.py", RESULT_ONLY)
    r = _run(monkeypatch, "fused", path, {})
    assert r["ok"] is True and r["result"] == {"answer": 42}


@needs_fused
def test_api_run_through_fused(client, v2_fused, monkeypatch):
    """The HTTP route, end to end, on the fused engine."""
    monkeypatch.setenv(engine.ENGINE_ENV, "fused")
    monkeypatch.setattr(env, "is_ready", lambda app_dir: True)
    monkeypatch.setattr(env, "interpreter_for", lambda app_dir: sys.executable)
    status, _, body = client.post("/api/open", {"file": v2_fused})
    assert status == 200, body
    entry = json.loads(body)["entry"]
    status, _, body = client.post("/api/run", {"py": "calc.py", "html": entry, "params": {"n": "21"}})
    r = json.loads(body)
    assert status == 200 and r["ok"] is True, r
    assert r["result"] == {"double": 42, "label": "x"}
    assert "hello from calc" in r["stdout"]
