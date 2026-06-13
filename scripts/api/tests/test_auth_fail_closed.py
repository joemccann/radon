"""Regression: the auth middleware must FAIL CLOSED.

Before this fix, `auth_middleware` returned `call_next(request)` unconditionally
when `CLERK_JWKS_URL` was unset — so a single missing/typo'd VPS env var made all
47 routes (orders/place, pi/exec, admin/*) world-callable through the public
Caddy proxy. This is the "middleware is the perimeter" / world-callable-/api/*
incident class. These tests pin the closed-by-default behavior: an untrusted
(public/proxied) caller with no JWKS configured is DENIED, not allowed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# A non-exempt, protected route that exists on the app. /portfolio is GET and
# protected; the exact handler outcome doesn't matter — we assert the request
# is STOPPED at the perimeter (503), never reaching the handler.
_PROTECTED_PATH = "/portfolio"


@pytest.fixture
def untrusted_client(monkeypatch):
    """A client whose requests are treated as untrusted/public (not loopback,
    arrived via proxy) — i.e. the threat model: a remote caller."""
    from scripts.api import server, auth

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    # No service API key on the request either.
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)
    from scripts.api.server import app
    return TestClient(app)


class TestAuthFailsClosed:
    def test_unset_jwks_denies_untrusted_request(self, untrusted_client, monkeypatch):
        """CLERK_JWKS_URL unset + untrusted caller → 503, NOT 200 (the bug)."""
        monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
        monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
        resp = untrusted_client.get(_PROTECTED_PATH)
        assert resp.status_code == 503, (
            "auth must fail CLOSED when JWKS is unconfigured — a public request "
            f"reached a protected route with status {resp.status_code}."
        )
        assert "Authentication unavailable" in resp.json().get("detail", "")

    def test_explicit_dev_optin_allows(self, untrusted_client, monkeypatch):
        """The ONLY way to disable auth is the loud, explicit dev flag."""
        monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
        monkeypatch.setenv("RADON_AUTH_DISABLED", "1")
        resp = untrusted_client.get(_PROTECTED_PATH)
        # Reaches the handler (not 503). Handler may 200/4xx/5xx on its own merits;
        # the point is it was NOT stopped by the perimeter.
        assert resp.status_code != 503

    def test_trusted_local_bypass_independent_of_jwks(self, monkeypatch):
        """Server-to-server (trusted local) calls work even when JWKS is unset —
        the bypass is checked BEFORE the JWKS gate."""
        from scripts.api import server, auth
        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
        monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
        from scripts.api.server import app
        resp = TestClient(app).get(_PROTECTED_PATH)
        assert resp.status_code != 503, (
            "trusted local server-to-server must not be denied when JWKS is unset"
        )

    def test_exempt_path_always_open(self, untrusted_client, monkeypatch):
        """Exempt paths (/health) stay reachable regardless of JWKS state."""
        monkeypatch.delenv("CLERK_JWKS_URL", raising=False)
        resp = untrusted_client.get("/health")
        assert resp.status_code == 200
