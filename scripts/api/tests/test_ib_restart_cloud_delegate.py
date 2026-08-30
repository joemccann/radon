"""POST /ib/restart on the gateway HOST (cloud mode + systemd + helper).

2026-07-26 incident: the operator page's gateway-restart button maps to
POST /ib/restart, which unconditionally refused in cloud mode ("Cannot
restart remote Gateway...") even on the Hetzner host that OWNS the
container. Meanwhile the sanctioned lifecycle path (radon-ib-gateway.service
via the installed control helper: 2FA lease owner, latched-transition state
machine) was reachable one endpoint over in /admin/services.

Contract: in cloud mode the endpoint delegates to
admin_services.control_unit(GATEWAY_UNIT, "restart") when the host can run
it (systemd + helper present); the laptop keeps the remote refusal.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from scripts.api import auth, server
from api import services as admin_services


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    # No context manager: entering it runs the app lifespan (IB pool startup,
    # recovery heartbeat), which fails on CI — sibling API tests construct
    # the client bare for the same reason.
    return TestClient(server.app)


def _auth_headers():
    return {}


def _ok_result():
    return admin_services.ActionResult(
        unit=admin_services.GATEWAY_UNIT,
        action="restart",
        ok=True,
        detail="2FA push lease: acquired holder=radon-cloud.ib-gateway-control",
        returncode=0,
    )


def _lease_held_result():
    return admin_services.ActionResult(
        unit=admin_services.GATEWAY_UNIT,
        action="restart",
        ok=False,
        detail="2FA push lease held by radon-cloud.ib-watchdog",
        returncode=admin_services.PUSH_LOCK_HELD_RC,
    )


def _failed_stack_result(returncode: int = 1):
    return admin_services.ActionResult(
        unit="radon-stack",
        action="restart",
        ok=False,
        detail="control-plane action refused",
        returncode=returncode,
    )


class TestCloudDelegate:
    def test_delegates_to_gateway_unit_on_gateway_host(self, client, monkeypatch):
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
        monkeypatch.setattr(server, "_gateway_unit_controllable", lambda: True)
        with patch.object(
            admin_services, "control_unit", new=AsyncMock(return_value=_ok_result())
        ) as control:
            resp = client.post("/ib/restart", headers=_auth_headers())
        assert resp.status_code == 200
        control.assert_awaited_once_with(admin_services.GATEWAY_UNIT, "restart")
        body = resp.json()
        assert body["restarted"] is True
        assert body["via"] == admin_services.GATEWAY_UNIT
        assert "approve" in body["note"].lower()

    def test_lease_held_maps_to_503_with_reason(self, client, monkeypatch):
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
        monkeypatch.setattr(server, "_gateway_unit_controllable", lambda: True)
        with patch.object(
            admin_services, "control_unit", new=AsyncMock(return_value=_lease_held_result())
        ):
            resp = client.post("/ib/restart", headers=_auth_headers())
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["restarted"] is False
        assert detail["reason"] == "2fa_push_in_flight"

    def test_laptop_keeps_remote_refusal(self, client, monkeypatch):
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
        monkeypatch.setattr(server, "_gateway_unit_controllable", lambda: False)
        resp = client.post("/ib/restart", headers=_auth_headers())
        assert resp.status_code == 503
        assert "remote" in resp.json()["detail"]["error"].lower()

    def test_app_role_is_not_controllable_even_with_helper(self, monkeypatch, tmp_path):
        helper = tmp_path / "radon-ib-gateway-control"
        helper.write_text("#!/bin/sh\n")
        helper.chmod(0o755)
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        assert server._gateway_unit_controllable() is False
        monkeypatch.setenv("RADON_HOST_ROLE", "combined")
        assert server._gateway_unit_controllable() is True
        monkeypatch.setenv("RADON_HOST_ROLE", "broker")
        assert server._gateway_unit_controllable() is True

    def test_service_action_maps_lease_conflict_to_409(self, client):
        with patch.object(
            admin_services, "control_unit", new=AsyncMock(return_value=_lease_held_result())
        ):
            resp = client.post("/admin/services/radon-ib-gateway.service/restart")
        assert resp.status_code == 409
        assert resp.json()["detail"]["returncode"] == admin_services.PUSH_LOCK_HELD_RC

    def test_stack_restart_maps_lease_conflict_to_409(self, client):
        with patch.object(
            admin_services, "restart_full_stack", new=AsyncMock(return_value=_failed_stack_result(409))
        ):
            resp = client.post("/admin/stack/restart")
        assert resp.status_code == 409
        assert resp.json()["detail"]["unit"] == "radon-stack"

    def test_stack_restart_maps_execution_failure_to_502(self, client):
        with patch.object(
            admin_services, "restart_full_stack", new=AsyncMock(return_value=_failed_stack_result(1))
        ):
            resp = client.post("/admin/stack/restart")
        assert resp.status_code == 502
