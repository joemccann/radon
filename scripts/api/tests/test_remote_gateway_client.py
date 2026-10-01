"""services.remote_gateway_action against the REAL broker daemon over loopback mTLS.

T-347: the app-host mTLS client (_remote_url_allowed, _remote_ssl_context,
_remote_http and the 409/lease mapping in remote_gateway_action) had no
coverage — every test patched remote_gateway_action wholesale. Here the
client talks to ``ib_gateway_remote.serve.make_server`` with a stub helper,
so a wrong verb, method, path, cert chain or status mapping fails at the
wire. Certs are minted once per module; each case gets its own daemon.
"""
from __future__ import annotations

import asyncio
import shutil
import threading

import pytest
from fastapi.testclient import TestClient

from scripts.api import auth, server
from api import services
from ib_gateway_remote import serve
from scripts.tests.test_ib_gateway_remote import _config, _helper_script, mint_mtls

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl is required to mint the mTLS test CA"
)


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    return mint_mtls(tmp_path_factory.mktemp("mtls"))


def _point_client_at(monkeypatch, certs, port: int) -> None:
    monkeypatch.setenv("RADON_HOST_ROLE", "app")
    monkeypatch.setenv("RADON_IB_REMOTE_URL", f"https://127.0.0.1:{port}")
    monkeypatch.setenv("RADON_IB_REMOTE_CA", str(certs["ca"]))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_CERT", str(certs["client_cert"]))
    monkeypatch.setenv("RADON_IB_REMOTE_CLIENT_KEY", str(certs["client_key"]))


@pytest.fixture()
def broker(certs, tmp_path, monkeypatch, request):
    """Start the daemon with a stub helper and point the app client at it."""

    def _start(*, rc: int = 0, stdout: str = "running"):
        # Each case gets a fresh broker; a cached /status (operator hold,
        # lease) from the previous case must not leak into this one.
        services._reset_remote_status_cache()
        request.addfinalizer(services._reset_remote_status_cache)
        helper = _helper_script(tmp_path, rc=rc, stdout=stdout)
        httpd = serve.make_server(_config(tmp_path, certs, helper=helper), port=0)
        threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        request.addfinalizer(httpd.server_close)
        request.addfinalizer(httpd.shutdown)
        _point_client_at(monkeypatch, certs, httpd.server_address[1])
        return httpd

    return _start


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    # The app-host Clerk gate is pinned at the wire in
    # test_ib_restart_cloud_delegate.py (T-346); the subject here is the
    # status mapping behind it.
    monkeypatch.setattr(server, "_is_app_role_gateway_mutation", lambda request: False)
    return TestClient(server.app)


def _act(verb: str) -> services.ActionResult:
    return asyncio.run(services.remote_gateway_action(verb))


def _helper_log(tmp_path) -> list[str]:
    log = tmp_path / "helper.log"
    return log.read_text().splitlines() if log.exists() else []


class TestRemoteGatewayAction:
    def test_status_running(self, broker, tmp_path):
        broker(rc=0, stdout="running")
        assert services.is_remote_gateway_configured() is True
        result = _act("status")
        assert (result.ok, result.returncode, result.detail) == (True, 0, "running")
        assert _helper_log(tmp_path) == ["status"]

    def test_restart_ok(self, broker, tmp_path):
        broker(rc=0, stdout="ok")
        result = _act("restart")
        assert (result.ok, result.returncode) == (True, 0)
        assert result.unit == services.GATEWAY_UNIT
        assert _helper_log(tmp_path) == ["restart"]

    def test_lease_held_maps_to_push_lock_rc(self, broker, tmp_path):
        broker(rc=75, stdout="held holder=watchdog")
        result = _act("restart")
        assert result.ok is False
        assert result.returncode == services.PUSH_LOCK_HELD_RC
        assert "watchdog" in result.detail
        assert _helper_log(tmp_path) == ["restart"]

    def test_helper_failure_keeps_rc(self, broker):
        broker(rc=1, stdout="boom")
        result = _act("start")
        assert (result.ok, result.returncode) == (False, 1)
        assert "boom" in result.detail

    def test_disallowed_verb_never_leaves_the_host(self, broker, tmp_path):
        broker()
        result = _act("restart-preheld")
        assert (result.ok, result.returncode) == (False, -1)
        assert _helper_log(tmp_path) == []

    def test_broker_down_is_unreachable_not_a_refusal(self, broker):
        httpd = broker()
        httpd.shutdown()
        httpd.server_close()  # port is now closed: connection refused
        result = _act("restart")
        assert result.ok is False
        assert result.returncode == services.REMOTE_UNREACHABLE_RC


