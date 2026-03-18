#!/usr/bin/env python3
"""
Interactive Brokers Order Management — Cancel & Modify

Connects to TWS/IB Gateway to cancel or modify open orders.
All output is JSON to stdout for API route parsing.

Usage:
  python3 scripts/ib_order_manage.py cancel --order-id 10 --perm-id 12345
  python3 scripts/ib_order_manage.py modify --order-id 10 --perm-id 12345 --new-price 22.50
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Add parent so clients is importable when run from project root
sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS["ib_order_manage"]


def output(status: str, message: str, **extra):
    """Print JSON result and exit."""
    print(json.dumps({"status": status, "message": message, **extra}))
    sys.exit(0 if status == "ok" else 1)


def json_number(value):
    """Return JSON-safe numeric primitives, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def modify_is_confirmed(trade, new_price, new_quantity, outside_rth):
    """Check refreshed IB open-order state for the requested modify fields."""
    if trade is None:
        return False

    status = trade.orderStatus.status
    if status not in ("Submitted", "PreSubmitted"):
        return False

    if new_price is not None:
        current_price = trade.order.lmtPrice
        if current_price is None or abs(current_price - new_price) >= 0.001:
            return False

    if new_quantity is not None and trade.order.totalQuantity != new_quantity:
        return False

    if outside_rth is not None and getattr(trade.order, "outsideRth", None) is not outside_rth:
        return False

    return True


def find_trade(client: IBClient, order_id: int, perm_id: int):
    """Find an open trade by permId (preferred) or orderId."""
    trades = client.get_open_orders()

    # Prefer permId (globally unique across IB sessions)
    if perm_id > 0:
        for trade in trades:
            if trade.order.permId == perm_id:
                return trade

    # Fallback to orderId
    if order_id > 0:
        for trade in trades:
            if trade.order.orderId == order_id:
                return trade

    return None


