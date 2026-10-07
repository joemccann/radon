"""Independent OAuth protocol boundary tests against pinned MCP SDK 1.28.1."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import tradingview_auth as auth  # noqa: E402


def run(awaitable):
    return asyncio.run(awaitable)


def _resource_metadata(resource: str = "https://mcp.tradingview.com/mcp", auth_server: str = "https://auth.example"):
    return {"resource": resource, "authorization_servers": [auth_server], "scopes_supported": ["research"]}


def _authorization_metadata():
    return {
        "issuer": "https://auth.example",
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "https://auth.example/register",
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
    }


def test_full_sdk_interactive_discovery_dcr_pkce_and_token_flow(tmp_path):
    """Drive SDK discovery, DCR, PKCE authorization and final resource retry."""
    token_file = tmp_path / "tokens.json"
    provider = auth.build_oauth_provider(token_file, interactive=True)
    redirects: list[str] = []
    requests: list[tuple[httpx.URL, dict[str, str]]] = []

    async def redirect(url: str):
        redirects.append(url)

    async def callback():
        params = parse_qs(urlsplit(redirects[-1]).query)
        return "code-for-protocol-test", params["state"][0]

    provider.context.redirect_handler = redirect
    provider.context.callback_handler = callback

    async def handler(request: httpx.Request):
        # The SDK mutates/reuses the original resource request when adding its
        # bearer token after the code exchange; snapshot headers at send time.
        requests.append((request.url, dict(request.headers)))
        if request.url.path == "/mcp":
            if request.headers.get("authorization") == "Bearer protocol-access":
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate":
                    'Bearer resource_metadata="https://mcp.tradingview.com/.well-known/oauth-protected-resource" scope="research"'
                },
            )
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json=_resource_metadata())
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=_authorization_metadata())
        if request.url.path == "/register":
            registration = json.loads(request.content)
            assert registration["token_endpoint_auth_method"] == "none"
            assert urlsplit(registration["redirect_uris"][0]).hostname == "127.0.0.1"
            return httpx.Response(201, json={
                "client_id": "client-for-protocol-test",
                "redirect_uris": registration["redirect_uris"],
                "token_endpoint_auth_method": "none",
            })
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code"] == ["code-for-protocol-test"]
            assert form["client_id"] == ["client-for-protocol-test"]
            assert urlsplit(form["redirect_uri"][0]).hostname == "127.0.0.1"
            verifier = form["code_verifier"][0]
            params = parse_qs(urlsplit(redirects[-1]).query)
            expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            assert params["code_challenge_method"] == ["S256"]
            assert params["code_challenge"] == [expected]
            assert "authorization" not in request.headers
            return httpx.Response(200, json={
                "access_token": "protocol-access", "refresh_token": "protocol-refresh",
                "token_type": "Bearer", "expires_in": 3600,
            })
        raise AssertionError(f"unexpected OAuth URL: {request.url}")

    async def exercise():
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(handler)) as client:
            response = await client.get(auth.SERVER_URL)
            assert response.status_code == 200

    run(exercise())

    assert len(redirects) == 1
    assert urlsplit(redirects[0]).netloc == "auth.example"
    stored = json.loads(token_file.read_text())
    assert stored["tokens"]["access_token"] == "protocol-access"
    assert stored["tokens"]["refresh_token"] == "protocol-refresh"
    assert stored["client_info"]["client_id"] == "client-for-protocol-test"
    assert stored["oauth_metadata"]["issuer"] == "https://auth.example/"
    assert [url.path for url, _ in requests] == [
        "/mcp", "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server", "/register", "/token", "/mcp",
    ]
    # A bearer credential is sent only to the fixed resource, on its final retry.
    assert [headers.get("authorization") for _, headers in requests] == [
        None, None, None, None, None, "Bearer protocol-access",
    ]


def test_state_mismatch_stops_before_exchange_and_suppresses_sdk_error_logs(tmp_path, caplog):
    provider = auth.build_oauth_provider(tmp_path / "tokens.json", interactive=True)
    redirects: list[str] = []
    requests: list[tuple[httpx.URL, dict[str, str]]] = []

    async def redirect(url):
        redirects.append(url)

    async def callback():
        return "authorization-code-SENTINEL", "wrong-state-SENTINEL"

    provider.context.redirect_handler = redirect
    provider.context.callback_handler = callback

    async def handler(request):
        requests.append((request.url, dict(request.headers)))
        if request.url.path == "/mcp":
            return httpx.Response(401, headers={
                "WWW-Authenticate":
                'Bearer resource_metadata="https://mcp.tradingview.com/.well-known/oauth-protected-resource"'
            })
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json=_resource_metadata())
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=_authorization_metadata())
        if request.url.path == "/register":
            registration = json.loads(request.content)
            return httpx.Response(201, json={
                "client_id": "client-state-test",
                "redirect_uris": registration["redirect_uris"],
                "token_endpoint_auth_method": "none",
            })
        raise AssertionError(f"state mismatch must stop before {request.url}")

    async def exercise():
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(handler)) as client:
            await client.get(auth.SERVER_URL)

    with pytest.raises(auth.TradingViewAuthError) as caught:
        run(exercise())

    assert caught.value.code == "TV_AUTH_FAILED"
    assert [url.path for url, _ in requests] == [
        "/mcp", "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server", "/register",
    ]
    assert "SENTINEL" not in str(caught.value)
    assert "SENTINEL" not in caplog.text
    staged_state = json.loads((tmp_path / "tokens.json").read_text())
    assert "tokens" not in staged_state


def test_mismatched_protected_resource_fails_before_cross_origin_auth(tmp_path):
    provider = auth.build_oauth_provider(tmp_path / "tokens.json", interactive=True)
    requests: list[tuple[httpx.URL, dict[str, str]]] = []

    async def handler(request):
        requests.append((request.url, dict(request.headers)))
        if request.url.path == "/mcp":
            return httpx.Response(401, headers={
                "WWW-Authenticate":
                'Bearer resource_metadata="https://mcp.tradingview.com/.well-known/oauth-protected-resource"'
            })
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json=_resource_metadata(
                resource="https://evil.example/private", auth_server="https://evil.example"
            ))
        raise AssertionError(f"resource mismatch must stop before {request.url}")

    async def exercise():
        async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(handler)) as client:
            await client.get(auth.SERVER_URL)

    with pytest.raises(auth.TradingViewAuthError) as caught:
        run(exercise())
    assert caught.value.code == "TV_AUTH_FAILED"
    assert [url.path for url, _ in requests] == ["/mcp", "/.well-known/oauth-protected-resource"]
    assert all("authorization" not in headers for _, headers in requests)
    assert all(url.host != "evil.example" for url, _ in requests)


def test_unauthorized_existing_access_token_refreshes_once(tmp_path):
    path = tmp_path / "tokens.json"
    storage = auth.FileTokenStorage(path)
    run(storage.set_client_info(OAuthClientInformationFull(
        client_id="stored-client", redirect_uris=["http://127.0.0.1:12345/callback"],
        token_endpoint_auth_method="none",
    )))
    run(storage.set_tokens(OAuthToken(
        access_token="old-access", refresh_token="refresh-secret", expires_in=3600,
    )))
    requests: list[tuple[httpx.URL, dict[str, str]]] = []

    async def handler(request):
        requests.append((request.url, dict(request.headers)))
        if request.url.path == "/mcp" and request.headers.get("authorization") == "Bearer old-access":
            return httpx.Response(401)
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form == {
                "grant_type": ["refresh_token"], "refresh_token": ["refresh-secret"],
                "client_id": ["stored-client"],
            }
            return httpx.Response(200, json={
                "access_token": "new-access", "token_type": "Bearer", "expires_in": 3600,
            })
        if request.url.path == "/mcp" and request.headers.get("authorization") == "Bearer new-access":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request {request.url}")

    async def exercise():
        async with httpx.AsyncClient(
            auth=auth.build_oauth_provider(path), transport=httpx.MockTransport(handler)
        ) as client:
            response = await client.get(auth.SERVER_URL)
            assert response.status_code == 200

    run(exercise())
    assert [url.path for url, _ in requests] == ["/mcp", "/token", "/mcp"]
    assert (run(storage.get_tokens())).access_token == "new-access"
    assert (run(storage.get_tokens())).refresh_token == "refresh-secret"


def test_expired_access_token_without_refresh_fails_before_network(tmp_path):
    path = tmp_path / "tokens.json"
    storage = auth.FileTokenStorage(path)
    run(storage.set_client_info(OAuthClientInformationFull(
        client_id="stored-client", redirect_uris=["http://127.0.0.1:12345/callback"],
    )))
    run(storage.set_tokens(OAuthToken(access_token="expired-access", expires_in=0)))
    requests: list[tuple[httpx.URL, dict[str, str]]] = []

    async def handler(request):
        requests.append((request.url, dict(request.headers)))
        return httpx.Response(401)

    async def exercise():
        async with httpx.AsyncClient(
            auth=auth.build_oauth_provider(path), transport=httpx.MockTransport(handler)
        ) as client:
            await client.get(auth.SERVER_URL)

    with pytest.raises(auth.TradingViewAuthError) as caught:
        run(exercise())
    assert caught.value.code == "TV_AUTH_REQUIRED"
    assert requests == []


def test_successful_auth_staging_commits_only_after_authorization(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    storage = auth.FileTokenStorage(path)
    run(storage.set_tokens(OAuthToken(access_token="old-access", expires_in=3600)))
    original = path.read_bytes()

    async def authorize_stage(stage_path):
        staged = auth.FileTokenStorage(stage_path)
        await staged.set_client_info(OAuthClientInformationFull(
            client_id="new-client", redirect_uris=["http://127.0.0.1:9876/callback"],
        ))
        await staged.set_tokens(OAuthToken(
            access_token="new-access", refresh_token="new-refresh", expires_in=3600,
        ))
        assert path.read_bytes() == original

    monkeypatch.setattr(auth, "_authorize_staged", authorize_stage)
    status = run(auth.authorize(path))
    assert status["configured"] is True
    saved = json.loads(path.read_text())
    assert saved["tokens"]["access_token"] == "new-access"
    assert saved["tokens"]["refresh_token"] == "new-refresh"
    assert saved["client_info"]["client_id"] == "new-client"
    assert not list(tmp_path.glob(".tradingview-consent-*"))
