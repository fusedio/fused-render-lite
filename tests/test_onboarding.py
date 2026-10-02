"""The first-run setup wizard's flag, stages and redirect (onboarding.py):
`/` sends a never-seen install to /onboarding and nothing else; the stage
routes validate; the server overrules a stage it can see (first bot); an
upgrade with bots is seeded as completed."""
import http.client
import json
import os

import pytest

from fused_render_app import onboarding
from fused_render_app.bots import paths as bpaths


@pytest.fixture
def fresh(monkeypatch):
    """The real rule: no FUSED_RENDER_ONBOARDING override (conftest forces it
    off for every other test)."""
    monkeypatch.delenv("FUSED_RENDER_ONBOARDING", raising=False)


def _raw_get(client, path):
    """A GET that does NOT follow redirects (urllib would)."""
    host = client.base[len("http://"):]
    conn = http.client.HTTPConnection(host, timeout=30)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read()
    headers = {k.lower(): v for k, v in r.getheaders()}
    conn.close()
    return r.status, headers, body


def j(resp):
    status, _headers, body = resp
    return status, json.loads(body or b"{}")


def test_front_door_redirects_a_never_seen_install(client, fresh):
    status, headers, _ = _raw_get(client, "/")
    assert status == 307
    assert headers["location"] == "/onboarding"
    # A deep link is honoured: the dock's `/?bot=…` must not land on the wizard.
    status, _, _ = _raw_get(client, "/?bot=abc")
    assert status != 307
    # The wizard's own route serves the bots page (or its not-built 503).
    status, _, _ = _raw_get(client, "/onboarding")
    assert status in (200, 503)


def test_opened_stops_the_redirect(client, fresh):
    status, body = j(client.post("/api/onboarding/opened", {}))
    assert status == 200 and body["opened_at"] is not None
    status, _, _ = _raw_get(client, "/")
    assert status != 307


def test_dismiss_and_complete_are_distinct_flags(client, fresh):
    _, body = j(client.post("/api/onboarding/dismiss", {}))
    assert body["dismissed_at"] is not None and body["completed_at"] is None
    _, body = j(client.post("/api/onboarding/complete", {}))
    assert body["completed_at"] is not None and body["dismissed_at"] is not None
    assert onboarding.should_auto_show() is False


def test_force_env_off_reads_as_dismissed_without_writing(client, monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_ONBOARDING", "0")
    _, body = j(client.get("/api/onboarding"))
    assert body["dismissed_at"] is not None
    assert not os.path.exists(onboarding._path())


def test_config_carries_the_snapshot(client, fresh):
    _, body = j(client.get("/api/config"))
    s = body["onboarding"]
    assert s["version"] == onboarding.VERSION
    assert set(s) >= {"completed_at", "dismissed_at", "opened_at", "stages", "chrome"}
    assert s["chrome"]["found"] in (True, False, None)


def test_stage_write_validates_and_merges_meta(client, fresh):
    status, body = j(client.post("/api/onboarding/stage", {"stage": "nope", "status": "complete"}))
    assert status == 400
    status, body = j(client.post("/api/onboarding/stage", {"stage": "about", "status": "later"}))
    assert status == 400
    status, body = j(client.post("/api/onboarding/stage", {"stage": "about", "status": "partial", "meta": {"a": 1}}))
    assert status == 200 and body["stages"]["about"]["status"] == "partial"
    status, body = j(client.post("/api/onboarding/stage", {"stage": "about", "status": "complete", "meta": {"b": 2}}))
    assert body["stages"]["about"]["status"] == "complete"
    assert body["stages"]["about"]["meta"] == {"a": 1, "b": 2}
    assert body["opened_at"] is not None  # any wizard write is an open
    # Guarded like every other mutating POST.
    status, _ = j(client.post("/api/onboarding/stage", {"stage": "about", "status": "complete"}, headers={"X-Fused": ""}))
    assert status == 403


def test_stage_meta_size_is_bounded(client, fresh):
    big = {"note": "x" * (onboarding.META_MAX_BYTES + 1)}
    status, _ = j(client.post("/api/onboarding/stage", {"stage": "about", "status": "complete", "meta": big}))
    assert status == 400


def test_first_bot_is_observed_from_the_bots_dir(client, fresh, tmp_path, monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_DIR", str(tmp_path / "ws"))
    _, body = j(client.get("/api/onboarding"))
    assert "bot" not in body["stages"]
    os.makedirs(os.path.join(bpaths.data_dir(), "bot-1"))
    _, body = j(client.get("/api/onboarding"))
    assert body["stages"]["bot"]["status"] == "complete"
    assert body["stages"]["bot"]["meta"]["bot_count"] == 1


def test_seed_marks_an_existing_install_completed(fresh, tmp_path, monkeypatch):
    monkeypatch.setenv("FUSED_RENDER_DIR", str(tmp_path / "ws"))
    onboarding.seed_for_existing_users()
    assert onboarding.should_auto_show() is True  # no bots: a new user
    os.makedirs(os.path.join(bpaths.data_dir(), "bot-1"))
    onboarding.seed_for_existing_users()
    assert onboarding.should_auto_show() is False
    s = onboarding.snapshot()
    assert s["completed_at"] is not None and s["opened_at"] is None


def test_models_route_offers_the_bots_local_models(client, fresh):
    from fused_render_app.bots import bot as botmod

    _, body = j(client.get("/api/onboarding/models"))
    rows = body["models"]
    assert [r["alias"] for r in rows] == list(botmod.LOCAL_MODELS)
    for r in rows:
        assert r["id"] == botmod.LOCAL_MODELS[r["alias"]]
        assert r["downloaded"] is False
        assert r["size_gb"] == botmod.LOCAL_MODEL_SIZES_GB[r["id"]]
        assert r["fit"] is None or r["fit"]["verdict"] in ("easy", "tight", "no")
