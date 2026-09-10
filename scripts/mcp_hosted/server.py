"""Hosted Streamable HTTP MCP server — https://app.radon.run/mcp (issue #232 chunk 1).

A dedicated FastMCP process on 127.0.0.1:8334 (radon-mcp.service) behind
Caddy's `handle /mcp*` intercept on app.radon.run. Deliberately NOT a mount
on scripts/api/server.py: this process serves exactly one route (/mcp), so
an anonymous caller can never reach the FastAPI /docs or operator
/openapi.json surface through it.

Auth split (v1, default deny — see mcp_hosted/auth.py):

  public   (no credentials)  radon_identity, radon_docs, radon_health —
      product identity, radon.run llms.txt / developer docs, and the
      trust-scoped public edge health verdict. No live quotes, no UW, no
      IB, no portfolio.
  demo     (Clerk demoRole)  demo_regime, demo_gex — read-only wraps of
      what demo.radon.run already shows, proxied to the demo surface with
      the CALLER'S OWN Clerk token so the demo gate and the 3-trading-day
      trial clock are enforced there again. No IB, no orders, no live
      operator book.
  operator (ALLOWED_USER_IDS) operator_portfolio, operator_journal,
      operator_blotter, operator_alerts — read-only wraps of the operator
      terminal's own Next.js routes, proxied with the caller's own Clerk
      token so app.radon.run re-authenticates every read.

No write tools: nothing named place_* / cancel_* / exercise_* is registered
(test-pinned), radon.trade is out of v1, and the knowledge corpus kb_* tools
stay checkout-only on the radon-kb stdio server. This process never
reads service tokens or vendor keys and never forwards them to MCP
clients (AST-pinned).

Tool logic lives in `_*_impl(principal, ...)` functions with an injectable
HTTP getter so tests never touch the network. Every tool that performs an
upstream read (or a JWKS fetch) runs it via asyncio.to_thread so a slow
upstream never stalls the event loop for other callers.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from mcp_hosted.auth import AuthError, Principal, resolve_principal  # noqa: E402

HOSTED_MCP_URL = "https://app.radon.run/mcp"

SITE_BASE = os.environ.get("RADON_MCP_SITE_BASE", "https://radon.run")
EDGE_BASE = os.environ.get("RADON_MCP_EDGE_BASE", "https://app.radon.run")
# Operator reads wrap the operator terminal's own Next.js routes. In
# production the process sits next to radon-nextjs, so loopback is the
# default; Clerk auth is enforced by Next.js middleware either way (its
# localhost bypass is dev-only, NODE_ENV !== "production").
APP_BASE = os.environ.get("RADON_MCP_APP_BASE", "http://127.0.0.1:3000")
# Demo reads wrap the demo surface itself (Vercel), which owns the demo
# gate, the AI quota, and the trial-expiry clock.
DEMO_BASE = os.environ.get("RADON_MCP_DEMO_BASE", "https://demo.radon.run")

HTTP_TIMEOUT_S = 15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# Largest POST /mcp body accepted from the anonymous internet; a JSON-RPC
# tools/call is a few hundred bytes.
MAX_REQUEST_BYTES = 1024 * 1024

# Public radon.run documents the anonymous rung may fetch. Wraps the
# published site surface only — never /knowledge/* or any FastAPI route.
PUBLIC_DOC_SLUGS: dict[str, str] = {
    "llms.txt": "/llms.txt",
    "agent-instructions": "/agent-instructions.md",
    "developers": "/developers.md",
    "developers/mcp": "/developers/mcp.md",
    "developers/auth": "/developers/auth.md",
    "developers/openapi": "/developers/openapi.md",
    "developers/webhooks": "/developers/webhooks.md",
}

HttpGetter = Callable[[str, dict], "HttpResult"]


class HttpResult:
    """Status + body of one bounded upstream GET."""

    def __init__(self, status: int, text: str):
        self.status = status
        self.text = text

    def json(self) -> Any:
        import json

        return json.loads(self.text)


def _http_get(url: str, headers: dict) -> HttpResult:
    """One bounded GET. Kept tiny; tests inject a fake instead.

    REL-193 (R-550): streamed with a running byte bound — `resp.content`
    buffers the whole upstream body before any slice, so the old form bounded
    what was RETURNED, not what was allocated inside the 512M cgroup.
    """
    import requests

    resp = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT_S, stream=True)
    try:
        chunks: list[bytes] = []
        received = 0
        for chunk in resp.iter_content(chunk_size=65536):
            received += len(chunk)
            if received > MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"upstream response exceeded {MAX_RESPONSE_BYTES} bytes"
                )
            chunks.append(chunk)
        body = b"".join(chunks)
    finally:
        resp.close()
    return HttpResult(resp.status_code, body.decode("utf-8", errors="replace"))


def _proxy_headers(principal: Principal) -> dict:
    """Headers for a proxied read on behalf of an authenticated caller.

    The caller's OWN Clerk token rides along so the wrapped surface
    re-authenticates the read; X-Forwarded-For is always present so the
    FastAPI trusted-local loopback bypass (scripts/api/auth.py denies the
    bypass to any forwarded request) can never apply to a proxied MCP call,
    whatever base URL this process is configured with. No service tokens,
    no API keys.
    """
    return {
        "Authorization": f"Bearer {principal.token}",
        "Accept": "application/json",
        "X-Forwarded-For": "0.0.0.0",
        "X-Radon-Mcp": "1",
    }


def _denied(exc: AuthError) -> dict:
    return {"error": exc.message, "status": exc.status}


def _upstream_json(result: HttpResult) -> dict:
    if result.status != 200:
        return {"error": f"upstream HTTP {result.status}", "status": result.status}
    try:
        return {"data": result.json()}
    except ValueError:
        return {"error": "upstream returned a non-JSON body", "status": 502}


# ── pure tool logic (tests call these directly) ──────────────────────


def _radon_identity_impl() -> dict:
    return {
        "product": "Radon Terminal",
        "summary": (
            "Market-structure research terminal: dark-pool / OTC flow scoring, "
            "GEX walls, CRI / VCG-R / GRG regimes, defined-risk options "
            "structures sized with fractional Kelly."
        ),
        "hosted_mcp_url": HOSTED_MCP_URL,
        "transport": "streamable-http",
        "auth": {
            "public": "no credentials — identity, docs pointers, edge health",
            "demo": "Clerk demo trial (demo.radon.run signup) — read-only demo data",
            "operator": "allowlisted Clerk operator — read-only journal/portfolio/blotter/alerts",
            "writes": "none — no order placement, cancellation, or exercise tools",
        },
        "docs": {
            "llms_txt": f"{SITE_BASE}/llms.txt",
            "agent_instructions": f"{SITE_BASE}/agent-instructions",
            "developers": f"{SITE_BASE}/developers",
            "mcp": f"{SITE_BASE}/developers/mcp",
        },
        "demo_signup": "https://demo.radon.run",
    }


def _radon_docs_impl(slug: str, *, http_get: HttpGetter = _http_get) -> dict:
    path = PUBLIC_DOC_SLUGS.get((slug or "").strip())
    if path is None:
        return {
            "error": f"unknown doc slug {slug!r}",
            "valid_slugs": sorted(PUBLIC_DOC_SLUGS),
        }
    url = f"{SITE_BASE}{path}"
    result = http_get(url, {"Accept": "text/markdown, text/plain"})
    if result.status != 200:
        return {"error": f"upstream HTTP {result.status}", "status": result.status, "url": url}
    return {"url": url, "markdown": result.text}


def _radon_health_impl(*, http_get: HttpGetter = _http_get) -> dict:
    # The public edge health floor — already trust-scoped for anonymous
    # internet traffic (aggregate verdict only; no unit inventory, no IB
    # auth_state, no account identifiers). Never the FastAPI /health.
    url = f"{EDGE_BASE}/edge-health/status"
    result = http_get(url, {"Accept": "application/json"})
    payload = _upstream_json(result)
    if "error" in payload:
        return payload
    return {"url": url, **payload}


def _demo_read_impl(
    principal: Principal, path: str, *, http_get: HttpGetter = _http_get
) -> dict:
    if not principal.is_demo_or_operator:
        return _denied(AuthError(401, "a demo or operator Clerk token is required"))
    result = http_get(f"{DEMO_BASE}{path}", _proxy_headers(principal))
    return _upstream_json(result)


def _operator_read_impl(
    principal: Principal, path: str, *, http_get: HttpGetter = _http_get
) -> dict:
    if not principal.is_operator:
        if principal.role == "demo":
            # A demo grant never reaches the operator book.
            return _denied(AuthError(403, "operator tools require the operator allowlist"))
        return _denied(AuthError(401, "an operator Clerk token is required"))
    result = http_get(f"{APP_BASE}{path}", _proxy_headers(principal))
    return _upstream_json(result)


# ── MCP registration ─────────────────────────────────────────────────

# Keep the SDK's DNS-rebinding protection ON, but name the real fronting
# hosts: Caddy forwards the original Host header (app.radon.run or
# mcp.radon.run), which the loopback-only default would 421.
_ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "RADON_MCP_ALLOWED_HOSTS",
        "app.radon.run,app.radon.run:*,mcp.radon.run,mcp.radon.run:*,127.0.0.1:*,localhost:*",
    ).split(",")
    if host.strip()
]
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "RADON_MCP_ALLOWED_ORIGINS",
        "https://app.radon.run,https://mcp.radon.run",
    ).split(",")
    if origin.strip()
]

mcp = FastMCP(
    "radon",
    instructions=(
        "Hosted read-only MCP for Radon Terminal (market-structure research). "
        "Public tools need no credentials. demo_* tools need a Clerk demo "
        "trial token (sign up at https://demo.radon.run); operator_* tools "
        "need the allowlisted operator token. There are no write tools: "
        "order routing stays on the operator terminal."
    ),
    host=os.environ.get("RADON_MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("RADON_MCP_PORT", "8334")),
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_ALLOWED_HOSTS,
        allowed_origins=_ALLOWED_ORIGINS,
    ),
)

# T-391: hard bound on the inbound request body. Caddy enforces the same
# 1MB at the edge (cloud/caddy/Caddyfile /mcp* request_body max_size); this
# is the app-side belt so a direct or misconfigured path can't hand the
# stateless transport an unbounded body under MemoryMax=512M.
MAX_REQUEST_BYTES = 1024 * 1024


class _BodySizeLimit:
    """Pure-ASGI middleware: 413 any request whose body exceeds max_bytes,
    before the JSON-RPC layer (and any tool) sees it."""

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def _reject(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"request body too large"})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers") or []:
            if name == b"content-length" and value.isdigit():
                if int(value) > self.max_bytes:
                    return await self._reject(send)
        received = 0
        overflowed = False

        async def bounded_receive():
            nonlocal received, overflowed
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    overflowed = True
                    # Present the stream as ended so the app stops reading.
                    return {"type": "http.disconnect"}
            return message

        response_started = False

        async def guarded_send(message):
            nonlocal response_started
            if overflowed:
                if response_started:
                    return
                response_started = True
                return await self._reject(send)
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, bounded_receive, guarded_send)
        if overflowed and not response_started:
            await self._reject(send)


_build_streamable_http_app = mcp.streamable_http_app


def _bounded_streamable_http_app():
    app = _build_streamable_http_app()
    app.add_middleware(_BodySizeLimit)
    return app


# Both serve.py (mcp.run) and tests build the app through this attribute,
# so the bound rides every construction path.
mcp.streamable_http_app = _bounded_streamable_http_app


def _principal_from_context(ctx: Context | None) -> Principal:
    """Resolve the caller's rung from the HTTP request behind the context."""
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    headers = getattr(request, "headers", None)
    authorization = headers.get("authorization") if headers is not None else None
    return resolve_principal(authorization)


