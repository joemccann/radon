"""Shared Unusual Whales surface fetch for theta and strength scanners."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

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


class _LockedIB:
    """Serialize get_historical_data; ib_insync is not thread-safe."""

    def __init__(self, ib: Any) -> None:
        self._ib = ib
        self._lock = threading.Lock()

    def get_historical_data(self, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return self._ib.get_historical_data(*args, **kwargs)

    def disconnect(self) -> None:
        disconnect = getattr(self._ib, "disconnect", None)
        if disconnect is not None:
            disconnect()


@contextmanager
def scan_ib_session() -> Iterator[Any]:
    """One IB connection for a scan run. Yields None if connect fails."""
    client = None
    adapter: Any = None
    try:
        from clients.ib_client import IBClient

        client = IBClient()
        client.connect(client_id="auto", timeout=8, max_retries=1)
        adapter = _LockedIB(client)
    except Exception:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
        adapter = None
        client = None
    try:
        yield adapter
    finally:
        if adapter is not None:
            try:
                adapter.disconnect()
            except Exception:
                pass
