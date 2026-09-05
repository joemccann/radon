"""REL-235 (R-637 secondary): jwt.PyJWKClientConnectionError subclasses
PyJWTError, so mcp_hosted's `_is_upstream_outage` classified a wrapped
connection failure as a token verdict and negative-cached the kid —
re-opening the R-606 hole through that one exception type."""
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


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(mcp_auth, "_jwks_negative", type(mcp_auth._jwks_negative)())
    monkeypatch.setattr(
        mcp_auth, "_jwks_refresh_after", type(mcp_auth._jwks_refresh_after)()
    )
    monkeypatch.setattr(
        mcp_auth, "_jwks_gate",
        threading.BoundedSemaphore(mcp_auth.MAX_JWKS_INFLIGHT),
    )


def _token_with_kid(kid: str) -> str:
    import jwt as pyjwt

    return pyjwt.encode({"sub": "x"}, "secret", algorithm="HS256", headers={"kid": kid})


def test_connection_error_is_classified_as_an_outage():
    import jwt as pyjwt

    assert mcp_auth._is_upstream_outage(
        pyjwt.PyJWKClientConnectionError("failed to fetch JWKS")
    ) is True


def test_connection_error_is_503_and_the_kid_stays_reprobeable(monkeypatch):
    import jwt as pyjwt

    state = {"fail": True}

    class Flaky:
        def get_signing_keys(self):
            return [type("K", (), {"key_id": "kid-conn"})()]

        def get_signing_key_from_jwt(self, token):
            if state["fail"]:
                raise pyjwt.PyJWKClientConnectionError("failed to fetch JWKS")
            return type("S", (), {"key": "the-key"})()

    monkeypatch.setattr(mcp_auth, "_get_jwks_client", lambda: Flaky())
    token = _token_with_kid("kid-conn")

    with pytest.raises(AuthError) as exc:
        mcp_auth._signing_key_for(token)
    assert exc.value.status == 503, "a wrapped connection failure is not a verdict"
    assert "kid-conn" not in mcp_auth._jwks_negative

    state["fail"] = False
    assert mcp_auth._signing_key_for(token) == "the-key"
