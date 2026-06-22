"""Resting-order matcher for the paper-trading shadow-fill engine.

Pure matching core: given a resting LIMIT/STOP order and a single incoming
market observation, decide whether the order triggers and/or fills. No I/O,
no network, no DB — mirrors the "pure core" discipline of
``scripts/lib/staleDataMachine.js``.

Marketable-when-crossed semantics:

  LIMIT BUY  fills when the market is AT or BELOW the limit (it can lift the
             ask cheaply enough).
  LIMIT SELL fills when the market is AT or ABOVE the limit.
  STOP BUY   triggers when the market is AT or ABOVE the stop; once triggered
             it is a market order and fills.
  STOP SELL  triggers when the market is AT or BELOW the stop; fills on trigger.

A STOP carried as ``triggered=True`` is already a live market order and fills
on the next tick without re-testing the stop crossing (price may have bounced
back through the stop after the elect).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

BUY = "BUY"
SELL = "SELL"
LIMIT = "LIMIT"
STOP = "STOP"


@dataclass(frozen=True)
class MarketTick:
    """One market observation. Any field may be absent.

    ``last`` is a trade print; ``bid``/``ask`` form a quote. The matcher
    prefers the marketable side of the quote for LIMIT crossing tests (BUY
    lifts the ask, SELL hits the bid) and falls back to ``last`` when the
    relevant side is missing.
    """

    last: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None

    def buy_reference(self) -> Optional[float]:
        """Price a BUY would pay: the ask, or the last print."""
        return self.ask if self.ask is not None else self.last

    def sell_reference(self) -> Optional[float]:
        """Price a SELL would receive: the bid, or the last print."""
        return self.bid if self.bid is not None else self.last

    def trade_reference(self) -> Optional[float]:
        """Price used for STOP trigger tests: the last print, or the mid."""
        if self.last is not None:
            return self.last
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2.0
        return self.bid if self.bid is not None else self.ask


@dataclass(frozen=True)
class RestingOrder:
    """A resting order awaiting a fill in the shadow book."""

    side: str                          # BUY | SELL
    order_type: str                    # LIMIT | STOP
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    triggered: bool = False            # STOP already elected on a prior tick


@dataclass(frozen=True)
class FillDecision:
    """Outcome of matching one order against one tick."""

    triggered: bool
    filled: bool


def match_order(order: RestingOrder, tick: MarketTick) -> FillDecision:
    """Decide whether ``order`` triggers and/or fills against ``tick``."""
    if order.order_type == LIMIT:
        return _match_limit(order, tick)
    if order.order_type == STOP:
        return _match_stop(order, tick)
    raise ValueError(f"Unsupported order_type: {order.order_type!r}")


def _match_limit(order: RestingOrder, tick: MarketTick) -> FillDecision:
    if order.limit_price is None:
        raise ValueError("LIMIT order requires limit_price")

    if order.side == BUY:
        reference = tick.buy_reference()
        filled = reference is not None and reference <= order.limit_price
        return FillDecision(triggered=filled, filled=filled)

    reference = tick.sell_reference()
    filled = reference is not None and reference >= order.limit_price
    return FillDecision(triggered=filled, filled=filled)


def _match_stop(order: RestingOrder, tick: MarketTick) -> FillDecision:
    if order.stop_price is None:
        raise ValueError("STOP order requires stop_price")

    if order.triggered:
        return FillDecision(triggered=True, filled=True)

    reference = tick.trade_reference()
    if reference is None:
        return FillDecision(triggered=False, filled=False)

    if order.side == BUY:
        triggered = reference >= order.stop_price
    else:
        triggered = reference <= order.stop_price

    return FillDecision(triggered=triggered, filled=triggered)
