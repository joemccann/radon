"""Subscription refresh jobs: no dropped requests, no stale retries, no masked reauth.

Audit implicit-state-machines-20261103 §3.4 "Subscription credential refresh
jobs": start() returned the active job whatever provider was asked for; the
engine retried with the token the async refresh had not yet replaced; and a
one-provider or lock-skipped pass heartbeated the fleet-wide row ``ok``.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import subscription_tokens as tokens
from model_gateway import engine as model_ladder
from model_gateway.engine import complete_multimodal_json
from model_gateway.lifecycle import RefreshManager
from model_gateway.provider_gate import ProviderGate

pytestmark = pytest.mark.usefixtures("isolated_model_credentials")

ALL = list(tokens.PROVIDERS)


def manager():
    names = ("anthropic", "grok", "codex", "antigravity", "nvidia", "cerebras")
    return RefreshManager({name: ProviderGate() for name in names})


def wait_complete(m, job_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if m.status({"job_id": job_id})["status"] == "complete":
            return m.status({"job_id": job_id})
        time.sleep(0.005)
    raise AssertionError(f"job {job_id} did not complete")


@pytest.fixture
def gated_run(monkeypatch):
    """tokens.run blocks until released; every call is recorded."""
    calls: list[tuple[str, list[str]]] = []
    entered = threading.Event()
    release = threading.Event()
    heartbeats: list[str] = []

    def run(mode, names, runtime):
        calls.append((mode, list(names)))
        entered.set()
        assert release.wait(3)
        return {"exit_code": 0, "providers": [{"provider": names[0], "state": "live"}]}

    monkeypatch.setattr(tokens, "run", run)
    monkeypatch.setattr(tokens, "record_heartbeat", lambda _svc, state, **_k: heartbeats.append(state))
    return calls, entered, release, heartbeats


class TestStartNeverDropsARequest:
    def test_other_provider_while_active_is_queued_and_runs(self, gated_run):
        calls, entered, release, _ = gated_run
        m = manager()
        first = m.start({"mode": "once", "providers": ["grok"]})
        assert entered.wait(1)
        second = m.start({"mode": "once", "providers": ["codex"]})
        assert second["job_id"] != first["job_id"]
        assert second["status"] == "queued"
        release.set()
        wait_complete(m, first["job_id"])
        wait_complete(m, second["job_id"])
        assert calls == [("once", ["grok"]), ("once", ["codex"])]

    def test_provider_already_finished_in_the_running_job_gets_a_new_job(self, gated_run):
        calls, entered, release, _ = gated_run
        m = manager()
        release.set()  # grok finishes at once
        gate = threading.Event()
        original = tokens.run

        def slow(mode, names, runtime):
            if names == ["codex"]:
                gate.wait(3)
            return original(mode, names, runtime)

        tokens.run = slow
        try:
            first = m.start({"mode": "once", "providers": ["grok", "codex"]})
            deadline = time.monotonic() + 2
            while ("once", ["codex"]) not in calls and time.monotonic() < deadline:
                time.sleep(0.005)
            again = m.start({"mode": "once", "providers": ["grok"]})
            assert again["job_id"] != first["job_id"], "grok's refresh already ran in that job"
            gate.set()
            wait_complete(m, first["job_id"])
            wait_complete(m, again["job_id"])
        finally:
            tokens.run = original
        assert calls.count(("once", ["grok"])) == 2

    def test_queued_requests_for_the_same_mode_coalesce(self, gated_run):
        _, entered, release, _ = gated_run
        m = manager()
        m.start({"mode": "once", "providers": ["grok"]})
        assert entered.wait(1)
        a = m.start({"mode": "once", "providers": ["codex"]})
        b = m.start({"mode": "once", "providers": ["anthropic"]})
        assert a["job_id"] == b["job_id"]
        release.set()
        wait_complete(m, a["job_id"])


class TestFleetHeartbeat:
    def test_one_provider_pass_never_writes_fleet_ok(self, gated_run):
        _, _, release, heartbeats = gated_run
        release.set()
        m = manager()
        job = m.start({"mode": "once", "providers": ["grok"]})
        wait_complete(m, job["job_id"])
        assert "ok" not in heartbeats

    def test_full_pass_writes_fleet_ok(self, gated_run):
        _, _, release, heartbeats = gated_run
        release.set()
        m = manager()
        job = m.start({"mode": "once"})
        wait_complete(m, job["job_id"])
        assert heartbeats == ["ok"]

    def test_lock_skipped_full_pass_writes_nothing(self, monkeypatch):
        heartbeats: list[str] = []
        monkeypatch.setattr(
            tokens, "run",
            lambda mode, names, rt: {"exit_code": 0, "providers": [], "skipped": "another run holds the lock"},
        )
        monkeypatch.setattr(tokens, "record_heartbeat", lambda _s, state, **_k: heartbeats.append(state))
        m = manager()
        job = m.start({"mode": "once"})
        status = wait_complete(m, job["job_id"])
        assert heartbeats == []
        assert status["result"]["skipped"] is True, "a skipped pass refreshed nothing"

    def test_partial_failure_still_reports_error(self, monkeypatch):
        heartbeats: list[str] = []
        monkeypatch.setattr(
            tokens, "run",
            lambda mode, names, rt: {"exit_code": 1, "providers": [{"provider": names[0], "state": "needs_reauth"}]},
        )
        monkeypatch.setattr(tokens, "record_heartbeat", lambda _s, state, **_k: heartbeats.append(state))
        m = manager()
        job = m.start({"mode": "once", "providers": ["codex"]})
        wait_complete(m, job["job_id"])
        assert heartbeats == ["error"]

    @pytest.mark.parametrize(
        "names, states, skipped, expected",
        [
            (ALL, ["live"] * len(ALL), False, "ok"),
            (ALL, ["live", "needs_reauth"] + ["live"] * (len(ALL) - 2), False, "error"),
            (["grok"], ["live"], False, None),
            (["grok"], ["needs_reauth"], False, "error"),
            (ALL, [], True, None),
        ],
    )
    def test_fleet_heartbeat_state_table(self, names, states, skipped, expected):
        assert tokens.fleet_heartbeat_state(names, states, skipped=skipped) == expected


def test_single_provider_cli_pass_does_not_overwrite_fleet_ok(tmp_path):
    from tests.test_subscription_tokens import make_runtime  # type: ignore

    calls: list[str] = []
    rt = make_runtime(tmp_path, heartbeat=lambda _svc, state, **_k: calls.append(state))
    tokens.run("check", ["grok"], rt)
    assert "ok" not in calls


# --- engine: async refresh is not a fresh token ------------------------------

EXPIRED_BODY = json.dumps({"type": "error", "error": {"type": "authentication_error",
                                                      "message": "OAuth access token has expired."}})


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self):
        return json.loads(self.text)


@pytest.fixture
def _hermetic_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


@pytest.mark.usefixtures("_hermetic_home")
def test_unchanged_token_after_refresh_is_not_resent(tmp_path, monkeypatch):
    token_file = tmp_path / "claude-token"
    token_file.write_text("old-token")
    sent: list[str] = []

    def post(url, *, headers=None, json=None, timeout=None, stream=False):
        sent.append((headers or {}).get("authorization", ""))
        return _Resp(401, EXPIRED_BODY)

    # The gateway's refresh only schedules a job; the token is not yet new.
    monkeypatch.setattr(model_ladder, "_refresh_subscription_token", lambda _name: None)
    with pytest.raises(model_ladder.ModelLadderExhausted) as raised:
        complete_multimodal_json(
            "evaluate",
            env={"CLAUDE_CODE_OAUTH_TOKEN_FILE": str(token_file)},
            post=post,
            stream_anthropic=False,
            providers=("anthropic",),
        )
    assert sent == ["Bearer old-token"], "the stale token must not be resent"
    assert "anthropic:token_refresh_pending" in str(raised.value)
