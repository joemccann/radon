#!/usr/bin/env python3
"""Fetch option chain data from IB for a given symbol.

Usage:
    python3 scripts/ib_option_chain.py --symbol AAPL
    python3 scripts/ib_option_chain.py --symbol AAPL --expiry 20260417
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.clients.ib_client import IBClient
from scripts.utils.ib_preflight import IB_REQUEST_TIMEOUT_S

# Transient-handshake resilience. The Gateway serialises API handshakes, so a
# connect issued while several scheduled scans are claiming their own sockets
# can time out even though the Gateway is authenticated and healthy — ib_insync
# surfaces that as `API connection failed: TimeoutError()`, which the
# auto-allocator does not treat as a collision and therefore does not rotate.
# Production 2026-08-27: /options/expirations?symbol=NOW 504'd on the single
# attempt and served in 1.1s two minutes later. Sibling `ib_chain.py` already
# retries this class; the budget below leaves room for the two bounded IB
# requests inside _EQUITY_OPTIONS_CHAIN_TIMEOUT_S (45s).
CONNECT_ATTEMPTS = 3
CONNECT_TIMEOUT_S = 4
CONNECT_BACKOFF_S = 1.0


def _normalize_expiry(expiry) -> str:
    return str(expiry).replace("-", "")


def _chain_expirations(chain) -> set[str]:
    return {_normalize_expiry(expiry) for expiry in getattr(chain, "expirations", [])}


def _canonical_trading_class_chains(chains, symbol: str) -> list:
    symbol_upper = symbol.upper()
    canonical = [
        chain
        for chain in chains
        if str(getattr(chain, "tradingClass", "")).upper() == symbol_upper
    ]
    return canonical or list(chains)


def _chains_for_expiry(chains, symbol: str, expiry: str) -> list:
    normalized_expiry = _normalize_expiry(expiry)
    matching = [
        chain
        for chain in chains
        if normalized_expiry in _chain_expirations(chain)
    ]
    return _canonical_trading_class_chains(matching, symbol)


def _union_strikes(chains) -> list[float]:
    return sorted({float(strike) for chain in chains for strike in getattr(chain, "strikes", [])})


def _preferred_exchange(chains) -> str:
    if not chains:
        return ""
    for chain in chains:
        exchange = str(getattr(chain, "exchange", ""))
        if exchange.upper() == "SMART":
            return exchange
    return str(getattr(chains[0], "exchange", ""))


def _preferred_multiplier(chains) -> str:
    if not chains:
        return ""
    return str(getattr(chains[0], "multiplier", ""))


def _connect_with_retry(client, port: int, client_id) -> None:
    """Connect, retrying a transient handshake timeout within a bounded budget.

    Raises the last exception when every attempt fails, so the caller's error
    envelope still names the timeout and the endpoint still answers 504.
    """
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            client.connect(
                port=port, client_id=client_id, timeout=CONNECT_TIMEOUT_S
            )
            return
        except Exception:
            if attempt == CONNECT_ATTEMPTS:
                raise
            time.sleep(CONNECT_BACKOFF_S)


def _set_ib_request_timeout(ib, timeout_s: float = IB_REQUEST_TIMEOUT_S) -> None:
    """Bound sync ib_insync requests; default RequestTimeout can block forever."""
    setattr(ib, "RequestTimeout", timeout_s)


def _qualify_underlying(ib, underlying, timeout_s: float = IB_REQUEST_TIMEOUT_S) -> None:
    _set_ib_request_timeout(ib, timeout_s)
    try:
        ib.qualifyContracts(underlying)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise TimeoutError(
            f"ib_insync qualifyContracts timed out after {timeout_s}s"
        ) from exc


def _request_option_chains(
    ib,
    symbol: str,
    sec_type: str,
    con_id: int,
    timeout_s: float = IB_REQUEST_TIMEOUT_S,
):
    _set_ib_request_timeout(ib, timeout_s)
    try:
        return ib.reqSecDefOptParams(symbol, "", sec_type, con_id)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise TimeoutError(
            f"ib_insync reqSecDefOptParams timed out after {timeout_s}s"
        ) from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--expiry", default=None, help="If provided, fetch strikes for this expiry")
    parser.add_argument("--port", type=int, default=4001)
    # client_id="auto" rotates through SUBPROCESS_ID_RANGE (20-49) so parallel
    # chain fetches (one per expiry) don't collide on a single hardcoded ID.
    # Accept int overrides for ad-hoc CLI use; default routes through the
    # auto-allocator that the rest of the subprocess scripts use.
    parser.add_argument("--client-id", default="auto")
    args = parser.parse_args()

    client_id = int(args.client_id) if args.client_id != "auto" else "auto"
    client = IBClient()

    try:
        _connect_with_retry(client, args.port, client_id)

        # Qualify the underlying to get a valid conId (required by reqSecDefOptParams).
        # Index symbols (VIX, SPX, NDX, RUT, XSP, VVIX) MUST be qualified as
        # `Index(secType=IND, exchange=CBOE/NASDAQ)` — hardcoding `Stock(SMART)`
        # silently fails because IB has no STK listing for them. The
        # resolver in `clients/contract_resolver.py` returns the right
        # contract type per symbol. The downstream `reqSecDefOptParams` call
        # must also use the matching `underlyingSecType` ("IND" for indices,
        # "STK" for stocks) or IB returns an empty chain list.
        # Repro (2026-05-27): `/api/options/expirations?symbol=VIX` returned
        # 502 because the chain script always passed "STK".
        from scripts.clients.contract_resolver import (
            is_index_symbol,
            resolve_quote_contract,
        )

        underlying = resolve_quote_contract(args.symbol)
        _qualify_underlying(client._ib, underlying)
        if not underlying.conId:
            print(json.dumps({"error": f"Could not qualify {args.symbol}"}))
            return

        sec_type = "IND" if is_index_symbol(args.symbol) else "STK"
        chains = _request_option_chains(
            client._ib, args.symbol, sec_type, underlying.conId
        )

        if args.expiry:
            # IB can return both adjusted and standard option chains for the
            # same expiry. Prefer the canonical trading class and union strikes
            # across matching venues so an adjusted chain listed first does not
            # collapse the visible book to a handful of strikes.
            target_chains = _chains_for_expiry(chains, args.symbol, args.expiry)

            if not target_chains:
                print(json.dumps({"error": f"No chain found for expiry {args.expiry}"}))
                return

            # Get strikes for this expiry
            strikes = _union_strikes(target_chains)

            print(json.dumps({
                "symbol": args.symbol,
                "expiry": args.expiry,
                "exchange": _preferred_exchange(target_chains),
                "strikes": strikes,
                "multiplier": _preferred_multiplier(target_chains),
            }))
        else:
            # Fetch all expirations
            all_expirations = set()
            exchanges = []
            for chain in _canonical_trading_class_chains(chains, args.symbol):
                for exp in chain.expirations:
                    all_expirations.add(_normalize_expiry(exp))
                exchanges.append(chain.exchange)

            expirations = sorted(all_expirations)

            print(json.dumps({
                "symbol": args.symbol,
                "expirations": expirations,
                "exchanges": exchanges,
            }))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
