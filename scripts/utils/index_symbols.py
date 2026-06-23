"""Canonical list of index symbols (IBKR `secType=IND`).

Python mirror of `web/lib/indexSymbols.ts`. Keep both in sync — a unit
test (`scripts/tests/test_index_symbols_sync.py`) asserts they match.

Used by:
- `scripts/clients/contract_resolver.py` to build an `Index(...)`
  contract instead of `Stock(...)` when the symbol is in this set.
- `scripts/ib_place_order.py` to reject orders against an index symbol
  (indices themselves are not tradeable — only futures / options on
  the index are).
"""
from __future__ import annotations

from typing import Optional


# Map of symbol → IBKR primary exchange.
INDEX_SYMBOLS: dict[str, str] = {
    # Volatility (CBOE Indexes feed)
    "VIX": "CBOE",
    "VXN": "CBOE",
    "VVIX": "CBOE",
    "VXX": "CBOE",
    "COR1M": "CBOE",
    "COR3M": "CBOE",
    "SKEW": "CBOE",
    # Broad-market indices
    "SPX": "CBOE",
    "NDX": "NASDAQ",
    "RUT": "RUSSELL",
    "DJX": "CBOE",
    "OEX": "CBOE",
    "XSP": "CBOE",
}


def is_index_symbol(symbol: Optional[str]) -> bool:
    """True iff IBKR exposes this symbol via secType=IND, not STK."""
    if not symbol:
        return False
    return symbol.upper() in INDEX_SYMBOLS


def index_exchange_for(symbol: Optional[str]) -> Optional[str]:
    """Return IBKR exchange code for an index symbol, or None when unknown."""
    if not symbol:
        return None
    return INDEX_SYMBOLS.get(symbol.upper())
