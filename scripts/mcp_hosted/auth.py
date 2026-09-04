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
service-token or machine-key header is accepted on this surface.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

ROLE_ANONYMOUS = "anonymous"
ROLE_DEMO = "demo"
ROLE_OPERATOR = "operator"

MAX_JWT_BYTES = 8_192  # mirrors scripts/api/auth.py
# An unknown kid forces a live JWKS refetch (PyJWKClient.get_signing_key).
# This surface is anonymous internet, so at most one such refetch per
# window; mirrors scripts/api/auth.py JWKS_NEGATIVE_TTL_SECONDS.
JWKS_REFRESH_COOLDOWN_SECONDS = 30.0
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
_jwks_refresh_lock = threading.Lock()
# R-620: this was ONE module-global scalar, so a flood of random kids against
# this internet-facing surface kept the cooldown perpetually in the future and
# the genuinely-new kid produced by a Clerk key rotation never triggered a
# refetch — every legitimate token 401'd for as long as the flood lasted. The
# cooldown is per kid, bounded like the negative cache.
_jwks_refresh_after: OrderedDict[str, float] = OrderedDict()

# REL-193 (R-551): the JWKS bounding controls scripts/api/auth.py has, ported
# to this mirror's synchronous shape. PyJWKClient force-refreshes on an
# unknown kid, so without these an anonymous flood of random-kid tokens made
# one outbound Clerk fetch per request (30s default timeout each) and pinned
# the sync-tool threadpool.
MAX_JWKS_INFLIGHT = 4
JWKS_LOOKUP_TIMEOUT_SECONDS = 3.0
JWKS_NEGATIVE_TTL_SECONDS = 30.0
_jwks_gate = threading.BoundedSemaphore(MAX_JWKS_INFLIGHT)
_jwks_negative: OrderedDict[str, float] = OrderedDict()
_jwks_negative_lock = threading.Lock()


def _get_jwks_client():
    global _jwks_client
    if _jwks_client is None:
        import jwt as pyjwt

        jwks_url = os.environ.get("CLERK_JWKS_URL", "")
        if not jwks_url:
            raise AuthError(401, "authentication is not configured on this server")
        _jwks_client = pyjwt.PyJWKClient(
            jwks_url, cache_keys=True, timeout=JWKS_LOOKUP_TIMEOUT_SECONDS
        )
    return _jwks_client


def _remember_negative_kid(kid: str) -> None:
    with _jwks_negative_lock:
        _jwks_negative[kid] = time.monotonic() + JWKS_NEGATIVE_TTL_SECONDS
        _jwks_negative.move_to_end(kid)
        while len(_jwks_negative) > 256:
            _jwks_negative.popitem(last=False)


def _negative_kid_active(kid: str) -> bool:
    with _jwks_negative_lock:
        expiry = _jwks_negative.get(kid)
        if expiry is None:
            return False
        if expiry > time.monotonic():
            return True
        _jwks_negative.pop(kid, None)
        return False


def _is_upstream_outage(exc: BaseException) -> bool:
    """True when the failure says nothing about this token (R-606).

    Only a PyJWT-class error is a verdict on the token; a socket timeout, an
    OSError or anything else is the JWKS endpoint being unreachable.
    """
    if isinstance(exc, (TimeoutError, OSError)):
        return True
    try:
        from jwt.exceptions import PyJWTError
    except Exception:  # noqa: BLE001 — without pyjwt nothing here is a verdict
        return True
    return not isinstance(exc, PyJWTError)


def _signing_key_for(token: str):
    """The JWKS signing key for this token. Split out so tests can stub it.

    A kid missing from the cached set gets one live refetch per
    JWKS_REFRESH_COOLDOWN_SECONDS; inside the window it is denied without
    an upstream request.
    """
    global _jwks_refresh_after
    import jwt as pyjwt

    kid = pyjwt.get_unverified_header(token).get("kid")
    if _negative_kid_active(kid):
        raise AuthError(401, "invalid token")
    if not _jwks_gate.acquire(blocking=False):
        raise AuthError(503, "authentication unavailable")

    try:
        client = _get_jwks_client()
        if kid not in {key.key_id for key in client.get_signing_keys()}:
            with _jwks_refresh_lock:
                now = time.monotonic()
                if now < _jwks_refresh_after.get(kid, 0.0):
                    raise AuthError(401, "invalid token")
                _jwks_refresh_after[kid] = now + JWKS_REFRESH_COOLDOWN_SECONDS
                _jwks_refresh_after.move_to_end(kid)
                while len(_jwks_refresh_after) > 256:
                    _jwks_refresh_after.popitem(last=False)
        try:
            return client.get_signing_key_from_jwt(token).key
        except AuthError:
            raise
        except Exception as exc:
            if _is_upstream_outage(exc):
                raise
            # A kid the freshly-fetched key set does not contain: a verdict.
            _remember_negative_kid(kid)
            raise AuthError(401, "invalid token") from exc
    except AuthError:
        raise
    except Exception as exc:
        # R-606: this used to cache the token's REAL kid on ANY failure —
        # including a timeout or a 5xx raised inside the JWKS fetch — and
        # `_negative_kid_active` then denied every later request with a 401
        # served without an upstream attempt, so nothing re-probed. An
        # upstream outage is not a verdict about this token's signature.
        if _is_upstream_outage(exc):
            raise AuthError(503, "authentication temporarily unavailable") from exc
        _remember_negative_kid(kid)
        raise
    finally:
        _jwks_gate.release()


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
