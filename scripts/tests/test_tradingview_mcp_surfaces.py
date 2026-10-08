"""Operator CLI and private API contracts, with no vendor or credential access."""
import asyncio
import json
import sys

import pytest

import tradingview_mcp as cli
from api.routes import tradingview as routes


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def list_tools(self):
        return [{"name": "get_ohlcv"}]

    async def search_symbols(self, query, type_filter="all"):
        return [{"symbol": "SP:SPX", "description": query}]

    async def get_ohlcv(self, symbol, interval="1D", count=300):
        return {"source": "tradingview", "symbol": symbol, "interval": interval,
                "requested_count": count, "returned_count": 1,
                "history_complete": False, "bars": [{"timestamp": 1, "close": 7}]}

    async def call_tool(self, tool, arguments):
        return {"tool": tool, "arguments": arguments}


@pytest.fixture
def client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setattr(routes, "_client", lambda: FakeClient())
    monkeypatch.setattr(routes, "_status", lambda: {"configured": False, "expired": False, "expires_at": None})
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_cli_search_and_bars_are_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda args: FakeClient())
    assert cli.main(["search", "SPX"]) == 0
    assert json.loads(capsys.readouterr().out)["data"][0]["symbol"] == "SP:SPX"
    assert cli.main(["ohlcv", "SP:SPX", "--count", "5"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["requested_count"] == 5


def test_cli_call_invalid_json_never_reaches_client(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda args: pytest.fail("client constructed"))
    assert cli.main(["call", "get_ohlcv", "--arguments", "{"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "TV_INVALID_ARGUMENTS"


def test_cli_status_does_not_construct_client(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda args: pytest.fail("network client"))
    monkeypatch.setattr(cli, "_status", lambda path: {"configured": False})
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["configured"] is False


def test_cli_dependency_error_is_safe(monkeypatch, capsys):
    def missing(args):
        raise ImportError("credential-SENTINEL")
    monkeypatch.setattr(cli, "_client", missing)
    assert cli.main(["tools"]) == 1
    payload = capsys.readouterr().out
    assert "TV_DEPENDENCY" in payload and "credential-SENTINEL" not in payload


def test_status_unconfigured_is_legitimate_empty(client):
    response = client.get("/research/tradingview/status")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.headers["cache-control"] == "private, no-store"


def test_api_symbol_search_and_ohlcv_contract(client):
    response = client.get("/research/tradingview/symbols", params={"query": "SPX"})
    assert response.json()["data"][0]["symbol"] == "SP:SPX"
    response = client.get("/research/tradingview/ohlcv", params={"symbol": "SP:SPX", "count": 5000})
    assert response.status_code == 200
    assert response.json()["data"]["history_complete"] is False
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("count", [0, 5001])
def test_api_ohlcv_bounds(client, count):
    assert client.get("/research/tradingview/ohlcv", params={"symbol": "SP:SPX", "count": count}).status_code == 422


def test_api_call_forwards_read_arguments(client):
    response = client.post("/research/tradingview/call", json={"tool": "get_economic_data", "arguments": {"symbol": "ECONOMICS:USIRYY"}})
    assert response.json()["data"]["arguments"]["symbol"] == "ECONOMICS:USIRYY"


def test_api_oversized_arguments_rejected_before_client(client, monkeypatch):
    monkeypatch.setattr(routes, "_client", lambda: pytest.fail("network client"))
    response = client.post("/research/tradingview/call", json={"tool": "search_symbols", "arguments": {"query": "x" * 17000}})
    assert response.status_code == 422


def test_api_errors_hide_provider_body(client, monkeypatch):
    class UnsafeError(Exception):
        code = "TV_RATE_LIMITED"
    class RateLimited(FakeClient):
        async def search_symbols(self, *args, **kwargs):
            raise UnsafeError("secret-SENTINEL")
    monkeypatch.setattr(routes, "_client", lambda: RateLimited())
    response = client.get("/research/tradingview/symbols", params={"query": "SPX"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "TV_RATE_LIMITED"
    assert "secret-SENTINEL" not in response.text


def test_api_missing_configuration_is_http_200(client, monkeypatch):
    class MissingError(Exception):
        code = "TV_NOT_CONFIGURED"
    def missing():
        raise MissingError("token unavailable")
    monkeypatch.setattr(routes, "_client", missing)
    response = client.get("/research/tradingview/ohlcv", params={"symbol": "SP:SPX"})
    assert response.status_code == 200
    assert response.json()["missing"] is True


def test_api_capability_pins():
    from api.assistant_catalog import capability_for
    assert capability_for("GET", "/research/tradingview/status") == "read"
    for method, path in [("GET", "/research/tradingview/symbols"),
                         ("GET", "/research/tradingview/ohlcv"),
                         ("POST", "/research/tradingview/call")]:
        assert capability_for(method, path) == "read.spawn"


def test_cli_tools_and_call(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda args: FakeClient())
    assert cli.main(["tools"]) == 0
    assert json.loads(capsys.readouterr().out)["data"] == [{"name": "get_ohlcv"}]
    assert cli.main(["call", "search_symbols", "--arguments", '{"query":"NYSE"}']) == 0
    assert json.loads(capsys.readouterr().out)["data"]["arguments"] == {"query": "NYSE"}


@pytest.mark.parametrize("raw", ["[]", '{"n":NaN}', '{"query":"' + "x" * 17000 + '"}'])
def test_cli_bad_argument_objects(monkeypatch, capsys, raw):
    monkeypatch.setattr(cli, "_client", lambda args: pytest.fail("network client"))
    assert cli.main(["call", "search_symbols", "--arguments", raw]) == 2
    assert "TV_INVALID_ARGUMENTS" in capsys.readouterr().out


@pytest.mark.parametrize("code,status", [
    ("TV_TOOL_FORBIDDEN", 403), ("TV_INVALID_ARGUMENT", 422),
    ("TV_TIMEOUT", 504), ("TV_AUTH_REQUIRED", 503),
    ("TV_DEPENDENCY_MISSING", 503), ("TV_PROVIDER_ERROR", 502),
    ("TV_secret-SENTINEL", 502),
])
def test_api_error_classification(client, monkeypatch, code, status):
    class Error(Exception):
        pass
    error = Error("provider-SENTINEL")
    error.code = code
    def fail():
        raise error
    monkeypatch.setattr(routes, "_client", fail)
    response = client.get("/research/tradingview/symbols", params={"query": "SPX"})
    assert response.status_code == status
    assert "SENTINEL" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_cli_provider_errors_never_echo_raw_message(monkeypatch, capsys):
    class Error(Exception):
        code = "TV_AUTH_REQUIRED"
    def fail(args):
        raise Error("private-SENTINEL")
    monkeypatch.setattr(cli, "_client", fail)
    assert cli.main(["tools"]) == 1
    output = capsys.readouterr().out
    assert "TV_AUTH_REQUIRED" in output and "SENTINEL" not in output


def test_cli_auth_delegates_only_explicit_command(monkeypatch, capsys):
    from clients import tradingview_auth
    calls = []
    async def authorize(path):
        calls.append(path)
        return {"configured": True, "expired": False, "expires_at": 100}
    monkeypatch.setattr(tradingview_auth, "authorize", authorize)
    assert cli.main(["--token-file", "/test/private-store", "auth"]) == 0
    assert calls == ["/test/private-store"]
    assert json.loads(capsys.readouterr().out)["configured"] is True


def test_status_storage_failure_is_safe(client, monkeypatch):
    def fail():
        raise OSError("private-SENTINEL")
    monkeypatch.setattr(routes, "_status", fail)
    response = client.get("/research/tradingview/status")
    assert response.status_code == 502 and "SENTINEL" not in response.text


def test_real_server_research_routes_require_auth(monkeypatch):
    # asyncio.run in CLI tests closes the current loop. The existing broker
    # import requires a loop on Python 3.13; establish it before server import.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    from fastapi.testclient import TestClient
    from scripts.api import server, auth
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: False)
    monkeypatch.setattr(server, "verify_api_key", lambda request: None)
    monkeypatch.delenv("RADON_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("CLERK_JWKS_URL", "https://clerk.example/.well-known/jwks.json")
    monkeypatch.setattr(routes, "_client", lambda: pytest.fail("unauthorized network call"))
    monkeypatch.setattr(routes, "_status", lambda: pytest.fail("unauthorized credential read"))
    client = TestClient(server.app)
    for path in ["/status", "/symbols?query=SPX", "/ohlcv?symbol=SP:SPX"]:
        assert client.get("/research/tradingview" + path).status_code == 401
    assert client.post("/research/tradingview/call", json={"tool": "search_symbols"}).status_code == 401
    loop.close()
    asyncio.set_event_loop(None)


def test_real_surface_factories_are_lazy_and_status_is_local(monkeypatch, tmp_path, capsys):
    from clients.tradingview_client import TradingViewClient
    monkeypatch.setenv("TRADINGVIEW_MCP_TOKEN_FILE", str(tmp_path / "absent"))
    assert isinstance(routes._client(), TradingViewClient)
    assert routes._status()["configured"] is False
    assert cli.main(["--token-file", str(tmp_path / "absent"), "status"]) == 0
    assert json.loads(capsys.readouterr().out)["configured"] is False


def test_api_missing_dependency_is_safe(client, monkeypatch):
    def unavailable():
        raise ImportError("private-SENTINEL")
    monkeypatch.setattr(routes, "_client", unavailable)
    response = client.get("/research/tradingview/symbols", params={"query": "SPX"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "TV_DEPENDENCY"
    assert "SENTINEL" not in response.text


@pytest.mark.parametrize("error", [KeyboardInterrupt(), RuntimeError("private-SENTINEL")])
def test_cli_cancel_and_unknown_failures_are_safe(monkeypatch, capsys, error):
    def unavailable(args):
        raise error
    monkeypatch.setattr(cli, "_client", unavailable)
    assert cli.main(["tools"]) == 1
    output = capsys.readouterr().out
    assert "SENTINEL" not in output
    assert json.loads(output)["error"]["code"] in {"TV_CANCELLED", "TV_INTERNAL"}


def test_cli_untrusted_error_code_cannot_leak(monkeypatch, capsys):
    class Error(Exception):
        code = {"secret": "private-SENTINEL"}
    def unavailable(args):
        raise Error("private-SENTINEL")
    monkeypatch.setattr(cli, "_client", unavailable)
    assert cli.main(["tools"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"]["code"] == "TV_INTERNAL"
    assert "SENTINEL" not in output


def test_api_invalid_json_numbers_remain_safe_validation_errors(client, monkeypatch):
    monkeypatch.setattr(routes, "_client", lambda: pytest.fail("network client"))
    response = client.post("/research/tradingview/call",
                           content='{"tool":"get_ohlcv","arguments":{"count":NaN}}',
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json()["error"]["code"] == "TV_INVALID_ARGUMENT"


def test_api_validation_does_not_echo_private_arguments(client):
    response = client.post("/research/tradingview/call",
                           json={"tool": "PRIVATE-SENTINEL", "arguments": {}})
    assert response.status_code == 422
    assert "PRIVATE-SENTINEL" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
