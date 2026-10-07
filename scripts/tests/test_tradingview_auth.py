import asyncio
import json
import os
import time

import httpx
import pytest
from mcp.shared.auth import OAuthToken, OAuthClientInformationFull
from clients.tradingview_auth import FileTokenStorage, build_oauth_provider, TradingViewAuthError

@pytest.mark.asyncio
async def test_secure_storage_and_expiry(tmp_path):
    path = tmp_path / "token.json"
    storage = FileTokenStorage(path)
    await storage.set_client_info(OAuthClientInformationFull(client_id="owned", redirect_uris=["http://127.0.0.1:1234/callback"]))
    await storage.set_tokens(OAuthToken(access_token="secret", refresh_token="refresh", expires_in=60))
    original = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert (await storage.get_client_info()).client_id == "owned"
    assert (await storage.get_tokens()).access_token == "secret"
    assert path.read_bytes() == original
    assert "secret" not in str(storage.status())

@pytest.mark.asyncio
async def test_symlink_refused(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("{}")
    link = tmp_path / "link"
    link.symlink_to(victim)
    with pytest.raises(TradingViewAuthError):
        await FileTokenStorage(link).get_tokens()

@pytest.mark.asyncio
async def test_missing_credentials_no_network(tmp_path):
    provider = build_oauth_provider(tmp_path / "missing")
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(401)
    async with httpx.AsyncClient(auth=provider, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TradingViewAuthError, match="authorization"):
            await client.get("https://mcp.tradingview.com/mcp")
    assert calls == []

@pytest.mark.asyncio
async def test_expired_token_refresh_persisted(tmp_path):
    path = tmp_path / "token"
    storage = FileTokenStorage(path)
    await storage.set_client_info(OAuthClientInformationFull(client_id="owned", redirect_uris=["http://127.0.0.1:1234/callback"]))
    await storage.set_tokens(OAuthToken(access_token="old", refresh_token="refresh", expires_in=1))
    data = json.loads(path.read_text()); data["expires_at"] = time.time()-10; path.write_text(json.dumps(data)); os.chmod(path, 0o600)
    from test_tradingview_mcp_protocol import _authorization_metadata
    data["oauth_metadata"] = _authorization_metadata()
    storage._write(data)
    calls=[]
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/token":
            assert request.url.host == "auth.example"
            assert request.headers.get("user-agent") == "radon/2.0"
            return httpx.Response(200, json={"access_token":"new","expires_in":60,"token_type":"Bearer"})
        assert request.headers["authorization"] == "Bearer new"
        return httpx.Response(200)
    async with httpx.AsyncClient(auth=build_oauth_provider(path), transport=httpx.MockTransport(handler)) as client:
        await client.get("https://mcp.tradingview.com/mcp")
    assert calls == ["/token", "/mcp"]
    assert (await storage.get_tokens()).refresh_token == "refresh"

@pytest.mark.asyncio
async def test_unauthorized_never_registers(tmp_path):
    path=tmp_path / "token"; storage=FileTokenStorage(path)
    await storage.set_tokens(OAuthToken(access_token="old", expires_in=60))
    calls=[]
    async def handler(request):
        calls.append(request.url.path); return httpx.Response(401)
    async with httpx.AsyncClient(auth=build_oauth_provider(path), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TradingViewAuthError):
            await client.get("https://mcp.tradingview.com/mcp")
    assert calls == ["/mcp"]

@pytest.mark.asyncio
async def test_revoked_refresh_stops_before_discovery(tmp_path):
    path = tmp_path / "token"
    storage = FileTokenStorage(path)
    await storage.set_client_info(OAuthClientInformationFull(client_id="owned", redirect_uris=["http://127.0.0.1:1234/callback"]))
    await storage.set_tokens(OAuthToken(access_token="old", refresh_token="refresh", expires_in=0))
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={"error": "invalid_grant", "private": "never printed"})
    async with httpx.AsyncClient(auth=build_oauth_provider(path), transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TradingViewAuthError) as error:
            await client.get("https://mcp.tradingview.com/mcp")
    assert error.value.code == "TV_AUTH_REQUIRED"
    assert "never printed" not in str(error.value)
    assert calls == ["/token"]

