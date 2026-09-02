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

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from scripts.api import auth, server
from api import auth as auth_mod  # the module object server.py binds
from api import services as admin_services

# One RSA keypair for the whole module (keygen is the slow part). Same shape
# as test_auth.py so the app-host gate is exercised with a REAL RS256 token.
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _PRIVATE_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUBLIC_PEM = (
    _PRIVATE_KEY.public_key()
    .public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)
_ISSUER = "https://clerk.radon.test"

# (route, broker verb the handler must forward) for every app-host Gateway
# mutation the middleware gate covers.
_GATEWAY_MUTATIONS = [
    ("/ib/restart", "restart"),
    ("/ib/reset-backoff", "reset-lease"),
    ("/admin/services/radon-ib-gateway.service/start", "start"),
    # systemd alias: missing .service suffix is the same unit. The
    # privileged-action gate must fire on this spelling too.
    ("/admin/services/radon-ib-gateway/restart", "restart"),
]


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


def _req(method: str, path: str):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _operator_jwt() -> str:
    payload = {"sub": "user_ok", "iss": _ISSUER, "exp": int(time.time()) + 3600}
    return pyjwt.encode(payload, _PRIVATE_PEM, algorithm="RS256")


@pytest.fixture()
def app_host(monkeypatch, tmp_path):
    """RADON_HOST_ROLE=app, Clerk configured, loopback trust NOT stubbed.

    The client really is 127.0.0.1 with no forwarding headers, i.e. exactly
    the peer a compromised Next.js on the public terminator would be. Only
    the JWKS fetch is faked; pyjwt verifies the RS256 token for real. Every
    Gateway handler is wired to the (mocked) broker call so the assertion is
    on the wire: did `remote_gateway_action` run, and with which verb.
    """
    monkeypatch.setenv("RADON_HOST_ROLE", "app")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", _ISSUER)
    monkeypatch.setenv("ALLOWED_USER_IDS", "user_ok")
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
    signing_key = SimpleNamespace(key=_PUBLIC_PEM)
    monkeypatch.setattr(
        auth_mod,
        "_get_jwks_client",
        lambda: SimpleNamespace(get_signing_key_from_jwt=lambda token: signing_key),
    )
    auth_mod._jwks_negative.clear()

    # Broker "configured" so /ib/restart and the unit route reach the proxy.
    for name in ("ca.pem", "client.pem", "client.key"):
        (tmp_path / name).write_text("x")
    monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
    monkeypatch.setenv("RADON_IB_REMOTE_CA", str(tmp_path / "ca.pem"))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(tmp_path / "client.pem"))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(tmp_path / "client.key"))
    monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
    # Never let a test clear the host's real 2FA push lock.
    monkeypatch.setattr(server, "reset_restart_backoff", lambda: {"reset": True})

    remote = AsyncMock(return_value=_ok_result())
    monkeypatch.setattr(admin_services, "remote_gateway_action", remote)
    client = TestClient(server.app, client=("127.0.0.1", 40000))
    return client, remote


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
        restart = _req("POST", "/ib/restart")
        health = _req("GET", "/health")
        stack = _req("POST", "/admin/stack/restart")
        assert server._is_app_role_gateway_mutation(restart) is True
        assert server._is_app_role_gateway_mutation(_req("POST", "/ib/reset-backoff")) is True
        for action in ("start", "stop", "restart"):
            unit_action = _req("POST", f"/admin/services/radon-ib-gateway.service/{action}")
            assert server._is_app_role_gateway_mutation(unit_action) is True, action
            unsuffixed = _req("POST", f"/admin/services/radon-ib-gateway/{action}")
            assert server._is_app_role_gateway_mutation(unsuffixed) is True, action
        assert server._is_app_role_gateway_mutation(health) is False
        assert server._is_app_role_gateway_mutation(stack) is False
        assert server._is_app_role_gateway_mutation(_req("GET", "/ib/restart")) is False
        assert server._is_app_role_gateway_mutation(
            _req("POST", "/admin/services/radon-api.service/restart")
        ) is False
        assert server._is_app_role_gateway_mutation(
            _req("POST", "/admin/services/radon-ib-gateway.service/status")
        ) is False
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


