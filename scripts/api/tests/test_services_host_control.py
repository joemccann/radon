"""``scripts.api.services`` routing through the host control socket.

In production radon-api runs in a container with no systemctl. When the
radon-control daemon's socket is mounted, status, unit actions and the full
stack restart go through it; when it is absent the panel stays read-only.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.api import host_control  # noqa: E402
from scripts.api import services as admin_services  # noqa: E402

_CONTROL_ROWS = [
    {
        "Id": "radon-api.service",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "Description": "Radon FastAPI server",
        "Type": "simple",
        "ActiveEnterTimestamp": "Tue 2026-05-19 09:00:00 UTC",
        "allowed_actions": ["restart"],
    },
    {
        "Id": "radon-relay.service",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "Description": "Radon IB realtime relay",
        "Type": "simple",
        "ActiveEnterTimestamp": "Tue 2026-05-19 09:00:00 UTC",
        "allowed_actions": ["start", "stop", "restart"],
    },
    {
        "Id": "radon-cor.timer",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "waiting",
        "Description": "COR1M daily refresh",
        "ActiveEnterTimestamp": "Mon 2026-05-11 00:00:00 UTC",
        "LastTriggerUSec": "Tue 2026-05-19 21:15:00 UTC",
        "allowed_actions": ["start", "stop", "restart"],
    },
    {
        "Id": "radon-control.service",
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "running",
        "Description": "Radon host control socket",
        "Type": "simple",
        "ActiveEnterTimestamp": "Tue 2026-05-19 09:00:00 UTC",
        "allowed_actions": [],
    },
    # Never trusted: a row outside the API allowlist is dropped.
    {"Id": "ssh.service", "ActiveState": "active", "allowed_actions": ["restart"]},
]


class _FakeHostControl:
    def __init__(self, replies):
        self.replies = replies
        self.requests: list[dict] = []

    async def __call__(self, request: dict, timeout: float) -> dict:
        self.requests.append(dict(request))
        reply = self.replies[request["op"]]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def no_systemctl(monkeypatch):
    monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
    monkeypatch.setattr(admin_services, "is_operator_cli_available", lambda: False)
    monkeypatch.setattr(host_control, "socket_present", lambda: True)
    monkeypatch.delenv("RADON_HOST_ROLE", raising=False)


class TestHostControlStatus:
    def test_snapshot_uses_real_descriptions_uptime_and_per_unit_actions(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"status": {"ok": True, "units": _CONTROL_ROWS}})
        monkeypatch.setattr(host_control, "acall", fake)
        snap = asyncio.run(admin_services.services_snapshot())
        assert snap["supported"] is True
        assert snap["status_source"] == "host-control"
        rows = {u["unit"]: u for u in snap["units"]}
        assert "ssh.service" not in rows
        api = rows["radon-api.service"]
        assert api["description"] == "Radon FastAPI server"
        assert api["uptime_secs"] is not None and api["uptime_secs"] > 0
        assert api["can_control"] is True
        assert api["allowed_actions"] == ["restart"]
        assert rows["radon-relay.service"]["allowed_actions"] == ["start", "stop", "restart"]
        # A timer's last run is its last trigger, not when the timer was armed.
        assert rows["radon-cor.timer"]["last_active_at"] == "2026-05-19T21:15:00Z"
        assert rows["radon-control.service"]["can_control"] is False
        assert fake.requests == [{"op": "status"}]

    def test_app_role_keeps_the_broker_gateway_row(self, no_systemctl, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        fake = _FakeHostControl({"status": {"ok": True, "units": _CONTROL_ROWS}})
        monkeypatch.setattr(host_control, "acall", fake)
        snap = asyncio.run(admin_services.services_snapshot())
        gateway = [u for u in snap["units"] if u["unit"] == admin_services.GATEWAY_UNIT]
        assert len(gateway) == 1
        assert gateway[0]["load_state"] == "remote"

    def test_unreachable_socket_degrades_to_unsupported_health_rows(self, monkeypatch):
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
        monkeypatch.setenv("RADON_CONTROL_SOCKET", "/nonexistent/radon-control/control.sock")
        observed = {"radon-api.service": {"active_state": "active", "sub_state": "running"}}
        monkeypatch.setattr(admin_services, "read_host_unit_states", lambda: observed)
        snap = asyncio.run(admin_services.services_snapshot())
        assert snap["supported"] is False
        assert snap["status_source"] == "host-health"
        api = next(u for u in snap["units"] if u["unit"] == "radon-api.service")
        assert api["active_state"] == "active"
        assert api["can_control"] is False
        # The source note lives once in the response, never in every row.
        assert api["description"] == ""

    def test_daemon_error_degrades_instead_of_raising(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"status": host_control.HostControlError("boom")})
        monkeypatch.setattr(host_control, "acall", fake)
        monkeypatch.setattr(admin_services, "read_host_unit_states", lambda: {})
        snap = asyncio.run(admin_services.services_snapshot())
        assert snap["supported"] is False
        assert snap["status_source"] == "unavailable"

    def test_systemd_host_reports_systemd_source(self, monkeypatch):
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)

        async def fake_list():
            return []

        monkeypatch.setattr(admin_services, "list_units", fake_list)
        snap = asyncio.run(admin_services.services_snapshot())
        assert snap == {"supported": True, "status_source": "systemd", "units": []}


class TestHostControlActions:
    def test_unit_action_goes_through_the_socket_with_the_actor(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"unit": {"ok": True, "returncode": 0, "detail": "restart completed"}})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.control_unit("radon-relay", "restart", actor="user_abc"))
        assert result.ok is True
        assert result.unit == "radon-relay.service"
        assert fake.requests == [{
            "op": "unit", "verb": "restart", "unit": "radon-relay.service", "actor": "user_abc",
        }]

    def test_allowlist_still_fires_before_the_socket(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({})
        monkeypatch.setattr(host_control, "acall", fake)
        bad = asyncio.run(admin_services.control_unit("ssh.service", "restart"))
        verb = asyncio.run(admin_services.control_unit("radon-relay.service", "enable"))
        assert bad.ok is False and verb.ok is False
        assert fake.requests == []

    def test_deploy_lock_refusal_maps_to_conflict(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"unit": {"ok": False, "returncode": 74, "detail": "deploy/control lock held"}})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.control_unit("radon-relay.service", "restart"))
        assert result.returncode == admin_services.PUSH_LOCK_HELD_RC

    def test_policy_refusal_is_a_client_error(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"unit": {
            "ok": False, "refused": True, "returncode": -1, "detail": "would take down the admin panel",
        }})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.control_unit("radon-api.service", "stop"))
        assert result.ok is False
        assert result.returncode == -1
        assert "admin panel" in result.detail

    def test_operator_failure_is_a_bad_gateway_not_a_client_error(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"unit": {"ok": False, "returncode": 1, "detail": "Job failed"}})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.control_unit("radon-relay.service", "start"))
        assert result.ok is False
        assert result.returncode == 1

    def test_unreachable_daemon_is_a_gateway_timeout(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"unit": host_control.HostControlError("connect refused")})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.control_unit("radon-relay.service", "restart"))
        assert result.ok is False
        assert result.returncode == admin_services.REMOTE_UNREACHABLE_RC

    def test_missing_socket_keeps_the_host_only_refusal(self, monkeypatch):
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
        monkeypatch.setenv("RADON_CONTROL_SOCKET", "/nonexistent/control.sock")
        result = asyncio.run(admin_services.control_unit("radon-relay.service", "restart"))
        assert result.ok is False
        assert result.returncode == -1

    def test_stack_restart_goes_through_the_socket(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"stack-restart": {
            "ok": True, "accepted": True, "returncode": 0, "detail": "accepted",
        }})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.restart_full_stack(actor="user_abc"))
        assert result.ok is True
        assert result.unit == "radon-stack"
        assert fake.requests == [{"op": "stack-restart", "actor": "user_abc"}]

    def test_stack_restart_lock_refusal_maps_to_conflict(self, no_systemctl, monkeypatch):
        fake = _FakeHostControl({"stack-restart": {"ok": False, "returncode": 74, "detail": "deploy/control lock held"}})
        monkeypatch.setattr(host_control, "acall", fake)
        result = asyncio.run(admin_services.restart_full_stack())
        assert result.returncode == admin_services.PUSH_LOCK_HELD_RC

    def test_stack_restart_without_cli_or_socket_still_refuses(self, monkeypatch):
        monkeypatch.setattr(admin_services, "is_operator_cli_available", lambda: False)
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
        monkeypatch.setenv("RADON_CONTROL_SOCKET", "/nonexistent/control.sock")
        result = asyncio.run(admin_services.restart_full_stack())
        assert result.ok is False
        assert result.returncode == -1


class TestRoutes:
    """The FastAPI routes report the snapshot and forward + audit the actor."""

    @pytest.fixture()
    def client(self, monkeypatch):
        from fastapi.testclient import TestClient

        from scripts.api import auth, server

        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        return server, TestClient(server.app)

    def test_list_reports_supported_and_status_source(self, client, monkeypatch):
        server, http = client

        async def snapshot():
            return {"supported": True, "status_source": "host-control", "units": []}

        monkeypatch.setattr(server.admin_services, "services_snapshot", snapshot)
        resp = http.get("/admin/services")
        assert resp.status_code == 200
        body = resp.json()
        assert body["supported"] is True
        assert body["status_source"] == "host-control"
        assert "host_role" in body

    def test_unit_action_forwards_and_audits_the_actor(self, client, monkeypatch, caplog):
        server, http = client
        seen = {}

        async def control(unit, action, actor="unknown"):
            seen.update(unit=unit, action=action, actor=actor)
            return admin_services.ActionResult(unit, action, True, "done", 0)

        monkeypatch.setattr(server.admin_services, "control_unit", control)
        with caplog.at_level("INFO", logger="radon.api"):
            resp = http.post("/admin/services/radon-relay.service/restart")
        assert resp.status_code == 200
        assert seen == {"unit": "radon-relay.service", "action": "restart", "actor": "local"}
        audit = [r.getMessage() for r in caplog.records if "admin service action" in r.getMessage()]
        assert audit and "actor=local" in audit[0] and "unit=radon-relay.service" in audit[0]
        assert "ok=True" in audit[0]

    def test_stack_restart_forwards_and_audits_the_actor(self, client, monkeypatch, caplog):
        server, http = client
        seen = {}

        async def restart(actor="unknown"):
            seen["actor"] = actor
            return admin_services.ActionResult("radon-stack", "restart", True, "accepted", 0)

        monkeypatch.setattr(server.admin_services, "restart_full_stack", restart)
        with caplog.at_level("INFO", logger="radon.api"):
            resp = http.post("/admin/stack/restart")
        assert resp.status_code == 200
        assert seen == {"actor": "local"}
        assert any("unit=radon-stack" in r.getMessage() for r in caplog.records)
