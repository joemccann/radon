"""Shared Unusual Whales surface fetch for theta and strength scanners."""

from __future__ import annotations

from typing import Any

# HV20 needs 21 daily closes; scanners reject fewer than this.
MIN_DAILY_CLOSES = 21


def _stock_contract(symbol: str) -> Any:
    try:
        from ib_insync import Stock
    except ImportError:
        return symbol
    return Stock(symbol, "SMART", "USD")


def _close_row(row: Any) -> dict[str, Any] | None:
    if isinstance(row, dict):
        close = row.get("close")
        date = row.get("date")
    else:
        close = getattr(row, "close", None)
        date = getattr(row, "date", "")
    try:
        value = float(close)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return {"date": str(date or "")[:10], "close": value}


def _as_uw_ohlc(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        rows = raw.get("data", [])
    else:
        rows = raw
    data: list[dict[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            parsed = _close_row(row)
            if parsed is not None:
                data.append(parsed)
    return {"data": data}


def fetch_daily_closes(
    ticker: str,
    ib: Any = None,
    uw: Any = None,
    *,
    min_bars: int = MIN_DAILY_CLOSES,
) -> Any:
    """IB historical daily closes first; UW OHLC only on short/empty/error.

    Never raises out of the IB path. UW errors (including daily-cap 429)
    propagate to the caller.
    """
    symbol = ticker.upper()
    if ib is not None:
        try:
            bars = ib.get_historical_data(
                _stock_contract(symbol),
                duration="1 Y",
                bar_size="1 day",
                what_to_show="TRADES",
                use_rth=True,
            )
            payload = _as_uw_ohlc(bars)
            if len(payload["data"]) >= min_bars:
                return payload
        except Exception:
            pass
    if uw is not None:
        return uw.get_stock_ohlc(symbol, candle_size="1d")
    return {"data": []}


def fetch_surface(client: Any, ticker: str, ib: Any = None) -> dict[str, Any]:
    """Return ohlc (IB-first), plus UW iv_rank, contracts, and strike GEX."""
    symbol = ticker.upper()
    return {
        "ohlc": fetch_daily_closes(symbol, ib=ib, uw=client),
        "iv_rank": client.get_iv_rank(symbol),
        "contracts": client.get_option_contracts(
            symbol, exclude_zero_vol_chains=True, maybe_otm_only=True
        ),
        "gex_strike": client.get_greek_exposure_by_strike(symbol),
    }
