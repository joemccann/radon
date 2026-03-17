#!/usr/bin/env python3
"""
Interactive Brokers Orders Sync

Connects to TWS/IB Gateway and syncs open orders + executed trades to orders.json

Requirements:
  pip install ib_insync

Usage:
  python3 scripts/ib_orders.py              # Display orders
  python3 scripts/ib_orders.py --sync       # Sync to orders.json
  python3 scripts/ib_orders.py --port 4001  # Custom port
"""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS["ib_orders"]

ORDERS_PATH = Path(__file__).parent.parent / "data" / "orders.json"

# IB uses this sentinel for "no value" on realizedPNL
IB_SENTINEL = 1.7976931348623157e308


def connect_ib(host: str, port: int, client_id="auto") -> IBClient:
    """Connect to TWS/IB Gateway, return an IBClient."""
    client = IBClient()
    try:
        client.connect(host=host, port=port, client_id=client_id)
        print(f"Connected to IB on {host}:{port}")
        return client
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)


def format_contract(contract) -> str:
    """Format contract into display string like 'ALAB C120'"""
    symbol = contract.symbol
    sec_type = contract.secType

    if sec_type == "STK":
        return symbol
    elif sec_type == "OPT":
        right = "C" if contract.right == "C" else "P"
        strike = int(contract.strike) if contract.strike == int(contract.strike) else contract.strike
        return f"{symbol} {right}{strike}"
    elif sec_type == "FUT":
        expiry = getattr(contract, "lastTradeDateOrContractMonth", "")
        return f"{symbol} {expiry}"
    elif sec_type == "BAG":
        return f"{symbol} Spread"
    else:
        return f"{symbol} ({sec_type})"


def serialize_contract(contract, resolved_legs: dict = None) -> dict:
    """Serialize contract fields for JSON.

    Args:
        contract: IB contract object.
        resolved_legs: Optional dict mapping conId -> qualified Contract for BAG legs.
    """
    expiry = getattr(contract, "lastTradeDateOrContractMonth", "")
    if len(expiry) == 8:
        expiry = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"

    result = {
        "conId": getattr(contract, "conId", None),
        "symbol": contract.symbol,
        "secType": contract.secType,
        "strike": getattr(contract, "strike", None),
        "right": getattr(contract, "right", None),
        "expiry": expiry or None,
    }

    # Include resolved combo leg details for BAG orders
    combo_legs = getattr(contract, "comboLegs", None)
    if contract.secType == "BAG" and combo_legs and resolved_legs:
        legs = []
        for cl in combo_legs:
            leg_data = {
                "conId": cl.conId,
                "ratio": cl.ratio,
                "action": cl.action,
            }
            resolved = resolved_legs.get(cl.conId)
            if resolved:
                leg_expiry = getattr(resolved, "lastTradeDateOrContractMonth", "")
                if len(leg_expiry) == 8:
                    leg_expiry = f"{leg_expiry[:4]}-{leg_expiry[4:6]}-{leg_expiry[6:8]}"
                leg_data["symbol"] = resolved.symbol
                leg_data["strike"] = getattr(resolved, "strike", None)
                leg_data["right"] = getattr(resolved, "right", None)
                leg_data["expiry"] = leg_expiry or None
            legs.append(leg_data)
        result["comboLegs"] = legs

    return result


def safe_float(value) -> Optional[float]:
    """Convert IB value to float, filtering sentinel values"""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f) or abs(f) >= IB_SENTINEL:
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def fetch_open_orders(client: IBClient) -> list:
    """Fetch open orders from all clients.

    For BAG orders, resolves combo leg conIds to full contract details
    (symbol, strike, right, expiry) so the frontend can subscribe to
    real-time prices and compute BID/MID/ASK for spread modification.
    """
    trades = client.get_open_orders()

    # Collect all unique combo leg conIds that need resolving
    combo_con_ids: set = set()
    for trade in trades:
        contract = trade.contract
        combo_legs = getattr(contract, "comboLegs", None)
        if contract.secType == "BAG" and combo_legs:
            for cl in combo_legs:
                combo_con_ids.add(cl.conId)

    # Batch-resolve combo leg contracts
    resolved_legs: dict = {}
    if combo_con_ids:
        from ib_insync import Contract as IBContract
        for con_id in combo_con_ids:
            try:
                c = IBContract(conId=con_id)
                qualified = client.qualify_contracts(c)
                if qualified:
                    resolved_legs[con_id] = qualified[0]
            except Exception:
                pass  # Skip unresolvable legs — frontend falls back to portfolio

    orders = []
    for trade in trades:
        contract = trade.contract
        order = trade.order
        status = trade.orderStatus

        orders.append({
            "orderId": order.orderId,
            "permId": order.permId,
            "symbol": format_contract(contract),
            "contract": serialize_contract(contract, resolved_legs),
            "action": order.action,  # BUY or SELL
            "orderType": order.orderType,  # LMT, MKT, STP, etc.
            "totalQuantity": float(order.totalQuantity),
            "limitPrice": safe_float(order.lmtPrice),
            "auxPrice": safe_float(order.auxPrice),  # stop price
            "status": status.status,  # Submitted, PreSubmitted, etc.
            "filled": float(status.filled),
            "remaining": float(status.remaining),
            "avgFillPrice": safe_float(status.avgFillPrice),
            "tif": order.tif,  # DAY, GTC, IOC, etc.
        })

    return orders


