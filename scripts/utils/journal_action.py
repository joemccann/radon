"""Canonical journal ``action`` classifier.

Every journal writer (fill_monitor, journal_sync, journal_rehydrate,
ib_reconcile, expiry_sweep, ib_execute) labels a fill through
``classify_fill`` / ``classify_net``; every reader takes the sign of a row
through ``signed_qty``. ``web/lib/blotter/fromJournal.ts`` reads the same
labels.

Labels (stock and option share the short-side pair):

=================  ==========================================
``BUY``            stock: open or extend a long
``SELL``           stock: reduce a long
``BUY_OPTION``     option: open or extend a long
``SELL_OPTION``    option: reduce a long
``SELL_TO_OPEN``   open or extend a short (stock or option)
``BUY_TO_CLOSE``   reduce a short (stock or option)
``CLOSED``         aggregate row whose buys and sells net to zero
=================  ==========================================

A fill that crosses zero is a ``FLIP``: it carries the closing label (it
realizes P&L on the prior position) plus ``open_qty`` for the residual it
opens on the other side. Writers persist that residual as
``flip_open_qty``. An unknown side or an unknown prior position returns
``None``: callers must not write a row rather than guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    BUY_OPTION = "BUY_OPTION"
    SELL_OPTION = "SELL_OPTION"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    CLOSED = "CLOSED"


class Effect(str, Enum):
    OPEN = "open"
    CLOSE = "close"
    FLIP = "flip"
    ROUND_TRIP = "round_trip"


@dataclass(frozen=True)
class FillLabel:
    action: Action
    effect: Effect
    close_qty: float
    open_qty: float

    def payload_fields(self) -> dict[str, Any]:
        """Extra journal payload fields this label implies (the flip residual)."""
        if self.effect is not Effect.FLIP:
            return {}
        qty = self.open_qty
        return {FLIP_OPEN_QTY_KEY: int(qty) if float(qty).is_integer() else qty}


# Payload key carrying the residual a FLIP opens on the other side.
FLIP_OPEN_QTY_KEY = "flip_open_qty"

_BUY_SIDES = frozenset({"BOT", "BUY", "B"})
_SELL_SIDES = frozenset({"SLD", "SELL", "S"})

# Directional sign per label. Legacy labels still read from history:
# ``SHORT`` (old ib_reconcile stock short), ``BUY_TO_OPEN``/``SELL_TO_CLOSE``.
_SIGNS = {
    Action.BUY.value: 1,
    Action.BUY_OPTION.value: 1,
    Action.BUY_TO_CLOSE.value: 1,
    "BUY_TO_OPEN": 1,
    Action.SELL.value: -1,
    Action.SELL_OPTION.value: -1,
    Action.SELL_TO_OPEN.value: -1,
    "SELL_TO_CLOSE": -1,
    "SHORT": -1,
    "SHORT_SELL": -1,
}

# The labels an option row can carry (stock-only BUY/SELL never do).
OPTION_ACTIONS = (Action.BUY_OPTION, Action.SELL_OPTION, Action.SELL_TO_OPEN, Action.BUY_TO_CLOSE)


def side_sign(side: Any) -> Optional[int]:
    """+1 buy, -1 sell, None for anything else."""
    upper = str(side or "").strip().upper()
    if upper in _BUY_SIDES:
        return 1
    if upper in _SELL_SIDES:
        return -1
    return None


def _is_stock(sec_type: Any) -> bool:
    return str(sec_type or "STK").strip().upper() == "STK"


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _label(sign: int, sec_type: Any, prior: float, qty: float) -> FillLabel:
    stock = _is_stock(sec_type)
    if prior == 0 or (prior > 0) == (sign > 0):
        if sign > 0:
            return FillLabel(Action.BUY if stock else Action.BUY_OPTION, Effect.OPEN, 0, qty)
        return FillLabel(Action.SELL_TO_OPEN, Effect.OPEN, 0, qty)
    close_qty = min(abs(prior), qty)
    open_qty = qty - close_qty
    if sign > 0:
        action = Action.BUY_TO_CLOSE
    else:
        action = Action.SELL if stock else Action.SELL_OPTION
    return FillLabel(action, Effect.FLIP if open_qty > 0 else Effect.CLOSE, close_qty, open_qty)


def classify_fill(side: Any, sec_type: Any, prior_qty: Optional[float], fill_qty: Any) -> Optional[FillLabel]:
    """Label one directional fill against the contract's prior signed qty."""
    sign = side_sign(side)
    prior = _finite(prior_qty) if prior_qty is not None else None
    qty = _finite(fill_qty)
    if sign is None or prior is None or qty is None:
        return None
    qty = abs(qty)
    if qty <= 0:
        return None
    return _label(sign, sec_type, prior, qty)


def classify_net(sec_type: Any, prior_qty: Optional[float], buy_qty: Any, sell_qty: Any) -> Optional[FillLabel]:
    """Label an aggregate (bucket) of fills from its gross buy/sell totals."""
    prior = _finite(prior_qty) if prior_qty is not None else None
    buys = _finite(buy_qty)
    sells = _finite(sell_qty)
    if prior is None or buys is None or sells is None:
        return None
    buys, sells = abs(buys), abs(sells)
    net = buys - sells
    if net == 0:
        if buys > 0:
            return FillLabel(Action.CLOSED, Effect.ROUND_TRIP, buys, 0)
        return None
    return _label(1 if net > 0 else -1, sec_type, prior, abs(net))


def action_sign(action: Any) -> Optional[int]:
    """Directional sign of a persisted label; None for CLOSED or unknown.

    Unlisted legacy labels keep the historical prefix rule (BUY* long,
    SELL*/SHORT* short) so old rows still net.
    """
    upper = str(action or "").strip().upper()
    if upper in _SIGNS:
        return _SIGNS[upper]
    if upper.startswith("BUY"):
        return 1
    if upper.startswith("SELL") or upper.startswith("SHORT"):
        return -1
    return None


def signed_qty(action: Any, qty: Any) -> float:
    """Signed position contribution of a journal row. CLOSED nets to zero."""
    amount = _finite(qty)
    sign = action_sign(action)
    if amount is None or amount <= 0 or sign is None:
        return 0.0
    return sign * amount


def structure_side(action: Any) -> str:
    """Structure-label prefix: Long / Short opens, everything else Closed."""
    upper = str(action or "").strip().upper()
    if upper in (Action.BUY.value, Action.BUY_OPTION.value, "BUY_TO_OPEN"):
        return "Long"
    if upper == Action.SELL_TO_OPEN.value:
        return "Short"
    return "Closed"
