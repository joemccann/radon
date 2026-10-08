"""Owned TradingView OAuth credentials; imports never initiate authorization."""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import math
from contextvars import ContextVar
import os
from pathlib import Path
import stat
import tempfile
import time
from urllib.parse import parse_qs, urlsplit
import webbrowser
from utils.atomic_io import atomic_save

SERVER_URL = "https://mcp.tradingview.com/mcp"
AUTH_TIMEOUT = 180
_private_flow = ContextVar("tradingview_private_oauth", default=False)


class _PrivateOAuthFilter(logging.Filter):
    def filter(self, record):
        return not _private_flow.get()


class TradingViewAuthError(RuntimeError):
    def __init__(self, code="TV_AUTH_REQUIRED", message="TradingView authorization required"):
        self.code = code
        super().__init__(message)


def _sdk():
    try:
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata, OAuthClientInformationFull, OAuthToken
        for name in ("mcp.client.auth.oauth2", "mcp.client.auth.utils", "httpx", "httpcore"):
            logger = logging.getLogger(name)
            if not any(isinstance(item, _PrivateOAuthFilter) for item in logger.filters):
                logger.addFilter(_PrivateOAuthFilter())
        return OAuthClientProvider, OAuthClientMetadata, OAuthClientInformationFull, OAuthToken
    except ImportError:
        raise TradingViewAuthError("TV_DEPENDENCY_MISSING", "TradingView MCP dependency unavailable") from None


class FileTokenStorage:
    """Structural implementation of SDK TokenStorage, with absolute expiry."""
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("TRADINGVIEW_MCP_TOKEN_FILE") or
                         Path(__file__).resolve().parents[2] / "data/tradingview_mcp_token.json")

    def _read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        except OSError:
            raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential file unavailable") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential file must be owned and mode 0600")
            with os.fdopen(fd, "r") as stream:
                fd = None
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError
            if "tokens" in data and not isinstance(data["tokens"], dict):
                raise ValueError
            return data
        except (ValueError, TypeError, OSError):
            raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential file invalid") from None
        finally:
            if fd is not None:
                os.close(fd)

    def _write(self, data):
        self._read()  # Reject existing symlinks or unsafe files before replacement.
        atomic_save(str(self.path), data)

    async def get_tokens(self):
        data = self._read()
        if not data.get("tokens"):
            return None
        try:
            return _sdk()[3].model_validate(data["tokens"])
        except Exception:
            raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential file invalid") from None

    async def set_tokens(self, tokens):
        data = self._read()
        token_data = tokens.model_dump(mode="json")
        if not token_data.get("refresh_token"):
            token_data["refresh_token"] = data.get("tokens", {}).get("refresh_token")
        data["tokens"] = token_data
        data["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in is not None else None
        self._write(data)

    async def get_client_info(self):
        data = self._read()
        if not data.get("client_info"):
            return None
        try:
            return _sdk()[2].model_validate(data["client_info"])
        except Exception:
            raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential file invalid") from None

    async def set_client_info(self, client_info):
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json")
        self._write(data)

    def status(self):
        data = self._read()
        expiry = data.get("expires_at")
        if expiry is not None and (isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry)):
            raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential expiry invalid")
        return {"configured": bool(data.get("tokens", {}).get("access_token")),
                "expires_at": expiry, "expired": expiry is not None and expiry <= time.time()}

    async def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                os.close(fd)
                raise OSError
            deadline = time.monotonic() + 30
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(fd)
                        raise TradingViewAuthError("TV_AUTH_BUSY", "TradingView credential file busy")
                    try:
                        await asyncio.sleep(0.05)
                    except BaseException:
                        os.close(fd)
                        raise
        except OSError:
            raise TradingViewAuthError("TV_AUTH_STORAGE", "TradingView credential lock unavailable") from None


def configuration_status(token_file=None):
    return FileTokenStorage(token_file).status()


tradingview_status = configuration_status


