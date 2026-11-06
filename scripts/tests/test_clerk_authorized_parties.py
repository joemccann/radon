"""CLERK_AUTHORIZED_PARTIES binds a Clerk session token to the app it was
minted for (the ``azp`` claim): FastAPI ``verify_clerk_bearer`` refuses it,
and hosted MCP ``resolve_principal`` withholds the operator rung.

Set: a token whose ``azp`` is outside the list is refused; a listed ``azp``
and a token with no ``azp`` still pass (Clerk's own semantics). Hosted MCP
keeps admitting demo-trial tokens, which the demo app mints. Unset: no check,
so dev and CI behave as before.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from api import auth as api_auth  # noqa: E402
from mcp_hosted import auth as mcp_auth  # noqa: E402
from mcp_hosted.auth import AuthError  # noqa: E402

KID = "ins_test_kid"
SUB = "user_operator"
APP = "https://app.example.test"
OTHER = "https://other.example.test"


@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(private_key, **extra) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {"sub": SUB, "iat": now, "exp": now + 300} | extra,
        private_key,
        algorithm="RS256",
        headers={"kid": KID},
    )


@pytest.fixture
def env(monkeypatch, private_key):
    monkeypatch.setenv("CLERK_ISSUER", "")
    monkeypatch.setenv("ALLOWED_USER_IDS", SUB)
    monkeypatch.delenv("CLERK_AUTHORIZED_PARTIES", raising=False)

    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    client = pyjwt.PyJWKClient("https://jwks.invalid/.well-known/jwks.json", cache_keys=True)
    client.jwk_set_cache.put({"keys": [jwk]})
    client.fetch_data = lambda: {"keys": [jwk]}  # no network

    monkeypatch.setattr(api_auth, "_get_jwks_client", lambda: client)
    monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: client)
    monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())
    monkeypatch.setattr(mcp_auth, "_jwks_refresh_after", type(mcp_auth._jwks_refresh_after)())
    monkeypatch.setattr(
        mcp_auth, "_jwks_gate", threading.BoundedSemaphore(mcp_auth.MAX_JWKS_INFLIGHT)
    )
    api_auth._jwks_negative.clear()
    api_auth._jwks_inflight.clear()
    yield monkeypatch
    api_auth._jwks_negative.clear()
    api_auth._jwks_inflight.clear()


def _api(token: str) -> dict:
    request = SimpleNamespace(headers={"Authorization": f"Bearer {token}"})
    return asyncio.run(api_auth.verify_clerk_bearer(request))


def _mcp(token: str) -> dict:
    principal = mcp_auth.resolve_principal(f"Bearer {token}")
    return {"sub": principal.user_id, "role": principal.role}


def _status(verify, token: str) -> int:
    try:
        verify(token)
    except HTTPException as exc:
        return exc.status_code
    except AuthError as exc:
        return exc.status
    return 200


@pytest.mark.parametrize("verify", [_api, _mcp], ids=["api", "mcp"])
def test_token_for_another_app_is_refused(env, private_key, verify):
    env.setenv("CLERK_AUTHORIZED_PARTIES", f"{APP}, http://localhost:3000")
    refused = 401 if verify is _api else 403
    assert _status(verify, _token(private_key, azp=OTHER)) == refused
    assert _status(verify, _token(private_key, azp=["not", "a", "string"])) == refused


@pytest.mark.parametrize("verify", [_api, _mcp], ids=["api", "mcp"])
def test_listed_party_and_absent_azp_pass(env, private_key, verify):
    env.setenv("CLERK_AUTHORIZED_PARTIES", f"{APP}, http://localhost:3000")
    assert verify(_token(private_key, azp=APP))["sub"] == SUB
    assert verify(_token(private_key, azp="http://localhost:3000"))["sub"] == SUB
    assert verify(_token(private_key))["sub"] == SUB


@pytest.mark.parametrize("verify", [_api, _mcp], ids=["api", "mcp"])
def test_unset_keeps_current_behavior(env, private_key, verify):
    assert verify(_token(private_key, azp=OTHER))["sub"] == SUB


def test_mcp_demo_rung_ignores_the_operator_binding(env, private_key):
    env.setenv("CLERK_AUTHORIZED_PARTIES", APP)
    token = _token(private_key, sub="user_demo", azp=OTHER, metadata={"demoRole": "trial"})
    principal = mcp_auth.resolve_principal(f"Bearer {token}")
    assert principal.role == mcp_auth.ROLE_DEMO