class TestAppRoleGateAtTheWire:
    """T-346: the app-host gate must hold with loopback trust UNSTUBBED.

    Sibling tests force `is_trusted_local_request` True and stub the predicate
    open, so deleting `and not _is_app_role_gateway_mutation(request)` from
    the middleware left the file green. These cases pin the contract itself:
    a Clerk-less loopback caller cannot cycle IBKR from the app host, an
    operator JWT can, and everything else keeps the server-to-server bypass.
    """

    @pytest.mark.parametrize(
        ("path", "verb"), _GATEWAY_MUTATIONS, ids=[p for p, _ in _GATEWAY_MUTATIONS]
    )
    def test_loopback_without_jwt_is_401_and_never_reaches_broker(
        self, app_host, path, verb
    ):
        client, remote = app_host
        resp = client.post(path)
        assert resp.status_code == 401, resp.text
        remote.assert_not_awaited()

    @pytest.mark.parametrize(
        ("path", "verb"), _GATEWAY_MUTATIONS, ids=[p for p, _ in _GATEWAY_MUTATIONS]
    )
    def test_operator_jwt_reaches_broker_once(self, app_host, path, verb):
        client, remote = app_host
        resp = client.post(path, headers={"Authorization": f"Bearer {_operator_jwt()}"})
        assert resp.status_code == 200, resp.text
        remote.assert_awaited_once_with(verb)

    def test_health_keeps_loopback_bypass(self, app_host, monkeypatch):
        client, remote = app_host

        async def _gw(*args, **kwargs):
            return {"auth_state": "authenticated", "port": 4001}

        monkeypatch.setattr(server, "check_ib_gateway", _gw)
        monkeypatch.setattr(server, "ib_pool", SimpleNamespace(status=lambda: {}))
        resp = client.get("/health")
        assert resp.status_code == 200, resp.text
        assert resp.json()["host_role"] == "app"
        remote.assert_not_awaited()

    def test_non_gateway_unit_keeps_loopback_bypass(self, app_host, monkeypatch):
        client, remote = app_host
        control = AsyncMock(
            return_value=admin_services.ActionResult(
                "radon-api.service", "restart", True, "", 0
            )
        )
        monkeypatch.setattr(admin_services, "control_unit", control)
        resp = client.post("/admin/services/radon-api.service/restart")
        assert resp.status_code == 200, resp.text
        control.assert_awaited_once_with("radon-api.service", "restart")
        remote.assert_not_awaited()


# Captured before any fixture replaces it with a mock.
_REAL_DOCKER_COMPOSE = server.ib_gateway._docker_compose


class TestAppRoleRuntimeGate:
    """REL-169 (R-472b): the app host must never take the combined path to a
    local ``docker compose`` even when its env drifts off ``cloud`` mode. The
    deploy-time check-env.py is not a runtime gate."""

    @pytest.fixture(autouse=True)
    def _app_role_docker_mode(self, monkeypatch):
        import asyncio

        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "docker")
        monkeypatch.setattr(server.ib_gateway, "_port_listening", lambda *a, **k: False)
        monkeypatch.setattr(
            server.ib_gateway,
            "_docker_container_state",
            AsyncMock(return_value=("exited", None)),
        )
        monkeypatch.setattr(server.ib_gateway, "_acquire_gateway_push_lease", lambda *a, **k: None)
        monkeypatch.setattr(server.ib_gateway, "_poll_port", AsyncMock(return_value=(False, 0)))
        monkeypatch.setattr(
            server.ib_gateway, "_acquire_2fa_push_lock_bounded", lambda *a, **k: (True, None)
        )
        monkeypatch.setattr(server.ib_gateway, "_release_2fa_push_lock_bounded", lambda *a, **k: None)
        server.ib_gateway.reset_restart_backoff()
        self.compose = AsyncMock(return_value=("", "", 0))
        monkeypatch.setattr(server.ib_gateway, "_docker_compose", self.compose)
        self.restart_docker = AsyncMock(return_value={"restarted": True, "port_listening": False})
        monkeypatch.setattr(server.ib_gateway, "_restart_docker", self.restart_docker)
        self.run = asyncio.run

    def test_ensure_ib_gateway_refuses_without_compose(self):
        result = self.run(server.ib_gateway.ensure_ib_gateway())
        assert result.get("restarted") is not True
        assert result.get("reason") == "app_role"
        assert "RADON_HOST_ROLE=app" in result["error"]
        self.compose.assert_not_awaited()

    def test_restart_ib_gateway_refuses_without_compose(self):
        result = self.run(server.ib_gateway.restart_ib_gateway())
        assert result["restarted"] is False
        assert result.get("reason") == "app_role"
        self.restart_docker.assert_not_awaited()
        self.compose.assert_not_awaited()

    def test_post_ib_restart_is_503_app_role(self, client, monkeypatch):
        # The Clerk carve-out for app-role Gateway POSTs is the primary gate;
        # this case hollows it out on purpose so the runtime gate under it is
        # what refuses (the finding's defence-in-depth claim).
        monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)
        monkeypatch.setattr(server, "_gateway_unit_controllable", lambda: False)
        resp = client.post("/ib/restart", headers=_auth_headers())
        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict), detail
        assert detail["reason"] == "app_role"
        self.compose.assert_not_awaited()
        self.restart_docker.assert_not_awaited()

    def test_docker_compose_chokepoint_refuses(self, monkeypatch):
        import asyncio

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            AsyncMock(side_effect=AssertionError("compose must not spawn on the app role")),
        )
        out, err, rc = self.run(_REAL_DOCKER_COMPOSE("up", "-d"))
        assert rc != 0
        assert "RADON_HOST_ROLE=app" in err

    def test_combined_role_still_reaches_compose(self, monkeypatch):
        monkeypatch.setenv("RADON_HOST_ROLE", "combined")
        result = self.run(server.ib_gateway.ensure_ib_gateway())
        assert result.get("reason") != "app_role"
        self.compose.assert_awaited()


