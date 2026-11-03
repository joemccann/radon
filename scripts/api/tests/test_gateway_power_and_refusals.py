"""Gateway power state, typed refusals and operator-hold policy (audit §3.2).

* show_unit guessed the power state from substrings of the helper/broker
  detail text ("stopped" anywhere read as stopped). It now maps the helper's
  status exit code + word to GatewayPowerState.
* Every lifecycle refusal collapsed to PUSH_LOCK_HELD_RC; ActionResult now
  carries a typed ``refusal`` reason.
* An unknown operator hold (None) let _control_gateway start a login while
  the heartbeat stood down only on a confirmed hold. One policy now: unknown
  refuses mutating Gateway actions and the self-exit escalation.
* A status probe started before set_operator_hold must not repopulate the
  cache after the hold changed.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.api import server
from scripts.api import services as admin_services

GATEWAY = admin_services.GATEWAY_UNIT
P = admin_services.GatewayPowerState


@pytest.fixture
def app_remote(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("RADON_HOST_ROLE", "app")
    for name in ("ca.pem", "client.pem", "client.key"):
        (tmp_path / name).write_text("x")
    monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
    monkeypatch.setenv("RADON_IB_REMOTE_CA", str(tmp_path / "ca.pem"))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(tmp_path / "client.pem"))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(tmp_path / "client.key"))
    monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
    admin_services._reset_remote_status_cache()
    yield
    admin_services._reset_remote_status_cache()


class TestPowerState:
    @pytest.mark.parametrize("rc, word, expected", [
        (0, "running", P.RUNNING),
        (1, "stopped", P.STOPPED),
        (74, "transition-pending", P.TRANSITIONING),
        (2, "unknown", P.UNKNOWN),
        # The word alone is not enough: a failed status that says "stopped".
        (2, "stopped", P.UNKNOWN),
        (0, "WARNING: docker stopped responding; running", P.UNKNOWN),
        (-1, "", P.UNKNOWN),
    ])
    def test_helper_status_maps_by_exit_code(self, rc, word, expected):
        assert admin_services.gateway_power_state(rc, word) is expected

    def test_broker_detail_substring_is_not_a_power_state(self, app_remote, monkeypatch):
        monkeypatch.setattr(admin_services, "_remote_http", lambda verb, timeout: (502, {
            "ok": False, "state": "unknown", "returncode": 2,
            "detail": "docker inspect failed: daemon stopped responding",
        }))
        status = asyncio.run(admin_services.show_unit(GATEWAY))
        assert status.active_state == "unknown"
        assert status.sub_state == "unknown"

    @pytest.mark.parametrize("http, payload, active, sub", [
        (200, {"ok": True, "state": "running", "returncode": 0, "detail": "running"}, "active", "running"),
        (200, {"ok": True, "state": "stopped", "returncode": 1, "detail": "stopped"}, "inactive", "dead"),
        (409, {"ok": False, "state": "transition-pending", "returncode": 74,
               "transition": "pending", "detail": "transition-pending"}, "activating", "transition-pending"),
        (-1, {"ok": False, "detail": "timed out"}, "unknown", "unknown"),
    ])
    def test_broker_status_rows(self, app_remote, monkeypatch, http, payload, active, sub):
        monkeypatch.setattr(admin_services, "_remote_http", lambda verb, timeout: (http, payload))
        status = asyncio.run(admin_services.show_unit(GATEWAY))
        assert (status.active_state, status.sub_state) == (active, sub)
        assert status.can_control is True

    def test_local_transition_pending_reads_activating(self, monkeypatch):
        async def fake_systemctl(*_a, **_k):
            return ("LoadState=loaded\nActiveState=active\nSubState=exited\nType=oneshot\n", "", 0)

        async def helper(_action, *, timeout):
            return ("transition-pending", "", admin_services.GATEWAY_CONTROL_BUSY_RC)

        monkeypatch.setenv("RADON_HOST_ROLE", "broker")
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        monkeypatch.setattr(admin_services, "is_gateway_control_available", lambda: True)
        monkeypatch.setattr(admin_services, "_systemctl", fake_systemctl)
        monkeypatch.setattr(admin_services, "_run_gateway_helper", helper)
        status = asyncio.run(admin_services.show_unit(GATEWAY))
        assert status.active_state == "activating"


class TestTypedRefusals:
    @pytest.fixture(autouse=True)
    def _broker(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RADON_HOST_ROLE", "broker")
        monkeypatch.setattr(admin_services, "DEPLOY_LOCK_FILE", tmp_path / "deploy.lock")
        monkeypatch.setattr(admin_services, "is_gateway_control_available", lambda: True)
        monkeypatch.setattr(admin_services, "operator_hold_state", AsyncMock(return_value={"held": False}))

    @pytest.mark.parametrize("rc, refusal", [
        (admin_services.GATEWAY_LEASE_HELD_RC, "lease_held"),
        (admin_services.GATEWAY_CONTROL_BUSY_RC, "control_busy"),
        (admin_services.HELPER_OPERATOR_HOLD_RC, "operator_hold"),
    ])
    def test_helper_refusals_are_typed(self, monkeypatch, rc, refusal):
        monkeypatch.setattr(admin_services, "_run_gateway_helper",
                            AsyncMock(return_value=("", "REFUSING", rc)))
        result = asyncio.run(admin_services.control_unit(GATEWAY, "restart"))
        assert result.ok is False
        assert result.refusal == refusal
        assert result.to_dict()["refusal"] == refusal

    def test_success_has_no_refusal(self, monkeypatch):
        monkeypatch.setattr(admin_services, "_run_gateway_helper",
                            AsyncMock(return_value=("ok", "", 0)))
        result = asyncio.run(admin_services.control_unit(GATEWAY, "restart"))
        assert result.ok is True
        assert result.refusal is None

    def test_deploy_lock_refusal_is_typed(self, monkeypatch, tmp_path):
        import fcntl

        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        handle = open(tmp_path / "deploy.lock", "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            result = asyncio.run(admin_services.control_unit("radon-relay.service", "restart"))
        finally:
            handle.close()
        assert result.returncode == admin_services.PUSH_LOCK_HELD_RC
        assert result.refusal == "deploy_lock_held"

    @pytest.mark.parametrize("http, rc, refusal", [
        (409, 75, "lease_held"),
        (409, 74, "control_busy"),
        (423, None, "operator_hold"),
    ])
    def test_broker_refusals_are_typed(self, app_remote, monkeypatch, http, rc, refusal):
        body = {"ok": False, "detail": "refused"}
        if rc is not None:
            body["returncode"] = rc
        monkeypatch.setattr(admin_services, "_remote_http", lambda verb, timeout, body_=None: (http, body))
        result = asyncio.run(admin_services.remote_gateway_action("restart"))
        assert result.refusal == refusal

    def test_ib_restart_reason_names_the_refusal(self, monkeypatch):
        from fastapi.testclient import TestClient
        from scripts.api import auth

        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
        monkeypatch.setattr(server, "_gateway_unit_controllable", lambda: True)
        monkeypatch.setattr(server.admin_services, "control_unit", AsyncMock(
            return_value=admin_services.ActionResult(
                GATEWAY, "restart", False, "deploy/control lock held",
                admin_services.PUSH_LOCK_HELD_RC, refusal="control_busy",
            )
        ))
        resp = TestClient(server.app).post("/ib/restart")
        assert resp.status_code == 503
        assert resp.json()["detail"]["reason"] == "control_busy"


class TestUnknownHoldPolicy:
    @pytest.mark.parametrize("hold, mutating, refuses", [
        ({"held": True}, True, True),
        ({"held": True}, False, True),
        ({"held": False}, True, False),
        (None, True, True),
        (None, False, False),
    ])
    def test_one_policy(self, hold, mutating, refuses):
        assert admin_services.operator_hold_blocks(hold, mutating=mutating) is refuses

    @pytest.mark.parametrize("action", ["start", "restart"])
    @pytest.mark.parametrize("http, rc", [
        (200, admin_services.OPERATOR_HOLD_RC),
        (-1, admin_services.REMOTE_UNREACHABLE_RC),
    ])
    def test_unknown_hold_refuses_a_login(self, app_remote, monkeypatch, action, http, rc):
        remote = AsyncMock(side_effect=AssertionError("no broker call"))
        monkeypatch.setattr(admin_services, "_remote_http", lambda verb, timeout: (http, {"ok": http == 200}))
        monkeypatch.setattr(admin_services, "operator_hold_state", AsyncMock(return_value=None))
        monkeypatch.setattr(admin_services, "remote_gateway_action", remote)
        result = asyncio.run(admin_services.control_unit(GATEWAY, action))
        assert result.ok is False
        assert result.returncode == rc
        assert result.refusal == "operator_hold_unknown"

    def test_unknown_hold_still_allows_stop(self, app_remote, monkeypatch):
        remote = AsyncMock(return_value=admin_services.ActionResult(GATEWAY, "stop", True, "stopped", 0))
        monkeypatch.setattr(admin_services, "operator_hold_state", AsyncMock(return_value=None))
        monkeypatch.setattr(admin_services, "remote_gateway_action", remote)
        result = asyncio.run(admin_services.control_unit(GATEWAY, "stop"))
        assert result.ok is True

    @pytest.mark.parametrize("hold, allow", [(None, False), ({"held": False}, True)])
    def test_heartbeat_withholds_self_exit_on_unknown_hold(self, monkeypatch, hold, allow):
        seen = {}

        async def guarded(*, allow_self_exit=True):
            seen["allow"] = allow_self_exit

        monkeypatch.setattr(server, "ib_pool", type("P", (), {"status": lambda self: {}})())
        monkeypatch.setattr(server.admin_services, "operator_hold_state", AsyncMock(return_value=hold))
        monkeypatch.setattr(server, "check_ib_gateway", AsyncMock(return_value={}))
        monkeypatch.setattr(server, "_recover_stuck_pool_guarded", guarded)
        asyncio.run(server._ib_recovery_heartbeat_tick())
        assert seen == {"allow": allow}


def test_probe_started_before_a_hold_change_does_not_refill_the_cache(app_remote, monkeypatch):
    calls = {"n": 0}
    started, release = threading.Event(), threading.Event()

    def slow_status(verb, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            started.set()
            release.wait(5)
            return 200, {"ok": True, "state": "stopped", "operator_hold": {"held": False}}
        return 200, {"ok": True, "state": "stopped", "operator_hold": {"held": True}}

    monkeypatch.setattr(admin_services, "_remote_http", slow_status)

    async def scenario():
        first = asyncio.ensure_future(admin_services._remote_status())
        await asyncio.to_thread(started.wait, 5)
        admin_services._reset_remote_status_cache()  # what set_operator_hold does
        release.set()
        await first
        _, payload = await admin_services._remote_status()
        return payload

    payload = asyncio.run(scenario())
    assert payload["operator_hold"] == {"held": True}
    assert calls["n"] == 2
