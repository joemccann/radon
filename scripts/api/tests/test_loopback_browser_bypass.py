"""F6 — the loopback auth bypass must not be reachable from the operator's browser.

`is_trusted_local_request` used to read "peer is loopback/tailnet AND carries no
reverse-proxy headers" as proof of a server-to-server caller. A page in the
operator's browser satisfies both conditions exactly: the browser sends no
X-Forwarded-For, and `request.client.host` is 127.0.0.1. A CORS *simple* request
(Content-Type: text/plain) is not preflighted and CORSMiddleware only
short-circuits OPTIONS, so the POST reached /orders/place, /trading/kill,
/ib/restart and the whole admin surface with full local trust.

Two controls are pinned here:

  1. Browser fetch metadata (`Sec-Fetch-Site`, `Origin`) denies the local bypass.
     Browsers always attach them; the Next.js node-fetch client, the WS relay,
     the watchdog/health daemon (urllib) and curl attach neither.
  2. `TrustedHostMiddleware` pins the Host header, closing the DNS-rebinding READ
     path (attacker.example re-resolved to 127.0.0.1 becomes same-origin).

Both directions are covered: the attacker shape is rejected AND every legitimate
caller (server-to-server, CLI, operator Swagger over an SSH tunnel, proxied Clerk)
still works.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
PROJECT_ROOT = SCRIPTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.auth import is_trusted_local_request  # noqa: E402


class _FakeRequest:
    """Minimal stand-in for starlette Request: .client.host, .headers, .url.path."""

    def __init__(self, host="127.0.0.1", headers=None, path="/"):
        self.client = SimpleNamespace(host=host) if host is not None else None
        self.headers = dict(headers or {})
        self.url = SimpleNamespace(path=path)


# --- 1. attacker shape: a page in the operator's browser ---------------------


class TestBrowserOriginatedLoopbackIsNotTrusted:
    def test_cross_site_fetch_metadata_denies_bypass(self):
        req = _FakeRequest(
            headers={
                "Host": "127.0.0.1:8321",
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "cors",
            }
        )
        assert is_trusted_local_request(req) is False

    def test_foreign_origin_alone_denies_bypass(self):
        """A CORS simple request carries Origin even when the attacker strips
        nothing else; older browsers may omit Sec-Fetch-*."""
        req = _FakeRequest(
            headers={"Host": "127.0.0.1:8321", "Origin": "https://evil.example"}
        )
        assert is_trusted_local_request(req) is False

    def test_same_site_fetch_from_another_local_port_denies_bypass(self):
        req = _FakeRequest(
            headers={"Host": "localhost:8321", "Sec-Fetch-Site": "same-site"}
        )
        assert is_trusted_local_request(req) is False

    def test_opaque_origin_denies_bypass(self):
        """A sandboxed iframe / data: document sends `Origin: null`."""
        req = _FakeRequest(headers={"Host": "127.0.0.1:8321", "Origin": "null"})
        assert is_trusted_local_request(req) is False

    def test_tailnet_peer_browser_request_denies_bypass(self):
        req = _FakeRequest(
            host="100.100.5.5",
            headers={"Host": "ib-gateway:8321", "Sec-Fetch-Site": "cross-site"},
        )
        assert is_trusted_local_request(req) is False


# --- 2. legitimate callers keep working --------------------------------------


class TestLegitimateCallersStillTrusted:
    def test_next_js_server_to_server_loopback(self):
        """web/lib/radonApi.ts radonFetch — node fetch sends no Origin and no
        Sec-Fetch-* metadata."""
        req = _FakeRequest(headers={"Host": "localhost:8321", "Accept": "*/*"})
        assert is_trusted_local_request(req) is True

    def test_cli_and_watchdog_curl(self):
        req = _FakeRequest(
            headers={"Host": "127.0.0.1:8321", "User-Agent": "curl/8.7.1"}
        )
        assert is_trusted_local_request(req) is True

    def test_cloud_thin_laptop_over_tailscale(self):
        """scripts/cloud.sh sets RADON_API_URL=http://ib-gateway:8321."""
        req = _FakeRequest(host="100.100.5.5", headers={"Host": "ib-gateway:8321"})
        assert is_trusted_local_request(req) is True

    def test_operator_typed_url_over_ssh_tunnel(self):
        """Opening /docs by typing the URL is user-initiated: Sec-Fetch-Site:none.
        No page can forge that value."""
        req = _FakeRequest(
            path="/docs",
            headers={
                "Host": "localhost:8321",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
            },
        )
        assert is_trusted_local_request(req) is True

    def test_swagger_ui_same_origin_call(self):
        """Swagger UI at /docs fetching /openapi.json (and its 'Try it out'
        POSTs, which carry a self Origin)."""
        req = _FakeRequest(
            path="/openapi.json",
            headers={
                "Host": "127.0.0.1:8321",
                "Origin": "http://127.0.0.1:8321",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert is_trusted_local_request(req) is True

    def test_proxied_request_still_denied(self):
        """Unchanged pre-existing behavior: Caddy-forwarded requests never get
        the bypass, they must present a Clerk JWT."""
        req = _FakeRequest(
            headers={"Host": "app.radon.run", "X-Forwarded-For": "203.0.113.9"}
        )
        assert is_trusted_local_request(req) is False


# --- 3. end-to-end through the middleware ------------------------------------


def _loopback_client(**kwargs) -> TestClient:
    from scripts.api.server import app

    return TestClient(app, client=("127.0.0.1", 51234), **kwargs)


class TestOrdersPlaceUnreachableFromBrowser:
    def test_cross_site_post_is_not_authenticated_as_local(self, monkeypatch):
        """The exact attack: a CORS simple request (text/plain, no preflight)
        from a page the operator has open, to the loopback API."""
        monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/jwks.json")
        monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)

        res = _loopback_client().post(
            "/orders/place",
            content='{"ticker":"AAPL","action":"BUY","quantity":1}',
            headers={
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
                "Sec-Fetch-Mode": "cors",
            },
        )
        assert res.status_code in (401, 403), (
            f"/orders/place returned {res.status_code} to a browser-shaped "
            "cross-site POST on loopback — the local bypass is still reachable "
            "from the operator's browser."
        )

    def test_server_to_server_post_still_reaches_the_handler(
        self, monkeypatch, tmp_path
    ):
        """The legitimate Next.js hop must NOT be affected: it gets past the
        perimeter.

        The assertion is about the PERIMETER, so the handler is put in test
        mode and the 2FA lease is pointed at a tmp path. Otherwise this drives
        the real order path into check_ib_gateway -> the 2FA lock, which mkdirs
        /var/lib/radon: writable on a Hetzner host, resolved under ~/Library on
        macOS, and permission-denied on a Linux CI runner.
        """
        monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/jwks.json")
        monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "2fa-lock.json"))
        from scripts.api import server as api_server

        monkeypatch.setattr(api_server, "test_mode", True)

        res = _loopback_client().post("/orders/place", json={})
        assert res.status_code not in (401, 403), (
            f"loopback server-to-server POST was denied ({res.status_code}) — "
            "the legitimate Next.js -> FastAPI hop is broken."
        )
        # 200 from the test-mode branch proves the request reached the handler
        # body rather than stopping at the perimeter for some other reason.
        assert res.status_code == 200, res.text


