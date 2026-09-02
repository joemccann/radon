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
  RADON_MAX_ORDER_QTY        max contracts per option order   (default 500)
  RADON_MAX_STOCK_ORDER_QTY  max shares per stock order       (default 10_000)
  RADON_MAX_ORDER_NOTIONAL   max $ per order (qty×price×mult) (default 250_000)
  RADON_MAX_COMBO_LOSS_DOLLARS combo worst-case loss cap     (default 10_000_000)
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


def max_order_qty() -> int:
    """Contract cap for options/combos (shares use max_stock_order_qty)."""
    return app_preferences.get_int("RADON_MAX_ORDER_QTY")


def max_stock_order_qty() -> int:
    """Share cap for stock orders — shares run larger than contracts, and
    the notional cap is the binding constraint anyway."""
    return app_preferences.get_int("RADON_MAX_STOCK_ORDER_QTY")


def max_order_notional() -> float:
    return app_preferences.get_float("RADON_MAX_ORDER_NOTIONAL")


def max_combo_loss_dollars() -> float:
    """Assignment/width fat-finger for combos. Distinct from
    RADON_MAX_ORDER_NOTIONAL, which is qty × limit × multiplier (the number
    the order ticket labels "notional"). A 100-lot $200 short put is $2M of
    assignment and $4.7k of debit; mixing those dollars refused a live
    risk-reversal (2026-08-21)."""
    return app_preferences.get_float("RADON_MAX_COMBO_LOSS_DOLLARS")


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

    IB's BAG sign convention has FOUR quadrants, and the envelope alone does
    not decide direction — the sign of the net price flips it:

        BUY  envelope, price > 0  → debit paid      → no credit
        BUY  envelope, price < 0  → credit received → credit (chain builder,
                                                      commit 1db9f558)
        SELL envelope, price > 0  → credit received → credit (close ticket)
        SELL envelope, price < 0  → debit paid      → no credit

    R-086: the fourth quadrant — closing or rolling a SHORT structure for a
    debit — used to be booked as *collecting* |price| × 100 per unit, and
    that phantom credit was subtracted from risk_per_unit. A 500-lot SELL
    combo on a $5-wide short vertical at limitPrice=-5.00 priced at $0 loss
    on an order paying $250,000.
    """
    try:
        signed_price = float(params.get("limitPrice") or 0)
    except (TypeError, ValueError):
        signed_price = 0.0
    is_sell_envelope = str(params.get("action") or "").upper().startswith("SELL")
    receives_premium = (signed_price < 0) != is_sell_envelope
    return price * multiplier if receives_premium else 0.0


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

        # R-087: combo_max_loss returns None — i.e. NO loss check at all —
        # when any option leg lacks a positive strike, and that hole was the
        # sole combo risk gate. Refuse instead of transmitting unbounded.
        for leg in legs:
            if not isinstance(leg, dict):
                return {
                    "code": "ORDER_COMBO_STRIKE",
                    "message": "combo leg is not an object — refused",
                }
            if str(leg.get("sec_type") or leg.get("secType") or "").upper() == "STK":
                continue
            try:
                strike = float(leg.get("strike") or 0)
            except (TypeError, ValueError):
                strike = 0.0
            if strike <= 0:
                return {
                    "code": "ORDER_COMBO_STRIKE",
                    "message": (
                        "combo option leg is missing a positive strike, so its "
                        "max loss cannot be priced — refused"
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
    loss_cap = max_combo_loss_dollars()
    if loss is not None and loss > loss_cap:
        return {
            "code": "ORDER_MAX_LOSS_LIMIT",
            "message": (
                f"combo max loss ${loss:,.0f} exceeds the server-side limit "
                f"of ${loss_cap:,.0f} (RADON_MAX_COMBO_LOSS_DOLLARS) — refused"
            ),
        }

    return None


def _legs_are_priceable(legs: Any) -> bool:
    """True when `_combo_risk_per_unit` can price every leg.

    `ib_orders.py:fetch_open_orders` skips combo legs it cannot qualify, so a
    snapshot BAG may carry legs with no strike. `check_order_limits` fails
    CLOSED on those (ORDER_COMBO_STRIKE), which is right for a placement the
    caller composed and wrong for a resize of an order IB already holds.
    """
    if not isinstance(legs, list) or not 2 <= len(legs) <= _MAX_COMBO_LEGS:
        return False
    for leg in legs:
        if not isinstance(leg, dict):
            return False
        if str(leg.get("sec_type") or leg.get("secType") or "").upper() == "STK":
            continue
        try:
            if float(leg.get("strike") or 0) <= 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _working_order_shape(working_order: dict) -> tuple[str, Optional[list]]:
    """(order type, combo legs) of a working order, from either shape.

    R-431: the Turso `open_orders` payload nests the contract —
    `contract.secType` and `contract.comboLegs` (``ib_orders.py``) — while a
    caller-composed replacement carries `secType`/`legs` flat. Reading only
    the flat keys resolved EVERY snapshot row to "option", so a working stock
    order was bounded by the contracts cap (RADON_MAX_ORDER_QTY, hard max
    2500): selling 10,000 shares was refused, and raising the contracts cap to
    its ceiling could not unblock it. A BAG likewise never reached the
    max-loss branch R-145 added.

    A BAG whose legs cannot be priced stays "option" — the quantity and
    notional bounds it already had, never a new fail-closed refusal of a
    legitimate resize (the same trade-off ``ib_order_manage.py`` documents).
    """
    contract = working_order.get("contract")
    contract = contract if isinstance(contract, dict) else {}

    sec_type = str(contract.get("secType") or working_order.get("secType") or "").upper()

    legs = working_order.get("legs")
    if not isinstance(legs, list):
        combo_legs = contract.get("comboLegs")
        legs = combo_legs if isinstance(combo_legs, list) else None

    if sec_type == "BAG" or (legs is not None and len(legs) >= 2):
        return ("combo" if _legs_are_priceable(legs) else "option"), legs
    if sec_type == "STK":
        return "stock", None
    return "option", None


def check_modify_limits(
    working_order: Optional[dict],
    *,
    new_quantity: Any = None,
    new_price: Any = None,
    action: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Full limit set for a modify, measured on the WORKING order's shape.

    R-145: `/orders/modify` only ever called `check_quantity_limit`, which
    hardcodes `{"type": "option", "limitPrice": 0}` — so `order_notional()`
    returned None (premium is 0) and `combo_max_loss()` returned None (type is
    not "combo"), and both branches were skipped. `newPrice` was bounded only
    by `> 0`. Modifying a working 1-lot BAG with a short 195 put leg to 500
    lots was accepted with its assignment exposure never computed. REL-005's
    contract named modify as a chokepoint for max qty AND max notional.

    An unreadable working order is NOT a bypass: the contract-quantity cap
    still applies, exactly as before.

    R-431: the shape is read through `_working_order_shape`, because the Turso
    `open_orders` payload nests it one level down and reading the top level
    typed every real working order as an option.
    """
    if not isinstance(working_order, dict):
        return check_quantity_limit(new_quantity) if new_quantity is not None else None

    order_type, legs = _working_order_shape(working_order)

    quantity = new_quantity
    if quantity is None:
        quantity = working_order.get("quantity") or working_order.get("totalQuantity")
    price = new_price
    if price is None:
        price = working_order.get("limitPrice") or working_order.get("lmtPrice") or 0

    params: dict[str, Any] = {
        "type": order_type,
        "quantity": quantity,
        "limitPrice": price,
        "action": action or working_order.get("action"),
    }
    if order_type == "combo" and legs is not None:
        params["legs"] = legs
    return check_order_limits(params)


def check_quantity_limit(quantity: Any) -> Optional[dict[str, Any]]:
    """Quantity-only bound (modify path: no type/price context).

    Applies the stricter contract cap: a working option 1-lot modified to
    10,000 lots is exactly the fat-finger this exists for. Oversized
    stock modifies need RADON_MAX_ORDER_QTY raised deliberately.
    """
    return check_order_limits({"type": "option", "quantity": quantity, "limitPrice": 0})
