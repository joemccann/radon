"""DS-2026-10-22-05: a malformed token must not negative-cache a real kid.

PyJWKClient.get_signing_key_from_jwt decodes the whole token, payload
included, and raises DecodeError (a PyJWTError) when the payload is not JSON.
Both auth modules treated that as a "this kid is not in the JWKS" verdict and
negative-cached the kid for 30s. JWKS kids are public, so an unauthenticated
caller could deny every operator login by replaying such a token. The signing
key must be resolved by the already-validated kid; a malformed token fails
only its own request.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from api import auth as api_auth  # noqa: E402
from mcp_hosted import auth as mcp_auth  # noqa: E402
from mcp_hosted.auth import AuthError  # noqa: E402

KID = "kid-operator"
SUB = "user_operator"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _client(private_key, *, prefilled: bool) -> pyjwt.PyJWKClient:
    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    data = {"keys": [jwk]}
    client = pyjwt.PyJWKClient(
        "https://jwks.invalid/.well-known/jwks.json", cache_keys=True, cooldown_duration=0
    )

    def fetch_data():  # no network: serve the local key set
        client.jwk_set_cache.put(data)
        return data

    client.fetch_data = fetch_data
    if prefilled:
        client.jwk_set_cache.put(data)
    return client


def _malformed_token() -> str:
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT", "kid": KID}).encode())
    return f"{header}.{_b64(b'not-json')}.{_b64(b'sig')}"


def _valid_token(private_key) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {"sub": SUB, "iat": now, "exp": now + 300},
        private_key,
        algorithm="RS256",
        headers={"kid": KID},
    )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "")
    monkeypatch.setenv("ALLOWED_USER_IDS", SUB)
    monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())
    monkeypatch.setattr(
        mcp_auth, "_jwks_refresh_after", type(mcp_auth._jwks_refresh_after)()
    )
    monkeypatch.setattr(
        mcp_auth, "_jwks_gate", threading.BoundedSemaphore(mcp_auth.MAX_JWKS_INFLIGHT)
    )
    api_auth._jwks_negative.clear()
    api_auth._jwks_inflight.clear()
    yield
    api_auth._jwks_negative.clear()
    api_auth._jwks_inflight.clear()


@pytest.mark.parametrize("prefilled", [True, False])
def test_mcp_malformed_payload_does_not_poison_the_kid(env, monkeypatch, private_key, prefilled):
    client = _client(private_key, prefilled=prefilled)
    monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: client)

    with pytest.raises(AuthError) as exc:
        mcp_auth._decode_clerk_jwt(_malformed_token())
    assert exc.value.status == 401
    assert KID not in mcp_auth._jwks_negative

    claims = mcp_auth._decode_clerk_jwt(_valid_token(private_key))
    assert claims["sub"] == SUB


@pytest.mark.parametrize("prefilled", [True, False])
def test_api_malformed_payload_does_not_poison_the_kid(env, monkeypatch, private_key, prefilled):
    client = _client(private_key, prefilled=prefilled)
    monkeypatch.setattr(api_auth, "_get_jwks_client", lambda: client)

    def request(token):
        return SimpleNamespace(headers={"Authorization": f"Bearer {token}"})

    async def run():
        with pytest.raises(api_auth.HTTPException) as exc:
            await api_auth.verify_clerk_bearer(request(_malformed_token()))
        assert exc.value.status_code == 401
        await asyncio.sleep(0)  # let the in-flight done-callback run
        assert KID not in api_auth._jwks_negative
        return await api_auth.verify_clerk_bearer(request(_valid_token(private_key)))

    assert asyncio.run(run())["sub"] == SUB
