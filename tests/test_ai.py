import http.client
import json
import os
import sys
import urllib.parse

import pytest

from fused_render_lite import ai

FAKE = os.path.join(os.path.dirname(__file__), "fake_claude.py")


@pytest.fixture
def fake_claude(monkeypatch, tmp_path):
    # A shim script so the binary is directly executable (env override
    # requires an executable file).
    shim = tmp_path / "claude"
    shim.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
    shim.chmod(0o755)
    monkeypatch.setenv(ai.BIN_ENV, str(shim))
    return str(shim)


@pytest.fixture
def no_claude(monkeypatch, tmp_path):
    monkeypatch.setenv(ai.BIN_ENV, str(tmp_path / "missing"))


def test_validate_maps_short_ids_and_warns_on_sampling():
    req, warnings = ai.validate({"prompt": "hi", "model": "sonnet", "temperature": 0.2})
    assert req["model"] == "claude-sonnet-5" and req["effort"] == "low"
    assert [w["setting"] for w in warnings] == ["temperature"]
    assert warnings[0]["type"] == "unsupported-setting"


@pytest.mark.parametrize("body,type_", [
    ({"prompt": ""}, "bad_request"),
    ({"prompt": "x", "bogus": 1}, "bad_request"),
    ({"prompt": "x", "history": []}, "bad_request"),
    ({"prompt": "x", "images": ["a.png"]}, "bad_request"),
    ({"prompt": "x", "effort": "max"}, "bad_request"),
    ({"prompt": "x", "provider": "local"}, "unavailable"),
    ({"prompt": "x", "model": "mlx-community/Qwen3-8B-4bit"}, "unavailable"),
])
def test_validate_rejections(body, type_):
    with pytest.raises(ai.AiError) as e:
        ai.validate(body)
    assert e.value.type == type_


def test_complete_non_streaming(fake_claude):
    result = ai.complete({"prompt": "say hi", "systemPrompt": "Be terse.", "effort": "high"})
    assert result["provider"] == "claude" and result["finishReason"] == "stop"
    assert result["text"].startswith("Hello from fake claude ")
    meta = json.loads(result["text"].split(" ", 4)[4])
    assert meta["model"] == "claude-haiku-4-5" and meta["effort"] == "high"
    assert meta["system_prompt"] == "Be terse." and meta["prompt"] == "say hi"
    assert meta["thinking_env"] is None  # high effort: thinking allowed
    assert result["usage"] == {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10}
    assert result["response"]["modelId"] == "claude-haiku-4-5"
    assert result["providerMetadata"]["claude"]["seconds"] == 0.123


def test_low_effort_disables_thinking(fake_claude):
    result = ai.complete({"prompt": "say hi"})
    meta = json.loads(result["text"].split(" ", 4)[4])
    assert meta["thinking_env"] == "0"


def test_chunks_and_failures(fake_claude):
    got = []
    ai.complete({"prompt": "SLOW please"}, on_chunk=got.append)
    assert got == ["Hello", " from", " fake claude"]
    with pytest.raises(ai.AiError) as e:
        ai.complete({"prompt": "FAIL"})
    assert e.value.type == "ai_error" and "simulated failure" in str(e.value)
    with pytest.raises(ai.AiError) as e:
        ai.complete({"prompt": "CRASH"})
    assert e.value.type == "ai_error" and "code 3" in str(e.value) and "boom" in str(e.value)


def test_missing_binary(no_claude):
    with pytest.raises(ai.AiError) as e:
        ai.complete({"prompt": "hi"})
    assert e.value.type == "ai_unavailable"
    assert ai.catalog()["capabilities"][0]["available"] is False
    assert ai.runtime()["available"] is False


def test_http_json_and_stream(client, fake_claude):
    status, _, body = client.post("/api/ai", {"prompt": "hi", "model": "opus"})
    data = json.loads(body)
    assert status == 200 and data["ok"] is True
    assert data["result"]["response"]["modelId"] == "claude-opus-5"

    status, _, body = client.post("/api/ai", {"prompt": "hi", "provider": "apple"})
    assert status == 409 and json.loads(body)["error"]["type"] == "unavailable"
    status, _, _ = client.post("/api/ai", {"prompt": "hi"}, headers={"X-Fused": "0"})
    assert status == 403

    # streaming: chunked NDJSON, chunk frames then one done frame
    host, port = urllib.parse.urlsplit(client.base).netloc.split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    conn.request("POST", "/api/ai", body=json.dumps({"prompt": "hi", "stream": True}),
                 headers={"Content-Type": "application/json", "X-Fused": "1"})
    res = conn.getresponse()
    assert res.status == 200 and "x-ndjson" in res.getheader("Content-Type")
    frames = [json.loads(l) for l in res.read().decode().splitlines() if l.strip()]
    conn.close()
    assert [f["text"] for f in frames if f["type"] == "chunk"] == ["Hello", " from", " fake claude"]
    done = frames[-1]
    assert done["type"] == "done" and done["ok"] is True
    assert done["result"]["text"].startswith("Hello from fake claude")

    # a failure after streaming began is a done frame, not a broken body
    conn = http.client.HTTPConnection(host, int(port), timeout=30)
    conn.request("POST", "/api/ai", body=json.dumps({"prompt": "FAIL", "stream": True}),
                 headers={"Content-Type": "application/json", "X-Fused": "1"})
    res = conn.getresponse()
    frames = [json.loads(l) for l in res.read().decode().splitlines() if l.strip()]
    conn.close()
    assert frames[-1]["ok"] is False and frames[-1]["error"]["type"] == "ai_error"


def test_catalog_routes(client, fake_claude):
    status, _, body = client.get("/api/ai/catalog")
    cat = json.loads(body)
    assert status == 200 and cat["capabilities"][0]["capability"] == "text-generation"
    assert "text-to-image" in cat["unsupported"]
    status, _, body = client.post("/api/ai/image", {"prompt": "x"})
    assert status == 409 and json.loads(body)["type"] == "unavailable"