def build_oauth_provider(token_file=None, interactive=False):
    SDKProvider, Metadata, _, _ = _sdk()
    storage = FileTokenStorage(token_file)

    class Provider(SDKProvider):
        async def _initialize(self):
            await super()._initialize()
            self.context.token_expiry_time = storage.status()["expires_at"]
            metadata = storage._read().get("oauth_metadata")
            if metadata:
                from mcp.shared.auth import OAuthMetadata
                self.context.oauth_metadata = OAuthMetadata.model_validate(metadata)
            if interactive:
                self.context.current_tokens = None

        async def _handle_token_response(self, response):
            await super()._handle_token_response(response)
            data = storage._read()
            if self.context.oauth_metadata:
                data["oauth_metadata"] = self.context.oauth_metadata.model_dump(mode="json")
                storage._write(data)

        async def async_auth_flow(self, request):
            fd = await storage.acquire()
            private_context = _private_flow.set(True)
            try:
                self._initialized = False  # Another process may have refreshed.
                await self._initialize()
                if not interactive and not self.context.current_tokens:
                    raise TradingViewAuthError("TV_NOT_CONFIGURED")
                if not interactive and not self.context.is_token_valid() and not self.context.can_refresh_token():
                    raise TradingViewAuthError()
                flow = super().async_auth_flow(request)
                outgoing = await flow.__anext__()
                try:
                    while True:
                        outgoing.headers["User-Agent"] = "radon/2.0"
                        response = yield outgoing
                        if not interactive and outgoing.method == "POST" and outgoing.url != request.url and response.status_code != 200:
                            raise TradingViewAuthError()
                        if not interactive and response.status_code in (401, 403):
                            if response.status_code == 401 and outgoing.url == request.url and self.context.can_refresh_token():
                                refresh_request = await self._refresh_token()
                                refresh_request.headers["User-Agent"] = "radon/2.0"
                                refresh_response = yield refresh_request
                                if await self._handle_refresh_response(refresh_response):
                                    self._add_auth_header(request)
                                    retried = yield request
                                    if retried.status_code not in (401, 403):
                                        return
                            raise TradingViewAuthError()
                        outgoing = await flow.asend(response)
                except StopAsyncIteration:
                    pass
                finally:
                    await flow.aclose()
            except TradingViewAuthError:
                raise
            except Exception:
                raise TradingViewAuthError("TV_AUTH_FAILED", "TradingView authorization failed") from None
            finally:
                _private_flow.reset(private_context)
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    return Provider(SERVER_URL, Metadata(client_name="Radon research", redirect_uris=["http://127.0.0.1/callback"],
                                         token_endpoint_auth_method="none"), storage, timeout=AUTH_TIMEOUT)


async def authorize(token_file=None):
    target = FileTokenStorage(token_file)
    fd = await target.acquire()
    stage_name = None
    try:
        stage_fd, stage_name = tempfile.mkstemp(prefix=".tradingview-consent-", dir=target.path.parent)
        os.close(stage_fd)
        os.unlink(stage_name)
        await _authorize_staged(stage_name)
        target._write(FileTokenStorage(stage_name)._read())
        return target.status()
    finally:
        for name in (() if stage_name is None else (stage_name, stage_name + ".lock")):
            try:
                os.unlink(name)
            except FileNotFoundError:
                pass
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


async def _authorize_staged(token_file):
    """Explicit operator consent using SDK discovery, registration, PKCE and state."""
    provider = build_oauth_provider(token_file, interactive=True)
    loop = asyncio.get_running_loop()
    callback = loop.create_future()

    async def accept(reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            line = raw.split(b"\r\n", 1)[0].decode("ascii").split()
            parsed = urlsplit(line[1]) if len(line) == 3 and line[0] == "GET" else None
            values = parse_qs(parsed.query) if parsed and parsed.path == "/callback" else {}
            if len(values.get("code", [])) == 1 and len(values.get("state", [])) == 1:
                if not callback.done():
                    callback.set_result((values["code"][0], values["state"][0]))
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 30\r\nConnection: close\r\n\r\nAuthorization callback received")
            else:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(accept, "127.0.0.1", 0, limit=8192)
    port = server.sockets[0].getsockname()[1]
    from pydantic import AnyUrl
    provider.context.client_metadata.redirect_uris = [AnyUrl(f"http://127.0.0.1:{port}/callback")]
    async def redirect(url):
        if not webbrowser.open(url):
            raise TradingViewAuthError("TV_AUTH_FAILED", "TradingView consent browser unavailable")

    async def receive():
        return await asyncio.wait_for(callback, AUTH_TIMEOUT)

    provider.context.redirect_handler = redirect
    provider.context.callback_handler = receive
    try:
        import httpx
        async with server, asyncio.timeout(AUTH_TIMEOUT):
            async with httpx.AsyncClient(auth=provider, timeout=30) as client:
                response = await client.post(SERVER_URL, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "radon-research", "version": "1"}}},
                    headers={"Accept": "application/json, text/event-stream"})
                if response.status_code not in (200, 202):
                    raise TradingViewAuthError("TV_AUTH_FAILED", "TradingView authorization failed")
        if not configuration_status(token_file)["configured"]:
            raise TradingViewAuthError("TV_AUTH_FAILED", "TradingView authorization failed")
        return configuration_status(token_file)
    except TradingViewAuthError:
        raise
    except Exception:
        raise TradingViewAuthError("TV_AUTH_FAILED", "TradingView authorization failed") from None
    finally:
        server.close()
        await server.wait_closed()
