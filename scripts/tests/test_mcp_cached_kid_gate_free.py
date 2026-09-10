"""RC-A3: a kid already present in the cached JWKS key set must resolve
without taking the 4-slot `_jwks_gate`.

The gate exists to bound live JWKS refetches for UNKNOWN kids. Taking it on
the cached-key fast path meant 4 concurrent slow unknown-kid lookups turned
every caller — including ones whose signing key was already in hand — into a
503, an anonymous-internet denial lever against valid operator tokens.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from mcp_hosted import auth as mcp_auth  # noqa: E402
from mcp_hosted.auth import AuthError  # noqa: E402


def _token_with_kid(kid: str) -> str:
    # _signing_key_for only reads the header, so an unsigned shell works.
    import jwt as pyjwt

    return pyjwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": kid})


class TestCachedKidServesWithoutTheGate:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())
        monkeypatch.setattr(
            mcp_auth, "_jwks_refresh_after", type(mcp_auth._jwks_refresh_after)()
        )
        monkeypatch.setattr(
            mcp_auth, "_jwks_gate",
            threading.BoundedSemaphore(mcp_auth.MAX_JWKS_INFLIGHT),
        )

    @pytest.fixture
    def cached_client(self, monkeypatch):
        class CachedClient:
            def get_signing_keys(self):
                return [type("K", (), {"key_id": "kid-cached"})()]

            def get_signing_key_from_jwt(self, token):
                return type("S", (), {"key": "the-cached-key"})()

        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: CachedClient())

    def test_cached_kid_resolves_while_the_gate_is_saturated(
        self, cached_client
    ):
        # 4 in-flight slow unknown-kid lookups hold every gate slot.
        for _ in range(mcp_auth.MAX_JWKS_INFLIGHT):
            assert mcp_auth._jwks_gate.acquire(blocking=False)
        assert (
            mcp_auth._signing_key_for(_token_with_kid("kid-cached"))
            == "the-cached-key"
        ), "a kid already in the cached key set must not need the refresh gate"

    def test_unknown_kid_still_503s_when_saturated(self, cached_client):
        for _ in range(mcp_auth.MAX_JWKS_INFLIGHT):
            assert mcp_auth._jwks_gate.acquire(blocking=False)
        with pytest.raises(AuthError) as exc:
            mcp_auth._signing_key_for(_token_with_kid("kid-fresh"))
        assert exc.value.status == 503

    def test_negative_cached_kid_stays_gate_free_401(self, cached_client):
        mcp_auth._remember_negative_kid("kid-bad")
        for _ in range(mcp_auth.MAX_JWKS_INFLIGHT):
            assert mcp_auth._jwks_gate.acquire(blocking=False)
        with pytest.raises(AuthError) as exc:
            mcp_auth._signing_key_for(_token_with_kid("kid-bad"))
        assert exc.value.status == 401
