"""Tests for scripts/paper/matcher.py — shadow-fill matching engine.

The matcher decides whether a resting LIMIT or STOP order fills against an
incoming tick/quote. It is a PURE function: given the order's resting state
and a single market observation, it returns a match decision. No I/O, no DB.

Matching rules (standard exchange semantics, marketable-when-crossed):

  LIMIT BUY  fills when the market trades AT or BELOW the limit price.
  LIMIT SELL fills when the market trades AT or ABOVE the limit price.
  STOP BUY   triggers when the market trades AT or ABOVE the stop price; once
             triggered it becomes marketable and fills.
  STOP SELL  triggers when the market trades AT or BELOW the stop price; once
             triggered it becomes marketable and fills.

A STOP order that has not yet triggered does NOT fill — match_order returns a
decision flagged as triggered-but-not-filled or no-action depending on the
crossing test. Once triggered, a follow-up tick (or the triggering tick
itself, treated as marketable) fills it.

The reference price for a LIMIT crossing test is the side the order would
take from the book: a BUY lifts the ASK, a SELL hits the BID. When only a
trade/last price is supplied, that price is used for both sides.
"""
import pytest

from paper.matcher import (
    RestingOrder,
    MarketTick,
    match_order,
)


def _tick(*, last=None, bid=None, ask=None):
    return MarketTick(last=last, bid=bid, ask=ask)


class TestLimitBuy:
    """A BUY limit fills when the ask (or last) is at or under the limit."""

    def test_fills_when_ask_under_limit(self):
        order = RestingOrder(side="BUY", order_type="LIMIT", limit_price=10.00)
        decision = match_order(order, _tick(bid=9.90, ask=9.95))
        assert decision.filled is True
        assert decision.triggered is True

    def test_fills_exactly_at_limit(self):
        order = RestingOrder(side="BUY", order_type="LIMIT", limit_price=10.00)
        decision = match_order(order, _tick(bid=9.95, ask=10.00))
        assert decision.filled is True

    def test_no_fill_when_ask_above_limit(self):
        order = RestingOrder(side="BUY", order_type="LIMIT", limit_price=10.00)
        decision = match_order(order, _tick(bid=10.05, ask=10.10))
        assert decision.filled is False

    def test_uses_last_when_no_quote(self):
        order = RestingOrder(side="BUY", order_type="LIMIT", limit_price=10.00)
        assert match_order(order, _tick(last=9.99)).filled is True
        assert match_order(order, _tick(last=10.01)).filled is False


class TestLimitSell:
    """A SELL limit fills when the bid (or last) is at or above the limit."""

    def test_fills_when_bid_over_limit(self):
        order = RestingOrder(side="SELL", order_type="LIMIT", limit_price=10.00)
        decision = match_order(order, _tick(bid=10.05, ask=10.10))
        assert decision.filled is True

    def test_fills_exactly_at_limit(self):
        order = RestingOrder(side="SELL", order_type="LIMIT", limit_price=10.00)
        decision = match_order(order, _tick(bid=10.00, ask=10.05))
        assert decision.filled is True

    def test_no_fill_when_bid_under_limit(self):
        order = RestingOrder(side="SELL", order_type="LIMIT", limit_price=10.00)
        decision = match_order(order, _tick(bid=9.90, ask=9.95))
        assert decision.filled is False


class TestStopBuy:
    """A BUY stop triggers when the market reaches AT or ABOVE the stop."""

    def test_triggers_and_fills_when_price_reaches_stop(self):
        order = RestingOrder(side="BUY", order_type="STOP", stop_price=20.00)
        decision = match_order(order, _tick(last=20.00))
        assert decision.triggered is True
        assert decision.filled is True

    def test_triggers_above_stop(self):
        order = RestingOrder(side="BUY", order_type="STOP", stop_price=20.00)
        decision = match_order(order, _tick(last=20.50))
        assert decision.triggered is True
        assert decision.filled is True

    def test_no_trigger_below_stop(self):
        order = RestingOrder(side="BUY", order_type="STOP", stop_price=20.00)
        decision = match_order(order, _tick(last=19.50))
        assert decision.triggered is False
        assert decision.filled is False


class TestStopSell:
    """A SELL stop triggers when the market reaches AT or BELOW the stop."""

    def test_triggers_and_fills_when_price_reaches_stop(self):
        order = RestingOrder(side="SELL", order_type="STOP", stop_price=20.00)
        decision = match_order(order, _tick(last=20.00))
        assert decision.triggered is True
        assert decision.filled is True

    def test_triggers_below_stop(self):
        order = RestingOrder(side="SELL", order_type="STOP", stop_price=20.00)
        decision = match_order(order, _tick(last=19.00))
        assert decision.triggered is True
        assert decision.filled is True

    def test_no_trigger_above_stop(self):
        order = RestingOrder(side="SELL", order_type="STOP", stop_price=20.00)
        decision = match_order(order, _tick(last=20.50))
        assert decision.triggered is False
        assert decision.filled is False


class TestAlreadyTriggeredStop:
    """A stop already marked triggered is marketable and fills on any tick."""

    def test_pretriggered_stop_fills_without_recrossing(self):
        order = RestingOrder(
            side="SELL", order_type="STOP", stop_price=20.00, triggered=True
        )
        # Price bounced back above the stop, but a triggered stop is now a
        # market order and fills regardless.
        decision = match_order(order, _tick(last=21.00))
        assert decision.filled is True
        assert decision.triggered is True
