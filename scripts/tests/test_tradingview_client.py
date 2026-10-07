"""TradingView research boundary exercised through the actual pinned MCP SDK."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clients import tradingview_client as tv


def run(awaitable):
    return asyncio.run(awaitable)


def bar(t=1791293400, **changes):
    value = {"t": t, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100}
    value.update(changes)
    return value


class FakeServer:
    """Mock HTTP responses, with real ClientSession and Streamable HTTP framing."""

    def __init__(self, payload=None, *, result=None, status=200, delay=0):
        self.payload = payload if payload is not None else {"bars": [bar()]}
        self.result = result
        self.status = status
        self.delay = delay
        self.calls = []
        self.requests = []
        self.catalog_results = None
        self.schema = {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "minLength": 3},
                "interval": {"type": "string", "enum": ["1D", "1W"]},
                "count": {"type": "integer", "minimum": 1, "maximum": 5000},
                "summary": {"type": "boolean"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        }
        self.tools = [
            {"name": "get_ohlcv", "inputSchema": self.schema},
            {"name": "search_symbols", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"},
                "type_filter": {"type": "string"}}, "required": ["query"],
                "additionalProperties": False,
            }},
            {"name": "delete_alert", "inputSchema": {"type": "object"}},
            {"name": "get_active_watchlist", "inputSchema": {"type": "object"}},
        ]

    async def handle(self, request):
        self.requests.append(request.method)
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        self.calls.append(body)
        method = body["method"]
        if method.startswith("notifications/"):
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "test-server", "version": "1"}}
        elif method == "tools/list":
            result = self.catalog_results.pop(0) if self.catalog_results is not None else {"tools": self.tools}
        elif method == "tools/call":
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.status != 200:
                return httpx.Response(self.status, text="Bearer secret-never-return")
            result = self.result if self.result is not None else {
                "content": [{"type": "text", "text": json.dumps(self.payload)}],
                "isError": False,
            }
        else:
            raise AssertionError(method)
        return httpx.Response(200, content=json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}),
                              headers={"Mcp-Session-Id": "test-session", "Content-Type": "application/json"})


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(tv, "_build_oauth_provider", lambda token_file: None)

    def make(server=None, **kwargs):
        server = server or FakeServer()
        return tv.TradingViewClient(http_transport=httpx.MockTransport(server.handle), **kwargs), server
    return make


def assert_code(code, awaitable):
    with pytest.raises(tv.TradingViewClientError) as caught:
        run(awaitable)
    assert caught.value.code == code
    assert "secret-never-return" not in str(caught.value)


def test_real_sdk_protocol_discovery_call_and_session_cleanup(make_client):
    client, server = make_client()
    result = run(client.get_ohlcv("INDEX:MAHN"))
    assert [x["method"] for x in server.calls][:4] == [
        "initialize", "notifications/initialized", "tools/list", "tools/call"]
    assert server.calls[-1]["params"] == {"name": "get_ohlcv", "arguments": {
        "symbol": "INDEX:MAHN", "interval": "1D", "count": 300, "summary": False}}
    assert "DELETE" in server.requests
    assert result["source"] == "tradingview"
    assert result["symbol"] == "INDEX:MAHN"
    assert result["interval"] == "1D"
    assert result["history_complete"] is False
    assert result["requested_count"] == 300
    assert result["returned_count"] == 1
    assert result["fetched_at"].endswith("+00:00")
    assert result["bars"] == [{"timestamp": 1791293400, "open": 10, "high": 12,
                               "low": 9, "close": 11, "volume": 100}]


@pytest.mark.parametrize("tool", ["create_alert", "delete_alert", "update_alert",
    "get_active_watchlist", "get_alerts_log", "get_news_story", "not_a_tool"])
def test_forbidden_tools_reject_before_auth_or_network(monkeypatch, tool):
    monkeypatch.setattr(tv, "_build_oauth_provider", lambda _: pytest.fail("auth reached"))
    assert_code("TV_TOOL_FORBIDDEN", tv.TradingViewClient().call_tool(tool, {}))


def test_discovery_does_not_widen_allowlist(make_client):
    client, _ = make_client()
    tools = run(client.list_tools())
    assert {x["name"] for x in tools} == {"get_ohlcv", "search_symbols"}


def test_live_input_schema_rejects_unknown_field_before_call(make_client):
    client, server = make_client()
    assert_code("TV_INVALID_ARGUMENT", client.call_tool("get_ohlcv", {
        "symbol": "INDEX:MAHN", "secret": "secret-never-return"}))
    assert not any(x["method"] == "tools/call" for x in server.calls)


def test_live_schema_changes_are_honored(make_client):
    server = FakeServer()
    server.schema["required"].append("interval")
    client, _ = make_client(server)
    assert_code("TV_INVALID_ARGUMENT", client.call_tool("get_ohlcv", {"symbol": "INDEX:MAHN"}))


def test_unavailable_allowlisted_tool_is_error(make_client):
    server = FakeServer()
    server.tools = []
    client, _ = make_client(server)
    assert_code("TV_TOOL_UNAVAILABLE", client.get_ohlcv("INDEX:MAHN"))


def test_structured_content_and_search(make_client):
    server = FakeServer(result={"content": [], "structuredContent": {
        "symbols": [{"symbol": "INDEX:MAHN", "description": "52-Week Highs NYSE"}]}})
    client, _ = make_client(server)
    assert run(client.search_symbols("NYSE", "index")) == {
        "symbols": [{"symbol": "INDEX:MAHN", "description": "52-Week Highs NYSE"}]}
    assert server.calls[-1]["params"]["arguments"] == {"query": "NYSE", "type_filter": "index"}


def test_empty_search_is_valid_but_empty_ohlcv_is_not(make_client):
    client, _ = make_client(FakeServer(payload=[]))
    assert run(client.search_symbols("missing")) == []
    client, _ = make_client(FakeServer(payload=[]))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


@pytest.mark.parametrize("result", [
    {"content": [{"type": "text", "text": "secret-never-return"}], "isError": True},
    {"content": [{"type": "text", "text": "not json secret-never-return"}]},
    {"content": []},
    {"content": [], "structuredContent": {"success": False, "error": "secret-never-return"}},
    {"content": [], "structuredContent": {"partial": True, "bars": [bar()]}},
    {"content": [], "structuredContent": {"error": "secret-never-return"}},
])
def test_provider_failures_and_malformed_content_stay_errors(make_client, result):
    client, _ = make_client(FakeServer(result=result))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


@pytest.mark.parametrize("changes", [
    {"o": True}, {"c": float("nan")}, {"h": float("inf")}, {"v": -1},
    {"h": 5}, {"l": 13}, {"t": True}, {"t": 1.5},
    {"t": "not-a-time"}, {"t": 253402300800}, {"c": None},
])
def test_bad_bars_rejected(make_client, changes):
    client, _ = make_client(FakeServer(payload={"bars": [bar(**changes)]}))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


def test_sort_and_null_volume_do_not_invent_session_date(make_client):
    client, _ = make_client(FakeServer(payload={"bars": [bar(1791379800, v=None), bar()]}))
    bars = run(client.get_ohlcv("INDEX:MAHN"))["bars"]
    assert [x["timestamp"] for x in bars] == [1791293400, 1791379800]
    assert bars[-1]["volume"] is None
    assert "date" not in bars[-1]


def test_pre1970_and_epoch_timestamps_are_valid(make_client):
    client, _ = make_client(FakeServer(payload={"bars": [bar(0), bar(-1279152000)]}))
    assert [x["timestamp"] for x in run(client.get_ohlcv("SP:SPX"))["bars"]] == [-1279152000, 0]


@pytest.mark.parametrize("payload", [{"bars": []}, {"bars": [bar(), bar()]},
    {"bars": "bad"}, {"bars": [bar()], "symbol": "SP:SPX"}, {"summary": {"close": 37}}])
def test_empty_duplicate_wrong_symbol_and_summary_only_fail(make_client, payload):
    client, _ = make_client(FakeServer(payload=payload))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


@pytest.mark.parametrize("count", [0, 5001, True, 1.5, "300"])
def test_count_bounds_reject_before_auth(monkeypatch, count):
    monkeypatch.setattr(tv, "_build_oauth_provider", lambda _: pytest.fail("auth reached"))
    assert_code("TV_INVALID_ARGUMENT", tv.TradingViewClient().get_ohlcv("INDEX:MAHN", count=count))


def test_provider_may_not_return_more_than_requested(make_client):
    client, _ = make_client(FakeServer(payload={"bars": [bar(), bar(1791379800)]}))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN", count=1))


@pytest.mark.parametrize("status,code", [(429, "TV_RATE_LIMITED"), (401, "TV_AUTH_REQUIRED"),
    (403, "TV_AUTH_REQUIRED"), (503, "TV_NETWORK_ERROR")])
def test_http_error_codes_are_sanitized(make_client, status, code):
    client, _ = make_client(FakeServer(status=status), timeout=.5)
    assert_code(code, client.get_ohlcv("INDEX:MAHN"))


def test_total_deadline(make_client):
    client, _ = make_client(FakeServer(delay=1), timeout=.03)
    assert_code("TV_TIMEOUT", client.get_ohlcv("INDEX:MAHN"))


def test_response_bytes_are_bounded(make_client, monkeypatch):
    monkeypatch.setattr(tv, "MAX_RESPONSE_BYTES", 1000)
    server = FakeServer(payload={"bars": [bar()], "padding": "secret-never-return" * 1000})
    client, _ = make_client(server, timeout=.5)
    assert_code("TV_RESPONSE_TOO_LARGE", client.get_ohlcv("INDEX:MAHN"))


def test_dependency_error_is_lazy_and_safe(monkeypatch):
    client = tv.TradingViewClient()
    original = importlib.import_module

    def missing(name, *args, **kwargs):
        if name == "mcp":
            raise ImportError("secret-never-return")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(tv.importlib, "import_module", missing)
    assert_code("TV_DEPENDENCY_MISSING", client.list_tools())


def test_provider_auth_error_is_preserved_without_message(monkeypatch):
    class AuthError(Exception):
        code = "TV_NOT_CONFIGURED"

    def fail(_):
        raise AuthError("secret-never-return")
    monkeypatch.setattr(tv, "_build_oauth_provider", fail)
    assert_code("TV_NOT_CONFIGURED", tv.TradingViewClient().list_tools())


def test_exception_alias_for_api():
    assert tv.TVClientError is tv.TradingViewClientError
    assert tv.TradingViewNotConfiguredError().code == "TV_NOT_CONFIGURED"


def test_external_schema_reference_is_never_fetched(make_client):
    server = FakeServer()
    server.schema["properties"]["symbol"] = {"$ref": "https://example.invalid/schema"}
    client, _ = make_client(server)
    assert_code("TV_SCHEMA_ERROR", client.get_ohlcv("INDEX:MAHN"))
    assert not any(x["method"] == "tools/call" for x in server.calls)


def test_changed_write_annotation_is_rejected(make_client):
    server = FakeServer()
    server.tools[0]["annotations"] = {"readOnlyHint": False}
    client, _ = make_client(server)
    assert_code("TV_TOOL_UNAVAILABLE", client.get_ohlcv("INDEX:MAHN"))


def test_provider_payload_is_not_logged_even_at_debug(make_client, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(FakeServer(payload={"bars": [bar()], "sensitive": "secret-never-return"}))
    run(client.get_ohlcv("INDEX:MAHN"))
    assert "secret-never-return" not in caplog.text


def test_huge_numeric_value_is_coded(make_client):
    client, _ = make_client(FakeServer(payload={"bars": [bar(t=10 ** 500)]}))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


@pytest.mark.parametrize("kwargs", [{"interval": []}, {"symbol": []}])
def test_wrong_argument_types_are_coded(make_client, kwargs):
    client, _ = make_client()
    arguments = {"symbol": "INDEX:MAHN", **kwargs}
    assert_code("TV_INVALID_ARGUMENT", client.get_ohlcv(**arguments))


def test_search_wrong_filter_type_is_coded(make_client):
    client, _ = make_client()
    assert_code("TV_INVALID_ARGUMENT", client.search_symbols("NYSE", []))


def test_configuration_status_uses_storage_only(monkeypatch):
    from clients import tradingview_auth
    monkeypatch.setattr(tradingview_auth, "configuration_status", lambda _: {
        "configured": False, "expires_at": None, "expired": False})
    monkeypatch.setattr(tv, "_build_oauth_provider", lambda _: pytest.fail("auth reached"))
    assert tv.configuration_status() == {"configured": False, "expires_at": None, "expired": False}


def test_compressed_expansion_cannot_bypass_byte_cap(make_client):
    import gzip
    server = FakeServer()
    original = server.handle

    async def compressed(request):
        assert request.headers["Accept-Encoding"] == "identity"
        response = await original(request)
        if request.method == "POST" and json.loads(request.content)["method"] == "tools/call":
            return httpx.Response(200, stream=httpx.ByteStream(gzip.compress(b"x" * (tv.MAX_RESPONSE_BYTES + 1))),
                                  headers={"content-encoding": "gzip", "content-type": "application/json"})
        return response
    server.handle = compressed
    client, _ = make_client(server)
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


def test_nonfinite_structured_research_payload_is_rejected(make_client):
    client, _ = make_client(FakeServer(result={"content": [], "structuredContent": {"value": float("nan")}}))
    assert_code("TV_PROVIDER_ERROR", client.search_symbols("NYSE"))


@pytest.mark.parametrize("count", [0, 5001, True, 1.5, "300"])
def test_generic_ohlcv_count_cap_precedes_auth(monkeypatch, count):
    monkeypatch.setattr(tv, "_build_oauth_provider", lambda _: pytest.fail("auth reached"))
    assert_code("TV_INVALID_ARGUMENT", tv.TradingViewClient().call_tool(
        "get_ohlcv", {"symbol": "INDEX:MAHN", "count": count}))


def test_paginated_discovery_uses_same_session_before_call(make_client):
    server = FakeServer()
    server.catalog_results = [
        {"tools": server.tools[1:], "nextCursor": "second-page"},
        {"tools": server.tools[:1]},
    ]
    client, _ = make_client(server)
    assert run(client.get_ohlcv("INDEX:MAHN"))["returned_count"] == 1
    lists = [call for call in server.calls if call["method"] == "tools/list"]
    assert len(lists) == 2
    assert lists[1]["params"]["cursor"] == "second-page"
    assert sum(call["method"] == "initialize" for call in server.calls) == 1
    assert server.calls[-1]["method"] == "tools/call"


def test_repeated_catalog_cursor_stops_discovery(make_client):
    server = FakeServer()
    server.catalog_results = [{"tools": [], "nextCursor": "loop"}] * 2
    client, _ = make_client(server)
    assert_code("TV_SCHEMA_ERROR", client.list_tools())
    assert len([call for call in server.calls if call["method"] == "tools/list"]) == 2


def test_catalog_page_limit_stops_unique_cursor_chain(make_client):
    server = FakeServer()
    server.catalog_results = [{"tools": [], "nextCursor": str(index)} for index in range(10)]
    client, _ = make_client(server)
    assert_code("TV_SCHEMA_ERROR", client.list_tools())
    assert len([call for call in server.calls if call["method"] == "tools/list"]) == 10


@pytest.mark.parametrize("kind", ["duplicate", "oversized"])
def test_invalid_catalog_size_or_duplicate_names_fail(make_client, kind):
    server = FakeServer()
    server.tools = [server.tools[0], server.tools[0]] if kind == "duplicate" else [
        {"name": f"untrusted_{index}", "inputSchema": {"type": "object"}} for index in range(201)]
    client, _ = make_client(server)
    assert_code("TV_SCHEMA_ERROR", client.list_tools())


@pytest.mark.parametrize("schema", [
    {"type": "not-a-json-schema-type"},
    {"type": "object", "properties": {"symbol": {"$ref": "#/$defs/missing"}}},
])
def test_invalid_or_unresolvable_local_schema_fails_safely(make_client, schema):
    server = FakeServer()
    server.tools[0]["inputSchema"] = schema
    client, _ = make_client(server)
    assert_code("TV_SCHEMA_ERROR", client.get_ohlcv("INDEX:MAHN"))
    assert not any(call["method"] == "tools/call" for call in server.calls)


def test_valid_local_schema_references_are_supported(make_client):
    server = FakeServer()
    server.schema["$defs"] = {"symbol": {"type": "string", "minLength": 3}}
    server.schema["properties"]["symbol"] = {"$ref": "#/$defs/symbol"}
    server.schema["allOf"] = [{"type": "object"}]
    client, _ = make_client(server)
    assert run(client.get_ohlcv("INDEX:MAHN"))["returned_count"] == 1


def test_destructive_annotation_overrides_safe_name(make_client):
    server = FakeServer()
    server.tools[0]["annotations"] = {"readOnlyHint": True, "destructiveHint": True}
    client, _ = make_client(server)
    assert_code("TV_TOOL_UNAVAILABLE", client.get_ohlcv("INDEX:MAHN"))


@pytest.mark.parametrize("timeout", [0, -1, 301, True, "30", None, float("nan"), float("inf")])
def test_invalid_deadlines_are_rejected_without_sdk(monkeypatch, timeout):
    monkeypatch.setattr(tv, "_sdk", lambda: pytest.fail("SDK reached"))
    with pytest.raises(tv.TradingViewClientError) as error:
        tv.TradingViewClient(timeout=timeout)
    assert error.value.code == "TV_INVALID_ARGUMENT"


@pytest.mark.parametrize("arguments", [[], None, "{}", {"bad": {1, 2}}, {"bad": float("nan")}])
def test_invalid_generic_arguments_precede_sdk(monkeypatch, arguments):
    monkeypatch.setattr(tv, "_sdk", lambda: pytest.fail("SDK reached"))
    assert_code("TV_INVALID_ARGUMENT", tv.TradingViewClient().call_tool("search_symbols", arguments))


def test_generic_argument_byte_limit_precedes_sdk(monkeypatch):
    monkeypatch.setattr(tv, "_sdk", lambda: pytest.fail("SDK reached"))
    assert_code("TV_INVALID_ARGUMENT", tv.TradingViewClient().call_tool(
        "search_symbols", {"query": "x" * (tv.MAX_ARGUMENT_BYTES + 1)}))


@pytest.mark.parametrize("failure,code", [
    (httpx.ReadTimeout, "TV_TIMEOUT"), (httpx.ConnectError, "TV_NETWORK_ERROR"),
])
def test_actual_transport_failures_are_sanitized(make_client, failure, code):
    server = FakeServer()
    original = server.handle

    async def fail(request):
        if request.method == "POST" and json.loads(request.content)["method"] == "tools/call":
            raise failure("secret-never-return", request=request)
        return await original(request)
    server.handle = fail
    client, _ = make_client(server)
    assert_code(code, client.get_ohlcv("INDEX:MAHN"))


def test_auth_factory_always_uses_noninteractive_mode(monkeypatch):
    from clients import tradingview_auth
    calls = []
    marker = object()

    def provider(**kwargs):
        calls.append(kwargs)
        return marker
    monkeypatch.setattr(tradingview_auth, "build_oauth_provider", provider)
    assert tv._build_oauth_provider("private-token-path") is marker
    assert calls == [{"token_file": "private-token-path", "interactive": False}]


@pytest.mark.parametrize("payload", [3, {}, ["malformed bar"]])
def test_scalar_empty_object_and_nonobject_bar_are_invalid(make_client, payload):
    client, _ = make_client(FakeServer(payload=payload))
    assert_code("TV_PROVIDER_ERROR", client.get_ohlcv("INDEX:MAHN"))


def test_raw_bar_array_is_normalized_without_summary_wrapper(make_client):
    client, _ = make_client(FakeServer(payload=[bar()]))
    assert run(client.get_ohlcv("INDEX:MAHN"))["bars"][0]["timestamp"] == 1791293400


def test_jsonrpc_protocol_error_never_exposes_provider_message(make_client):
    server = FakeServer()
    original = server.handle

    async def rpc_error(request):
        if request.method == "POST":
            body = json.loads(request.content)
            if body["method"] == "tools/call":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {
                    "code": -32603, "message": "secret-never-return"}})
        return await original(request)
    server.handle = rpc_error
    client, _ = make_client(server)
    assert_code("TV_PROTOCOL_ERROR", client.get_ohlcv("INDEX:MAHN"))


@pytest.mark.parametrize("reference", ["$ref", "$dynamicRef"])
def test_remote_output_schema_is_rejected_before_sdk_can_fetch(make_client, monkeypatch, reference):
    import urllib.request
    fetched = []

    def forbidden_fetch(*args, **kwargs):
        fetched.append(True)
        raise RuntimeError("unexpected schema fetch")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_fetch)
    server = FakeServer(result={"content": [], "structuredContent": {"bars": [bar()]}})
    server.tools[0]["outputSchema"] = {reference: "https://example.invalid/output-schema"}
    client, _ = make_client(server)
    with pytest.raises(tv.TradingViewClientError) as caught:
        run(client.get_ohlcv("INDEX:MAHN"))
    assert fetched == []
    assert caught.value.code == "TV_SCHEMA_ERROR"
    assert not any(call["method"] == "tools/call" for call in server.calls)


def test_invalid_output_schema_is_rejected_before_call(make_client):
    server = FakeServer()
    server.tools[0]["outputSchema"] = {"type": "not-a-schema-type"}
    client, _ = make_client(server)
    assert_code("TV_SCHEMA_ERROR", client.get_ohlcv("INDEX:MAHN"))
    assert not any(call["method"] == "tools/call" for call in server.calls)


@pytest.mark.parametrize("valid", [True, False])
def test_local_output_schema_keeps_sdk_result_validation(make_client, valid):
    server = FakeServer(result={"content": [], "structuredContent": {
        "bars": [bar()] if valid else "bad bars"}})
    server.tools[0]["outputSchema"] = {
        "type": "object", "$defs": {"bars": {"type": "array"}},
        "properties": {"bars": {"$ref": "#/$defs/bars"}}, "required": ["bars"],
    }
    client, _ = make_client(server)
    if valid:
        assert run(client.get_ohlcv("INDEX:MAHN"))["returned_count"] == 1
    else:
        assert_code("TV_PROTOCOL_ERROR", client.get_ohlcv("INDEX:MAHN"))
    assert any(call["method"] == "tools/call" for call in server.calls)
