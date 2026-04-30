#!/usr/bin/env python3
"""
Naked Short Audit — scan open orders and cancel naked short violations.

⚠️ DISABLED 2026-04-30: ``find_naked_short_violations`` short-circuits to
``[]``. The detection logic is preserved in ``_find_naked_short_violations_impl``
for re-enable. See "Naked Short Protection (Gate 4)" in ``CLAUDE.md`` for the
6-step re-enable procedure.

Detects orders that would create naked short positions (stock or call options)
and optionally cancels them via IB Gateway.

Usage:
  python3 scripts/naked_short_audit.py --dry-run
  python3 scripts/naked_short_audit.py
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add parent so clients is importable when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = {"Submitted", "PreSubmitted"}
DATA_DIR = Path(__file__).parent.parent / "data"


def _get_stock_shares(positions: list, ticker: str) -> int:
    """Sum shares held for a ticker across all Stock positions."""
    total = 0
    for pos in positions:
        if pos.get("ticker", pos.get("symbol", "")).upper() != ticker.upper():
            continue
        if pos.get("structure_type") != "Stock":
            continue
        total += int(pos.get("contracts", 0))
    return total


def _get_short_call_contracts(positions: list, ticker: str) -> int:
    """Sum existing SHORT call contracts for a ticker across all positions."""
    total = 0
    for pos in positions:
        if pos.get("ticker", pos.get("symbol", "")).upper() != ticker.upper():
            continue
        for leg in pos.get("legs", []):
            if leg.get("direction") == "SHORT" and leg.get("type") == "Call":
                total += int(leg.get("contracts", 0))
    return total


def find_naked_short_violations(orders: list, positions: list) -> list:
    """Pure function: detect naked short violations in open orders.

    GUARD DISABLED — always returns []. Re-enable by removing the early
    return below.
    """
    return []


def _find_naked_short_violations_impl(orders: list, positions: list) -> list:
    """Original detection logic, retained for reference."""
    violations = []

    for order in orders:
        # Only check active orders
        if order.get("status") not in ACTIVE_STATUSES:
            continue

        action = order.get("action", "").upper()
        if action != "SELL":
            continue

        contract = order.get("contract", {})
        sec_type = contract.get("secType", "")
        symbol = contract.get("symbol", "")
        qty = int(order.get("totalQuantity", 0))
        order_id = order.get("orderId")
        perm_id = order.get("permId")

        # BAG/combo orders are spreads — never a violation
        if sec_type == "BAG":
            continue

        # SELL stock
        if sec_type == "STK":
            shares_held = _get_stock_shares(positions, symbol)
            if shares_held == 0:
                violations.append({
                    "order_id": order_id,
                    "perm_id": perm_id,
                    "symbol": symbol,
                    "reason": f"SELL {qty} shares of {symbol}: no LONG stock position exists",
                })
            elif qty > shares_held:
                violations.append({
                    "order_id": order_id,
                    "perm_id": perm_id,
                    "symbol": symbol,
                    "reason": (f"SELL {qty} shares of {symbol} exceeds "
                               f"{shares_held} shares held"),
                })
            continue

        # SELL option
        if sec_type == "OPT":
            right = contract.get("right", "").upper()

            # SELL put is cash-secured — never a violation
            if right == "P":
                continue

            # SELL call — must be covered by stock
            if right == "C":
                shares_held = _get_stock_shares(positions, symbol)
                if shares_held == 0:
                    violations.append({
                        "order_id": order_id,
                        "perm_id": perm_id,
                        "symbol": symbol,
                        "reason": (f"SELL {qty} call(s) on {symbol}: "
                                   f"no LONG stock position — naked short call"),
                    })
                else:
                    existing_short_calls = _get_short_call_contracts(positions, symbol)
                    total_short_calls = existing_short_calls + qty
                    shares_needed = total_short_calls * 100
                    if shares_needed > shares_held:
                        violations.append({
                            "order_id": order_id,
                            "perm_id": perm_id,
                            "symbol": symbol,
                            "reason": (f"SELL {qty} call(s) on {symbol}: "
                                       f"total short calls ({total_short_calls}) * 100 = "
                                       f"{shares_needed} shares needed, "
                                       f"only {shares_held} held — under-covered"),
                        })
            continue

    return violations


def cancel_violations(client, violations: list) -> int:
    """Cancel each violating order via IBClient.

    Args:
        client: connected IBClient instance
        violations: list of violation dicts from find_naked_short_violations

    Returns:
        Count of orders cancelled.
    """
    if not violations:
        return 0

    cancelled = 0
    for v in violations:
        order_id = v["order_id"]
        perm_id = v["perm_id"]
        symbol = v["symbol"]
        try:
            trades = client.get_open_orders()
            trade = None
            # Find by permId first
            if perm_id and perm_id > 0:
                for t in trades:
                    if t.order.permId == perm_id:
                        trade = t
                        break
            # Fallback to orderId
            if trade is None and order_id and order_id > 0:
                for t in trades:
                    if t.order.orderId == order_id:
                        trade = t
                        break

            if trade is None:
                logger.warning("Order not found for cancellation: %s (orderId=%s, permId=%s)",
                               symbol, order_id, perm_id)
                continue

            client.cancel_order(trade.order)
            client.sleep(1)
            cancelled += 1
            logger.info("Cancelled naked short violation: %s orderId=%s — %s",
                        symbol, order_id, v["reason"])
        except Exception as e:
            logger.error("Failed to cancel order %s (orderId=%s): %s",
                         symbol, order_id, e)

    return cancelled


def main(argv=None):
    """CLI entry point. Returns summary dict (for testing)."""
    parser = argparse.ArgumentParser(description="Naked short audit — detect and cancel violations")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print violations without cancelling")
    parser.add_argument("--portfolio", type=str,
                        default=str(DATA_DIR / "portfolio.json"),
                        help="Path to portfolio.json")
    parser.add_argument("--orders", type=str,
                        default=str(DATA_DIR / "orders.json"),
                        help="Path to orders.json")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_GATEWAY_PORT)

    args = parser.parse_args(argv)

    # Load data
    with open(args.portfolio) as f:
        portfolio_data = json.load(f)
    with open(args.orders) as f:
        orders_data = json.load(f)

    positions = portfolio_data.get("positions", [])
    orders = orders_data.get("open_orders", [])

    # Detect violations
    violations = find_naked_short_violations(orders, positions)

    summary = {
        "violations_found": len(violations),
        "violations": violations,
        "cancelled": 0,
        "dry_run": args.dry_run,
    }

    if not violations:
        print(json.dumps(summary, indent=2))
        return summary

    if args.dry_run:
        print(json.dumps(summary, indent=2))
        return summary

    # Live mode: connect and cancel
    client = IBClient()
    try:
        client.connect(host=args.host, port=args.port,
                       client_id=CLIENT_IDS.get("ib_order_manage", 25))
        summary["cancelled"] = cancel_violations(client, violations)
    except Exception as e:
        logger.error("IB connection failed: %s", e)
        summary["error"] = str(e)
    finally:
        try:
            client.disconnect()
        except Exception:
            pass

    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
