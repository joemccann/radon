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
                return [type("K", (), {"key_id": "kid-cached", "key": "the-cached-key"})()]

            def get_signing_key(self, kid):
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


class TestCachedKidDoesNotWaitOnTheClientLock:
    """DS-2026-10-24-05: PyJWKClient.get_signing_key holds the client lock,
    which a forged-kid refetch keeps for its whole network round trip. A kid
    already in the unexpired key set resolves from that set without it."""

    def test_cached_kid_resolves_while_a_refetch_holds_the_lock(self, monkeypatch):
        import json

        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jwt.algorithms import RSAAlgorithm

        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
        jwk.update({"kid": "kid-cached", "use": "sig", "alg": "RS256"})
        client = pyjwt.PyJWKClient("https://jwks.invalid/.well-known/jwks.json")
        client.jwk_set_cache.put(pyjwt.PyJWKSet.from_dict({"keys": [jwk]}))
        monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: client)
        monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())

        held = threading.Event()
        release = threading.Event()

        def refetch_in_flight():
            with client._client_lock:
                held.set()
                release.wait(5)

        holder = threading.Thread(target=refetch_in_flight)
        holder.start()
        held.wait(5)
        result: list = []
        worker = threading.Thread(
            target=lambda: result.append(mcp_auth._signing_key_for(_token_with_kid("kid-cached")))
        )
        try:
            worker.start()
            worker.join(1.0)
            assert result, "a cached kid waited on the client lock a refetch holds"
        finally:
            release.set()
            holder.join()
            worker.join()
