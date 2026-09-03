"""Hosted MCP (issue #232 chunk 1) — auth rungs, proxy contracts, and pins.

Tool logic is exercised directly (`_*_impl`) with injected HTTP getters —
no network. Auth is exercised through resolve_principal with a stubbed
signing key so the real pyjwt verification path runs. Transport-level tests
drive the actual streamable HTTP ASGI app.

Pins (DoD for chunk 1):
  * no place_* / cancel_* / exercise_* tool is registered on the hosted server
  * no kb_* corpus tool is registered (operator journal/P&L stays local-only)
  * the process serves ONLY /mcp — no /docs, no /openapi.json
  * server-side service secrets are never read by the hosted MCP modules
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from mcp_hosted import auth as mcp_auth  # noqa: E402
from mcp_hosted import server as hosted  # noqa: E402
from mcp_hosted.auth import (  # noqa: E402
    ANONYMOUS,
    AuthError,
    Principal,
    resolve_principal,
)

OPERATOR_ID = "user_operator_1"
DEMO_ID = "user_demo_1"


# ── auth fixtures: a real RSA keypair driving the real pyjwt path ─────


@pytest.fixture(scope="module")
def rsa_keys():
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def clerk_env(monkeypatch, rsa_keys):
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.test")
    monkeypatch.setenv("ALLOWED_USER_IDS", f"{OPERATOR_ID}, user_operator_2")
    monkeypatch.setattr(mcp_auth, "_signing_key_for", lambda token: rsa_keys[1])


def _mint(rsa_keys, claims: dict, *, headers: dict | None = None) -> str:
    import jwt as pyjwt

    payload = {
        "iss": "https://clerk.example.test",
        "exp": int(time.time()) + 60,
        **claims,
    }
    return pyjwt.encode(
        payload,
        rsa_keys[0],
        algorithm="RS256",
        headers={"kid": "test-kid", **(headers or {})},
    )


def operator_bearer(rsa_keys) -> str:
    return "Bearer " + _mint(rsa_keys, {"sub": OPERATOR_ID})


def demo_bearer(rsa_keys) -> str:
    return "Bearer " + _mint(
        rsa_keys, {"sub": DEMO_ID, "metadata": {"demoRole": "trial"}}
    )


# ── injected HTTP getter ──────────────────────────────────────────────


class FakeHttp:
    def __init__(self, status=200, body='{"ok": true}'):
        self.status = status
        self.body = body
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, headers: dict) -> hosted.HttpResult:
        self.calls.append((url, headers))
        return hosted.HttpResult(self.status, self.body)


# ── rung resolution ───────────────────────────────────────────────────


class TestResolvePrincipal:
    def test_no_header_is_anonymous(self):
        assert resolve_principal(None) is ANONYMOUS
        assert resolve_principal("") is ANONYMOUS

    def test_non_bearer_is_401(self):
        with pytest.raises(AuthError) as exc:
            resolve_principal("Basic abc")
        assert exc.value.status == 401

    def test_oversized_token_is_401(self):
        with pytest.raises(AuthError) as exc:
            resolve_principal("Bearer " + "a" * (mcp_auth.MAX_JWT_BYTES + 1))
        assert exc.value.status == 401

    def test_garbage_token_is_401(self, clerk_env):
        with pytest.raises(AuthError) as exc:
            resolve_principal("Bearer not-a-jwt")
        assert exc.value.status == 401

    def test_expired_token_is_401(self, clerk_env, rsa_keys):
        token = _mint(rsa_keys, {"sub": OPERATOR_ID, "exp": int(time.time()) - 10})
        with pytest.raises(AuthError) as exc:
            resolve_principal("Bearer " + token)
        assert exc.value.status == 401

    def test_wrong_issuer_is_401(self, clerk_env, rsa_keys):
        token = _mint(rsa_keys, {"sub": OPERATOR_ID, "iss": "https://evil.test"})
        with pytest.raises(AuthError) as exc:
            resolve_principal("Bearer " + token)
        assert exc.value.status == 401

    def test_non_rs256_is_401(self, clerk_env):
        import jwt as pyjwt

        token = pyjwt.encode({"sub": OPERATOR_ID}, "secret", algorithm="HS256")
        with pytest.raises(AuthError) as exc:
            resolve_principal("Bearer " + token)
        assert exc.value.status == 401

    def test_allowlisted_sub_is_operator(self, clerk_env, rsa_keys):
        principal = resolve_principal(operator_bearer(rsa_keys))
        assert principal.role == "operator"
        assert principal.user_id == OPERATOR_ID
        assert principal.is_operator

    def test_demo_role_is_demo(self, clerk_env, rsa_keys):
        principal = resolve_principal(demo_bearer(rsa_keys))
        assert principal.role == "demo"
        assert principal.is_demo_or_operator and not principal.is_operator

    def test_valid_identity_without_grant_is_403(self, clerk_env, rsa_keys):
        token = _mint(rsa_keys, {"sub": "user_random", "metadata": {}})
        with pytest.raises(AuthError) as exc:
            resolve_principal("Bearer " + token)
        assert exc.value.status == 403

    def test_empty_allowlist_fails_closed(self, clerk_env, rsa_keys, monkeypatch):
        monkeypatch.setenv("ALLOWED_USER_IDS", "")
        with pytest.raises(AuthError) as exc:
            resolve_principal(operator_bearer(rsa_keys))
        assert exc.value.status == 403

    def test_allowlist_parser_matches_fastapi(self, monkeypatch):
        """One allowlist, one parse: pinned against scripts/api/auth.py."""
        from api import auth as api_auth

        for raw in ("a, b ,,c", "", "  ", "solo"):
            monkeypatch.setenv("ALLOWED_USER_IDS", raw)
            assert mcp_auth._get_allowed_users() == api_auth._get_allowed_users()


# ── public rung (zero credentials) ────────────────────────────────────


class TestPublicTools:
    def test_identity_is_static_and_credential_free(self):
        result = hosted._radon_identity_impl()
        assert result["hosted_mcp_url"] == "https://app.radon.run/mcp"
        assert result["transport"] == "streamable-http"
        assert result["auth"]["writes"].startswith("none")
        assert result["docs"]["llms_txt"] == "https://radon.run/llms.txt"

    def test_docs_fetches_allowlisted_slug(self):
        http = FakeHttp(body="# Radon Terminal\n")
        result = hosted._radon_docs_impl("llms.txt", http_get=http)
        assert result["markdown"].startswith("# Radon Terminal")
        assert http.calls[0][0] == "https://radon.run/llms.txt"
        # public rung carries NO Authorization header
        assert "Authorization" not in http.calls[0][1]

    def test_docs_rejects_unknown_slug_without_network(self):
        http = FakeHttp()
        result = hosted._radon_docs_impl("../../etc/passwd", http_get=http)
        assert "error" in result and http.calls == []
        assert "llms.txt" in result["valid_slugs"]

    def test_health_hits_public_edge_floor_only(self):
        http = FakeHttp(body='{"reachable": true}')
        result = hosted._radon_health_impl(http_get=http)
        assert result["data"] == {"reachable": True}
        assert http.calls[0][0] == "https://app.radon.run/edge-health/status"


# ── demo / operator rungs ─────────────────────────────────────────────


class TestDemoRung:
    def test_anonymous_gets_401(self):
        result = hosted._demo_read_impl(ANONYMOUS, "/api/regime", http_get=FakeHttp())
        assert result["status"] == 401 and "error" in result

    def test_demo_token_proxies_to_demo_surface_with_own_bearer(self):
        http = FakeHttp(body='{"vix": 17.2}')
        principal = Principal(role="demo", user_id=DEMO_ID, token="demo-token")
        result = hosted._demo_read_impl(principal, "/api/regime", http_get=http)
        assert result == {"data": {"vix": 17.2}}
        url, headers = http.calls[0]
        assert url == "https://demo.radon.run/api/regime"
        assert headers["Authorization"] == "Bearer demo-token"
        assert headers["X-Forwarded-For"]  # loopback bypass can never apply

    def test_operator_token_is_also_accepted(self):
        http = FakeHttp()
        principal = Principal(role="operator", user_id=OPERATOR_ID, token="op-token")
        result = hosted._demo_read_impl(principal, "/api/gex", http_get=http)
        assert result == {"data": {"ok": True}}

    def test_upstream_denial_passes_through(self):
        http = FakeHttp(status=403, body="denied")
        principal = Principal(role="demo", user_id=DEMO_ID, token="demo-token")
        result = hosted._demo_read_impl(principal, "/api/regime", http_get=http)
        assert result["status"] == 403


class TestOperatorRung:
    def test_anonymous_gets_401(self):
        result = hosted._operator_read_impl(
            ANONYMOUS, "/api/portfolio", http_get=FakeHttp()
        )
        assert result["status"] == 401 and "error" in result

    def test_demo_cannot_hit_operator_book(self):
        http = FakeHttp()
        principal = Principal(role="demo", user_id=DEMO_ID, token="demo-token")
        result = hosted._operator_read_impl(principal, "/api/portfolio", http_get=http)
        assert result["status"] == 403
        assert http.calls == []  # denied BEFORE any upstream request

    def test_operator_token_proxies_full_url_with_own_bearer(self):
        http = FakeHttp(body='{"positions": []}')
        principal = Principal(role="operator", user_id=OPERATOR_ID, token="op-token")
        result = hosted._operator_read_impl(principal, "/api/portfolio", http_get=http)
        assert result == {"data": {"positions": []}}
        url, headers = http.calls[0]
        assert url == "http://127.0.0.1:3000/api/portfolio"
        assert headers["Authorization"] == "Bearer op-token"
        assert headers["X-Forwarded-For"]

    def test_no_service_token_ever_rides_a_proxied_read(self):
        http = FakeHttp()
        principal = Principal(role="operator", user_id=OPERATOR_ID, token="op-token")
        hosted._operator_read_impl(principal, "/api/journal", http_get=http)
        _, headers = http.calls[0]
        assert "X-Radon-Service-Token" not in headers
        assert "X-API-Key" not in headers


# ── DoD pins ──────────────────────────────────────────────────────────

def _environ_key(node) -> str | None:
    """The literal key of an os.environ read AST node, if this is one."""
    import ast

    def is_environ(expr) -> bool:
        return (
            isinstance(expr, ast.Attribute)
            and expr.attr == "environ"
            and isinstance(expr.value, ast.Name)
            and expr.value.id == "os"
        )

    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and is_environ(node.func.value)
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ):
        return str(node.args[0].value)
    if (
        isinstance(node, ast.Subscript)
        and is_environ(node.value)
        and isinstance(node.slice, ast.Constant)
    ):
        return str(node.slice.value)
    return None


EXPECTED_TOOLS = {
    "radon_identity",
    "radon_docs",
    "radon_health",
    "demo_regime",
    "demo_gex",
    "operator_portfolio",
    "operator_journal",
    "operator_blotter",
    "operator_alerts",
}

FORBIDDEN_TOOL_PREFIXES = ("place_", "cancel_", "exercise_", "kb_")


class TestHostedServerPins:
    def _tool_names(self) -> set[str]:
        tools = asyncio.run(hosted.mcp.list_tools())
        return {tool.name for tool in tools}

    def test_registered_tool_set_is_pinned(self):
        assert self._tool_names() == EXPECTED_TOOLS

    def test_no_write_or_corpus_tools_registered(self):
        for name in self._tool_names():
            for prefix in FORBIDDEN_TOOL_PREFIXES:
                assert not name.startswith(prefix), (
                    f"{name} must not be registered on the hosted server"
                )
            assert name.startswith(("radon_", "demo_", "operator_")), name

    def test_process_serves_only_mcp_no_docs_no_openapi(self):
        app = hosted.mcp.streamable_http_app()
        paths = {getattr(route, "path", "") for route in app.routes}
        assert any(path.startswith("/mcp") for path in paths), paths
        for path in paths:
            lowered = path.lower()
            assert "docs" not in lowered and "openapi" not in lowered, (
                f"hosted MCP must never expose {path}"
            )

    def test_module_never_reads_service_secrets(self):
        """AST pin: every os.environ key the hosted MCP modules read is either
        an RADON_MCP_* knob or one of the three Clerk/allowlist values. No
        service-token or vendor-key env vars."""
        import ast

        allowed = {"CLERK_JWKS_URL", "CLERK_ISSUER", "ALLOWED_USER_IDS"}
        for name in ("server.py", "auth.py", "serve.py", "__init__.py"):
            tree = ast.parse((_SCRIPTS_DIR / "mcp_hosted" / name).read_text())
            for node in ast.walk(tree):
                key = _environ_key(node)
                if key is None:
                    continue
                assert key in allowed or key.startswith("RADON_MCP_"), (
                    f"{name} reads os.environ[{key!r}] — the hosted MCP must "
                    "never touch service secrets"
                )

    def test_stateless_http_binds_loopback_by_default(self):
        assert hosted.mcp.settings.host == "127.0.0.1"
        assert hosted.mcp.settings.port == 8334
        assert hosted.mcp.settings.streamable_http_path == "/mcp"


# ── transport-level: the real streamable HTTP app ─────────────────────


def _rpc_call(client, tool: str, headers: dict | None = None):
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {}},
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )


def _tool_payload(response) -> dict:
    body = response.json()
    content = body["result"]["content"]
    return json.loads(content[0]["text"])


# Module-scoped: the SDK's StreamableHTTPSessionManager can only be run once
# per FastMCP instance, so every transport test shares one lifespan.
@pytest.fixture(scope="module")
def http_client():
    from starlette.testclient import TestClient

    # base_url pins the production Host header: Caddy forwards the original
    # `app.radon.run` Host, which the transport security allowlist must admit.
    with TestClient(hosted.build_app(), base_url="https://app.radon.run") as client:
        yield client


class TestStreamableHttpTransport:
    def test_public_tool_answers_with_zero_credentials(self, http_client):
        response = _rpc_call(http_client, "radon_identity")
        assert response.status_code == 200
        payload = _tool_payload(response)
        assert payload["hosted_mcp_url"] == "https://app.radon.run/mcp"

    def test_operator_tool_401s_without_a_token(self, http_client):
        response = _rpc_call(http_client, "operator_portfolio")
        assert response.status_code == 200  # JSON-RPC transport stays 200
        payload = _tool_payload(response)
        assert payload["status"] == 401 and "error" in payload

    def test_demo_tool_401s_without_a_token(self, http_client):
        response = _rpc_call(http_client, "demo_regime")
        payload = _tool_payload(response)
        assert payload["status"] == 401 and "error" in payload

    def test_demo_token_cannot_reach_operator_tool(
        self, http_client, clerk_env, rsa_keys
    ):
        response = _rpc_call(
            http_client,
            "operator_portfolio",
            headers={"Authorization": demo_bearer(rsa_keys)},
        )
        payload = _tool_payload(response)
        assert payload["status"] == 403

    def test_invalid_bearer_is_denied_not_crashed(self, http_client, clerk_env):
        response = _rpc_call(
            http_client,
            "operator_portfolio",
            headers={"Authorization": "Bearer garbage"},
        )
        payload = _tool_payload(response)
        assert payload["status"] == 401

    def test_oversized_body_is_413_before_any_tool_runs(
        self, http_client, monkeypatch
    ):
        """T-391: anonymous POST with a > 1MB body must be rejected 413 by
        the app itself (belt to Caddy's edge max_size) before the JSON-RPC
        layer or any tool executes."""
        calls = []
        monkeypatch.setattr(
            hosted, "_radon_identity_impl", lambda: calls.append(1) or {}
        )
        padding = "x" * (hosted.MAX_REQUEST_BYTES + 1)
        response = http_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "radon_identity",
                    "arguments": {"_pad": padding},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 413
        assert calls == [], "tool must not execute on an oversized body"

    def test_app_body_bound_matches_the_edge_bound(self):
        assert hosted.MAX_REQUEST_BYTES == 1024 * 1024

    def test_docs_path_404s_on_this_process(self, http_client):
        assert http_client.get("/docs").status_code == 404
        assert http_client.get("/openapi.json").status_code == 404

    def test_oversize_post_is_413_on_the_real_app(self, http_client):
        response = http_client.post(
            "/mcp",
            content=b"x" * (hosted.MAX_REQUEST_BYTES + 1),
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 413
        # the cap never touches a well-formed call
        assert _rpc_call(http_client, "radon_identity").status_code == 200


# ── resource bounds on the anonymous surface ──────────────────────────


def _body_reading_app(calls: list):
    """A fake inner ASGI app that, like the SDK transport, reads the whole
    body before answering."""

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # Starlette raises ClientDisconnect here; no handler runs
            if not message.get("more_body"):
                break
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


class TestRequestBodyCap:
    HEADERS = {"Content-Type": "application/json"}

    def _client(self, calls):
        from starlette.testclient import TestClient

        return TestClient(hosted.RequestBodyLimit(_body_reading_app(calls)))

    def test_oversize_content_length_is_413_before_the_handler(self):
        calls: list = []
        client = self._client(calls)
        response = client.post(
            "/mcp", content=b"x" * (hosted.MAX_REQUEST_BYTES + 1), headers=self.HEADERS
        )
        assert response.status_code == 413
        assert calls == []

    def test_oversize_streamed_body_is_413_and_never_answered_200(self):
        calls: list = []
        client = self._client(calls)

        def chunks():
            for _ in range(hosted.MAX_REQUEST_BYTES // 65536 + 2):
                yield b"x" * 65536

        response = client.post("/mcp", content=chunks(), headers=self.HEADERS)
        assert response.status_code == 413
        assert calls == []

    def test_body_at_the_cap_passes_through(self):
        calls: list = []
        client = self._client(calls)
        response = client.post(
            "/mcp", content=b"x" * hosted.MAX_REQUEST_BYTES, headers=self.HEADERS
        )
        assert response.status_code == 200
        assert calls == ["/mcp"]

    def test_cap_is_one_mib(self):
        assert hosted.MAX_REQUEST_BYTES == 1024 * 1024

    def test_serve_entrypoint_runs_the_bounded_app(self):
        source = (_SCRIPTS_DIR / "mcp_hosted" / "serve.py").read_text()
        assert "build_app()" in source
        assert "mcp.run(" not in source


class TestJwksRefreshThrottle:
    """An unknown kid forces a live JWKS refetch; anonymous callers must not
    be able to turn that into an unbounded upstream request stream."""

    @pytest.fixture
    def jwks_fetches(self, monkeypatch, rsa_keys):
        import jwt as pyjwt
        from jwt.algorithms import RSAAlgorithm

        jwk = RSAAlgorithm.to_jwk(rsa_keys[1], as_dict=True)
        jwk.update({"kid": "test-kid", "use": "sig", "alg": "RS256"})
        fetches: list = []

        def fetch_data(self):
            fetches.append(1)
            data = {"keys": [jwk]}
            self.jwk_set_cache.put(data)  # the real fetch_data caches too
            return data

        monkeypatch.setattr(pyjwt.PyJWKClient, "fetch_data", fetch_data)
        monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example.test/.well-known/jwks.json")
        monkeypatch.setenv("CLERK_ISSUER", "https://clerk.example.test")
        monkeypatch.setenv("ALLOWED_USER_IDS", OPERATOR_ID)
        monkeypatch.setattr(mcp_auth, "_jwks_client", None)
        monkeypatch.setattr(mcp_auth, "_jwks_refresh_after", 0.0)
        return fetches

    def test_unknown_kids_get_one_refetch_per_window(
        self, jwks_fetches, rsa_keys, monkeypatch
    ):
        clock = [1000.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        assert resolve_principal(operator_bearer(rsa_keys)).is_operator
        assert len(jwks_fetches) == 1  # the initial JWKS load

        for kid in ("rotated-1", "rotated-2"):
            token = _mint(rsa_keys, {"sub": OPERATOR_ID}, headers={"kid": kid})
            with pytest.raises(AuthError) as exc:
                resolve_principal("Bearer " + token)
            assert exc.value.status == 401
        assert len(jwks_fetches) == 2  # one live refetch for the pair

        # a known kid keeps verifying inside the window, with no fetch
        assert resolve_principal(operator_bearer(rsa_keys)).is_operator
        assert len(jwks_fetches) == 2

        clock[0] += mcp_auth.JWKS_REFRESH_COOLDOWN_SECONDS
        token = _mint(rsa_keys, {"sub": OPERATOR_ID}, headers={"kid": "rotated-3"})
        with pytest.raises(AuthError):
            resolve_principal("Bearer " + token)
        assert len(jwks_fetches) == 3

    def test_cooldown_mirrors_fastapi_negative_ttl(self):
        from api import auth as api_auth

        assert mcp_auth.JWKS_REFRESH_COOLDOWN_SECONDS == api_auth.JWKS_NEGATIVE_TTL_SECONDS


class TestUpstreamReadsLeaveTheEventLoop:
    @pytest.fixture
    def request_threads(self, monkeypatch):
        import requests

        seen: list = []

        def fake_get(url, headers=None, timeout=None):
            seen.append(threading.current_thread())
            return SimpleNamespace(status_code=200, content=b'{"ok": true}')

        monkeypatch.setattr(requests, "get", fake_get)
        return seen

    def test_public_docs_read_runs_in_a_worker_thread(self, request_threads):
        async def main():
            result = await hosted.radon_docs("llms.txt")
            assert result["markdown"] == '{"ok": true}'
            assert request_threads[0] is not threading.current_thread()

        asyncio.run(main())

    def test_gated_operator_read_runs_in_a_worker_thread(
        self, request_threads, clerk_env, rsa_keys
    ):
        request = SimpleNamespace(headers={"authorization": operator_bearer(rsa_keys)})
        ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))

        async def main():
            result = await hosted.operator_portfolio(ctx)
            assert result == {"data": {"ok": True}}
            assert request_threads[0] is not threading.current_thread()

        asyncio.run(main())
