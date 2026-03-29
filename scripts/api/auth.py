"""Clerk JWT verification middleware for FastAPI.

Validates Bearer tokens against Clerk's JWKS endpoint.
Single-tenant: only allowlisted user IDs can access trading endpoints.
"""

import os
import logging

from fastapi import Request, HTTPException, Depends

logger = logging.getLogger("radon.auth")

_jwks_client = None
_algorithms = ["RS256"]


def _get_jwks_client():
    """Lazy-initialize JWKS client with key caching."""
    global _jwks_client
    if _jwks_client is None:
        import jwt as pyjwt
        jwks_url = os.environ.get("CLERK_JWKS_URL", "")
        if not jwks_url:
            raise RuntimeError("CLERK_JWKS_URL not set")
        _jwks_client = pyjwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def _get_allowed_users() -> set[str]:
    """Parse comma-separated ALLOWED_USER_IDS env var."""
    raw = os.environ.get("ALLOWED_USER_IDS", "")
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


def _get_issuer() -> str:
    """Get Clerk issuer URL from env."""
    return os.environ.get("CLERK_ISSUER", "")


async def verify_clerk_jwt(request: Request) -> dict:
    """FastAPI dependency: extract and validate Clerk JWT from Authorization header.

    Returns the decoded payload on success.
    Raises HTTPException(401) for missing/invalid tokens.
    Raises HTTPException(403) for non-allowlisted users.
    Bypasses validation for localhost requests (server-to-server).
    """
    # Skip auth for server-to-server calls from localhost (Next.js → FastAPI)
    client_host = request.client.host if request.client else None
    if client_host in ("127.0.0.1", "::1"):
        return {"sub": "localhost", "local": True}

    import jwt as pyjwt

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.removeprefix("Bearer ")

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        issuer = _get_issuer()
        decode_options = {"verify_aud": False}

        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=_algorithms,
            issuer=issuer if issuer else None,
            options=decode_options,
        )
    except pyjwt.exceptions.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.exceptions.PyJWTError as e:
        logger.warning("JWT validation failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")

    allowed = _get_allowed_users()
    if allowed and payload.get("sub") not in allowed:
        logger.warning("Access denied for user %s", payload.get("sub"))
        raise HTTPException(status_code=403, detail="Not authorized")

    return payload


def auth_required():
    """Return the verify_clerk_jwt dependency for use in route decorators.

    Usage: @app.get("/protected", dependencies=[Depends(auth_required())])
    """
    return Depends(verify_clerk_jwt)
