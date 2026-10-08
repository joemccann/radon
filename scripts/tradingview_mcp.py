#!/usr/bin/env python3.13
"""Private operator entry point for the official TradingView MCP research client."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

MAX_ARGUMENT_BYTES = 16 * 1024
SAFE_CODES = frozenset({
    "TV_CANCELLED", "TV_DEPENDENCY", "TV_DEPENDENCY_MISSING", "TV_INTERNAL",
    "TV_NOT_CONFIGURED", "TV_AUTH_REQUIRED", "TV_AUTH_BUSY", "TV_AUTH_FAILED",
    "TV_AUTH_STORAGE", "TV_RATE_LIMITED", "TV_TIMEOUT", "TV_NETWORK",
    "TV_INVALID_ARGUMENT", "TV_INVALID_ARGUMENTS", "TV_TOOL_FORBIDDEN",
    "TV_TOOL_UNAVAILABLE", "TV_PROVIDER_ERROR", "TV_RESPONSE_TOO_LARGE",
    "TV_SCHEMA_ERROR", "TV_PROTOCOL_ERROR", "TV_NETWORK_ERROR",
})


def _client(args):
    from clients.tradingview_client import TradingViewClient
    return TradingViewClient(token_file=args.token_file)


def _status(path):
    from clients.tradingview_auth import configuration_status
    return configuration_status(path)


def _arguments(raw):
    if len(raw.encode("utf-8")) > MAX_ARGUMENT_BYTES:
        raise ValueError
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError
    # JSON's optional NaN/Infinity spellings are not legal MCP arguments.
    json.dumps(result, allow_nan=False)
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--token-file", help="Protected operator OAuth store")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("status", "auth", "tools"):
        commands.add_parser(name)
    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--type-filter", default="all")
    bars = commands.add_parser("ohlcv")
    bars.add_argument("symbol")
    bars.add_argument("--interval", default="1D")
    bars.add_argument("--count", type=int, default=300)
    call = commands.add_parser("call")
    call.add_argument("tool")
    call.add_argument("--arguments", default="{}")
    return result


async def _run(args, arguments):
    if args.command == "status":
        return {"source": "tradingview", **_status(args.token_file)}
    if args.command == "auth":
        from clients.tradingview_auth import authorize
        return {"source": "tradingview", **await authorize(args.token_file)}
    client = _client(args)
    if args.command == "tools":
        data = await client.list_tools()
    elif args.command == "search":
        data = await client.search_symbols(args.query, type_filter=args.type_filter)
    elif args.command == "ohlcv":
        data = await client.get_ohlcv(args.symbol, interval=args.interval, count=args.count)
    else:
        data = await client.call_tool(args.tool, arguments)
    return {"source": "tradingview", "missing": False, "data": data}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        arguments = _arguments(args.arguments) if args.command == "call" else None
    except (ValueError, RecursionError):
        print(json.dumps({"error": {"code": "TV_INVALID_ARGUMENTS", "message": "Arguments must be a bounded JSON object"}}))
        return 2
    try:
        payload = asyncio.run(_run(args, arguments))
        print(json.dumps(payload, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        code = "TV_CANCELLED"
    except ImportError:
        code = "TV_DEPENDENCY"
    except Exception as exc:
        code = getattr(exc, "code", "TV_INTERNAL")
        if not isinstance(code, str) or code not in SAFE_CODES:
            code = "TV_INTERNAL"
    print(json.dumps({"source": "tradingview", "missing": code == "TV_NOT_CONFIGURED",
                      "error": {"code": code, "message": "TradingView request unavailable"}}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
