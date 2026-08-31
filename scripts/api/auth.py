"""Authentication middleware for FastAPI.

Supports two auth methods:
1. Clerk JWT — browser-based user auth via JWKS
2. API key — headless machine-to-machine auth (scoped to read-only data endpoints)
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import logging
import re
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from fastapi import Request, HTTPException, Depends

logger = logging.getLogger("radon.auth")

_TAILNET = ipaddress.ip_network("100.64.0.0/10")
# Hetzner Cloud Network radon-private (app 10.0.0.2, broker 10.0.0.4).
# NIC attachment is the authenticated channel, same class as the tailnet.
# Do not widen to all RFC1918 — docker0 is 172.17.0.0/16.
_HETZNER_PRIVATE = ipaddress.ip_network("10.0.0.0/16")


def is_local_or_tailnet(host: str | None) -> bool:
    """True for loopback, Tailscale CGNAT, or the Hetzner private net.

    Tailnet membership is itself an authenticated channel, so tailnet peers
    are treated as 'local' for server-to-server calls — this is what lets
    the laptop's Next.js (in cloud-thin mode) reach the Hetzner FastAPI
    without forwarding a Clerk JWT. After the broker/app split the watchdog
    on 10.0.0.4 probes FastAPI on 10.0.0.2 the same way.
    """
    if host in ("127.0.0.1", "::1"):
        return True
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in _TAILNET or address in _HETZNER_PRIVATE


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


def _header(request, name: str) -> str:
    """Case-insensitive header read that also works on a plain dict."""
    headers = getattr(request, "headers", None) or {}
    for key, value in headers.items():
        if key.lower() == name:
            return value or ""
    return ""


# `Sec-Fetch-Site` values that a page CANNOT forge into a cross-site attack:
# `none` is only set for user-initiated navigation (typing the URL, a bookmark),
# and `same-origin` only for a document this API itself served (Swagger UI at
# /docs calling /openapi.json).
_FIRST_PARTY_FETCH_SITES = frozenset({"same-origin", "none"})


def _is_first_party_origin(request, origin: str) -> bool:
    """True when Origin names the exact host:port this request was addressed to."""
    host = _header(request, "host").strip().lower()
    if not host:
        return False
    return urlsplit(origin).netloc.strip().lower() == host


def _is_browser_page_request(request) -> bool:
    """True when the request carries the fetch metadata of a page-issued call.

    The absence of forwarding headers is NOT proof of a server-to-server caller:
    a page in the operator's browser POSTing to http://127.0.0.1:8321 sends no
    X-Forwarded-For either, and a CORS *simple* request (Content-Type:
    text/plain) is never preflighted, so it reaches the handler. Browsers do
    always attach `Sec-Fetch-Site` to every fetch and `Origin` to every
    cross-origin or non-GET request; the Next.js node-fetch client, the WS relay,
    the watchdog/health daemon (urllib) and curl attach neither. Requiring that
    positive evidence is what separates the two.
    """
    fetch_site = _header(request, "sec-fetch-site").strip().lower()
    if fetch_site and fetch_site not in _FIRST_PARTY_FETCH_SITES:
        return True
    origin = _header(request, "origin").strip()
    return bool(origin) and not _is_first_party_origin(request, origin)


def is_trusted_local_request(request) -> bool:
    """True only for genuine server-to-server calls.

    The peer must be loopback/tailnet, the request must NOT have entered through
    the public reverse proxy, and it must NOT look like it was issued by a page
    in a browser. Caddy's `handle_path /api/ib/*` proxies app.radon.run into
    FastAPI from loopback, so trusting `client.host` alone would expose the
    entire admin/order/exec surface to the internet — a remote caller's request
    reaches FastAPI with `client.host == 127.0.0.1`. Forwarded requests always
    carry forwarding headers, so we deny the bypass for them and require a real
    Clerk JWT. See `_is_browser_page_request` for the same-machine browser case.
    """
    client_host = request.client.host if getattr(request, "client", None) else None
    if not is_local_or_tailnet(client_host):
        return False
    if _arrived_via_proxy(request):
        return False
    return not _is_browser_page_request(request)


def is_trusted_service_request(request) -> bool:
    """True for a trusted service caller presenting the shared service token.

    The demo frontend (Vercel, demo.radon.run) calls the demo VM's FastAPI over
    the public reverse proxy, where loopback/tailnet trust does not apply. The
    frontend has already authenticated the Clerk user and applied the demo gate,
    so it authenticates to the backend with the shared ``RADON_SERVICE_TOKEN``
    header rather than forwarding a per-user JWT. Returns False (no-op) when
    RADON_SERVICE_TOKEN is unset — i.e. on prod, where it is never configured,
    so prod trust behavior is unchanged.
    """
    expected = os.environ.get("RADON_SERVICE_TOKEN")
    if not expected:
        return False
    headers = getattr(request, "headers", None) or {}
    provided = (
        headers.get("X-Radon-Service-Token")
        or headers.get("x-radon-service-token")
        or ""
    )
    if not provided:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


_jwks_client = None
_algorithms = ["RS256"]
MAX_JWT_BYTES = 8_192
MAX_JWKS_INFLIGHT = 4
JWKS_LOOKUP_TIMEOUT_SECONDS = 3.0
JWKS_NEGATIVE_TTL_SECONDS = 30.0
_KID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_jwks_inflight: dict[str, asyncio.Task] = {}
_jwks_negative: OrderedDict[str, float] = OrderedDict()


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


def _remember_negative_kid(kid: str) -> None:
    _jwks_negative[kid] = time.monotonic() + JWKS_NEGATIVE_TTL_SECONDS
    _jwks_negative.move_to_end(kid)
    while len(_jwks_negative) > 256:
        _jwks_negative.popitem(last=False)


def _finish_jwks_lookup(kid: str, task: asyncio.Task) -> None:
    if _jwks_inflight.get(kid) is task:
        _jwks_inflight.pop(kid, None)
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        _remember_negative_kid(kid)


async def _bounded_signing_key_lookup(token: str, kid: str):
    now = time.monotonic()
    expiry = _jwks_negative.get(kid)
    if expiry is not None:
        if expiry > now:
            raise HTTPException(status_code=401, detail="Invalid token")
        _jwks_negative.pop(kid, None)

    task = _jwks_inflight.get(kid)
    if task is None:
        if len(_jwks_inflight) >= MAX_JWKS_INFLIGHT:
            raise HTTPException(status_code=503, detail="Authentication unavailable")

        async def lookup():
            return await asyncio.to_thread(
                _get_jwks_client().get_signing_key_from_jwt,
                token,
            )

        task = asyncio.create_task(lookup())
        _jwks_inflight[kid] = task
        task.add_done_callback(lambda completed: _finish_jwks_lookup(kid, completed))

    try:
        return await asyncio.wait_for(
            asyncio.shield(task),
            timeout=JWKS_LOOKUP_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=503, detail="Authentication unavailable") from exc
    except Exception as exc:
        _remember_negative_kid(kid)
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _get_allowed_users() -> set[str]:
    """Parse comma-separated ALLOWED_USER_IDS env var."""
    raw = os.environ.get("ALLOWED_USER_IDS", "")
    return {uid.strip() for uid in raw.split(",") if uid.strip()}


def _require_operator_allowlist() -> bool:
    """Whether an EMPTY ALLOWED_USER_IDS should fail CLOSED (deny) rather than
    open.

    Set ``RADON_REQUIRE_OPERATOR_ALLOWLIST=1`` on production (the app.radon.run
    radon-api env) so a blanked/typo'd allowlist cannot silently admit a
    non-operator JWT caller through the public Caddy ``/api/ib/*`` mount. Unset
    everywhere else (dev, CI, demo VM), so behavior there is unchanged. Mirrors
    the Next.js ``isAuthorizedUser`` interlock in ``web/middleware.ts``.
    """
    return os.environ.get("RADON_REQUIRE_OPERATOR_ALLOWLIST") == "1"


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

    # Demo frontend (Vercel) -> demo VM FastAPI over the public proxy. The
    # frontend has already authenticated the Clerk user and applied the demo
    # gate; it presents the shared RADON_SERVICE_TOKEN instead of a per-user
    # JWT. Inert on prod, where RADON_SERVICE_TOKEN is unset.
    if is_trusted_service_request(request):
        return {"sub": "demo-frontend", "service": True}

    return await verify_clerk_bearer(request)


async def verify_clerk_bearer(request: Request) -> dict:
    """Validate the Clerk JWT in ``Authorization`` with NO caller-trust bypass.

    The middleware uses this for app-host Gateway mutations
    (``server._is_app_role_gateway_mutation``): a loopback peer must still
    present an operator JWT there, so the loopback / service-token shortcuts
    in :func:`verify_clerk_jwt` must not be reachable.
    """
    import jwt as pyjwt

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header.removeprefix("Bearer ")
    if len(token.encode("utf-8")) > MAX_JWT_BYTES:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256":
            raise pyjwt.exceptions.InvalidTokenError("invalid JWT header")
        # Clerk always supplies kid. Legacy/test tokens without one share a
        # fixed cache key so they cannot create attacker-chosen work keys.
        if kid is None:
            kid = "__default__"
        if not isinstance(kid, str) or not _KID_PATTERN.fullmatch(kid):
            raise pyjwt.exceptions.InvalidTokenError("invalid JWT header")
        signing_key = await _bounded_signing_key_lookup(token, kid)

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
    if not allowed:
        # No operator allowlist configured. Default (dev, CI, demo VM): don't
        # enforce. Production sets RADON_REQUIRE_OPERATOR_ALLOWLIST=1 so a
        # blanked/typo'd ALLOWED_USER_IDS fails CLOSED — this untrusted JWT
        # caller (public via Caddy /api/ib/*) is denied rather than admitted to
        # the operator surface. Mirrors web/middleware.ts:isAuthorizedUser.
        if _require_operator_allowlist():
            logger.error(
                "RADON_REQUIRE_OPERATOR_ALLOWLIST=1 but ALLOWED_USER_IDS is empty "
                "— denying user %s (fail-closed operator interlock)",
                payload.get("sub"),
            )
            raise HTTPException(status_code=403, detail="Not authorized")
        return payload
    if payload.get("sub") not in allowed:
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
