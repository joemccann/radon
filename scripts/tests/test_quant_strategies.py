"""Ported quants.md / QuantJourney strategies: registry contract and the exact
rule of each strategy on synthetic closes. No DB, no network.
"""

from __future__ import annotations

import numpy as np
import pytest
from quant.indicators import realized_vol
from quant.strategies import STRATEGIES, get_strategy, list_strategies

EXPECTED_KEYS = {
    "trend_inverse_vol",
    "inverse_vol",
    "monthly_rotation",
    "dual_momentum",
    "vol_target_trend",
    "signal_change_defensive",
}


def _trend(n: int, drift: float, seed: int = 0, start: float = 100.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, 0.005, n)))


class TestRegistry:
    def test_keys_and_provenance(self):
        assert set(STRATEGIES) == EXPECTED_KEYS
        for entry in list_strategies():
            assert entry["label"] and entry["source"].startswith("quants.md")
            assert entry["universe"] and entry["warmup"] > 0
            assert entry["policy"]["frequency"] in {"D", "W", "BME", "BMS", "BQE", "SIGNAL"}

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            get_strategy("nope")

    @pytest.mark.parametrize("key", sorted(EXPECTED_KEYS))
    def test_target_weights_shape_and_warmup_rows_are_flat(self, key):
        strategy = get_strategy(key)
        n = 320
        closes = np.column_stack([_trend(n, 0.0004, seed=i) for i in range(len(strategy.universe))])
        weights = strategy.target_weights(closes)
        assert weights.shape == closes.shape
        assert not np.isnan(weights).any()
        assert (weights[: strategy.warmup] == 0.0).all()


class TestTrendInverseVol:
    """W25: sign(SMA63 - SMA252) / vol63, unit gross, long/short."""

    def test_signs_follow_trend_and_gross_is_one(self):
        strategy = get_strategy("trend_inverse_vol")
        n = 320
        cols = [_trend(n, 0.002, seed=1), _trend(n, -0.002, seed=2)]
        cols += [_trend(n, 0.0, seed=3 + i) for i in range(len(strategy.universe) - 2)]
        closes = np.column_stack(cols)
        w = strategy.target_weights(closes)
        assert w[-1, 0] > 0 and w[-1, 1] < 0
        assert np.abs(w[-1]).sum() == pytest.approx(1.0)
        vol = realized_vol(closes, 63)[-1]
        ratio = np.abs(w[-1]) * vol
        assert ratio[0] == pytest.approx(ratio[1], rel=1e-9)

    def test_is_long_short_and_month_end(self):
        strategy = get_strategy("trend_inverse_vol")
        assert strategy.long_short is True
        assert strategy.policy.frequency == "BME"
        assert strategy.params == {"fast": 63, "slow": 252, "vol_window": 63}


class TestInverseVol:
    """W05: long-only, weights proportional to 1 / vol63, clipped at 0.35."""

    def test_weights_inverse_to_vol_and_clipped(self):
        strategy = get_strategy("inverse_vol")
        n = 200
        rng = np.random.default_rng(5)
        vols = [0.002, 0.004, 0.008, 0.016, 0.02, 0.02, 0.02, 0.02]
        closes = np.column_stack([100 * np.exp(np.cumsum(rng.normal(0, s, n))) for s in vols])
        w = strategy.target_weights(closes)
        last = w[-1]
        assert (last >= 0).all() and last.sum() <= 1.0 + 1e-9
        assert last.max() <= 0.35 + 1e-12
        # the two highest-vol assets keep the inverse-vol ratio (neither is clipped)
        rv = realized_vol(closes, 63)[-1]
        assert last[6] * rv[6] == pytest.approx(last[7] * rv[7], rel=1e-9)


class TestMonthlyRotation:
    """O17: top 3 by 126-session return, equal weight, first session of month."""

    def test_top_three_get_a_third_each(self):
        strategy = get_strategy("monthly_rotation")
        n = 200
        drifts = [0.003, 0.002, 0.001, 0.0, -0.001, -0.002, -0.003, -0.004]
        closes = np.column_stack([_trend(n, d, seed=i) for i, d in enumerate(drifts)])
        w = strategy.target_weights(closes)
        assert w[-1].tolist() == pytest.approx([1 / 3, 1 / 3, 1 / 3, 0, 0, 0, 0, 0])
        assert strategy.policy.frequency == "BMS"


class TestDualMomentum:
    """W04: top 2 by 252-session return, only if positive, quarter-end."""

    def test_all_negative_momentum_goes_to_cash(self):
        strategy = get_strategy("dual_momentum")
        n = 300
        closes = np.column_stack([_trend(n, -0.002, seed=i) for i in range(8)])
        w = strategy.target_weights(closes)
        assert (w[-1] == 0.0).all()

    def test_two_winners_half_each(self):
        strategy = get_strategy("dual_momentum")
        n = 300
        drifts = [0.003, 0.002, -0.001, -0.001, -0.001, -0.001, -0.001, -0.001]
        closes = np.column_stack([_trend(n, d, seed=i) for i, d in enumerate(drifts)])
        w = strategy.target_weights(closes)
        assert w[-1].tolist() == pytest.approx([0.5, 0.5, 0, 0, 0, 0, 0, 0])
        assert strategy.policy.frequency == "BQE"


class TestVolTargetTrend:
    """W17: SMA50 > SMA200 long/cash, equal weight capped 0.34, 10% vol target."""

    def test_active_names_equal_weighted_then_scaled(self):
        strategy = get_strategy("vol_target_trend")
        n = 320
        drifts = [0.002, 0.002, -0.002, -0.002, -0.002, -0.002, -0.002, -0.002]
        closes = np.column_stack([_trend(n, d, seed=i) for i, d in enumerate(drifts)])
        w = strategy.target_weights(closes)
        last = w[-1]
        assert last[0] == pytest.approx(last[1])
        assert (last[2:] == 0.0).all()
        assert last.sum() <= 0.34 * 2 * 1.5 + 1e-9
        assert strategy.max_gross == 1.5


class TestSignalChangeDefensive:
    """W06: SPY above SMA200 -> risk-on basket, else defensive basket."""

    def test_regime_switch(self):
        strategy = get_strategy("signal_change_defensive")
        n = 260
        up = np.column_stack([_trend(n, 0.002, seed=i) for i in range(8)])
        down = np.column_stack([_trend(n, -0.002, seed=i) for i in range(8)])
        w_up, w_down = strategy.target_weights(up)[-1], strategy.target_weights(down)[-1]
        universe = list(strategy.universe)
        risk_on = [universe.index(s) for s in ("SPY", "EFA", "EEM", "DBC", "VNQ")]
        defensive = [universe.index(s) for s in ("TLT", "IEF", "GLD")]
        assert all(w_up[i] == pytest.approx(0.2) for i in risk_on)
        assert all(w_up[i] == 0.0 for i in defensive)
        assert all(w_down[i] == pytest.approx(1 / 3) for i in defensive)
        assert all(w_down[i] == 0.0 for i in risk_on)
        assert strategy.policy.frequency == "SIGNAL"
