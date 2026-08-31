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
        monkeypatch.delenv("RADON_IB_REMOTE_URL", raising=False)
        assert server._gateway_unit_controllable() is False
        monkeypatch.setenv("RADON_HOST_ROLE", "combined")
        assert server._gateway_unit_controllable() is True
        monkeypatch.setenv("RADON_HOST_ROLE", "broker")
        assert server._gateway_unit_controllable() is True

    def test_app_role_is_controllable_via_remote_not_helper(
        self, monkeypatch, tmp_path
    ):
        helper = tmp_path / "radon-ib-gateway-control"
        helper.write_text("#!/bin/sh\n")
        helper.chmod(0o755)
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client.key"
        ca.write_text("x")
        cert.write_text("x")
        key.write_text("x")
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
        monkeypatch.setenv("RADON_IB_REMOTE_CA", str(ca))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(cert))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(key))
        assert server._gateway_unit_controllable() is True
        assert admin_services.is_remote_gateway_configured() is True

    def test_app_role_admin_gateway_restart_does_not_touch_helper(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)
        log = tmp_path / "gateway-helper.log"
        helper = tmp_path / "radon-ib-gateway-control"
        helper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n"
            "exit 0\n"
        )
        helper.chmod(0o755)
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: True)
        resp = client.post("/admin/services/radon-ib-gateway.service/restart")
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["ok"] is False
        assert "broker" in detail["detail"].lower()
        assert not log.exists()

    def test_app_role_restart_proxies_remote_not_helper(
        self, client, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
        log = tmp_path / "gateway-helper.log"
        helper = tmp_path / "radon-ib-gateway-control"
        helper.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$*\" >> '{log}'\n"
            "exit 0\n"
        )
        helper.chmod(0o755)
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client.key"
        ca.write_text("x")
        cert.write_text("x")
        key.write_text("x")
        monkeypatch.setattr(admin_services, "GATEWAY_CONTROL_PATH", str(helper))
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
        monkeypatch.setenv("RADON_IB_REMOTE_CA", str(ca))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(cert))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(key))
        remote = AsyncMock(return_value=_ok_result())
        with patch.object(admin_services, "remote_gateway_action", new=remote):
            resp = client.post("/ib/restart", headers=_auth_headers())
        assert resp.status_code == 200
        remote.assert_awaited_once_with("restart")
        assert not log.exists()

    def test_app_role_rejects_http_and_public_remote_url(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        ca = tmp_path / "ca.pem"
        cert = tmp_path / "client.pem"
        key = tmp_path / "client.key"
        for path in (ca, cert, key):
            path.write_text("x")
        monkeypatch.setenv("RADON_IB_REMOTE_CA", str(ca))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(cert))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(key))
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "http://10.0.0.4:8340")
        assert admin_services.is_remote_gateway_configured() is False
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://5.78.144.125:8340")
        assert admin_services.is_remote_gateway_configured() is False
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://broker.example:8340")
        assert admin_services.is_remote_gateway_configured() is False

    def test_app_role_gateway_mutation_flag(self, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        restart = type("R", (), {"method": "POST", "url": type("U", (), {"path": "/ib/restart"})()})()
        health = type("R", (), {"method": "GET", "url": type("U", (), {"path": "/health"})()})()
        stack = type("R", (), {"method": "POST", "url": type("U", (), {"path": "/admin/stack/restart"})()})()
        assert server._is_app_role_gateway_mutation(restart) is True
        assert server._is_app_role_gateway_mutation(health) is False
        assert server._is_app_role_gateway_mutation(stack) is False
        monkeypatch.setenv("RADON_HOST_ROLE", "combined")
        assert server._is_app_role_gateway_mutation(restart) is False

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