class TestRouteMapping:
    ROUTE = "/admin/services/radon-ib-gateway.service/restart"

    def test_lease_held_is_409(self, broker, client):
        broker(rc=75, stdout="held holder=watchdog")
        resp = client.post(self.ROUTE)
        assert resp.status_code == 409
        assert resp.json()["detail"]["returncode"] == services.PUSH_LOCK_HELD_RC

    def test_helper_failure_is_502(self, broker, client):
        broker(rc=1, stdout="boom")
        resp = client.post(self.ROUTE)
        assert resp.status_code == 502
        assert resp.json()["detail"]["returncode"] == 1

    def test_broker_down_is_504_not_400(self, broker, client):
        # T-347 pinned "not the 400 caller-error bucket"; REL-171 (R-500) then
        # split the upstream failures: a dead mTLS link is a gateway TIMEOUT,
        # a malformed broker reply stays a 502.
        httpd = broker()
        httpd.shutdown()
        httpd.server_close()
        resp = client.post(self.ROUTE)
        assert resp.status_code == 504, resp.text
        assert resp.json()["detail"]["returncode"] == services.REMOTE_UNREACHABLE_RC
        assert resp.json()["detail"]["ok"] is False


class TestRemoteUrlAllowed:
    @pytest.mark.parametrize(
        "url",
        [
            "https://10.0.0.4:8340",
            "https://10.0.0.4:8340/",
            "https://10.0.255.1",
            "https://127.0.0.1:1",
            "https://[::1]:8340",
        ],
    )
    def test_https_to_loopback_or_hetzner_private(self, url):
        assert services._remote_url_allowed(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "http://10.0.0.4:8340",
            "https://5.78.144.125:8340",
            "https://broker.example:8340",
            "https://10.1.0.4:8340",
            "https://100.64.0.1:8340",
            "https://10.0.0.4:8340/status",
            "https://10.0.0.4:8340/../",
        ],
    )
    def test_rejects_plain_public_named_or_pathed(self, url):
        assert services._remote_url_allowed(url) is False


# --- IBKR operator hold: set/clear from the admin panel, refuse logins -------
# 2026-10-01: in an emergency the operator flattens from IBKR Mobile, which
# takes the shared IBKR username. Every app-host login path must stand down,
# and the hold must be settable from /admin when the app is up. Tested at the
# wire: the real broker daemon, the real mTLS client, a recording transport.


@pytest.fixture()
def hold_env(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_PATH", str(tmp_path / "hold.json"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_AUDIT", str(tmp_path / "hold.jsonl"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid()))
    from utils import ib_operator_hold

    with serve._verb_history_guard:
        serve._verb_history.clear()
        serve._verb_wall_history.clear()
    return ib_operator_hold


@pytest.fixture()
def wire(monkeypatch):
    """Every request the app sends to the broker: (method, path, body)."""
    sent: list[tuple[str, str, object]] = []
    real = services._remote_http

    def recording(verb, timeout, body=None):
        method = "GET" if verb == "status" else "POST"
        sent.append((method, "/status" if verb == "status" else f"/{verb}", body))
        return real(verb, timeout, body)

    monkeypatch.setattr(services, "_remote_http", recording)
    return sent


def _posts(sent) -> list[tuple[str, object]]:
    return [(path, body) for method, path, body in sent if method == "POST"]


