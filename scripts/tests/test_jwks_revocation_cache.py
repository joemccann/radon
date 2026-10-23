"""DS-2026-10-23-01: a kid removed from the JWKS must stop verifying.

``PyJWKClient(cache_keys=True)`` rebinds ``get_signing_key`` to an
``lru_cache`` with no expiry, so once a kid has been resolved the published
JWK Set is never consulted for it again. Removing a compromised key from the
JWKS is the only remedy after a signing-key compromise, and a long-lived
``radon-api`` or hosted-MCP worker would keep honouring it for the life of the
process. Both auth modules must rely on the expiring ``jwk_set_cache`` only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from api import auth as api_auth  # noqa: E402
from mcp_hosted import auth as mcp_auth  # noqa: E402

REVOKED = "kid-revoked"
LIVE = "kid-live"


@pytest.fixture(scope="module")
def keys():
    return {
        REVOKED: rsa.generate_private_key(public_exponent=65537, key_size=2048),
        LIVE: rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


def _key_set(keys, kids):
    out = []
    for kid in kids:
        jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(
            keys[kid].public_key(), as_dict=True
        )
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        out.append(jwk)
    return {"keys": out}


def _served_client(module, monkeypatch, keys, served):
    """The module's own client, serving ``served['kids']`` with no network."""
    monkeypatch.setenv("CLERK_JWKS_URL", "https://jwks.invalid/.well-known/jwks.json")
    monkeypatch.setattr(module, "_jwks_client", None)
    client = module._get_jwks_client()

    def fetch_data():
        data = _key_set(keys, served["kids"])
        if client.jwk_set_cache is not None:
            client.jwk_set_cache.put(data)
        return data

    monkeypatch.setattr(client, "fetch_data", fetch_data)
    return client


@pytest.mark.parametrize("module", [api_auth, mcp_auth], ids=["api", "mcp_hosted"])
def test_revoked_kid_is_refused_after_the_set_cache_expires(
    module, monkeypatch, keys
):
    served = {"kids": [REVOKED, LIVE]}
    client = _served_client(module, monkeypatch, keys, served)

    assert client.get_signing_key(REVOKED).key_id == REVOKED

    # The operator revokes the key and the expiring JWK Set cache lapses.
    served["kids"] = [LIVE]
    client.jwk_set_cache.jwk_set_with_timestamp = None

    with pytest.raises(pyjwt.exceptions.PyJWKClientError):
        client.get_signing_key(REVOKED)

    # A still-published key keeps verifying.
    assert client.get_signing_key(LIVE).key_id == LIVE


@pytest.mark.parametrize("module", [api_auth, mcp_auth], ids=["api", "mcp_hosted"])
def test_signing_key_lookup_is_not_wrapped_in_a_non_expiring_cache(
    module, monkeypatch, keys
):
    client = _served_client(module, monkeypatch, keys, {"kids": [LIVE]})
    assert not hasattr(client.get_signing_key, "cache_info"), (
        "cache_keys=True installs a functools.lru_cache with no expiry in front "
        "of get_signing_key, which defeats JWKS revocation"
    )