@pytest.mark.asyncio
async def test_sdk_pkce_and_state_validation(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    provider = build_oauth_provider(tmp_path / "token", interactive=True)
    provider.context.client_info = OAuthClientInformationFull(client_id="owned", redirect_uris=["http://127.0.0.1/callback"])
    urls = []
    async def redirect(url):
        urls.append(url)
    async def callback():
        return "test-code", parse_qs(urlsplit(urls[0]).query)["state"][0]
    provider.context.redirect_handler = redirect
    provider.context.callback_handler = callback
    code, verifier = await provider._perform_authorization_code_grant()
    params = parse_qs(urlsplit(urls[0]).query)
    assert code == "test-code" and len(verifier) >= 43
    assert params["code_challenge_method"] == ["S256"]
    async def wrong_state():
        return "test-code", "wrong"
    provider.context.callback_handler = wrong_state
    with pytest.raises(Exception, match="State parameter mismatch"):
        await provider._perform_authorization_code_grant()

@pytest.mark.asyncio
async def test_cancelled_consent_preserves_existing_pair(tmp_path, monkeypatch):
    import clients.tradingview_auth as auth
    path = tmp_path / "token"
    storage = FileTokenStorage(path)
    await storage.set_tokens(OAuthToken(access_token="old", expires_in=60))
    original = path.read_bytes()
    async def cancel(stage):
        await FileTokenStorage(stage).set_client_info(OAuthClientInformationFull(client_id="new", redirect_uris=["http://127.0.0.1/callback"]))
        raise asyncio.CancelledError
    monkeypatch.setattr(auth, "_authorize_staged", cancel)
    with pytest.raises(asyncio.CancelledError):
        await auth.authorize(path)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".tradingview-consent-*"))