# --- 4. DNS-rebinding read path ----------------------------------------------


class TestTrustedHostMiddleware:
    @pytest.mark.parametrize(
        "host",
        ["localhost:8321", "127.0.0.1:8321", "app.radon.run", "ib-gateway:8321"],
    )
    def test_real_deployment_hosts_are_served(self, host):
        res = _loopback_client().get("/health", headers={"Host": host})
        assert res.status_code == 200, f"legitimate Host {host} was rejected"

    def test_rebound_attacker_host_is_rejected(self):
        res = _loopback_client().get(
            "/health", headers={"Host": "rebind.attacker.example"}
        )
        assert res.status_code == 400, (
            "an attacker-controlled hostname re-resolved to 127.0.0.1 must be "
            "rejected on the Host header, otherwise the rebound page is "
            "same-origin and can read every loopback-trusted response."
        )

    def test_middleware_is_configured_without_a_wildcard(self):
        from starlette.middleware.trustedhost import TrustedHostMiddleware

        from scripts.api.server import app

        for mw in app.user_middleware:
            if mw.cls is TrustedHostMiddleware:
                allowed = (getattr(mw, "kwargs", {}) or {}).get("allowed_hosts") or []
                assert "*" not in allowed, "Host pinning must not be a wildcard"
                assert "app.radon.run" in allowed
                assert "demo.radon.run" in allowed
                assert "beta.radon.run" not in allowed
                return
        raise AssertionError("TrustedHostMiddleware not installed on app")