def fetch_executed_orders(client: IBClient) -> list:
    """Fetch executed fills via fills()"""
    fills = client.get_fills()
    executed = []

    for fill in fills:
        contract = fill.contract
        execution = fill.execution
        commission_report = fill.commissionReport

        realized_pnl = safe_float(getattr(commission_report, "realizedPNL", None))
        commission = safe_float(getattr(commission_report, "commission", None))

        executed.append({
            "execId": execution.execId,
            "symbol": format_contract(contract),
            "contract": serialize_contract(contract),
            "side": execution.side,  # BOT or SLD
            "quantity": float(execution.shares),
            "avgPrice": safe_float(execution.avgPrice),
            "commission": commission,
            "realizedPNL": realized_pnl,
            "time": execution.time.isoformat() if hasattr(execution.time, "isoformat") else str(execution.time),
            "exchange": execution.exchange,
        })

    # Sort by time descending (most recent first)
    executed.sort(key=lambda x: x["time"], reverse=True)
    return executed


def build_orders_data(open_orders: list, executed_orders: list) -> dict:
    """Build the orders.json structure"""
    return {
        "last_sync": datetime.now().isoformat(),
        "open_orders": open_orders,
        "executed_orders": executed_orders,
        "open_count": len(open_orders),
        "executed_count": len(executed_orders),
    }


def save_orders(data: dict):
    """Save orders to JSON file"""
    ORDERS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if ORDERS_PATH.exists():
        backup = ORDERS_PATH.with_suffix(".json.bak")
        backup.write_text(ORDERS_PATH.read_text())

    with open(ORDERS_PATH, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Saved orders to {ORDERS_PATH}")


def display_orders(open_orders: list, executed_orders: list):
    """Pretty print orders"""
    print(f"\n{'='*60}")
    print("OPEN ORDERS")
    print(f"{'='*60}")

    if not open_orders:
        print("  No open orders")
    else:
        for o in open_orders:
            lmt = f" @ ${o['limitPrice']}" if o["limitPrice"] else ""
            print(f"  {o['action']} {o['totalQuantity']:.0f}x {o['symbol']}{lmt} [{o['orderType']}] — {o['status']}")

    print(f"\n{'='*60}")
    print("EXECUTED ORDERS")
    print(f"{'='*60}")

    if not executed_orders:
        print("  No fills this session")
    else:
        for e in executed_orders:
            side = "BUY" if e["side"] == "BOT" else "SELL"
            pnl_str = f" P&L: ${e['realizedPNL']:,.2f}" if e["realizedPNL"] is not None else ""
            print(f"  {side} {e['quantity']:.0f}x {e['symbol']} @ ${e['avgPrice']}{pnl_str} — {e['time']}")

    print(f"\nSummary: {len(open_orders)} open, {len(executed_orders)} executed")


def main():
    parser = argparse.ArgumentParser(description="Sync orders from Interactive Brokers")
    parser.add_argument("--host", default=DEFAULT_HOST, help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TWS/Gateway port")
    parser.add_argument("--client-id", type=int, default=None, help="Client ID (omit for auto-allocation)")
    parser.add_argument("--sync", action="store_true", help="Sync to orders.json")

    args = parser.parse_args()

    client = connect_ib(args.host, args.port, args.client_id or "auto")

    try:
        print("Fetching open orders...")
        open_orders = fetch_open_orders(client)

        print("Fetching executed fills...")
        executed_orders = fetch_executed_orders(client)

        display_orders(open_orders, executed_orders)

        if args.sync:
            data = build_orders_data(open_orders, executed_orders)
            save_orders(data)
        else:
            print("\nRun with --sync to save to orders.json")

    finally:
        client.disconnect()
        print("Disconnected from IB")


if __name__ == "__main__":
    main()