class TestOperatorHoldRoutes:
    def test_set_posts_exactly_one_hold_with_reason_and_actor(self, broker, client, hold_env, wire, tmp_path):
        broker(rc=0, stdout="stopped")
        resp = client.post("/ib/operator-hold", json={"held": True, "reason": "flatten on IBKR Mobile"})
        assert resp.status_code == 200, resp.text
        assert _posts(wire) == [("/hold", {"actor": "local", "reason": "flatten on IBKR Mobile"})]
        state = hold_env.hold_state()
        assert (state["held"], state["actor"]) == (True, "app:local")
        assert _helper_log(tmp_path) == ["stop"]

    def test_clear_posts_exactly_one_unhold_and_logs_in_once(self, broker, client, hold_env, wire, tmp_path):
        broker(rc=0, stdout="ok")
        hold_env.set_hold("flatten", "ssh:joe")
        resp = client.post("/ib/operator-hold", json={"held": False})
        assert resp.status_code == 200, resp.text
        assert _posts(wire) == [("/unhold", {"actor": "local"})]
        assert hold_env.is_held() is False
        assert resp.json()["gateway_start"]["ok"] is True
        assert _helper_log(tmp_path) == ["start"]

    @pytest.mark.parametrize(
        "payload",
        [{}, {"held": "yes"}, {"held": True}, {"held": True, "reason": "   "},
         {"held": True, "reason": "x", "expires_at": 5}],
    )
    def test_unarmed_requests_send_nothing(self, broker, client, hold_env, wire, payload):
        broker()
        resp = client.post("/ib/operator-hold", json=payload)
        assert resp.status_code == 400
        assert wire == []
        assert hold_env.is_held() is False

    def test_status_route_mirrors_the_broker_hold(self, broker, client, hold_env):
        broker(rc=0, stdout="stopped")
        hold_env.set_hold("flatten", "ssh:joe")
        body = client.get("/ib/operator-hold").json()
        assert body["operator_hold"]["held"] is True
        assert body["operator_hold"]["actor"] == "ssh:joe"

    def test_app_host_hold_requires_an_operator_jwt(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setenv("RADON_HOST_ROLE", "app")
        req = SimpleNamespace(method="POST", url=SimpleNamespace(path="/ib/operator-hold"))
        assert server._is_app_role_gateway_mutation(req) is True


class TestLoginsRefusedWhileHeld:
    @pytest.mark.parametrize(
        "route",
        ["/ib/restart", "/admin/services/radon-ib-gateway.service/restart",
         "/admin/services/radon-ib-gateway/start"],
    )
    def test_login_routes_answer_423_and_send_no_login(self, broker, client, hold_env, wire, tmp_path, route):
        broker(rc=0, stdout="stopped")
        hold_env.set_hold("flatten", "ssh:joe")
        resp = client.post(route)
        assert resp.status_code == 423, resp.text
        assert _posts(wire) == [], "a login request left the app host during the hold"
        assert "restart" not in _helper_log(tmp_path)
        assert "start" not in _helper_log(tmp_path)

    def test_broker_423_maps_to_423_when_the_mirror_missed_it(self, broker, client, hold_env, monkeypatch):
        broker(rc=0, stdout="running")

        async def unknown():
            return None

        monkeypatch.setattr(services, "operator_hold_state", unknown)
        hold_env.set_hold("flatten", "ssh:joe")
        resp = client.post("/admin/services/radon-ib-gateway.service/restart")
        assert resp.status_code == 423, resp.text
        assert resp.json()["detail"]["returncode"] == services.OPERATOR_HOLD_RC

    def test_stop_is_still_allowed_while_held(self, broker, client, hold_env, tmp_path):
        broker(rc=0, stdout="ok")
        hold_env.set_hold("flatten", "ssh:joe")
        resp = client.post("/admin/services/radon-ib-gateway.service/stop")
        assert resp.status_code == 200, resp.text
        assert _helper_log(tmp_path)[-1] == "stop"

    def test_recovery_heartbeat_stands_down_while_held(self, broker, hold_env, monkeypatch):
        broker(rc=0, stdout="stopped")
        hold_env.set_hold("flatten", "ssh:joe")
        calls: list[str] = []

        async def fake_check(**kwargs):
            calls.append("check")
            return {}

        async def fake_recover():
            calls.append("recover")

        monkeypatch.setattr(server, "ib_pool", object())
        monkeypatch.setattr(server, "check_ib_gateway", fake_check)
        monkeypatch.setattr(server, "_recover_stuck_pool_guarded", fake_recover)
        asyncio.run(server._ib_recovery_heartbeat_tick())
        assert calls == []
