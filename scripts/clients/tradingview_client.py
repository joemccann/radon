"""Bounded private research reads from TradingView's official MCP server.

Protocol, discovery and OAuth refresh belong to the pinned MCP SDK. This module
owns Radon's narrower tool policy and data validation; discovery cannot expand it.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import importlib
import json
import logging
import math
import re

SERVER_URL = "https://mcp.tradingview.com/mcp"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARGUMENT_BYTES = 64 * 1024
MAX_BARS = 5000
# https://www.tradingview.com/mcp/docs, verified 2026-10-07. No alert
# operations, active-watchlist side effects, or news story bodies are admitted.
SAFE_RESEARCH_TOOLS = frozenset({
    "list_watchlists", "get_watchlist", "get_ohlcv", "get_economic_data",
    "get_economic_symbols", "search_symbols", "get_symbol_data", "run_screener",
    "get_symbol_data_batch", "get_screener_columns", "get_technicals_rating",
    "get_news", "get_forecasts", "get_financials", "get_financial_history",
    "get_documents", "get_document_view", "get_earnings_calendar",
    "get_economic_calendar", "get_dividends_calendar",
})
_MESSAGES = {
    "TV_NOT_CONFIGURED": "TradingView credentials are not configured.",
    "TV_AUTH_REQUIRED": "TradingView authorization is required.",
    "TV_AUTH_FAILED": "TradingView authorization failed.",
    "TV_AUTH_STORAGE": "TradingView credential storage is unavailable.",
    "TV_AUTH_BUSY": "TradingView authorization is busy.",
    "TV_DEPENDENCY_MISSING": "TradingView MCP dependencies are unavailable.",
    "TV_TOOL_FORBIDDEN": "This TradingView tool is not permitted.",
    "TV_TOOL_UNAVAILABLE": "This TradingView tool is unavailable.",
    "TV_INVALID_ARGUMENT": "Invalid TradingView research arguments.",
    "TV_SCHEMA_ERROR": "TradingView returned an invalid tool schema.",
    "TV_PROVIDER_ERROR": "TradingView returned an invalid or incomplete result.",
    "TV_PROTOCOL_ERROR": "TradingView MCP protocol failed.",
    "TV_NETWORK_ERROR": "TradingView connection failed.",
    "TV_RATE_LIMITED": "TradingView request limit reached.",
    "TV_TIMEOUT": "TradingView research request timed out.",
    "TV_RESPONSE_TOO_LARGE": "TradingView response exceeds the research limit.",
}


class TradingViewClientError(Exception):
    def __init__(self, code="TV_PROVIDER_ERROR"):
        self.code = code if code in _MESSAGES else "TV_PROVIDER_ERROR"
        super().__init__(_MESSAGES[self.code])


TVClientError = TradingViewClientError


class TradingViewNotConfiguredError(TradingViewClientError):
    def __init__(self):
        super().__init__("TV_NOT_CONFIGURED")


def configuration_status(token_file=None):
    """Inspect storage only; never initialize an SDK or request consent."""
    from .tradingview_auth import configuration_status as status
    return status(token_file)


def _build_oauth_provider(token_file):
    from .tradingview_auth import build_oauth_provider
    return build_oauth_provider(token_file=token_file, interactive=False)


_private_call = ContextVar("tradingview_private_call", default=False)


@contextmanager
def _quiet_protocol_logs():
    # SDK debug logs include complete messages; exception logs may include an
    # upstream body. Suppress only this call's context, including its SDK tasks.
    class Filter(logging.Filter):
        def filter(self, record):
            return not (_private_call.get() and record.name.startswith(("mcp", "httpx", "httpcore")))

    for name in ("mcp.client.streamable_http", "mcp.client.session",
                 "mcp.shared.session", "httpx", "httpcore"):
        logging.getLogger(name)
    names = [name for name in logging.Logger.manager.loggerDict
             if name == "mcp" or name.startswith(("mcp.", "httpx", "httpcore"))]
    loggers = [logging.getLogger(name) for name in names]
    handlers = {handler for logger in [logging.getLogger(), *loggers] for handler in logger.handlers}
    log_filter = Filter()
    for logger in loggers:
        logger.addFilter(log_filter)
    for handler in handlers:
        handler.addFilter(log_filter)
    token = _private_call.set(True)
    try:
        yield
    finally:
        _private_call.reset(token)
        for logger in loggers:
            logger.removeFilter(log_filter)
        for handler in handlers:
            handler.removeFilter(log_filter)


def _coded_error(error):
    """Discard all SDK/provider strings, including nested task-group errors."""
    if isinstance(error, BaseExceptionGroup):
        errors = [_coded_error(item) for item in error.exceptions]
        return next((item for item in errors if item.code != "TV_PROTOCOL_ERROR"), errors[0])
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in _MESSAGES:
        return TradingViewClientError(code)
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return TradingViewClientError("TV_TIMEOUT")
    # Imported only on use; module import remains valid without optional SDK.
    httpx = importlib.import_module("httpx")
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        code = "TV_RATE_LIMITED" if status == 429 else (
            "TV_AUTH_REQUIRED" if status in (401, 403) else "TV_NETWORK_ERROR")
        return TradingViewClientError(code)
    if isinstance(error, httpx.TimeoutException):
        return TradingViewClientError("TV_TIMEOUT")
    if isinstance(error, httpx.RequestError):
        return TradingViewClientError("TV_NETWORK_ERROR")
    return TradingViewClientError("TV_PROTOCOL_ERROR")


def _sdk():
    try:
        importlib.import_module("mcp")
        return (importlib.import_module("httpx"),
                importlib.import_module("mcp").ClientSession,
                importlib.import_module("mcp.client.streamable_http").streamable_http_client,
                importlib.import_module("jsonschema"))
    except ImportError:
        raise TradingViewClientError("TV_DEPENDENCY_MISSING") from None


def _bounded_transport(httpx, inner):
    failure = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        def __init__(self, stream):
            self.stream = stream

        async def __aiter__(self):
            total = 0
            async for chunk in self.stream:
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    failure.set()
                    raise TradingViewClientError("TV_RESPONSE_TOO_LARGE")
                yield chunk

        async def aclose(self):
            await self.stream.aclose()

    class Transport(httpx.AsyncBaseTransport):
        async def monitor(self):
            await failure.wait()
            raise TradingViewClientError("TV_RESPONSE_TOO_LARGE")

        async def handle_async_request(self, request):
            # Streaming HTTP decoding can expand a small compressed body past
            # the cap. Require identity so this boundary covers decoded bytes.
            request.headers["Accept-Encoding"] = "identity"
            response = await inner.handle_async_request(request)
            if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                await response.aclose()
                raise TradingViewClientError("TV_PROVIDER_ERROR")
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=Stream(response.stream),
                                  extensions=response.extensions, request=request)

        async def aclose(self):
            await inner.aclose()

    return Transport()


def _validate_schema(schema, arguments, jsonschema):
    def check_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("$ref", "$dynamicRef") and (not isinstance(item, str) or not item.startswith("#")):
                    raise TradingViewClientError("TV_SCHEMA_ERROR")
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)
    check_refs(schema)
    try:
        validator_type = jsonschema.validators.validator_for(schema)
        validator_type.check_schema(schema)
    except Exception:
        raise TradingViewClientError("TV_SCHEMA_ERROR") from None
    if arguments is None:
        return  # Schema-only check before the SDK validates a tool result.
    try:
        validator_type(schema).validate(arguments)
    except jsonschema.ValidationError:
        raise TradingViewClientError("TV_INVALID_ARGUMENT") from None
    except Exception:
        raise TradingViewClientError("TV_SCHEMA_ERROR") from None


def _decode_result(result):
    if result.isError:
        raise TradingViewClientError()
    payload = result.structuredContent
    if payload is None:
        if len(result.content) != 1 or result.content[0].type != "text":
            raise TradingViewClientError()
        try:
            def invalid_constant(_):
                raise ValueError()
            payload = json.loads(result.content[0].text, parse_constant=invalid_constant)
        except (ValueError, TypeError):
            raise TradingViewClientError() from None
    if not isinstance(payload, (dict, list)) or payload == {}:
        raise TradingViewClientError()
    try:
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise TradingViewClientError() from None
    if isinstance(payload, dict) and (
        payload.get("success") is False or payload.get("partial") is True
        or payload.get("status") in ("error", "failed")
        or any(payload.get(key) for key in ("error", "errors", "warnings", "missing"))
    ):
        raise TradingViewClientError()
    return payload


def _number(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise TradingViewClientError()
    return value


def _normalize_bars(payload, symbol, interval, count):
    if isinstance(payload, dict):
        if payload.get("symbol", symbol) != symbol or payload.get("interval", interval) != interval:
            raise TradingViewClientError()
        bars = payload.get("bars")
    else:
        bars = payload
    if not isinstance(bars, list) or not bars or len(bars) > count or len(bars) > MAX_BARS:
        raise TradingViewClientError()
    normalized, seen = [], set()
    for bar in bars:
        if not isinstance(bar, dict):
            raise TradingViewClientError()
        timestamp = _number(bar.get("t"))
        if timestamp != int(timestamp):
            raise TradingViewClientError()
        timestamp = int(timestamp)
        try:
            datetime.fromtimestamp(timestamp, timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise TradingViewClientError() from None
        if timestamp in seen:
            raise TradingViewClientError()
        seen.add(timestamp)
        o, h, l, c = (_number(bar.get(key)) for key in ("o", "h", "l", "c"))
        volume = bar.get("v")
        if volume is not None:
            volume = _number(volume)
            if volume < 0:
                raise TradingViewClientError()
        if not l <= min(o, c) <= max(o, c) <= h:
            raise TradingViewClientError()
        normalized.append({"timestamp": timestamp, "open": o, "high": h,
                           "low": l, "close": c, "volume": volume})
    return {"source": "tradingview", "symbol": symbol, "interval": interval,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "requested_count": count, "returned_count": len(normalized),
            "history_complete": False,
            "bars": sorted(normalized, key=lambda item: item["timestamp"])}


class TradingViewClient:
    def __init__(self, token_file=None, timeout=30, *, http_transport=None):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise TradingViewClientError("TV_INVALID_ARGUMENT")
        self.token_file = token_file
        self.timeout = timeout
        self._http_transport = http_transport

    async def _request(self, name=None, arguments=None):
        try:
            async with asyncio.timeout(self.timeout):
                httpx, Session, transport_context, jsonschema = _sdk()
                with _quiet_protocol_logs():
                    provider = _build_oauth_provider(self.token_file)
                    transport = _bounded_transport(httpx, self._http_transport or httpx.AsyncHTTPTransport())
                    # SDK JSON parsing errors are sent as uncorrelated messages;
                    # monitor the byte boundary separately to abort promptly.
                    async with asyncio.TaskGroup() as group:
                        monitor = group.create_task(transport.monitor())
                        try:
                            async with httpx.AsyncClient(auth=provider, transport=transport,
                                                        timeout=httpx.Timeout(self.timeout), follow_redirects=False) as http:
                                async with transport_context(SERVER_URL, http_client=http) as (read, write, _):
                                    async with Session(read, write, read_timeout_seconds=timedelta(seconds=self.timeout)) as session:
                                        await session.initialize()
                                        tools, cursor, seen = {}, None, set()
                                        for _ in range(10):
                                            catalog = await session.list_tools(cursor=cursor)
                                            for tool in catalog.tools:
                                                if tool.name in tools or len(tools) >= 200:
                                                    raise TradingViewClientError("TV_SCHEMA_ERROR")
                                                tools[tool.name] = tool
                                            cursor = catalog.nextCursor
                                            if not cursor:
                                                break
                                            if cursor in seen:
                                                raise TradingViewClientError("TV_SCHEMA_ERROR")
                                            seen.add(cursor)
                                        else:
                                            raise TradingViewClientError("TV_SCHEMA_ERROR")
                                        safe = {key: value for key, value in tools.items()
                                                if key in SAFE_RESEARCH_TOOLS and not (
                                                    value.annotations and (value.annotations.readOnlyHint is False
                                                                          or value.annotations.destructiveHint is True))}
                                        if name is None:
                                            return [tool.model_dump(mode="json", exclude_none=True) for tool in safe.values()]
                                        if name not in safe:
                                            raise TradingViewClientError("TV_TOOL_UNAVAILABLE")
                                        _validate_schema(safe[name].inputSchema, arguments, jsonschema)
                                        if safe[name].outputSchema is not None:
                                            _validate_schema(safe[name].outputSchema, None, jsonschema)
                                        return _decode_result(await session.call_tool(name, arguments,
                                            read_timeout_seconds=timedelta(seconds=self.timeout)))
                        finally:
                            monitor.cancel()
        except Exception as error:
            raise _coded_error(error) from None

    async def list_tools(self):
        return await self._request()

    async def call_tool(self, name, arguments):
        if not isinstance(name, str) or name not in SAFE_RESEARCH_TOOLS:
            raise TradingViewClientError("TV_TOOL_FORBIDDEN")
        if not isinstance(arguments, dict):
            raise TradingViewClientError("TV_INVALID_ARGUMENT")
        if name == "get_ohlcv":
            count = arguments.get("count", 300)
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_BARS:
                raise TradingViewClientError("TV_INVALID_ARGUMENT")
        try:
            if len(json.dumps(arguments, allow_nan=False).encode()) > MAX_ARGUMENT_BYTES:
                raise ValueError()
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise TradingViewClientError("TV_INVALID_ARGUMENT") from None
        return await self._request(name, arguments)

    async def search_symbols(self, query, type_filter="all"):
        if not isinstance(query, str) or not query.strip() or len(query) > 256 or not isinstance(type_filter, str) or type_filter not in {
            "all", "stock", "etf", "bond", "forex", "index", "futures", "crypto"}:
            raise TradingViewClientError("TV_INVALID_ARGUMENT")
        return await self.call_tool("search_symbols", {"query": query, "type_filter": type_filter})

    async def get_ohlcv(self, symbol, interval="1D", count=300):
        if (not isinstance(symbol, str) or len(symbol) > 256
            or not re.fullmatch(r"[A-Za-z0-9_]+:[A-Za-z0-9_.!/^+=-]+", symbol)
            or not isinstance(interval, str) or interval not in {"1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "M", "1mo", "month"}
            or isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_BARS):
            raise TradingViewClientError("TV_INVALID_ARGUMENT")
        payload = await self.call_tool("get_ohlcv", {
            "symbol": symbol, "interval": interval, "count": count, "summary": False})
        return _normalize_bars(payload, symbol, interval, count)