@pytest.mark.asyncio
async def test_concurrent_requests_refresh_once(tmp_path):
    path = tmp_path / "token"
    storage = FileTokenStorage(path)
    await storage.set_client_info(OAuthClientInformationFull(client_id="owned", redirect_uris=["http://127.0.0.1/callback"]))
    await storage.set_tokens(OAuthToken(access_token="old", refresh_token="refresh", expires_in=0))
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/token":
            await asyncio.sleep(.1)
            return httpx.Response(200, json={"access_token":"new", "expires_in":60,"token_type":"Bearer"})
        assert request.headers["authorization"] == "Bearer new"
        return httpx.Response(200)
    async def request():
        async with httpx.AsyncClient(auth=build_oauth_provider(path), transport=httpx.MockTransport(handler)) as client:
            await client.get("https://mcp.tradingview.com/mcp")
    await asyncio.gather(request(), request())
    assert calls.count("/token") == 1

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "browser", "timeout", "resource", "empty", "state"])
@pytest.mark.filterwarnings("error:Pydantic serializer warnings:UserWarning")
async def test_authorize_real_loopback_transaction(tmp_path, monkeypatch, failure):
    from urllib.parse import parse_qs, urlsplit, urlencode
    import clients.tradingview_auth as auth
    from test_tradingview_mcp_protocol import _resource_metadata, _authorization_metadata
    target = tmp_path / "tokens"
    await FileTokenStorage(target).set_tokens(OAuthToken(access_token="previous", expires_in=3600))
    original = target.read_bytes()
    requests = []
    callbacks = []
    jobs = []
    original_client = httpx.AsyncClient

    async def handler(request):
        requests.append(request.url.path)
        if request.headers.get("user-agent") != "radon/2.0":
            return httpx.Response(403)
        if request.url.path == "/mcp":
            if request.headers.get("authorization"):
                return httpx.Response(500 if failure == "resource" else 200, json={"ok": True})
            return httpx.Response(401, headers={"WWW-Authenticate":
                'Bearer resource_metadata="https://mcp.tradingview.com/.well-known/oauth-protected-resource"'})
        if request.url.path == "/.well-known/oauth-protected-resource":
            return httpx.Response(200, json=_resource_metadata())
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=_authorization_metadata())
        if request.url.path == "/register":
            registration = json.loads(request.content)
            return httpx.Response(201, json={"client_id":"owned-new", "redirect_uris":registration["redirect_uris"],
                                           "token_endpoint_auth_method":"none"})
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert "code_verifier" in form
            return httpx.Response(200, json={"access_token":"issued", "token_type":"Bearer", "expires_in":3600})
        pytest.fail("Unexpected protocol request")

    def client_factory(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    async def send_callback(params):
        redirect = urlsplit(params["redirect_uri"][0])
        assert redirect.hostname == "127.0.0.1" and redirect.port
        callbacks.append(redirect.port)
        reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
        writer.write(b"GET /\xff HTTP/1.1\r\n\r\n")
        await writer.drain()
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
        for target_path in ("/wrong", "/callback?code=x&code=y&state=x", "/callback?" + urlencode({
            "code":"consent-test", "state":"wrong" if failure == "state" else params["state"][0]})):
            reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
            writer.write(f"GET {target_path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
            await writer.drain()
            response = await reader.read()
            assert response.startswith(b"HTTP/1.1 200" if target_path.startswith("/callback?code=consent-test") else b"HTTP/1.1 400")
            writer.close()
            await writer.wait_closed()

    def open_browser(url):
        if failure == "browser":
            return False
        params = parse_qs(urlsplit(url).query)
        assert params["code_challenge_method"] == ["S256"]
        if failure != "timeout":
            jobs.append(asyncio.create_task(send_callback(params)))
        return True

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(auth.webbrowser, "open", open_browser)
    monkeypatch.setattr(auth, "AUTH_TIMEOUT", .3 if failure == "timeout" else 5)
    if failure == "empty":
        real_status = auth.configuration_status
        monkeypatch.setattr(auth, "configuration_status", lambda path: {**real_status(path), "configured": False})
    if failure:
        with pytest.raises(TradingViewAuthError) as caught:
            await auth.authorize(target)
        assert caught.value.code == "TV_AUTH_FAILED"
        assert target.read_bytes() == original
    else:
        result = await auth.authorize(target)
        assert result["configured"] and not result["expired"]
        saved = json.loads(target.read_text())
        assert saved["tokens"]["access_token"] == "issued"
        assert saved["client_info"]["client_id"] == "owned-new"
        assert saved["oauth_metadata"]["token_endpoint"] == "https://auth.example/token"
    await asyncio.gather(*jobs)
    for port in callbacks:
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)
    assert not list(tmp_path.glob(".tradingview-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["not json", "[]", '{"tokens":[]}', '{"tokens":{"expires_in":4}}',
                                 '{"client_info":{"client_id":"x","redirect_uris":[]}}'])
async def test_corrupt_storage_is_safe_error(tmp_path, data):
    path = tmp_path / "token"
    path.write_text(data)
    os.chmod(path, 0o600)
    storage = FileTokenStorage(path)
    with pytest.raises(TradingViewAuthError) as caught:
        if "client_info" in data:
            await storage.get_client_info()
        else:
            await storage.get_tokens()
    assert caught.value.code == "TV_AUTH_STORAGE"


@pytest.mark.parametrize("expiry", [True, "tomorrow", float("inf"), float("nan")])
def test_invalid_expiry(tmp_path, expiry):
    path = tmp_path / "token"
    path.write_text(json.dumps({"expires_at":expiry}))
    os.chmod(path, 0o600)
    with pytest.raises(TradingViewAuthError):
        FileTokenStorage(path).status()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unsafe_token", "directory_token", "unsafe_lock", "symlink_lock"])
async def test_unsafe_files_refused(tmp_path, kind):
    path = tmp_path / "token"
    if kind == "unsafe_token":
        path.write_text("{}"); os.chmod(path, 0o644)
    elif kind == "directory_token":
        path.mkdir()
    elif kind == "unsafe_lock":
        lock = tmp_path / "token.lock"; lock.write_text(""); os.chmod(lock, 0o644)
    else:
        (tmp_path / "token.lock").symlink_to(tmp_path / "victim")
    with pytest.raises(TradingViewAuthError):
        if kind.endswith("token"):
            await FileTokenStorage(path).get_tokens()
        else:
            await FileTokenStorage(path).acquire()


@pytest.mark.asyncio
async def test_atomic_write_failure_preserves_credentials(tmp_path, monkeypatch):
    import clients.tradingview_auth as auth
    path = tmp_path / "token"
    storage = FileTokenStorage(path)
    await storage.set_tokens(OAuthToken(access_token="previous"))
    original = path.read_bytes()
    def fail_replace(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(auth.os, "replace", fail_replace)
    with pytest.raises(OSError):
        await storage.set_tokens(OAuthToken(access_token="replacement"))
    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".tradingview-*"))
    assert storage.status()["expires_at"] is None


