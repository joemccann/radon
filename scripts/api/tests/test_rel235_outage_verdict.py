"""REL-235 (R-637): an upstream outage is not a token verdict — FastAPI port.

REL-229 fixed scripts/mcp_hosted/auth.py; scripts/api/auth.py still
negative-cached a Clerk outage (timeout/OSError/5xx) under the token's REAL
kid and served 401 from cache for 30s to every valid token. Only a PyJWT
signature/claim error may populate the negative-kid cache; an outage yields
503 and leaves the kid re-probeable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fastapi import HTTPException  # noqa: E402

from api import auth  # noqa: E402


class _Key:
    key = "the-key"


@pytest.mark.asyncio
async def test_oserror_is_503_and_the_kid_stays_reprobeable(monkeypatch):
    state = {"fail": True}
    calls = []

    class Flaky:
        def get_signing_key_from_jwt(self, token):
            calls.append(1)
            if state["fail"]:
                raise OSError("connection refused")
            return _Key()

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Flaky())

    with pytest.raises(HTTPException) as exc:
        await auth._bounded_signing_key_lookup("token", "kid-outage")
    assert exc.value.status_code == 503, "an upstream outage is not a 401"

    state["fail"] = False
    result = await auth._bounded_signing_key_lookup("token", "kid-outage")
    assert result is not None and result.key == "the-key", (
        "the kid must be re-probeable after a transient failure"
    )
    assert len(calls) == 2, "the second call must re-probe, not serve 401 from cache"


@pytest.mark.asyncio
async def test_outage_does_not_populate_the_negative_cache_via_task_callback(monkeypatch):
    """The add_done_callback at auth.py:219 must make the same distinction."""
    import asyncio

    class Down:
        def get_signing_key_from_jwt(self, token):
            raise TimeoutError("clerk 5xx / socket timeout")

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Down())
    with pytest.raises(HTTPException):
        await auth._bounded_signing_key_lookup("token", "kid-cb")
    # let the done callback run
    await asyncio.sleep(0)
    assert "kid-cb" not in auth._jwks_negative, (
        "the task done-callback negative-cached an outage"
    )


@pytest.mark.asyncio
async def test_pyjwk_connection_error_is_an_outage_not_a_verdict(monkeypatch):
    """PyJWKClientConnectionError subclasses PyJWTError; it is still an outage."""
    import jwt as pyjwt

    class Down:
        def get_signing_key_from_jwt(self, token):
            raise pyjwt.PyJWKClientConnectionError("failed to reach JWKS")

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: Down())
    with pytest.raises(HTTPException) as exc:
        await auth._bounded_signing_key_lookup("token", "kid-conn")
    assert exc.value.status_code == 503
    assert "kid-conn" not in auth._jwks_negative


@pytest.mark.asyncio
async def test_a_pyjwt_verdict_is_still_negative_cached(monkeypatch):
    import jwt as pyjwt

    calls = []

    class NoSuchKid:
        def get_signing_key_from_jwt(self, token):
            calls.append(1)
            raise pyjwt.exceptions.PyJWKClientError("Unable to find a signing key")

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: NoSuchKid())
    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            await auth._bounded_signing_key_lookup("token", "kid-bogus")
        assert exc.value.status_code == 401
    assert len(calls) == 1, "a cached bogus kid re-hit JWKS"
