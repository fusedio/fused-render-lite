"""The job-banner policy (fused_render_app/notify_policy.py).

`webnotify`'s display half needs the bundled .app and never runs in CI;
every decision it acts on lives in `notify_policy.py` so it can be pinned
here on any platform.
"""
from dataclasses import asdict

import pytest

from fused_render_app import jobs
from fused_render_app import notify_policy as np


def rec(id, state="running", tier="trail", **kw):
    """A public job record (`jobs._public` shape) with every Job key defaulted."""
    record = asdict(jobs.Job(id=id, title=kw.pop("title", "Job")))
    record["stalled"] = False
    record.update(state=state, tier=tier, **kw)
    return record


MODEL = "sys:ai-model:org/repo"
INSTALL = "sys:env-install:abc"


# --- family gate --------------------------------------------------------------

@pytest.mark.parametrize("jid", [
    "page-job-1",              # page-raised row
    "sys:schedule:xyz",        # scheduled run, drawn nowhere
    "sys:other:1",
    "ai-model:org/repo",       # missing sys: prefix
])
def test_unknown_or_page_ids_never_banner(jid):
    assert np.decide(None, rec(jid, "error", message="boom")) is None
    assert np.decide(None, rec(jid, "waiting")) is None
    assert np.decide(rec(jid), rec(jid, "done")) is None


def test_benchmark_family_uses_dash_prefix():
    assert np.decide(rec("sys:ai-benchmark-1"), rec("sys:ai-benchmark-1", "error")) is not None


# --- START ------------------------------------------------------------------

def test_ai_model_resident_load_silent_start_is_none():
    assert np.decide(None, rec(MODEL, tier="silent")) is None


def test_ai_model_download_trail_start_is_silent_banner():
    b = np.decide(None, rec(MODEL, title="Downloading weights", detail="8 GB"))
    assert b == np.Banner("job:" + MODEL, "Downloading weights", "8 GB", "", False)


def test_start_body_falls_back_when_detail_blank():
    assert np.decide(None, rec(INSTALL, detail="  \n ")).body == "Starting…"


def test_redownload_after_prior_done_is_start():
    b = np.decide(rec(MODEL, "done"), rec(MODEL))
    assert b is not None and b.sound is False


@pytest.mark.parametrize("prev_state", ["running", "waiting"])
def test_start_not_repeated_from_non_terminal(prev_state):
    assert np.decide(rec(MODEL, prev_state), rec(MODEL)) is None


@pytest.mark.parametrize("jid", ["sys:ai-image:1", "sys:ai-text:1", "sys:ai-claude:1"])
def test_non_start_families_do_not_announce_start(jid):
    assert np.decide(None, rec(jid)) is None


# --- WAITING ----------------------------------------------------------------

def test_env_install_waiting_banner():
    b = np.decide(rec(INSTALL), rec(INSTALL, "waiting", message="Install anyway?", page="/env"))
    assert b == np.Banner("job:" + INSTALL, "Job", "Install anyway?", "/env", True)


def test_waiting_from_nothing_still_banners_and_falls_back():
    b = np.decide(None, rec("sys:ai-image:1", "waiting", tier="silent"))
    assert b is not None and b.body == "Waiting for you" and b.sound is True
    assert np.decide(None, rec(INSTALL, "waiting", detail="d")).body == "d"


def test_waiting_to_waiting_is_none():
    assert np.decide(rec(INSTALL, "waiting"), rec(INSTALL, "waiting")) is None


# --- TERMINAL ---------------------------------------------------------------

def test_ai_text_done_transient_is_none():
    assert np.decide(rec("sys:ai-text:1", tier="transient"),
                     rec("sys:ai-text:1", "done", tier="transient")) is None


def test_ai_text_error_banners_regardless_of_tier():
    b = np.decide(rec("sys:ai-text:1", tier="transient"),
                  rec("sys:ai-text:1", "error", tier="transient", message="oom"))
    assert b is not None and b.body == "oom" and b.sound is True


def test_silent_error_still_banners():
    b = np.decide(rec(MODEL, tier="silent"), rec(MODEL, "error", tier="silent"))
    assert b is not None and b.body == "Failed"


def test_ai_claude_done_is_none_but_error_is_banner():
    cid = "sys:ai-claude:abc"
    assert np.decide(rec(cid), rec(cid, "done")) is None
    assert np.decide(rec(cid), rec(cid, "error")) is not None


@pytest.mark.parametrize("tier", ["trail", "attention"])
def test_done_kept_tiers_banner(tier):
    b = np.decide(rec(MODEL, tier=tier), rec(MODEL, "done", tier=tier, detail="Ready"))
    assert b is not None and b.body == "Ready" and b.sound is True
    assert np.decide(rec(MODEL), rec(MODEL, "done")).body == "Done"


def test_cancelled_body():
    assert np.decide(rec(MODEL), rec(MODEL, "cancelled", message="x")).body == "Cancelled"


def test_env_install_error_multiline_message_first_line():
    b = np.decide(rec(INSTALL), rec(INSTALL, "error", message="\n\n  pip   failed \nTraceback:\n  x"))
    assert b.body == "pip failed"


def test_error_body_falls_back_to_detail():
    assert np.decide(rec(INSTALL), rec(INSTALL, "error", detail="d")).body == "d"


def test_terminal_from_first_report_counts():
    assert np.decide(None, rec(MODEL, "error")) is not None


def test_terminal_to_terminal_is_none():
    assert np.decide(rec(MODEL, "done"), rec(MODEL, "error")) is None


def test_running_to_running_is_none():
    assert np.decide(rec(MODEL), rec(MODEL, done=5)) is None


def test_unknown_state_is_none():
    assert np.decide(rec(MODEL), rec(MODEL, "bogus")) is None


# --- identifier / hygiene ---------------------------------------------------

def test_identifier_stable_across_start_and_terminal():
    start = np.decide(None, rec(MODEL))
    end = np.decide(rec(MODEL), rec(MODEL, "done"))
    assert start.identifier == end.identifier == "job:" + MODEL


def test_title_hygiene():
    assert np.decide(None, rec(MODEL, title="  \n ")).title == "Render App"
    assert np.decide(None, rec(MODEL, title="\n a   b \nc")).title == "a b"
    t = np.decide(None, rec(MODEL, title="x" * 200)).title
    assert len(t) == 80 and t.endswith("…")


def test_body_cap():
    b = np.decide(rec(MODEL), rec(MODEL, "error", message="y" * 400)).body
    assert len(b) == 150 and b.endswith("…")
    assert np.decide(rec(MODEL), rec(MODEL, "error", message="y" * 150)).body == "y" * 150


def test_page_none_becomes_empty():
    assert np.decide(None, rec(MODEL, page=None)).page == ""


def test_job_id_from_round_trip():
    b = np.decide(None, rec(MODEL))
    assert np.job_id_from(b.identifier) == MODEL
    assert np.job_id_from("web-12") is None
    assert np.job_id_from(None) is None
    assert np.job_id_from("job:") is None
