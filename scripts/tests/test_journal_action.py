"""Shared journal action classifier (audit §1 #11).

One function maps (side, secType, prior signed qty, fill qty) to the journal
``action`` label every writer persists and every reader interprets.
"""
from __future__ import annotations

import pytest

from utils import journal_action as ja
from utils.journal_action import Action, Effect, classify_fill, classify_net, signed_qty


class TestClassifyFillOptions:
    def test_open_long(self):
        label = classify_fill("BOT", "OPT", prior_qty=0, fill_qty=5)
        assert (label.action, label.effect) == (Action.BUY_OPTION, Effect.OPEN)

    def test_close_long(self):
        label = classify_fill("SLD", "OPT", prior_qty=5, fill_qty=5)
        assert (label.action, label.effect) == (Action.SELL_OPTION, Effect.CLOSE)

    def test_open_short(self):
        label = classify_fill("SELL", "OPT", prior_qty=0, fill_qty=3)
        assert (label.action, label.effect) == (Action.SELL_TO_OPEN, Effect.OPEN)

    def test_cover_short(self):
        label = classify_fill("BUY", "OPT", prior_qty=-4, fill_qty=4)
        assert (label.action, label.effect) == (Action.BUY_TO_CLOSE, Effect.CLOSE)

    def test_flip_long_to_short_is_explicit(self):
        label = classify_fill("SLD", "OPT", prior_qty=5, fill_qty=8)
        assert label.action is Action.SELL_OPTION
        assert label.effect is Effect.FLIP
        assert (label.close_qty, label.open_qty) == (5, 3)

    def test_flip_short_to_long_is_explicit(self):
        label = classify_fill("BOT", "OPT", prior_qty=-2, fill_qty=7)
        assert label.action is Action.BUY_TO_CLOSE
        assert label.effect is Effect.FLIP
        assert (label.close_qty, label.open_qty) == (2, 5)


class TestClassifyFillStock:
    def test_stock_short_sale_is_an_open_not_a_close(self):
        label = classify_fill("SLD", "STK", prior_qty=0, fill_qty=100)
        assert (label.action, label.effect) == (Action.SELL_TO_OPEN, Effect.OPEN)

    def test_stock_sell_against_long_is_close(self):
        label = classify_fill("SLD", "STK", prior_qty=100, fill_qty=100)
        assert (label.action, label.effect) == (Action.SELL, Effect.CLOSE)

    def test_stock_buy_to_cover_is_close(self):
        label = classify_fill("BOT", "STK", prior_qty=-100, fill_qty=40)
        assert (label.action, label.effect) == (Action.BUY_TO_CLOSE, Effect.CLOSE)

    def test_stock_open_long(self):
        label = classify_fill("BOT", "STK", prior_qty=0, fill_qty=10)
        assert (label.action, label.effect) == (Action.BUY, Effect.OPEN)


class TestRefusals:
    @pytest.mark.parametrize("side", ["", None, "XYZ", "SSHORT"])
    def test_unknown_side_refuses(self, side):
        assert classify_fill(side, "OPT", prior_qty=0, fill_qty=1) is None
        assert classify_fill(side, "STK", prior_qty=0, fill_qty=1) is None

    def test_unknown_prior_refuses(self):
        assert classify_fill("SLD", "OPT", prior_qty=None, fill_qty=1) is None

    def test_zero_qty_refuses(self):
        assert classify_fill("BOT", "OPT", prior_qty=0, fill_qty=0) is None


class TestClassifyNet:
    def test_round_trip_is_closed(self):
        label = classify_net("OPT", prior_qty=0, buy_qty=5, sell_qty=5)
        assert (label.action, label.effect) == (Action.CLOSED, Effect.ROUND_TRIP)

    def test_empty_bucket_refuses(self):
        assert classify_net("OPT", prior_qty=0, buy_qty=0, sell_qty=0) is None

    def test_net_sell_against_long_flips(self):
        label = classify_net("STK", prior_qty=10, buy_qty=0, sell_qty=15)
        assert label.action is Action.SELL
        assert (label.effect, label.close_qty, label.open_qty) == (Effect.FLIP, 10, 5)

    def test_unknown_prior_refuses(self):
        assert classify_net("OPT", prior_qty=None, buy_qty=1, sell_qty=0) is None


class TestSignedQty:
    @pytest.mark.parametrize(
        "action, sign",
        [
            ("BUY", 1), ("BUY_OPTION", 1), ("BUY_TO_CLOSE", 1), ("BUY_TO_OPEN", 1),
            ("SELL", -1), ("SELL_OPTION", -1), ("SELL_TO_OPEN", -1), ("SHORT", -1),
        ],
    )
    def test_directional_labels(self, action, sign):
        assert signed_qty(action, 4) == sign * 4

    def test_closed_round_trip_is_net_zero(self):
        assert signed_qty("CLOSED", 8) == 0.0

    def test_unknown_label_is_zero(self):
        assert signed_qty("NEUTRAL", 8) == 0.0
        assert ja.action_sign("NEUTRAL") is None
        assert ja.action_sign("CLOSED") is None


def test_structure_side():
    assert ja.structure_side("SELL_TO_OPEN") == "Short"
    assert ja.structure_side("BUY_OPTION") == "Long"
    assert ja.structure_side("BUY") == "Long"
    assert ja.structure_side("SELL") == "Closed"
    assert ja.structure_side("BUY_TO_CLOSE") == "Closed"
    assert ja.structure_side("CLOSED") == "Closed"
