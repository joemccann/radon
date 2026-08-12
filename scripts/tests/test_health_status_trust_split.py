"""F10: /status must split its surface by trust.

Caddy reverse_proxies https://app.radon.run/edge-health/status to the daemon with
no auth directive, so ANY anonymous internet client used to receive the whole
body: the FastAPI /health/lite payload (IB auth_state incl. awaiting_2fa), the
radon-* systemd unit inventory, and service_health rows whose last_error strings
are raw handler exception text. The FastAPI perimeter denies that same data to
untrusted callers, so this was a side door around an existing control.

The unauthenticated response must keep only the aggregate the off-box prober
validates; detail requires the shared bearer token or an unproxied on-box call.
"""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from health_probe import probe as health_probe
from health_service import serve


TOKEN = "test-status-token"
SENSITIVE_UNIT = "radon-api.service"
SENSITIVE_ERROR = "exit_orders: IB Gateway parked awaiting 2FA (order 1122334455 NVDA)"

_PROBE_RESULTS = {
    "radon-api": {
        "state": "up",
        "http_status": 200,
        "payload": {
            "service_state": "reachable",
            "auth_state": "awaiting_2fa",
            "upstream_dead": False,
            "port_listening": True,
        },
    },
    "ib-gateway": {"state": "down", "detail": "ConnectionRefusedError"},
}


class _FakeUnitCache:
    def snapshot(self):
        return {SENSITIVE_UNIT: {"active_state": "active", "sub_state": "running",
                                 "result": "success", "state": "up"}}, 1.0

    def stop(self):
        pass


class _FakeServiceHealthCache:
    def snapshot(self):
        return {"state": "ok", "rows": [
            {"service": "exit-orders", "status": "error", "last_error": SENSITIVE_ERROR},
        ]}


@pytest.fixture(scope="class")
def running():
    """Real ephemeral daemon with deterministic, deliberately sensitive content."""
    server, cache = serve.build_server(bind="127.0.0.1", port=0, units=[])
    cache.stop()
    server.unit_cache = _FakeUnitCache()
    server.service_health_cache = _FakeServiceHealthCache()
    server.external_probe_cache = None
    original_probes = serve.run_probes
    original_token = getattr(serve, "STATUS_TOKEN", "")
    serve.run_probes = lambda: dict(_PROBE_RESULTS)
    serve.STATUS_TOKEN = TOKEN
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        serve.run_probes = original_probes
        serve.STATUS_TOKEN = original_token
        server.shutdown()
        server.server_close()


def _get(port, path="/status", headers=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     headers=headers or {})
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read().decode()
    return response.status, raw, json.loads(raw)


class TestUnauthenticatedEdgeCaller:
    def test_public_body_carries_no_units_errors_or_auth_state(self, running):
        status, raw, body = _get(running, headers={"X-Forwarded-For": "203.0.113.9"})
        assert status == 200
        assert SENSITIVE_UNIT not in raw
        assert "awaiting_2fa" not in raw
        assert "parked awaiting 2FA" not in raw
        assert "units" not in body
        assert "probes" not in body
        assert "service_health" not in body
        assert "external_probe" not in body

    def test_public_body_still_satisfies_the_off_box_prober(self, running):
        _, _, body = _get(running, headers={"X-Forwarded-For": "203.0.113.9"})
        assert body["schema_version"] == 2
        assert isinstance(body["ok"], bool)
        assert isinstance(body["overall_state"], str)
        assert health_probe._classify_status_payload(body) != "invalid"

    def test_caddy_public_marker_alone_redacts(self, running):
        _, raw, _ = _get(running, headers={"X-Radon-Public-Edge": "1"})
        assert SENSITIVE_UNIT not in raw

    def test_wrong_token_from_the_edge_is_still_public(self, running):
        _, raw, _ = _get(running, headers={"X-Forwarded-For": "203.0.113.9",
                                           "Authorization": "Bearer not-the-token"})
        assert SENSITIVE_UNIT not in raw

    def test_redaction_never_becomes_a_401(self, running):
        status, _, _ = _get(running, headers={"X-Forwarded-For": "203.0.113.9"})
        assert status == 200


class TestAuthorizedCaller:
    def test_bearer_token_from_the_edge_gets_the_full_body(self, running):
        status, raw, body = _get(running, headers={
            "X-Forwarded-For": "203.0.113.9",
            "Authorization": f"Bearer {TOKEN}",
        })
        assert status == 200
        assert body["units"][SENSITIVE_UNIT]["state"] == "up"
        assert body["probes"]["radon-api"]["payload"]["auth_state"] == "awaiting_2fa"
        assert SENSITIVE_ERROR in raw

    def test_unproxied_on_box_caller_keeps_the_full_body(self, running):
        """ib_watchdog, incident_watchdog, deploy.sh and CI all curl
        127.0.0.1:8330/status directly with no proxy headers."""
        _, _, body = _get(running)
        assert body["probes"]["radon-api"]["payload"]["auth_state"] == "awaiting_2fa"
        assert body["units"][SENSITIVE_UNIT]["state"] == "up"


class TestDetailAuthorization:
    def test_proxied_request_is_untrusted(self):
        assert serve.status_detail_authorized({"X-Forwarded-For": "203.0.113.9"}, TOKEN) is False

    def test_unproxied_request_is_trusted(self):
        assert serve.status_detail_authorized({}, TOKEN) is True

    def test_token_overrides_proxy_marker(self):
        assert serve.status_detail_authorized(
            {"X-Forwarded-For": "203.0.113.9", "Authorization": f"Bearer {TOKEN}"}, TOKEN) is True

    def test_empty_configured_token_never_authorizes_an_edge_caller(self):
        assert serve.status_detail_authorized(
            {"X-Forwarded-For": "203.0.113.9", "Authorization": "Bearer "}, "") is False


class TestCaddyMarksTheEdge:
    def test_edge_health_status_stamps_the_public_marker(self):
        caddyfile = Path(__file__).resolve().parents[2] / "cloud" / "caddy" / "Caddyfile"
        content = caddyfile.read_text(encoding="utf-8")
        assert content.count("header_up X-Radon-Public-Edge") == 2, (
            "both app.radon.run and beta.radon.run /edge-health/status proxies must "
            "mark the request as public so the daemon redacts it"
        )
