"""Clerk-JWT rung resolution for the hosted MCP (issue #232 chunk 1).

Three rungs, default deny:

  anonymous — no Authorization header. Public tools only (product identity,
      radon.run docs pointers, the trust-scoped edge health verdict).
  demo      — a valid Clerk JWT whose ``metadata.demoRole`` is set (the trial
      grant demo.radon.run provisions; absence of the role means "not a demo
      user", mirroring web/lib/demo/demoContext.ts). The 3-trading-day trial
      clock is enforced by the wrapped demo surface on every proxied read,
      not re-implemented here.
  operator  — a valid Clerk JWT whose ``sub`` is in ``ALLOWED_USER_IDS``,
      the SAME allowlist scripts/api/auth.py enforces (no second allowlist).
      An empty allowlist admits nobody to the operator rung — this surface
      is public internet, so it fails CLOSED unconditionally (unlike the
      FastAPI dev-open default behind RADON_REQUIRE_OPERATOR_ALLOWLIST).

A valid Clerk JWT with neither grant is denied (403). Verification mirrors
scripts/api/auth.py (RS256 only, kid pattern, bounded token size, issuer
from CLERK_ISSUER, JWKS from CLERK_JWKS_URL) but is synchronous and carries
NO caller-trust bypass: there is no loopback/tailnet shortcut and no
RADON_SERVICE_TOKEN / X-API-Key acceptance on this surface.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable

ROLE_ANONYMOUS = "anonymous"
ROLE_DEMO = "demo"
ROLE_OPERATOR = "operator"

MAX_JWT_BYTES = 8_192  # mirrors scripts/api/auth.py
_KID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ALGORITHMS = ["RS256"]


class AuthError(Exception):
    """A denied rung resolution; ``status`` is the HTTP-shaped code (401/403)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Principal:
    role: str
    user_id: str | None = None
    token: str | None = None

    @property
    def is_operator(self) -> bool:
        return self.role == ROLE_OPERATOR

    @property
    def is_demo_or_operator(self) -> bool:
        return self.role in (ROLE_DEMO, ROLE_OPERATOR)


ANONYMOUS = Principal(role=ROLE_ANONYMOUS)

_jwks_client = None


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        import jwt as pyjwt

        jwks_url = os.environ.get("CLERK_JWKS_URL", "")
        if not jwks_url:
            raise AuthError(401, "authentication is not configured on this server")
        _jwks_client = pyjwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def _signing_key_for(token: str):
    """The JWKS signing key for this token. Split out so tests can stub it."""
    return _get_jwks_client().get_signing_key_from_jwt(token).key


def _get_allowed_users() -> set[str]:
    """Parse ALLOWED_USER_IDS — the one operator allowlist, mirrored from
    scripts/api/auth.py:_get_allowed_users (test-pinned parity)."""
    raw = os.environ.get("ALLOWED_USER_IDS", "")
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


def _decode_clerk_jwt(token: str) -> dict:
    """Verified Clerk JWT claims, or AuthError(401). Mirrors the checks in
    scripts/api/auth.py:verify_clerk_bearer."""
    import jwt as pyjwt

    try:
        header = pyjwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise pyjwt.exceptions.InvalidTokenError("invalid JWT header")
        kid = header.get("kid")
        if kid is not None and (
            not isinstance(kid, str) or not _KID_PATTERN.fullmatch(kid)
        ):
            raise pyjwt.exceptions.InvalidTokenError("invalid JWT header")
        issuer = os.environ.get("CLERK_ISSUER", "")
        return pyjwt.decode(
            token,
            _signing_key_for(token),
            algorithms=_ALGORITHMS,
            issuer=issuer if issuer else None,
            options={"verify_aud": False},
        )
    except pyjwt.exceptions.ExpiredSignatureError:
        raise AuthError(401, "token expired") from None
    except pyjwt.exceptions.PyJWTError:
        raise AuthError(401, "invalid token") from None


def resolve_principal(
    authorization: str | None,
    *,
    decoder: Callable[[str], dict] | None = None,
) -> Principal:
    """Resolve the rung for one request's Authorization header.

    No header → anonymous. A malformed/oversized/invalid Bearer token →
    AuthError(401). A verified Clerk identity with neither the operator
    allowlist membership nor a demoRole grant → AuthError(403).
    """
    if not authorization:
        return ANONYMOUS
    if not authorization.startswith("Bearer "):
        raise AuthError(401, "missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    if not token or len(token.encode("utf-8")) > MAX_JWT_BYTES:
        raise AuthError(401, "invalid token")

    if decoder is None:
        decoder = _decode_clerk_jwt
    claims = decoder(token)

    sub = claims.get("sub")
    if isinstance(sub, str) and sub and sub in _get_allowed_users():
        return Principal(role=ROLE_OPERATOR, user_id=sub, token=token)

    # Clerk session tokens carry publicMetadata as the `metadata` claim
    # (web/middleware.ts reads sessionClaims.metadata the same way).
    metadata = claims.get("metadata")
    if isinstance(metadata, dict) and metadata.get("demoRole"):
        return Principal(role=ROLE_DEMO, user_id=sub, token=token)

    raise AuthError(403, "this Clerk identity has no Radon MCP grant")