class TestSplit2faContract:
    """REL-172 (R-475): on the app host, /health.ib_gateway.restart_backoff
    reflects the BROKER's lease and transition, and show_unit maps a pending
    transition to `activating` so the panel disarms Start."""

    @pytest.fixture(autouse=True)
    def _app_remote(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "no-local-lock.json"))
        for name in ("ca.pem", "client.pem", "client.key"):
            (tmp_path / name).write_text("x")
        monkeypatch.setenv("RADON_IB_REMOTE_URL", "https://10.0.0.4:8340")
        monkeypatch.setenv("RADON_IB_REMOTE_CA", str(tmp_path / "ca.pem"))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(tmp_path / "client.pem"))
        monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(tmp_path / "client.key"))
        monkeypatch.setattr(server.ib_gateway, "GATEWAY_MODE", "cloud")
        monkeypatch.setattr(server.ib_gateway, "_port_listening", lambda *a, **k: False)
        monkeypatch.setattr(admin_services, "is_systemd_available", lambda: False)
        admin_services._reset_remote_status_cache()

    def _stub_status(self, monkeypatch, status, payload):
        monkeypatch.setattr(admin_services, "_remote_http", lambda verb, timeout: (status, payload))

    def test_transition_pending_disarms_force_and_start(self, monkeypatch):
        import asyncio

        self._stub_status(
            monkeypatch, 409, {"ok": False, "detail": "transition-pending", "returncode": 74,
                               "state": "transition-pending", "transition": "pending", "lease": None},
        )
        gw = asyncio.run(server.ib_gateway.check_ib_gateway())
        lock = gw["restart_backoff"]["push_lock"]
        assert lock is not None
        assert lock["remaining_secs"] > 0
        assert "transition" in lock["holder"]
        admin_services._reset_remote_status_cache()
        unit = asyncio.run(admin_services.show_unit(admin_services.GATEWAY_UNIT))
        assert unit.active_state == "activating"

    def test_broker_lease_is_proxied_into_push_lock(self, monkeypatch):
        import asyncio
        import time

        now = time.time()
        self._stub_status(
            monkeypatch, 200, {"ok": True, "state": "running", "detail": "running", "returncode": 0,
                               "transition": None,
                               "lease": {"holder": "radon-cloud.ib-watchdog", "acquired_at": now - 5,
                                         "expires_at": now + 300, "remaining_secs": 300, "reason": "probe"}},
        )
        gw = asyncio.run(server.ib_gateway.check_ib_gateway())
        lock = gw["restart_backoff"]["push_lock"]
        assert lock["holder"] == "radon-cloud.ib-watchdog"
        assert lock["remaining_secs"] > 0
        assert gw["restart_backoff"]["source"] == "broker"

    def test_no_broker_lease_means_no_push_lock(self, monkeypatch):
        import asyncio

        self._stub_status(
            monkeypatch, 200, {"ok": True, "state": "running", "detail": "running", "returncode": 0,
                               "transition": None, "lease": None},
        )
        gw = asyncio.run(server.ib_gateway.check_ib_gateway())
        assert gw["restart_backoff"]["push_lock"] is None

    def test_reset_backoff_response_names_the_broker_lease(self, client, monkeypatch):
        monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)
        remote = AsyncMock(return_value=admin_services.ActionResult(
            admin_services.GATEWAY_UNIT, "reset-lease", True, "2FA push lease: released", 0))
        with patch.object(admin_services, "remote_gateway_action", new=remote):
            resp = client.post("/ib/reset-backoff", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        remote.assert_awaited_once_with("reset-lease")
        assert body["remote"]["action"] == "reset-lease"
        assert body["broker_lease_released"] is True