def _gated(read, ctx: Context | None) -> dict:
    try:
        principal = _principal_from_context(ctx)
    except AuthError as exc:
        return _denied(exc)
    return read(principal)


async def _gated_off_loop(read, ctx: Context | None) -> dict:
    """JWT verification (a possible JWKS fetch) and the proxied read both
    block, so they run together in a worker thread."""
    return await asyncio.to_thread(_gated, read, ctx)


@mcp.tool()
def radon_identity() -> dict:
    """What Radon Terminal is, the hosted MCP URL and auth rungs, and where
    the public machine-readable docs live. Needs no credentials."""
    return _radon_identity_impl()


@mcp.tool()
async def radon_docs(
    slug: Annotated[
        str,
        Field(description="One of: " + ", ".join(sorted(PUBLIC_DOC_SLUGS))),
    ],
) -> dict:
    """Fetch one public radon.run document as markdown (llms.txt, agent
    instructions, or a named developer page). Needs no credentials."""
    return await asyncio.to_thread(_radon_docs_impl, slug)


@mcp.tool()
async def radon_health() -> dict:
    """The public, trust-scoped Radon edge health verdict (reachable or not).
    Carries no account, unit, or broker detail. Needs no credentials."""
    return await asyncio.to_thread(_radon_health_impl)


