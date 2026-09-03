"""Consecutive daily gains for one ticker — on-demand daily closes.

Source ladder per repo priority: IB (pool, bounded) -> Unusual Whales ->
Robinhood (read-only MCP, skipped cleanly when unconfigured) -> Yahoo
(ABSOLUTE LAST RESORT). The first source returning >= MIN_ACCEPT_BARS
closes wins; if none reaches it, the longest non-empty result is used (a
young listing is short on every source). Winning results cache to
utils.price_cache (15 min market hours / 24 h after close). Turso is
deliberately not written: the payload is derived on demand from upstream
vendors and carries no state a deploy could lose (docs/indicators/streaks.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from fastapi import APIRouter, HTTPException, Request

from utils.price_cache import (
    STOCKS_DIR,
    TTL_AFTER_CLOSE,
    TTL_MARKET_HOURS,
    cache_key_stock,
    is_market_hours,
    read_cache_envelope,
    write_cache,
)
from utils.streaks import build_streaks_payload, parse_yahoo_chart

from .historical import ContractSpec, _bar_date_to_iso, _bounded_pool_call, make_ib_contract

logger = logging.getLogger("radon.streaks")

router = APIRouter()

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
# One bounded IB request; ~2,500 daily bars is plenty of run history.
IB_DURATION = "10 Y"
UW_OHLC_LIMIT = 2500
# Matches utils.uw_surface.MIN_DAILY_CLOSES: fewer bars than this reads as a
# short/partial answer and the ladder keeps trying deeper sources.
MIN_ACCEPT_BARS = 21
# Yahoo is the deep-history last resort; ~20 years.
YAHOO_LOOKBACK_DAYS = 7300
_CACHE_WINDOW_TAG = "streaks-max"


def _require_ticker(raw: str) -> str:
    ticker = (raw or "").strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail="Invalid ticker")
    return ticker


def _cache_key(symbol: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return cache_key_stock(symbol, _CACHE_WINDOW_TAG, today)


def _read_cached_envelope(symbol: str) -> Optional[dict]:
    return read_cache_envelope(STOCKS_DIR, _cache_key(symbol))


def _write_cached_closes(symbol: str, closes: dict[str, float], source: str) -> None:
    ttl = TTL_MARKET_HOURS if is_market_hours() else TTL_AFTER_CLOSE
    write_cache(STOCKS_DIR, _cache_key(symbol), closes, source=source, ttl=ttl)


async def _fetch_ib_closes(request: Request, symbol: str) -> dict[str, float]:
    """Daily closes from the IB pool; {} on any unavailability (no pool,
    unauthenticated gateway, timeout) so the ladder falls through."""
    pool = getattr(request.app.state, "ib_pool", None)
    if pool is None:
        return {}
    contract = make_ib_contract(ContractSpec(symbol=symbol))
    try:
        async with pool.acquire("data") as client:
            await _bounded_pool_call(
                pool, "data", client, client.qualify_contracts, contract
            )
            bars = await _bounded_pool_call(
                pool, "data", client, client.get_historical_data,
                contract,
                end_date="",
                duration=IB_DURATION,
                bar_size="1 day",
                what_to_show="TRADES",
                use_rth=True,
            )
    except HTTPException:
        # 503/504 from the bounded pool call: gateway down or wedged.
        return {}
    except Exception as exc:  # noqa: BLE001 — any IB failure falls through
        logger.warning("IB streaks fetch failed for %s: %s", symbol, exc)
        return {}

    closes: dict[str, float] = {}
    for bar in bars or []:
        date = _bar_date_to_iso(getattr(bar, "date", ""))
        try:
            value = float(getattr(bar, "close", None))
        except (TypeError, ValueError):
            continue
        if value > 0:
            closes[date] = value
    return closes


def _uw_rows_to_closes(raw: object) -> dict[str, float]:
    rows = raw.get("data", []) if isinstance(raw, dict) else raw
    closes: dict[str, float] = {}
    if not isinstance(rows, list):
        return closes
    for row in rows:
        if isinstance(row, dict):
            date, close = row.get("date"), row.get("close")
        else:
            date, close = getattr(row, "date", ""), getattr(row, "close", None)
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            closes[str(date or "")[:10]] = value
    return closes


def _fetch_uw_closes(symbol: str) -> dict[str, float]:
    from clients.uw_client import UWClient

    with UWClient() as client:
        raw = client.get_stock_ohlc(symbol, candle_size="1d", limit=UW_OHLC_LIMIT)
    return _uw_rows_to_closes(raw)


def _fetch_rh_closes(symbol: str) -> dict[str, float]:
    from clients.robinhood_client import fetch_robinhood_closes

    return fetch_robinhood_closes([symbol]).get(symbol, {})


def _fetch_yahoo_closes(symbol: str) -> dict[str, float]:
    now = int(datetime.now(timezone.utc).timestamp())
    params = urlencode({
        "period1": now - YAHOO_LOOKBACK_DAYS * 86400,
        "period2": now + 86400,
        "interval": "1d",
        "includePrePost": "false",
        "events": "div,splits",
    })
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{params}"
    request = UrlRequest(url, headers={"User-Agent": "radon/2.0"})
    with urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_yahoo_chart(payload)


# REL-174 (R-485): one spelling for the Robinhood source across the persisted
# ladders, price_history_daily and this payload.
RH_SOURCE = "rh"
# (label, fetcher NAME): resolved at call time so tests can patch the rungs.
FALLBACK_LADDER = (
    ("uw", "_fetch_uw_closes"),
    (RH_SOURCE, "_fetch_rh_closes"),
    ("yahoo", "_fetch_yahoo_closes"),
)


def _fetch_fallback_closes(
    symbol: str,
    best: dict[str, float],
    best_source: Optional[str],
    errors: Optional[list] = None,
) -> tuple[dict[str, float], Optional[str]]:
    """UW -> Robinhood -> Yahoo, keeping the longest short answer as backup.

    REL-177 (R-489): vendor FAILURES are collected into ``errors`` so an
    expired UW token or a throttle embargo stops reading as a bad ticker.
    """
    ladder = tuple((label, globals()[name]) for label, name in FALLBACK_LADDER)
    for source, fetch in ladder:
        try:
            closes = fetch(symbol)
        except Exception as exc:  # noqa: BLE001 — a dead vendor falls through
            logger.warning("%s streaks fetch failed for %s: %s", source, symbol, exc)
            if errors is not None:
                errors.append({"source": source, "error": str(exc)})
            continue
        if len(closes) >= MIN_ACCEPT_BARS:
            return closes, source
        if len(closes) > len(best):
            best, best_source = closes, source
    return best, best_source


# One in-flight ladder per symbol (REL-177 / R-488): the assistant and the
# panel can land concurrent lookups; the loser awaits the winner's result.
_inflight: dict[str, asyncio.Task] = {}
# Overall deadline, comfortably under the Next route's 60s budget. Also caps
# any UW Retry-After sleep the client performs inside the ladder.
STREAKS_DEADLINE_S = 45.0


def _ib_gate_reason() -> Optional[str]:
    """A reason to skip the IB socket, or None. REL-177 (R-488)."""
    from ..ib_gateway import last_observed_auth_state

    state = last_observed_auth_state()
    if state is not None and state != "authenticated":
        return f"gateway auth_state={state}"
    return None


async def _resolve_streaks(request: Request, symbol: str, scan_time: str) -> dict:
    envelope = await asyncio.to_thread(_read_cached_envelope, symbol)
    cached_data = (envelope or {}).get("data") or {}
    cached_source = (envelope or {}).get("source")
    # A cached higher-rung series serves with its PROVENANCE (R-490). A Yahoo
    # or short cached answer does not short-circuit the ladder — rule 7: IB/UW
    # are retried once they recover, the cache is only the backup.
    if (
        cached_data
        and cached_source not in (None, "yahoo", "unknown", "cache")
        and len(cached_data) >= MIN_ACCEPT_BARS
    ):
        payload = build_streaks_payload(
            symbol, cached_data, source=cached_source, scan_time=scan_time
        )
        payload["cached"] = True
        payload["fetched_at"] = (envelope or {}).get("fetched_at")
        return payload

    errors: list[dict] = []
    gate_reason = _ib_gate_reason()
    if gate_reason is None:
        closes = await _fetch_ib_closes(request, symbol)
    else:
        logger.info("IB skipped for %s streaks: %s", symbol, gate_reason)
        closes = {}
    source: Optional[str] = "ib" if closes else None
    if len(closes) < MIN_ACCEPT_BARS:
        closes, source = await asyncio.to_thread(
            _fetch_fallback_closes, symbol, closes, source, errors
        )

    if not closes and cached_data:
        # Every live rung failed or came back empty: the stale cache beats
        # nothing, served with its provenance and age.
        payload = build_streaks_payload(
            symbol, cached_data, source=cached_source, scan_time=scan_time
        )
        payload["cached"] = True
        payload["fetched_at"] = (envelope or {}).get("fetched_at")
        if errors:
            payload["errors"] = errors
        return payload

    if not closes:
        # Empty-payload guard: never cache an empty result. Failures are NOT
        # the same as an unlisted symbol (R-489): the errors list says which.
        payload = build_streaks_payload(symbol, {}, source=None, scan_time=scan_time)
        if errors:
            payload["errors"] = errors
        return payload

    await asyncio.to_thread(_write_cached_closes, symbol, closes, source or "unknown")
    payload = build_streaks_payload(symbol, closes, source=source, scan_time=scan_time)
    if errors:
        payload["errors"] = errors
    return payload


@router.get("/streaks/{ticker}")
async def daily_streaks(ticker: str, request: Request):
    """Daily closes + consecutive-gain streaks for one symbol.

    Absent data is HTTP 200 with missing:true (never a 4xx); an out-of-bounds
    symbol is a 400 request error.
    """
    symbol = _require_ticker(ticker)
    scan_time = datetime.now(timezone.utc).isoformat(timespec="seconds")

    task = _inflight.get(symbol)
    if task is None or task.done():
        task = asyncio.ensure_future(
            asyncio.wait_for(
                _resolve_streaks(request, symbol, scan_time),
                timeout=STREAKS_DEADLINE_S,
            )
        )
        _inflight[symbol] = task
        task.add_done_callback(
            lambda done: _inflight.pop(symbol, None)
            if _inflight.get(symbol) is done
            else None
        )
    try:
        return await asyncio.shield(task)
    except asyncio.TimeoutError:
        payload = build_streaks_payload(symbol, {}, source=None, scan_time=scan_time)
        payload["errors"] = [
            {"source": "ladder", "error": f"deadline {STREAKS_DEADLINE_S:.0f}s exceeded"}
        ]
        return payload