def cancel_order(client: IBClient, order_id: int, perm_id: int,
                 host: str, port: int):
    """Cancel an open order.

    IB's cancelOrder is scoped by (clientId, orderId) -- a cancel from a
    different clientId than the one that placed the order fails with
    Error 10147 (order not found).  We detect the original clientId
    from trade.order.clientId and reconnect as that client before cancelling.
    """
    trade = find_trade(client, order_id, perm_id)
    if trade is None:
        output("error", f"Trade not found (orderId={order_id}, permId={perm_id})")

    status = trade.orderStatus.status
    if status in ("Filled", "Cancelled", "ApiCancelled"):
        output("error", f"Order already {status} — cannot cancel")

    # Reconnect as the original placer if needed (fixes Error 10147)
    original_client_id = trade.order.clientId
    if client.ib.client.clientId != original_client_id:
        client.disconnect()
        client.connect(host=host, port=port, client_id=original_client_id)
        trade = find_trade(client, order_id, perm_id)
        if trade is None:
            output("error", "Trade not found after reconnect as original clientId")

    # Capture IB error events during the cancel attempt
    error_msgs = []
    def on_error(reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if reqId == trade.order.orderId or reqId == -1:
            error_msgs.append((errorCode, errorString))

    client.ib.errorEvent += on_error

    client.cancel_order(trade.order)
    # Wait for status change
    for _ in range(10):
        client.sleep(0.5)
        if trade.orderStatus.status in ("Cancelled", "ApiCancelled"):
            break
        # Check for fatal errors (10147=order not found, 201=rejected)
        fatal = [e for e in error_msgs if e[0] in (10147, 201)]
        if fatal:
            client.ib.errorEvent -= on_error
            output("error", f"IB rejected cancel: {fatal[0][1]}")

    client.ib.errorEvent -= on_error

    final_status = trade.orderStatus.status
    if final_status in ("Cancelled", "ApiCancelled"):
        output("ok", f"Order cancelled (orderId={trade.order.orderId})",
               orderId=trade.order.orderId, finalStatus=final_status)
    else:
        output("error", f"Cancel failed — order still {final_status}",
               orderId=trade.order.orderId, finalStatus=final_status)


def modify_order(client: IBClient, order_id: int, perm_id: int, new_price: Optional[float],
                 host: str, port: int, outside_rth=None, new_quantity: Optional[int] = None):
    """Modify an open order.

    IB's placeOrder is scoped by (clientId, orderId) -- a modify from a
    different clientId than the one that placed the order silently fails
    with Error 103 (Duplicate order id).  We detect the original clientId
    from trade.order.clientId and reconnect as that client before modifying.
    """
    trade = find_trade(client, order_id, perm_id)
    if trade is None:
        output("error", f"Trade not found (orderId={order_id}, permId={perm_id})")

    status = trade.orderStatus.status
    if status in ("Filled", "Cancelled", "ApiCancelled"):
        output("error", f"Order already {status} — cannot modify")

    order_type = trade.order.orderType
    if order_type not in ("LMT", "STP LMT"):
        output("error", f"Cannot modify price on {order_type} order — only LMT and STP LMT supported")

    if new_price is None and new_quantity is None and outside_rth is None:
        output("error", "Must provide at least one modify field")

    if new_price is not None and new_price <= 0:
        output("error", "New price must be > 0")
    if new_quantity is not None and new_quantity <= 0:
        output("error", "New quantity must be > 0")

    # Reconnect as the original placer if needed (fixes Error 103)
    original_client_id = trade.order.clientId
    if client.ib.client.clientId != original_client_id:
        client.disconnect()
        client.connect(host=host, port=port, client_id=original_client_id)
        trade = find_trade(client, order_id, perm_id)
        if trade is None:
            output("error", "Trade not found after reconnect as original clientId")

    # Capture IB error events during the modify attempt
    error_msgs = []
    def on_error(reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if reqId == trade.order.orderId or reqId == -1:
            error_msgs.append((errorCode, errorString))

    client.ib.errorEvent += on_error

    old_price = trade.order.lmtPrice
    old_quantity = trade.order.totalQuantity
    applied_price = old_price
    if new_price is not None:
        trade.order.lmtPrice = new_price
        applied_price = new_price
    if new_quantity is not None:
        trade.order.totalQuantity = new_quantity
    if outside_rth is not None:
        trade.order.outsideRth = outside_rth
    client.place_order(trade.contract, trade.order)

    # Wait for refreshed IB state to reflect the requested modify.
    confirmed_trade = None
    latest_trade = trade
    for _ in range(10):
        client.sleep(0.5)
        refreshed_trade = find_trade(client, order_id, perm_id)
        if refreshed_trade is not None:
            latest_trade = refreshed_trade
        if modify_is_confirmed(refreshed_trade, new_price, new_quantity, outside_rth):
            confirmed_trade = refreshed_trade
            break
        # Check for fatal errors (103=duplicate id, 201=rejected, 202=cancelled)
        fatal = [e for e in error_msgs if e[0] in (103, 201, 202)]
        if fatal:
            client.ib.errorEvent -= on_error
            output("error", f"IB rejected modify: {fatal[0][1]}")

    client.ib.errorEvent -= on_error

    if confirmed_trade is None:
        latest_status = latest_trade.orderStatus.status if latest_trade is not None else "Unknown"
        latest_price = latest_trade.order.lmtPrice if latest_trade is not None else None
        latest_quantity = json_number(latest_trade.order.totalQuantity) if latest_trade is not None else None
        output(
            "error",
            "Modify not confirmed by refreshed IB open orders",
            orderId=trade.order.orderId,
            requestedPrice=new_price,
            requestedQuantity=new_quantity,
            currentPrice=latest_price,
            currentQuantity=latest_quantity,
            finalStatus=latest_status,
        )

    final_status = confirmed_trade.orderStatus.status
    changes = []
    if new_price is not None and old_price != applied_price:
        changes.append(f"${old_price} → ${applied_price}")
    if new_quantity is not None and old_quantity != confirmed_trade.order.totalQuantity:
        changes.append(f"qty {old_quantity} → {confirmed_trade.order.totalQuantity}")
    if outside_rth is not None:
        changes.append("outside RTH updated")
    suffix = f": {', '.join(changes)}" if changes else ""
    output("ok", f"Order modified{suffix}",
           orderId=trade.order.orderId, oldPrice=old_price,
           newPrice=applied_price, oldQuantity=json_number(old_quantity),
           newQuantity=json_number(confirmed_trade.order.totalQuantity), finalStatus=final_status)


def main():
    parser = argparse.ArgumentParser(description="Cancel or modify IB orders")
    sub = parser.add_subparsers(dest="action", required=True)

    cancel_p = sub.add_parser("cancel")
    cancel_p.add_argument("--order-id", type=int, default=0)
    cancel_p.add_argument("--perm-id", type=int, default=0)
    cancel_p.add_argument("--host", default=DEFAULT_HOST)
    cancel_p.add_argument("--port", type=int, default=DEFAULT_PORT)

    modify_p = sub.add_parser("modify")
    modify_p.add_argument("--order-id", type=int, default=0)
    modify_p.add_argument("--perm-id", type=int, default=0)
    modify_p.add_argument("--new-price", type=float)
    modify_p.add_argument("--new-quantity", type=int)
    modify_p.add_argument("--outside-rth", action="store_true", default=None,
                          help="Allow order to fill outside regular trading hours")
    modify_p.add_argument("--no-outside-rth", dest="outside_rth", action="store_false",
                          help="Restrict order to regular trading hours only")
    modify_p.add_argument("--host", default=DEFAULT_HOST)
    modify_p.add_argument("--port", type=int, default=DEFAULT_PORT)

    args = parser.parse_args()

    if args.order_id == 0 and args.perm_id == 0:
        output("error", "Must provide --order-id or --perm-id")

    client = IBClient()
    try:
        client.connect(host=args.host, port=args.port, client_id="auto")
    except Exception as e:
        output("error", f"IB connection failed: {e}")

    try:
        if args.action == "cancel":
            cancel_order(client, args.order_id, args.perm_id,
                         args.host, args.port)
        elif args.action == "modify":
            modify_order(client, args.order_id, args.perm_id, args.new_price,
                         args.host, args.port, outside_rth=args.outside_rth,
                         new_quantity=args.new_quantity)
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
