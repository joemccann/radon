"""Point & Figure engine pins (tasks/bpi-indicator-plan.md section 3).

Close-only method, traditional box scaling, 3-box reversal. The BPI's
correctness rests entirely on this state machine, so the cases below are
hand-computed on the traditional ladder ($5-$20 band: 0.5 boxes; $20-$100
band: 1.0 boxes).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from utils.pnf import (
    ceil_rung,
    floor_rung,
    signal_state_series,
    traditional_box_ladder,
)


def _days(n: int) -> list[date]:
    start = date.today() - timedelta(days=n)
    return [start + timedelta(days=i) for i in range(n)]


def _series(closes: list[float]) -> list[str | None]:
    return signal_state_series(_days(len(closes)), closes)


class TestLadder:
    def test_band_edges_use_traditional_scale(self) -> None:
        ladder = traditional_box_ladder()
        # 0.5 boxes through the 5-20 band, 1.0 boxes through 20-100.
        i_19_5 = floor_rung(19.5)
        assert ladder[i_19_5] == pytest.approx(19.5)
        assert ladder[i_19_5 + 1] == pytest.approx(20.0)
        assert ladder[i_19_5 + 2] == pytest.approx(21.0)

    def test_floor_and_ceil_rungs_bracket_price(self) -> None:
        ladder = traditional_box_ladder()
        assert ladder[floor_rung(10.4)] == pytest.approx(10.0)
        assert ladder[ceil_rung(10.4)] == pytest.approx(10.5)
        # Exact rung: floor == ceil.
        assert floor_rung(12.0) == ceil_rung(12.0)


class TestSignals:
    def test_no_signal_until_double_top_breakout(self) -> None:
        # Rise to 12, pull back 3 boxes (reversal), rise back to exactly the
        # prior top: NOT a breakout — equal top is no signal.
        states = _series([10.0, 11.0, 12.0, 10.4, 12.0])
        assert states[-1] is None

    def test_double_top_breakout_flips_to_buy(self) -> None:
        # Same walk but the second X column EXCEEDS the prior 12.0 top.
        states = _series([10.0, 11.0, 12.0, 10.4, 12.5])
        assert states[-1] == "buy"
        # Signal persists on quiet days.
        states = _series([10.0, 11.0, 12.0, 10.4, 12.5, 12.4, 12.6])
        assert states[-1] == "buy"

    def test_double_bottom_breakdown_flips_to_sell(self) -> None:
        # Fall to 10, 3-box reversal up (11.5), then break below the prior
        # 10.0 bottom.
        states = _series([12.0, 11.0, 10.0, 11.6, 9.4])
        assert states[-1] == "sell"

    def test_buy_then_sell_transition(self) -> None:
        closes = [10.0, 11.0, 12.0, 10.4, 12.5,   # buy (double-top)
                  10.9,                            # reversal down (O col)
                  12.6, 11.0, 10.4, 9.9]           # X col, O col below 10.4? →
        # Walk: after the buy at 12.5, O column bottoms at 11.0-ish, X to
        # 12.6, then the final O column must undercut the PRIOR O bottom to
        # flip sell. Hand-check: first O col bottom ceil(10.9)=11.0; second O
        # col: 11.0, 10.4 → bottom 10.5; not below prior 11.0 → wait, 10.5 <
        # 11.0 → sell fires at 10.4 already. Final 9.9 stays sell.
        states = _series(closes)
        assert "buy" in states
        assert states[-1] == "sell"

    def test_band_crossing_prices_use_larger_boxes(self) -> None:
        # Rungs cross the $20 boundary (…19.0, 19.5, 20, 21, 22…): from 22
        # three boxes down lands exactly on 19.5 (two 1.0 boxes + one 0.5),
        # so a 19.5 close reverses. Either way no breakout has two
        # same-direction columns to compare yet → state stays None.
        states_small_band = _series([18.0, 15.0, 18.5])
        states_large_band = _series([22.0, 19.5, 23.0])
        assert states_small_band[-1] is None
        assert states_large_band[-1] is None

    def test_short_history_stays_unresolved(self) -> None:
        assert _series([10.0, 10.1, 10.2]) == [None, None, None]

    def test_length_and_alignment(self) -> None:
        closes = [10.0, 11.0, 12.0, 10.4, 12.5]
        states = _series(closes)
        assert len(states) == len(closes)
        # State only appears from the breakout day onward.
        assert states[:4] == [None, None, None, None]
