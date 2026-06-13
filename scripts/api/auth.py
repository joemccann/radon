"""Authentication middleware for FastAPI.

Supports two auth methods:
1. Clerk JWT — browser-based user auth via JWKS
2. API key — headless machine-to-machine auth (scoped to read-only data endpoints)
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import logging

from fastapi import Request, HTTPException, Depends

logger = logging.getLogger("radon.auth")

_TAILNET = ipaddress.ip_network("100.64.0.0/10")


def is_local_or_tailnet(host: str | None) -> bool:
    """True for loopback or Tailscale CGNAT (RFC 6598) addresses.

    Tailnet membership is itself an authenticated channel, so tailnet peers
    are treated as 'local' for server-to-server calls — this is what lets
    the laptop's Next.js (in cloud-thin mode) reach the Hetzner FastAPI
    without forwarding a Clerk JWT.
    """
    if host in ("127.0.0.1", "::1"):
        return True
    if not host:
        return False
    try:
        return ipaddress.ip_address(host) in _TAILNET
    except ValueError:
        return False


# Headers a reverse proxy adds when it forwards a request. Caddy sets
# X-Forwarded-For on every reverse_proxy hop by default. A genuine
# server-to-server call (Next.js → FastAPI on loopback, or the cloud-thin
# laptop → Hetzner over Tailscale) is made with a plain client and carries
# none of these.
_FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")


def _arrived_via_proxy(request) -> bool:
    """True if the request carries reverse-proxy forwarding headers."""
    headers = getattr(request, "headers", None) or {}
    present = {key.lower() for key in headers.keys()}
    return any(name in present for name in _FORWARDING_HEADERS)


def is_trusted_local_request(request) -> bool:
    """True only for genuine server-to-server calls.

    The peer must be loopback/tailnet AND the request must NOT have entered
    through the public reverse proxy. Caddy's `handle_path /api/ib/*` proxies
    app.radon.run into FastAPI from loopback, so trusting `client.host` alone
    would expose the entire admin/order/exec surface to the internet — a remote
    caller's request reaches FastAPI with `client.host == 127.0.0.1`. Forwarded
    requests always carry forwarding headers, so we deny the bypass for them and
    require a real Clerk JWT.
    """
    client_host = request.client.host if getattr(request, "client", None) else None
    if not is_local_or_tailnet(client_host):
        return False
    return not _arrived_via_proxy(request)

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
    # Skip auth for genuine server-to-server calls from localhost or tailnet
    # (Next.js → FastAPI; cloud-thin laptop dev → Hetzner FastAPI). Requests
    # forwarded through the public reverse proxy are NOT trusted — see
    # is_trusted_local_request.
    if is_trusted_local_request(request):
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


# ---------------------------------------------------------------------------
# API key auth — scoped to read-only historical/contract endpoints
# ---------------------------------------------------------------------------

API_KEY_ALLOWED_PATHS = frozenset({
    "/contract/qualify",
    "/historical/head-timestamp",
    "/historical/bars",
})


def verify_api_key(request: Request) -> dict | None:
    """Check X-API-Key header against MDW_API_KEY env var.

    Returns service identity dict if valid AND path is allowed.
    Returns None if no key provided or path not in scope.
    API key cannot access trading/order endpoints.
    """
    api_key = request.headers.get("X-API-Key")
    mdw_key = os.environ.get("MDW_API_KEY")
    if not api_key or not mdw_key:
        return None
    if not hmac.compare_digest(api_key.encode(), mdw_key.encode()):
        return None
    if request.url.path not in API_KEY_ALLOWED_PATHS:
        return None
    return {"sub": "mdw-service", "service": True}
