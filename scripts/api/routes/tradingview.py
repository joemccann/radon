"""Operator research only. Registered behind Radon's default JWT middleware."""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator

PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


class PrivateResearchRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_validation(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Framework validation echoes the input, including nonfinite
                # values that its JSON error renderer cannot encode.
                return JSONResponse({"source": "tradingview", "missing": False,
                                     "error": {"code": "TV_INVALID_ARGUMENT",
                                               "message": "Invalid research request"}},
                                    status_code=422, headers=PRIVATE_HEADERS)
        return private_validation


router = APIRouter(prefix="/research/tradingview", route_class=PrivateResearchRoute)


def _client():
    from clients.tradingview_client import TradingViewClient
    return TradingViewClient()


def _status():
    from clients.tradingview_auth import configuration_status
    return configuration_status()


def _error(exc):
    code = getattr(exc, "code", "TV_INTERNAL")
    if isinstance(exc, ImportError):
        code = "TV_DEPENDENCY"
    statuses = {
        "TV_NOT_CONFIGURED": 200, "TV_RATE_LIMITED": 429,
        "TV_TIMEOUT": 504, "TV_READ_ONLY": 403, "TV_FORBIDDEN_TOOL": 403,
        "TV_TOOL_FORBIDDEN": 403, "TV_INVALID_ARGUMENT": 422,
        "TV_INVALID_ARGUMENTS": 422, "TV_DEPENDENCY": 503,
        "TV_DEPENDENCY_MISSING": 503, "TV_AUTH_REQUIRED": 503,
        "TV_AUTH_BUSY": 503, "TV_AUTH_FAILED": 503,
    }
    # Unknown upstream exception strings and attributes never reach the response.
    known = set(statuses) | {"TV_INTERNAL", "TV_PROVIDER_ERROR", "TV_NETWORK",
                             "TV_TOOL_UNAVAILABLE", "TV_AUTH_STORAGE",
                             "TV_RESPONSE_TOO_LARGE", "TV_SCHEMA_ERROR",
                             "TV_PROTOCOL_ERROR", "TV_NETWORK_ERROR"}
    if not isinstance(code, str) or code not in known:
        code = "TV_INTERNAL"
    return JSONResponse({"source": "tradingview", "missing": code == "TV_NOT_CONFIGURED",
                         "error": {"code": code, "message": "TradingView research unavailable"}},
                        status_code=statuses.get(code, 502), headers=PRIVATE_HEADERS)


async def _read(operation):
    try:
        data = await operation(_client())
        return JSONResponse({"source": "tradingview", "missing": False, "data": data},
                            headers=PRIVATE_HEADERS)
    except Exception as exc:
        return _error(exc)


@router.get("/status")
async def status():
    try:
        return JSONResponse({"source": "tradingview", **_status()}, headers=PRIVATE_HEADERS)
    except Exception as exc:
        return _error(exc)


@router.get("/symbols")
async def symbols(query: str = Query(min_length=1, max_length=200),
                  type_filter: Literal["all", "stock", "etf", "bond", "forex", "index", "futures", "crypto"] = "all"):
    return await _read(lambda client: client.search_symbols(query, type_filter=type_filter))


@router.get("/ohlcv")
async def ohlcv(symbol: str = Query(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_]+:[^\s:]+$"),
                interval: Literal["1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "M"] = "1D",
                count: int = Query(default=300, ge=1, le=5000)):
    return await _read(lambda client: client.get_ohlcv(symbol, interval=interval, count=count))


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=100, pattern=r"^[a-z_]+$")
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments")
    @classmethod
    def bounded_arguments(cls, value):
        try:
            if len(json.dumps(value, allow_nan=False).encode("utf-8")) > 16 * 1024:
                raise ValueError
        except (ValueError, RecursionError):
            raise ValueError("Arguments must be a bounded JSON object") from None
        return value


@router.post("/call")
async def call(body: ToolRequest):
    return await _read(lambda client: client.call_tool(body.tool, body.arguments))