@pytest.mark.asyncio
async def test_cancelled_lock_wait_closes_descriptor(tmp_path, monkeypatch):
    import clients.tradingview_auth as auth
    storage = FileTokenStorage(tmp_path / "token")
    held = await storage.acquire()
    real_close = os.close
    closed = []
    def close(fd):
        closed.append(fd)
        real_close(fd)
    monkeypatch.setattr(auth.os, "close", close)
    pending = asyncio.create_task(storage.acquire())
    await asyncio.sleep(.01)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(closed) == 1
    auth.fcntl.flock(held, auth.fcntl.LOCK_UN)
    os.close(held)


def test_sdk_absence_is_lazy_coded_error(tmp_path, monkeypatch):
    import builtins
    import clients.tradingview_auth as auth
    original_import = builtins.__import__
    def importing(name, *args, **kwargs):
        if name.startswith("mcp"):
            raise ImportError("optional missing")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", importing)
    assert not auth.configuration_status(tmp_path / "missing")["configured"]
    with pytest.raises(TradingViewAuthError) as caught:
        auth.build_oauth_provider(tmp_path / "missing")
    assert caught.value.code == "TV_DEPENDENCY_MISSING"


@pytest.mark.asyncio
async def test_contended_lock_deadline_is_coded(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import clients.tradingview_auth as auth
    storage = FileTokenStorage(tmp_path / "token")
    held = await storage.acquire()
    clock = iter([0, 31])
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    try:
        with pytest.raises(TradingViewAuthError) as caught:
            await storage.acquire()
        assert caught.value.code == "TV_AUTH_BUSY"
    finally:
        auth.fcntl.flock(held, auth.fcntl.LOCK_UN)
        os.close(held)


@pytest.mark.asyncio
async def test_unauthorized_refresh_keeps_honest_user_agent(tmp_path):
    path = tmp_path / "tokens"
    storage = FileTokenStorage(path)
    await storage.set_client_info(OAuthClientInformationFull(client_id="owned", redirect_uris=["http://127.0.0.1/callback"]))
    await storage.set_tokens(OAuthToken(access_token="old", refresh_token="refresh", expires_in=3600))
    calls = []
    async def handler(request):
        calls.append(request.url.path)
        if request.headers.get("user-agent") != "radon/2.0":
            return httpx.Response(403)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token":"new", "token_type":"Bearer", "expires_in":60})
        return httpx.Response(401 if request.headers.get("authorization") == "Bearer old" else 200)
    async with httpx.AsyncClient(auth=build_oauth_provider(path), transport=httpx.MockTransport(handler)) as client:
        response = await client.get("https://mcp.tradingview.com/mcp")
    assert response.status_code == 200
    assert calls == ["/mcp", "/token", "/mcp"]


@pytest.mark.asyncio
async def test_credential_write_fsyncs_file_and_directory(tmp_path, monkeypatch):
    import stat
    import clients.tradingview_auth as auth
    real_fsync = os.fsync
    synced = []
    def fsync(fd):
        synced.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)
    monkeypatch.setattr(auth.os, "fsync", fsync)
    path = tmp_path / "token"
    await FileTokenStorage(path).set_tokens(OAuthToken(access_token="owned", expires_in=60))
    assert synced == ["file", "directory"]
    assert path.stat().st_mode & 0o777 == 0o600
