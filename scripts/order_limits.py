#!/usr/bin/env python3
"""Server-side order limits — fat-finger bounds (REL-005 / R-002).

Before this module the only bounds between ANY caller and IB were
``quantity > 0`` and ``limitPrice > 0``. These are static authoritative
caps enforced at the placement funnel (``ib_place_order.place_order``)
and mirrored at the FastAPI routes for fast refusal; the client-side
risk UI remains a display, never the enforcement.

Operator-tunable (read at call time so operators can adjust without restart)
through ``app_preferences``, which resolves DB row > env var > code default
and discards any stored value outside the declared hard band:
  RADON_MAX_ORDER_QTY        max contracts/shares per order   (default 500)
  RADON_MAX_ORDER_NOTIONAL   max $ per order (qty×price×mult) (default 250_000)
  Combo assignment/width is a separate loss cap (_MAX_COMBO_LOSS_DOLLARS).
  RADON_MAX_ORDERS_PER_MIN   max accepted placements per min  (default 10)
  RADON_WORKFLOW_MAX_ORDERS  max orders per workflow run      (default 3)

These are deliberately generous ceilings that normal Radon trading never
touches (typical position: tens of contracts, ~$40k) — they exist to stop
the 10x/100x fat-finger and the runaway automation loop, not to encode
Kelly policy (that stays in the evaluation pipeline).
"""

from __future__ import annotations

from typing import Any, Optional

try:  # scripts/ on sys.path (subprocess scripts, pytest)
    import app_preferences
except ImportError:  # imported as scripts.order_limits from the repo root
    from scripts import app_preferences

_OPTION_MULTIPLIER = 100
_MAX_COMBO_LEGS = 8
_MAX_COMBO_RATIO = 100
# Assignment/width fat-finger for combos. Distinct from RADON_MAX_ORDER_NOTIONAL,
# which is qty × limit × multiplier (the number the order ticket labels
# "notional"). A 100-lot $200 short put is $2M of assignment and $4.7k of
# debit; mixing those dollars refused a live risk-reversal (2026-08-21).
_MAX_COMBO_LOSS_DOLLARS = 10_000_000.0


def max_order_qty() -> int:
    """Contract cap for options/combos (shares use max_stock_order_qty)."""
    return app_preferences.get_int("RADON_MAX_ORDER_QTY")


def max_stock_order_qty() -> int:
    """Share cap for stock orders — shares run larger than contracts, and
    the notional cap is the binding constraint anyway."""
    return app_preferences.get_int("RADON_MAX_STOCK_ORDER_QTY")


def max_order_notional() -> float:
    return app_preferences.get_float("RADON_MAX_ORDER_NOTIONAL")


def max_orders_per_min() -> int:
    return app_preferences.get_int("RADON_MAX_ORDERS_PER_MIN")


def workflow_max_orders() -> int:
    return app_preferences.get_int("RADON_WORKFLOW_MAX_ORDERS")


def _combo_risk_per_unit(legs: Any) -> Optional[float]:
    """Worst-case loss of ONE combo unit, in dollars; None when unpriceable.

    Shorts are paired against longs of the same right — the nearest strike
    first, so the residual width is the conservative one — and a paired short
    risks only the width. Pairing ignores expiry on purpose: a calendar's long
    leg does cover its short, and grouping by expiry would price every index
    calendar off the strike and refuse it.

    An unpaired short put's max loss IS its strike. An unpaired short call's
    is unbounded, so the strike stands in as a documented margin proxy — not a
    true bound, but a number in the right order of magnitude, which is the
    whole point of the cap.
    """
    if not isinstance(legs, list) or not legs:
        return None

    by_right: dict[str, dict[str, list[float]]] = {}
    for leg in legs:
        if not isinstance(leg, dict):
            return None
        try:
            strike = float(leg.get("strike") or 0)
            ratio = int(leg.get("ratio", 1) or 1)
        except (TypeError, ValueError):
            return None
        if strike <= 0 or ratio <= 0:
            return None
        right = str(leg.get("right") or "").upper()[:1]
        side = "short" if str(leg.get("action") or "").upper().startswith("SELL") else "long"
        sides = by_right.setdefault(right, {"long": [], "short": []})
        sides[side].extend([strike] * ratio)

    risk = 0.0
    for sides in by_right.values():
        longs = sorted(sides["long"])
        for short_strike in sorted(sides["short"]):
            if longs:
                nearest = min(longs, key=lambda strike: abs(strike - short_strike))
                longs.remove(nearest)
                risk += abs(short_strike - nearest) * _OPTION_MULTIPLIER
            else:
                risk += short_strike * _OPTION_MULTIPLIER
    return risk


def _combo_credit_per_unit(params: dict, price: float, multiplier: int) -> float:
    """Premium ONE combo unit collects, in dollars; 0 when the order pays.

    Two credit encodings reach the funnel: the chain builder keeps a BUY
    envelope and signs the credit negative (commit 1db9f558); the position
    close ticket ships a SELL envelope with a positive price. Either way the
    order receives the premium.
    """
    try:
        signed_price = float(params.get("limitPrice") or 0)
    except (TypeError, ValueError):
        signed_price = 0.0
    is_sell_envelope = str(params.get("action") or "").upper().startswith("SELL")
    if signed_price < 0 or is_sell_envelope:
        return price * multiplier
    return 0.0


def _quantity_and_price(params: dict) -> tuple[Optional[float], Optional[float]]:
    try:
        quantity = abs(float(params.get("quantity") or 0))
        price = abs(float(params.get("limitPrice") or 0))
        if not price:
            price = abs(float(params.get("stopPrice") or 0))
    except (TypeError, ValueError):
        return None, None
    if not quantity:
        return None, None
    return quantity, price


def order_notional(params: dict) -> Optional[float]:
    """Dollar notional: quantity × |limit| × multiplier.

    Matches RADON_MAX_ORDER_NOTIONAL's preference text and the order-ticket
    "notional" line. Combo assignment/width is ``combo_max_loss``, not this.
    """
    quantity, price = _quantity_and_price(params)
    if quantity is None or price is None:
        return None
    multiplier = 1 if str(params.get("type", "")).lower() == "stock" else _OPTION_MULTIPLIER
    premium = quantity * price * multiplier
    return premium or None


def combo_max_loss(params: dict) -> Optional[float]:
    """Worst-case combo loss in dollars; None when not a priceable combo.

    Short strangles still cannot sneak past on a tiny credit (R-052). A
    risk reversal's assignment-to-zero is this number, not notional.
    """
    if str(params.get("type", "")).lower() != "combo":
        return None
    quantity, price = _quantity_and_price(params)
    if quantity is None or price is None:
        return None
    risk_per_unit = _combo_risk_per_unit(params.get("legs"))
    if risk_per_unit is None:
        return None
    multiplier = _OPTION_MULTIPLIER
    credit_per_unit = _combo_credit_per_unit(params, price, multiplier)
    return quantity * max(risk_per_unit - credit_per_unit, 0.0)


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

    loss = combo_max_loss(params)
    if loss is not None and loss > _MAX_COMBO_LOSS_DOLLARS:
        return {
            "code": "ORDER_MAX_LOSS_LIMIT",
            "message": (
                f"combo max loss ${loss:,.0f} exceeds the server-side limit "
                f"of ${_MAX_COMBO_LOSS_DOLLARS:,.0f} — refused"
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
