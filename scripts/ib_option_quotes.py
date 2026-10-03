#!/usr/bin/env python3
"""Snapshot-price option strikes around spot from IB for one or more expiries.

Feeds the assistant's priced chain (`GET /options/ib-quotes`). One connection
serves every requested expiry, so a term-structure read costs one secdef call
and batched snapshots instead of a subprocess per tenor.

Usage:
    python3 scripts/ib_option_quotes.py --symbol SPCX --expiries 20261030,20261120 --wings 4 --right C
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.clients.ib_client import IBClient, is_valid_ib_number
from scripts.ib_option_chain import (
    _chains_for_expiry,
    _connect_with_retry,
    _qualify_underlying,
    _request_option_chains,
    _set_ib_request_timeout,
    _union_strikes,
)
from scripts.utils.ib_preflight import IB_REQUEST_TIMEOUT_S

# IB caps concurrent market-data lines (~100 on a base account); snapshots in
# batches stay well under it alongside the relay's streaming subscriptions.
SNAPSHOT_BATCH = 40


def _num(value: Any) -> Optional[float]:
    if not is_valid_ib_number(value):
        return None
    return float(value)


def _positive(value: Any) -> Optional[float]:
    num = _num(value)
    return num if num is not None and num > 0 else None


def _iso(expiry: str) -> str:
    digits = str(expiry).replace("-", "")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else digits


def select_strikes(strikes: list[float], spot: Optional[float], wings: int) -> list[float]:
    """The 2*wings+1 strikes nearest spot, in strike order."""
    ordered = sorted({float(s) for s in strikes})
    width = wings * 2 + 1
    if not ordered:
        return []
    if spot is None or spot <= 0:
        start = max(0, len(ordered) // 2 - wings)
        return ordered[start : start + width]
    nearest = sorted(ordered, key=lambda strike: abs(strike - spot))[:width]
    return sorted(nearest)


def spot_from_ticker(ticker: Any) -> Optional[float]:
    if ticker is None:
        return None
    last = _positive(getattr(ticker, "last", None))
    if last is not None:
        return last
    bid = _positive(getattr(ticker, "bid", None))
    ask = _positive(getattr(ticker, "ask", None))
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    return _positive(getattr(ticker, "close", None))


def quote_row(ticker: Any) -> dict:
    """One IB ticker in the chat contract shape (`assistant_market.normalize_contract`)."""
    contract = ticker.contract
    bid = _positive(ticker.bid)
    ask = _positive(ticker.ask)
    greeks = getattr(ticker, "modelGreeks", None) or getattr(ticker, "lastGreeks", None)

    def greek(name: str) -> Optional[float]:
        return _num(getattr(greeks, name, None)) if greeks is not None else None

    volume = _num(getattr(ticker, "volume", None))
    return {
        "strike": float(contract.strike),
        "right": contract.right,
        "expiry": _iso(contract.lastTradeDateOrContractMonth),
        "bid": bid,
        "ask": ask,
        "mid": round((bid + ask) / 2.0, 4) if bid is not None and ask is not None else None,
        "last": _positive(getattr(ticker, "last", None)),
        "iv": greek("impliedVol"),
        "oi": None,
        "volume": int(volume) if volume is not None and volume >= 0 else None,
        "delta": greek("delta"),
        "gamma": greek("gamma"),
        "theta": greek("theta"),
        "vega": greek("vega"),
    }


def _req_tickers(ib, contracts: list, timeout_s: float = IB_REQUEST_TIMEOUT_S) -> list:
    _set_ib_request_timeout(ib, timeout_s)
    tickers: list = []
    for start in range(0, len(contracts), SNAPSHOT_BATCH):
        try:
            tickers.extend(ib.reqTickers(*contracts[start : start + SNAPSHOT_BATCH]))
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise TimeoutError(f"ib_insync reqTickers timed out after {timeout_s}s") from exc
    return tickers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--expiries", required=True, help="Comma-separated YYYYMMDD")
    parser.add_argument("--wings", type=int, default=8)
    parser.add_argument("--right", choices=["C", "P"], default=None)
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", default="auto")
    args = parser.parse_args()

    from ib_insync import Option

    from scripts.clients.contract_resolver import is_index_symbol, resolve_quote_contract

    symbol = args.symbol.upper()
    expiries = [e.replace("-", "") for e in args.expiries.split(",") if e.strip()]
    rights = [args.right] if args.right else ["C", "P"]
    client_id = int(args.client_id) if args.client_id != "auto" else "auto"
    client = IBClient()

    try:
        _connect_with_retry(client, args.port, client_id)
        ib = client._ib
        # Delayed-frozen: live where entitled, last frozen book after hours.
        ib.reqMarketDataType(4)

        underlying = resolve_quote_contract(symbol)
        _qualify_underlying(ib, underlying)
        if not underlying.conId:
            print(json.dumps({"error": f"Could not qualify {symbol}"}))
            return

        [und_ticker] = _req_tickers(ib, [underlying])
        spot = spot_from_ticker(und_ticker)

        sec_type = "IND" if is_index_symbol(symbol) else "STK"
        chains = _request_option_chains(ib, symbol, sec_type, underlying.conId)

        contracts = []
        missing: list[str] = []
        for expiry in expiries:
            selected = _chains_for_expiry(chains, symbol, expiry)
            if not selected:
                missing.append(_iso(expiry))
                continue
            exchange = "SMART" if any(c.exchange == "SMART" for c in selected) else selected[0].exchange
            trading_class = selected[0].tradingClass
            for strike in select_strikes(_union_strikes(selected), spot, args.wings):
                for right in rights:
                    contracts.append(
                        Option(symbol, expiry, strike, right, exchange, tradingClass=trading_class)
                    )

        _set_ib_request_timeout(ib, IB_REQUEST_TIMEOUT_S)
        qualified = [c for c in ib.qualifyContracts(*contracts) if c and getattr(c, "conId", 0)]
        by_expiry: dict[str, list[dict]] = {_iso(e): [] for e in expiries if _iso(e) not in missing}
        for ticker in _req_tickers(ib, qualified):
            row = quote_row(ticker)
            by_expiry.setdefault(row["expiry"], []).append(row)
        for rows in by_expiry.values():
            rows.sort(key=lambda row: (row["strike"], row["right"]))

        print(json.dumps({
            "ticker": symbol,
            "spot": spot,
            "source": "ib",
            "expirations": by_expiry,
            "missing_expiries": missing,
        }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
