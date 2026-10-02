"""A kid already in the cached JWKS set resolves without an in-flight slot.

The 4-slot in-flight bound exists for live refetches of UNKNOWN kids. Making
a cached kid wait behind it let slow unknown-kid lookups turn the operator's
valid token into a 503. Mirrors RC-A3 in scripts/mcp_hosted/auth.py.
"""
from __future__ import annotations

import asyncio

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from scripts.api import auth


def _client_with_cached_kid(kid: str) -> pyjwt.PyJWKClient:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    client = pyjwt.PyJWKClient(
        "https://jwks.invalid/.well-known/jwks.json", cache_keys=True, cooldown_duration=0
    )

    def no_network():
        raise AssertionError("a cached kid must not refetch the JWKS")

    client.fetch_data = no_network
    client.jwk_set_cache.put({"keys": [jwk]})
    return client


@pytest.fixture(autouse=True)
def _clean():
    auth._jwks_inflight.clear()
    auth._jwks_negative.clear()
    yield
    auth._jwks_inflight.clear()
    auth._jwks_negative.clear()


@pytest.mark.asyncio
async def test_cached_kid_resolves_while_every_slot_is_busy(monkeypatch):
    client = _client_with_cached_kid("kid-cached")
    monkeypatch.setattr(auth, "_get_jwks_client", lambda: client)
    blocker = asyncio.get_running_loop().create_future()
    for i in range(auth.MAX_JWKS_INFLIGHT):
        auth._jwks_inflight[f"unknown-{i}"] = asyncio.ensure_future(blocker)
    try:
        key = await auth._bounded_signing_key_lookup("token", "kid-cached")
        assert key.key_id == "kid-cached"
        # Unknown kids are still bounded.
        with pytest.raises(auth.HTTPException) as exc:
            await auth._bounded_signing_key_lookup("token", "unknown-new")
        assert exc.value.status_code == 503
    finally:
        blocker.cancel()


@pytest.mark.asyncio
async def test_unknown_kid_still_takes_the_bounded_lookup(monkeypatch):
    client = _client_with_cached_kid("kid-cached")
    calls = []

    def lookup(token):
        calls.append(token)
        raise pyjwt.exceptions.PyJWKClientError("unknown kid")

    client.get_signing_key_from_jwt = lookup
    monkeypatch.setattr(auth, "_get_jwks_client", lambda: client)
    with pytest.raises(auth.HTTPException) as exc:
        await auth._bounded_signing_key_lookup("token", "kid-other")
    assert exc.value.status_code == 401
    assert calls == ["token"]


@pytest.mark.asyncio
async def test_expired_cache_is_not_served(monkeypatch):
    client = _client_with_cached_kid("kid-cached")
    client.jwk_set_cache.lifespan = 0
    client.jwk_set_cache.jwk_set_with_timestamp.timestamp -= 10
    blocker = asyncio.get_running_loop().create_future()
    for i in range(auth.MAX_JWKS_INFLIGHT):
        auth._jwks_inflight[f"unknown-{i}"] = asyncio.ensure_future(blocker)
    monkeypatch.setattr(auth, "_get_jwks_client", lambda: client)
    try:
        with pytest.raises(auth.HTTPException) as exc:
            await auth._bounded_signing_key_lookup("token", "kid-cached")
        assert exc.value.status_code == 503
    finally:
        blocker.cancel()
