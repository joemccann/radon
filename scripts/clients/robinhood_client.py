"""Robinhood trading-MCP client — READ-ONLY market-data failover.

Robinhood's only official agent surface is the Trading MCP server at
https://agent.robinhood.com/mcp/trading (OAuth 2.1 + PKCE, Streamable HTTP
JSON-RPC). There is no official equities REST SDK, and the unofficial
wrappers (robin-stocks, private api.robinhood.com scrapers) are off-limits.

Radon's use of this surface is deliberately narrow:

  - Rank: below IB, UW and the official Cboe feeds; ABOVE Yahoo. A ladder
    that used to end at Yahoo tries Robinhood first when configured.
  - READ-ONLY. Execution stays on Interactive Brokers. ``call_tool`` refuses
    anything outside the documented read-tool allowlist, so a place_* /
    cancel_* call can never leave this process even if a caller asks.
  - No dark pool, OTC, sweeps, GEX, or vol surface: Robinhood does not
    serve them, and the option-quote schema is unpublished — options are
    NBBO/last failover only, never a greeks/surface source.
  - Unconfigured is a clean no-op: without ``ROBINHOOD_MCP_TOKEN`` every
    module-level fetch helper returns empty immediately (no network, no
    raise) and the ladder falls through to Yahoo.

Env (documented in .env.example + docs/external-services.md):
  ROBINHOOD_MCP_URL    — optional; defaults to the official trading URL.
  ROBINHOOD_MCP_TOKEN  — OAuth 2.1 access token minted by the operator's
                         one-time PKCE authorization. Absent = unconfigured.

The token is a credential: it must never appear in logs, reprs, or
exception text. ``_redact`` strips it defensively from error strings.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

import requests

DEFAULT_MCP_URL = "https://agent.robinhood.com/mcp/trading"
_PROTOCOL_VERSION = "2025-06-18"
_DEFAULT_TIMEOUT = 20
_USER_AGENT = "radon/2.0"

# Documented ceiling on get_equity_quotes symbols per call.
EQUITY_QUOTES_BATCH_MAX = 20

# The documented READ tools Radon is allowed to call. Deny-by-default:
# anything not in this set — including every order-write tool the server
# may expose (place_*, cancel_*, …) — is refused client-side.
READ_ONLY_TOOLS = frozenset({
    # quotes / chains failover
    "get_equity_quotes",
    "get_equity_historicals",
    "get_equity_price_book",
    "get_option_chains",
    "get_option_instruments",
    "get_option_quotes",
    "get_option_historicals",
    "get_index_quotes",
    # crowding overlay
    "get_popular_watchlists",
    "get_watchlists",
    "get_watchlist_items",
    "get_scans",
    "get_scanner_filter_specs",
    "run_scan",
    # earnings backup
    "get_earnings_calendar",
    "get_earnings_results",
})


class RobinhoodClientError(Exception):
    """Base exception for Robinhood MCP failures."""


class RobinhoodNotConfiguredError(RobinhoodClientError):
    """Raised when a call is attempted without ROBINHOOD_MCP_TOKEN set."""


class RobinhoodAuthError(RobinhoodClientError):
    """Token rejected (401/403)."""


class RobinhoodReadOnlyError(RobinhoodClientError):
    """A tool outside READ_ONLY_TOOLS was requested. Execution stays on IB."""


def robinhood_configured() -> bool:
    """True when the OAuth token is present in the environment."""
    return bool(os.environ.get("ROBINHOOD_MCP_TOKEN"))


class RobinhoodClient:
    """Minimal Streamable-HTTP MCP consumer for the official trading server.

    JSON-RPC over one POST per request; ``initialize`` runs lazily before the
    first ``tools/call`` and the returned ``Mcp-Session-Id`` (when the server
    issues one) rides on every later request.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ):
        self._url = url or os.environ.get("ROBINHOOD_MCP_URL") or DEFAULT_MCP_URL
        self._token = token if token is not None else os.environ.get("ROBINHOOD_MCP_TOKEN", "")
        self._timeout = timeout
        self._session_id: Optional[str] = None
        self._initialized = False
        self._next_id = 0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": _USER_AGENT})

    def __repr__(self) -> str:  # never leak the token
        return f"RobinhoodClient(url={self._url!r}, configured={self.is_configured()})"

    def is_configured(self) -> bool:
        return bool(self._token)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "RobinhoodClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── transport ────────────────────────────────────────────────

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "[REDACTED]") if self._token else text

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            resp = self._session.post(
                self._url, json=payload, headers=self._headers(), timeout=self._timeout
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise RobinhoodClientError(
                f"Robinhood MCP unreachable: {self._redact(str(exc))}"
            ) from None
        if resp.status_code in (401, 403):
            raise RobinhoodAuthError(f"Robinhood MCP rejected the token (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise RobinhoodClientError(
                f"Robinhood MCP HTTP {resp.status_code}: {self._redact(resp.text[:300])}"
            )
        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        if resp.status_code == 202 or not resp.content:
            return None  # accepted notification
        content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_response(resp.text)
        try:
            return resp.json()
        except ValueError:
            raise RobinhoodClientError(
                f"Robinhood MCP returned non-JSON body: {self._redact(resp.text[:200])}"
            ) from None

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._next_id += 1
        message = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        })
        if message is None:
            raise RobinhoodClientError(f"Robinhood MCP returned no response for {method}")
        if message.get("error"):
            error = message["error"]
            raise RobinhoodClientError(
                f"Robinhood MCP error on {method}: "
                f"{self._redact(str(error.get('message', error)))}"
            )
        return message.get("result") or {}

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if not self.is_configured():
            raise RobinhoodNotConfiguredError(
                "ROBINHOOD_MCP_TOKEN is not set; Robinhood is skipped"
            )
        self._rpc("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "radon", "version": "2.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True

    # ── tools ────────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Call one documented READ tool. Anything else is refused here,
        before any network I/O — Radon never places, cancels, or modifies
        anything through Robinhood; execution stays on IB."""
        if name not in READ_ONLY_TOOLS:
            raise RobinhoodReadOnlyError(
                f"tool {name!r} is not in the Robinhood read-only allowlist; "
                "Radon never writes through Robinhood (execution stays on IB)"
            )
        self._ensure_initialized()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise RobinhoodClientError(
                f"Robinhood tool {name} failed: "
                f"{self._redact(str(_tool_text(result))[:300])}"
            )
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        text = _tool_text(result)
        if text is not None:
            try:
                return json.loads(text)
            except ValueError:
                return text
        return result

    # ── convenience reads ────────────────────────────────────────

    def get_equity_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """Batched quotes, chunked at the documented 20-symbol ceiling."""
        quotes: List[Dict[str, Any]] = []
        for start in range(0, len(symbols), EQUITY_QUOTES_BATCH_MAX):
            chunk = symbols[start:start + EQUITY_QUOTES_BATCH_MAX]
            payload = self.call_tool("get_equity_quotes", {"symbols": chunk})
            quotes.extend(_result_rows(payload))
        return quotes

    def get_equity_historicals(
        self, symbol: str, interval: str = "day", span: str = "year"
    ) -> List[Dict[str, Any]]:
        payload = self.call_tool(
            "get_equity_historicals",
            {"symbol": symbol, "interval": interval, "span": span},
        )
        return _result_rows(payload)

    def get_index_quotes(self, symbols: List[str]) -> List[Dict[str, Any]]:
        payload = self.call_tool("get_index_quotes", {"symbols": symbols})
        return _result_rows(payload)

    def get_popular_watchlists(self) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("get_popular_watchlists"))

    def get_watchlist_items(self, watchlist_id: str) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("get_watchlist_items", {"watchlist_id": watchlist_id}))

    def get_scans(self) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("get_scans"))

    def run_scan(self, scan_id: str) -> List[Dict[str, Any]]:
        return _result_rows(self.call_tool("run_scan", {"scan_id": scan_id}))

    def fetch_daily_closes(self, symbol: str) -> Dict[str, float]:
        """date (YYYY-MM-DD) -> close from get_equity_historicals.

        The payload schema is unpublished, so field names are probed
        defensively; any shape this cannot read yields {} and the ladder
        falls through to Yahoo.
        """
        return _closes_from_historicals(self.get_equity_historicals(symbol))


# ── payload parsing (schema unpublished — probe, never assume) ────

_ROW_LIST_KEYS = ("data_points", "historicals", "results", "quotes", "data", "bars", "items")
_DATE_KEYS = ("begins_at", "date", "timestamp", "time", "session_date")
_CLOSE_KEYS = ("close_price", "close", "last_trade_price", "adjusted_close")


def _tool_text(result: Dict[str, Any]) -> Optional[str]:
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return item.get("text")
    return None


def _result_rows(payload: Any) -> List[Dict[str, Any]]:
    """Normalize a tool payload to a list of row dicts."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in _ROW_LIST_KEYS:
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _closes_from_historicals(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    closes: Dict[str, float] = {}
    for row in rows:
        date = next((row[k] for k in _DATE_KEYS if row.get(k)), None)
        close = next((row[k] for k in _CLOSE_KEYS if row.get(k) is not None), None)
        if date is None or close is None:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            closes[str(date)[:10]] = value
    return closes


def _parse_sse_response(body: str) -> Optional[Dict[str, Any]]:
    """Last JSON-RPC message carrying a result/error from an SSE body."""
    message: Optional[Dict[str, Any]] = None
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[len("data:"):].strip())
        except ValueError:
            continue
        if isinstance(parsed, dict) and ("result" in parsed or "error" in parsed):
            message = parsed
    return message


# ── module-level ladder helpers (unconfigured = clean empty) ──────


def fetch_robinhood_closes(symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """Daily closes per symbol for the failover ladders.

    Ranked BELOW IB / UW / Cboe and ABOVE Yahoo. Returns {} immediately —
    no network — when ROBINHOOD_MCP_TOKEN is unset, so unconfigured hosts
    fall straight through to Yahoo.
    """
    if not robinhood_configured():
        return {}
    out: Dict[str, Dict[str, float]] = {}
    try:
        with RobinhoodClient() as rh:
            for symbol in symbols:
                try:
                    closes = rh.fetch_daily_closes(symbol)
                except RobinhoodClientError as exc:
                    print(f"  Robinhood: {symbol} failed — {exc}", file=sys.stderr)
                    continue
                if closes:
                    out[symbol] = closes
                    print(f"  Robinhood: {symbol} — {len(closes)} bars", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - the ladder falls through to Yahoo
        print(f"  Robinhood connection failed — {exc}", file=sys.stderr)
    return out


def fetch_robinhood_quote(symbol: str, *, index: bool = False) -> Optional[float]:
    """One last/quote value for the current-quote failover (IB → RH → Yahoo).

    None when unconfigured or the (unpublished) quote shape is unreadable.
    """
    if not robinhood_configured():
        return None
    try:
        with RobinhoodClient() as rh:
            rows = (
                rh.get_index_quotes([symbol]) if index else rh.get_equity_quotes([symbol])
            )
    except Exception as exc:  # noqa: BLE001 - the ladder falls through to Yahoo
        print(f"  Robinhood quote failed for {symbol}: {exc}", file=sys.stderr)
        return None
    for row in rows:
        for key in ("last_trade_price", "last", "last_price", "price", "mark_price"):
            value = row.get(key)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
    return None
