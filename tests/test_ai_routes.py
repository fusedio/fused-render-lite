"""The copied fused-render AI routers, mounted through _web.APIRouter. These
tests never spawn a worker or reach the Hub: they cover the dispatcher, the
request contract and the offline error paths."""
import json
import os

import pytest

from fused_render_lite import claude_health


@pytest.fixture(autouse=True)
def no_claude(monkeypatch, tmp_path):
    monkeypatch.setenv(claude_health.LITE_BIN_ENV, str(tmp_path / "missing"))
    monkeypatch.setenv(claude_health.BIN_ENV, str(tmp_path / "missing"))


def test_runtime_and_catalog_shapes(client):
    status, _, body = client.get("/api/ai/runtime")
    data = json.loads(body)
    assert status == 200 and isinstance(data["runners"], list)
    codes = {r["code"] for r in data["runners"]}
    assert {"mlx-text", "llamacpp-text", "faster-whisper", "onnx-embed"} <= codes
    status, _, body = client.get("/api/ai/catalog")
    cat = json.loads(body)
    assert status == 200
    caps = {c["capability"] for c in cat["capabilities"]}
    assert {"text-generation", "text-to-image", "automatic-speech-recognition", "embeddings"} <= caps
    for c in cat["capabilities"]:
        assert "available" in c and isinstance(c["models"], list)
        for m in c["models"]:
            assert "id" in m and "fit" in m and "acceptsImage" in m


def test_text_requires_guard_and_validates(client):
    status, _, _ = client.post("/api/ai", {"prompt": "hi"}, headers={"X-Fused": "0"})
    assert status == 403
    status, _, body = client.post("/api/ai", {"prompt": "hi", "bogus": 1})
    assert status == 400 and json.loads(body)["error"]["type"] == "bad_request"
    status, _, body = client.post("/api/ai", {"prompt": ""})
    assert status == 400
    # Claude tier, binary missing -> ai_unavailable, never a 500
    status, _, body = client.post("/api/ai", {"prompt": "hi", "model": "haiku"})
    data = json.loads(body)
    assert status == 502 and data["error"]["type"] == "ai_unavailable", body


def test_local_model_ids_route_to_the_local_tier(client, monkeypatch):
    # A repo id selects the local tier; a folder-less request must not spawn
    # anything here, so make the supervisor say "not resident" without loading.
    from fused_render_lite.ai import supervisor

    def fake_generate_text(model, body):
        raise supervisor.ModelNotReady(f"{model} is loading", "sys:ai-model:test")

    monkeypatch.setattr(supervisor, "generate_text", fake_generate_text)
    status, _, body = client.post("/api/ai", {"prompt": "hi", "model": "mlx-community/x-4bit"})
    data = json.loads(body)
    assert status == 409 and data["error"]["type"] == "model_loading", body
    assert data["error"]["jobId"] == "sys:ai-model:test"


def test_image_and_embed_validation(client):
    status, _, body = client.post("/api/ai/image", {"prompt": "x", "nope": 1})
    assert status == 400
    status, _, body = client.post("/api/ai/embed", {"texts": ["a"], "paths": ["b"]})
    assert status == 400
    status, _, body = client.post("/api/ai/image", {"prompt": "x", "provider": "claude"})
    assert status in (400, 409), body


def test_runtime_load_rejects_garbage(client):
    status, _, body = client.post("/api/ai/runtime/load", {"model": ""})
    assert status == 400
    status, _, body = client.post("/api/ai/cancel", {})
    assert status == 200 and "cancelled" in json.loads(body)


def test_runner_folders_ship_with_the_package():
    from fused_render_lite.ai import registry

    for runner in registry._RUNNERS:
        assert os.path.isfile(os.path.join(runner.folder, "pyproject.toml")), runner.code
        assert os.path.isfile(os.path.join(runner.folder, "worker.py")), runner.code


def test_frozen_app_never_builds_venvs_on_its_own_interpreter(monkeypatch):
    """The py2app interpreter is a stub that cannot run standalone. When frozen,
    envinstall must resolve a uv-managed 3.12 and the engine shim must hand
    that to the install worker (an empty slot makes the worker use the stub)."""
    import sys

    from fused_render_lite import engine, envinstall

    monkeypatch.setattr(sys, "frozen", "macosx_app", raising=False)
    envinstall.reset_script_python_cache()
    monkeypatch.setattr(envinstall, "_running_version", lambda: (3, 12))
    monkeypatch.setattr(envinstall, "uv_bin", lambda: "/fake/uv")
    calls = []

    class Proc:
        returncode = 0
        stdout = "/managed/python3.12\n"
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(envinstall.subprocess, "run", fake_run)
    monkeypatch.setattr(envinstall, "_probe_python", lambda path: True)
    assert envinstall.script_python() == "/managed/python3.12"
    assert any("--managed-python" in c for c in calls)
    assert engine.get_backend()._python_executable == "/managed/python3.12"
    envinstall.reset_script_python_cache()
