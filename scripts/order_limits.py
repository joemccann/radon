#!/usr/bin/env python3
"""Server-side order limits — fat-finger bounds (REL-005 / R-002).

Before this module the only bounds between ANY caller and IB were
``quantity > 0`` and ``limitPrice > 0``. These are static authoritative
caps enforced at the placement funnel (``ib_place_order.place_order``)
and mirrored at the FastAPI routes for fast refusal; the client-side
risk UI remains a display, never the enforcement.

Env-tunable (read at call time so operators can adjust without restart):
  RADON_MAX_ORDER_QTY        max contracts/shares per order   (default 500)
  RADON_MAX_ORDER_NOTIONAL   max $ per order (qty×price×mult) (default 250_000)
  RADON_MAX_ORDERS_PER_MIN   max accepted placements per min  (default 10)
  RADON_WORKFLOW_MAX_ORDERS  max orders per workflow run      (default 3)

These are deliberately generous ceilings that normal Radon trading never
touches (typical position: tens of contracts, ~$40k) — they exist to stop
the 10x/100x fat-finger and the runaway automation loop, not to encode
Kelly policy (that stays in the evaluation pipeline).
"""

from __future__ import annotations

import os
from typing import Any, Optional

_OPTION_MULTIPLIER = 100
_MAX_COMBO_LEGS = 8
_MAX_COMBO_RATIO = 100


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def max_order_qty() -> int:
    """Contract cap for options/combos (shares use max_stock_order_qty)."""
    return int(_env_num("RADON_MAX_ORDER_QTY", 500))


def max_stock_order_qty() -> int:
    """Share cap for stock orders — shares run larger than contracts, and
    the notional cap is the binding constraint anyway."""
    return int(_env_num("RADON_MAX_STOCK_ORDER_QTY", 10_000))


def max_order_notional() -> float:
    return _env_num("RADON_MAX_ORDER_NOTIONAL", 250_000)


def max_orders_per_min() -> int:
    return int(_env_num("RADON_MAX_ORDERS_PER_MIN", 10))


def workflow_max_orders() -> int:
    return int(_env_num("RADON_WORKFLOW_MAX_ORDERS", 3))


def order_notional(params: dict) -> Optional[float]:
    """Worst-case premium notional of the order; None when unpriceable."""
    try:
        quantity = abs(float(params.get("quantity") or 0))
        price = abs(float(params.get("limitPrice") or 0))
        if not price:
            price = abs(float(params.get("stopPrice") or 0))
    except (TypeError, ValueError):
        return None
    if not quantity or not price:
        return None
    multiplier = 1 if str(params.get("type", "")).lower() == "stock" else _OPTION_MULTIPLIER
    return quantity * price * multiplier


def check_order_limits(params: dict) -> Optional[dict[str, Any]]:
    """Return {"code", "message"} on violation, None when within limits."""
    try:
        quantity = abs(float(params.get("quantity") or 0))
    except (TypeError, ValueError):
        quantity = 0

    is_stock = str(params.get("type", "")).lower() == "stock"
    qty_cap = max_stock_order_qty() if is_stock else max_order_qty()
    cap_env = "RADON_MAX_STOCK_ORDER_QTY" if is_stock else "RADON_MAX_ORDER_QTY"
    if quantity > qty_cap:
        return {
            "code": "ORDER_QTY_LIMIT",
            "message": (
                f"quantity {int(quantity)} exceeds the server-side limit of "
                f"{qty_cap} ({cap_env}) — refused"
            ),
        }

    if str(params.get("type", "")).lower() == "combo":
        legs = params.get("legs")
        if not isinstance(legs, list) or not 2 <= len(legs) <= _MAX_COMBO_LEGS:
            return {
                "code": "ORDER_COMBO_LEG_LIMIT",
                "message": f"combo must contain 2-{_MAX_COMBO_LEGS} legs — refused",
            }
        ratios: list[int] = []
        for leg in legs:
            ratio = leg.get("ratio", 1) if isinstance(leg, dict) else None
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not float(ratio).is_integer():
                return {"code": "ORDER_COMBO_RATIO", "message": "combo ratios must be positive integers — refused"}
            ratio_int = int(ratio)
            if not 1 <= ratio_int <= _MAX_COMBO_RATIO:
                return {
                    "code": "ORDER_COMBO_RATIO",
                    "message": f"combo ratio {ratio_int} exceeds the 1-{_MAX_COMBO_RATIO} bound — refused",
                }
            ratios.append(ratio_int)
        effective_contracts = int(quantity) * max(ratios)
        if effective_contracts > max_order_qty():
            return {
                "code": "ORDER_EFFECTIVE_QTY_LIMIT",
                "message": (
                    f"effective combo contracts {effective_contracts} exceed the server-side "
                    f"limit of {max_order_qty()} (quantity × max leg ratio) — refused"
                ),
            }

    notional = order_notional(params)
    notional_cap = max_order_notional()
    if notional is not None and notional > notional_cap:
        return {
            "code": "ORDER_NOTIONAL_LIMIT",
            "message": (
                f"order notional ${notional:,.0f} exceeds the server-side limit "
                f"of ${notional_cap:,.0f} (RADON_MAX_ORDER_NOTIONAL) — refused"
            ),
        }

    return None


def check_quantity_limit(quantity: Any) -> Optional[dict[str, Any]]:
    """Quantity-only bound (modify path: no type/price context).

    Applies the stricter contract cap: a working option 1-lot modified to
    10,000 lots is exactly the fat-finger this exists for. Oversized
    stock modifies need RADON_MAX_ORDER_QTY raised deliberately.
    """
    return check_order_limits({"type": "option", "quantity": quantity, "limitPrice": 0})
