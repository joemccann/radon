"""Pool-based order cancel/modify — no subprocess, no extra connections.

Routes cancel/modify through the IBPool's sync connection (clientId=0, master).
The master client can manage ALL orders regardless of which clientId placed them,
eliminating the need to spawn subprocess scripts with their own IB connections.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("radon.pool_order_manage")


def _find_trade(client, order_id: int, perm_id: int):
    """Find an open trade by permId (preferred) or orderId."""
    trades = client.get_open_orders()

    if perm_id > 0:
        for trade in trades:
            if trade.order.permId == perm_id:
                return trade

    if order_id > 0:
        for trade in trades:
            if trade.order.orderId == order_id:
                return trade

    return None


async def pool_cancel_order(
    client,
    order_id: int = 0,
    perm_id: int = 0,
    max_wait: float = 5.0,
) -> dict:
    """Cancel an open order using a pool connection (master clientId=0).

    Master client can cancel ANY order regardless of which clientId placed it.
    No reconnection needed — no subprocess spawned.
    """
    trade = await asyncio.to_thread(_find_trade, client, order_id, perm_id)
    if trade is None:
        return {"status": "error", "message": f"Trade not found (orderId={order_id}, permId={perm_id})"}

    status = trade.orderStatus.status
    if status in ("Filled", "Cancelled", "ApiCancelled"):
        return {"status": "error", "message": f"Order already {status} — cannot cancel"}

    await asyncio.to_thread(client.cancel_order, trade.order)

    # Poll for confirmation (order disappears or status → Cancelled)
    poll_interval = 0.5
    elapsed = 0.0
    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        refreshed = await asyncio.to_thread(_find_trade, client, order_id, perm_id)
        if refreshed is None:
            return {
                "status": "ok",
                "message": f"Order cancelled (orderId={trade.order.orderId})",
                "orderId": trade.order.orderId,
                "finalStatus": "Cancelled",
            }
        if refreshed.orderStatus.status in ("Cancelled", "ApiCancelled"):
            return {
                "status": "ok",
                "message": f"Order cancelled (orderId={trade.order.orderId})",
                "orderId": trade.order.orderId,
                "finalStatus": refreshed.orderStatus.status,
            }

    final_status = trade.orderStatus.status
    return {
        "status": "error",
        "message": f"Cancel not confirmed after {max_wait}s — order still {final_status}",
        "orderId": trade.order.orderId,
        "finalStatus": final_status,
    }


async def pool_modify_order(
    client,
    order_id: int = 0,
    perm_id: int = 0,
    new_price: Optional[float] = None,
    new_quantity: Optional[int] = None,
    outside_rth: Optional[bool] = None,
    max_wait: float = 5.0,
) -> dict:
    """Modify an open order using a pool connection (master clientId=0).

    Master client can modify ANY order regardless of which clientId placed it.
    No reconnection needed — no subprocess spawned.
    """
    trade = await asyncio.to_thread(_find_trade, client, order_id, perm_id)
    if trade is None:
        return {"status": "error", "message": f"Trade not found (orderId={order_id}, permId={perm_id})"}

    status = trade.orderStatus.status
    if status in ("Filled", "Cancelled", "ApiCancelled"):
        return {"status": "error", "message": f"Order already {status} — cannot modify"}

    order_type = trade.order.orderType
    if order_type not in ("LMT", "STP LMT"):
        return {"status": "error", "message": f"Cannot modify price on {order_type} order — only LMT and STP LMT supported"}

    if new_price is None and new_quantity is None and outside_rth is None:
        return {"status": "error", "message": "Must provide at least one modify field"}

    if new_price is not None and new_price <= 0:
        return {"status": "error", "message": "New price must be > 0"}
    if new_quantity is not None and new_quantity <= 0:
        return {"status": "error", "message": "New quantity must be > 0"}

    old_price = trade.order.lmtPrice
    old_quantity = trade.order.totalQuantity

    if new_price is not None:
        trade.order.lmtPrice = new_price
    if new_quantity is not None:
        trade.order.totalQuantity = new_quantity
    if outside_rth is not None:
        trade.order.outsideRth = outside_rth

    # Clear VOL-related fields to prevent IB error 321 on non-VOL orders.
    # IB populates these on open order snapshots; re-submitting them on a
    # LMT order causes "VOL order requires non-negative floating point value".
    if order_type not in ("VOL",):
        trade.order.volatility = 1.7976931348623157e+308  # IB sentinel = unset
        trade.order.volatilityType = 2147483647            # IB sentinel = unset

    await asyncio.to_thread(client.place_order, trade.contract, trade.order)

    # Poll for confirmation
    poll_interval = 0.5
    elapsed = 0.0
    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        refreshed = await asyncio.to_thread(_find_trade, client, order_id, perm_id)
        if refreshed is not None and _modify_confirmed(refreshed, new_price, new_quantity, outside_rth):
            changes = []
            if new_price is not None and old_price != new_price:
                changes.append(f"${old_price} → ${new_price}")
            if new_quantity is not None and old_quantity != new_quantity:
                changes.append(f"qty {old_quantity} → {new_quantity}")
            if outside_rth is not None:
                changes.append("outside RTH updated")
            suffix = f": {', '.join(changes)}" if changes else ""
            return {
                "status": "ok",
                "message": f"Order modified{suffix}",
                "orderId": trade.order.orderId,
                "oldPrice": old_price,
                "newPrice": new_price or old_price,
                "finalStatus": refreshed.orderStatus.status,
            }

    return {
        "status": "error",
        "message": "Modify not confirmed by refreshed IB open orders",
        "orderId": trade.order.orderId,
        "requestedPrice": new_price,
        "requestedQuantity": new_quantity,
    }


def _modify_confirmed(trade, new_price, new_quantity, outside_rth) -> bool:
    """Check if refreshed order reflects requested changes."""
    if trade is None:
        return False

    status = trade.orderStatus.status
    if status not in ("Submitted", "PreSubmitted"):
        return False

    if new_price is not None:
        current = trade.order.lmtPrice
        if current is None or abs(current - new_price) >= 0.001:
            return False

    if new_quantity is not None and trade.order.totalQuantity != new_quantity:
        return False

    if outside_rth is not None and getattr(trade.order, "outsideRth", None) is not outside_rth:
        return False

    return True