@mcp.tool()
async def demo_regime(ctx: Context) -> dict:
    """Current market regime snapshot (VIX / VVIX / CRI / correlation) from
    the Radon demo surface. Requires a Clerk demo-trial or operator token."""
    return await _gated_off_loop(lambda p: _demo_read_impl(p, "/api/regime"), ctx)


@mcp.tool()
async def demo_gex(ctx: Context) -> dict:
    """Latest gamma-exposure (GEX) scan from the Radon demo surface.
    Requires a Clerk demo-trial or operator token."""
    return await _gated_off_loop(lambda p: _demo_read_impl(p, "/api/gex"), ctx)


@mcp.tool()
async def operator_portfolio(ctx: Context) -> dict:
    """The operator's live portfolio snapshot (read-only). Requires the
    allowlisted operator Clerk token."""
    return await _gated_off_loop(lambda p: _operator_read_impl(p, "/api/portfolio"), ctx)


@mcp.tool()
async def operator_journal(ctx: Context) -> dict:
    """The operator's trade journal rows (read-only). Requires the
    allowlisted operator Clerk token."""
    return await _gated_off_loop(lambda p: _operator_read_impl(p, "/api/journal"), ctx)


@mcp.tool()
async def operator_blotter(ctx: Context) -> dict:
    """The operator's blotter (today's fills, read-only). Requires the
    allowlisted operator Clerk token."""
    return await _gated_off_loop(lambda p: _operator_read_impl(p, "/api/blotter"), ctx)


