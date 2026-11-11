"""Centralized IBKR contract resolution.

Every script that constructs a Stock(symbol, "SMART", "USD") to look up
quotes / qualify a contract / place an order should route through here
instead. Indices (VIX, SPX, NDX, RUT, ...) need `secType=IND` not STK
or IBKR returns no data.

Surface: STK + IND quotes, Future() and Option() (index options).
"""
from __future__ import annotations

from typing import Optional

from utils.index_symbols import index_exchange_for, is_index_symbol

# ib_insync may not be importable in all contexts (tests, isolated
# scripts). Lazy-import inside the function so callers that pass in an
# already-built contract still work.


def resolve_quote_contract(symbol: str):
    """Return the ib_insync Contract used for quote / chain / historical lookups.

    Order of precedence:
      1. Symbol is in INDEX_SYMBOLS → `Index(symbol, currency='USD', exchange=<from table>)`
      2. Otherwise → `Stock(symbol, 'SMART', 'USD')`

    Raises ValueError if `symbol` is empty.
    """
    if not symbol or not symbol.strip():
        raise ValueError("symbol is required")

    from ib_insync import Index, Stock

    upper = symbol.strip().upper()
    if is_index_symbol(upper):
        exchange = index_exchange_for(upper) or "CBOE"
        # ib_insync.Index(symbol, exchange, currency) — positional.
        return Index(upper, exchange, "USD")
    return Stock(upper, "SMART", "USD")


def is_tradeable(symbol: str) -> bool:
    """False for index symbols (you can only trade futures/options on them)."""
    return not is_index_symbol(symbol)


# Map of underlying symbol → futures root + exchange (VIX on CFE;
# SPX/NDX/RUT on CME).
FUTURES_ROOTS: dict[str, dict[str, str]] = {
    "VIX": {"root": "VIX", "exchange": "CFE", "multiplier": "1000"},
    # Cash indices → their CME E-mini future. `root` is IB's contract symbol;
    # multiplier pins the E-mini (excludes the Micro: MES/MNQ/M2K).
    "SPX": {"root": "ES", "exchange": "CME", "multiplier": "50"},
    "NDX": {"root": "NQ", "exchange": "CME", "multiplier": "20"},
    "RUT": {"root": "RTY", "exchange": "CME", "multiplier": "50"},
}


def supports_futures(symbol: str) -> bool:
    """True iff Radon knows how to resolve a futures contract for this symbol."""
    return symbol.upper() in FUTURES_ROOTS


def resolve_future_contract(symbol: str, expiry: str = ""):
    """Return ib_insync Future contract for `symbol` at `expiry` (YYYYMM / YYYYMMDD).

    Pass empty `expiry` to get a partial contract suitable for
    `reqContractDetails` (returns the full chain). For order placement
    `expiry` is required.
    """
    if not symbol or not symbol.strip():
        raise ValueError("symbol is required")

    from ib_insync import Future

    upper = symbol.strip().upper()
    if upper not in FUTURES_ROOTS:
        raise ValueError(f"futures not supported for {upper}")

    meta = FUTURES_ROOTS[upper]
    contract = Future(
        symbol=meta["root"],
        lastTradeDateOrContractMonth=expiry,
        exchange=meta["exchange"],
        currency="USD",
    )
    if meta.get("multiplier"):
        contract.multiplier = meta["multiplier"]
    return contract


# Indices that have CBOE-traded options. Each entry pins the exchange
# + tradingClass IB needs to disambiguate from weeklies (e.g. VIXW)
# and other related products. Multiplier is informational; IB sets it.
INDEX_OPTION_ROOTS: dict[str, dict[str, str]] = {
    "VIX": {"tradingClass": "VIX", "exchange": "CBOE", "multiplier": "100"},
    "SPX": {"tradingClass": "SPX", "exchange": "CBOE", "multiplier": "100"},
    "NDX": {"tradingClass": "NDX", "exchange": "CBOE", "multiplier": "100"},
    "RUT": {"tradingClass": "RUT", "exchange": "CBOE", "multiplier": "100"},
    "XSP": {"tradingClass": "XSP", "exchange": "CBOE", "multiplier": "100"},
}


def supports_index_options(symbol: str) -> bool:
    """True iff Radon knows how to resolve index options for this symbol."""
    return symbol.upper() in INDEX_OPTION_ROOTS


def resolve_option_contract(
    symbol: str,
    expiry: str = "",
    strike: float | None = None,
    right: str = "",
):
    """Return ib_insync Option contract for `symbol`.

    Indices (VIX/SPX/NDX/RUT/XSP) route to CBOE with explicit
    tradingClass so IB doesn't pick a weekly (VIXW) or related root.
    Equities use exchange=SMART (legacy behaviour).

    Pass blank `expiry` / `strike` / `right` to get a partial contract
    suitable for `reqContractDetails` (chain enumeration).
    """
    if not symbol or not symbol.strip():
        raise ValueError("symbol is required")

    from ib_insync import Option

    upper = symbol.strip().upper()

    if upper in INDEX_OPTION_ROOTS:
        meta = INDEX_OPTION_ROOTS[upper]
        contract = Option(
            symbol=upper,
            lastTradeDateOrContractMonth=expiry,
            strike=float(strike) if strike is not None else 0.0,
            right=right,
            exchange=meta["exchange"],
            currency="USD",
        )
        contract.tradingClass = meta["tradingClass"]
        if meta.get("multiplier"):
            contract.multiplier = meta["multiplier"]
        return contract

    # Equity option — legacy SMART routing
    return Option(
        symbol=upper,
        lastTradeDateOrContractMonth=expiry,
        strike=float(strike) if strike is not None else 0.0,
        right=right,
        exchange="SMART",
        currency="USD",
    )
