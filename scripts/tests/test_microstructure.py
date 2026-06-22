"""Tests for the microstructure imbalance / microprice scan (F10).

Pure-function coverage: known two-sided depth books map to known imbalance and
microprice values, plus the degenerate edge cases (empty side, zero size). No
network, no IB, no DB — these exercise math only.
"""
import math

import pytest

from microstructure import (
    DepthLevel,
    DepthSnapshot,
    book_imbalance,
    microprice,
    microstructure_metrics,
)


def _level(price, size):
    return DepthLevel(price=price, size=size)


class TestBookImbalance:
    def test_symmetric_book_is_neutral(self):
        bid = [_level(99.0, 100), _level(98.0, 100)]
        ask = [_level(101.0, 100), _level(102.0, 100)]
        assert book_imbalance(bid, ask) == 0.0

    def test_bid_heavy_book_is_positive(self):
        # (300 - 100) / (300 + 100) = 0.5
        bid = [_level(99.0, 200), _level(98.0, 100)]
        ask = [_level(101.0, 100)]
        assert book_imbalance(bid, ask) == 0.5

    def test_ask_heavy_book_is_negative(self):
        # (100 - 300) / 400 = -0.5
        bid = [_level(99.0, 100)]
        ask = [_level(101.0, 200), _level(102.0, 100)]
        assert book_imbalance(bid, ask) == -0.5

    def test_top_n_truncates_levels(self):
        # Only the top 1 level per side counts -> (100 - 100)/200 = 0.
        bid = [_level(99.0, 100), _level(98.0, 9999)]
        ask = [_level(101.0, 100), _level(102.0, 9999)]
        assert book_imbalance(bid, ask, top_n=1) == 0.0

    def test_empty_ask_side_is_fully_bid(self):
        bid = [_level(99.0, 100)]
        ask = []
        assert book_imbalance(bid, ask) == 1.0

    def test_empty_bid_side_is_fully_ask(self):
        bid = []
        ask = [_level(101.0, 100)]
        assert book_imbalance(bid, ask) == -1.0

    def test_both_sides_empty_is_none(self):
        assert book_imbalance([], []) is None

    def test_zero_size_book_is_none(self):
        bid = [_level(99.0, 0)]
        ask = [_level(101.0, 0)]
        assert book_imbalance(bid, ask) is None


class TestMicroprice:
    def test_volume_weighted_microprice_leans_to_thin_side(self):
        # bestBid=99 askSize=300, bestAsk=101 bidSize=100
        # (99*300 + 101*100) / (100 + 300) = (29700 + 10100)/400 = 99.5
        assert microprice(
            best_bid=99.0, best_ask=101.0, bid_size=100, ask_size=300
        ) == 99.5

    def test_equal_sizes_give_simple_mid(self):
        assert microprice(
            best_bid=99.0, best_ask=101.0, bid_size=100, ask_size=100
        ) == 100.0

    def test_zero_total_size_is_none(self):
        assert (
            microprice(best_bid=99.0, best_ask=101.0, bid_size=0, ask_size=0)
            is None
        )

    def test_missing_quote_is_none(self):
        assert (
            microprice(best_bid=None, best_ask=101.0, bid_size=100, ask_size=100)
            is None
        )


class TestMicrostructureMetrics:
    def test_full_snapshot_produces_both_metrics(self):
        snap = DepthSnapshot(
            bid=[_level(99.0, 100), _level(98.0, 50)],
            ask=[_level(101.0, 300), _level(102.0, 50)],
        )
        out = microstructure_metrics(snap, top_n=5)
        # imbalance = (150 - 350)/500 = -0.4
        assert out.imbalance == pytest.approx(-0.4)
        # microprice off the touch: (99*300 + 101*100)/400 = 99.5
        assert out.microprice == pytest.approx(99.5)
        assert out.mid == pytest.approx(100.0)
        # micro premium = microprice - mid
        assert out.micro_premium == pytest.approx(-0.5)

    def test_empty_snapshot_yields_nones(self):
        out = microstructure_metrics(DepthSnapshot(bid=[], ask=[]))
        assert out.imbalance is None
        assert out.microprice is None
        assert out.mid is None
        assert out.micro_premium is None

    def test_accepts_dict_levels(self):
        # Convenience: callers may hand raw relay dicts.
        snap = DepthSnapshot.from_book(
            {
                "bid": [{"price": 99.0, "size": 100}],
                "ask": [{"price": 101.0, "size": 100}],
            }
        )
        out = microstructure_metrics(snap)
        assert out.imbalance == 0.0
        assert out.microprice == pytest.approx(100.0)