@mcp.tool()
async def operator_alerts(ctx: Context) -> dict:
    """The operator's configured alerts (read-only). Requires the
    allowlisted operator Clerk token."""
    return await _gated_off_loop(lambda p: _operator_read_impl(p, "/api/alerts"), ctx)


# ── ASGI app ─────────────────────────────────────────────────────────


class RequestBodyLimit:
    """Pure-ASGI cap on POST bodies: 413 above MAX_REQUEST_BYTES, decided
    from Content-Length before the transport reads a byte, and enforced
    while streaming for bodies that arrive without one."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or ()).get(b"content-length", b"")
        if length.isdigit() and int(length) > MAX_REQUEST_BYTES:
            await self._reject(send)
            return

        received = 0
        rejected = False

        async def bounded_receive():
            nonlocal received, rejected
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_REQUEST_BYTES and not rejected:
                rejected = True
                await self._reject(send)
                return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            if not rejected:
                await send(message)

        await self.app(scope, bounded_receive, guarded_send)

    @staticmethod
    async def _reject(send):
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})


def build_app():
    """The ASGI app radon-mcp.service serves: the SDK's streamable HTTP app
    behind the request-body cap."""
    app = mcp.streamable_http_app()
    app.add_middleware(RequestBodyLimit)
    return app
