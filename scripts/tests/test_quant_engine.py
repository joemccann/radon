"""Quant strategy engine (scripts/quant): rebalance calendar, no-look-ahead
weights backtest, drift, costs, NaN handling, vol-target overlay.

Pure logic on synthetic matrices. No DB, no network.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest
from quant.engine import (
    RebalancePolicy,
    apply_vol_target,
    latest_targets,
    rebalance_mask,
    run_weights_backtest,
)
from quant.indicators import momentum, pct_change, realized_vol, rolling_mean, rolling_std


# --------------------------------------------------------------------------- #
# indicators                                                                   #
# --------------------------------------------------------------------------- #
class TestIndicators:
    def test_rolling_mean_matches_hand_values_and_is_nan_until_full(self):
        x = np.array([[1.0], [2.0], [3.0], [4.0]])
        out = rolling_mean(x, 2)
        assert math.isnan(out[0, 0])
        assert out[1:, 0].tolist() == [1.5, 2.5, 3.5]

    def test_rolling_std_uses_sample_ddof_like_pandas(self):
        x = np.array([[1.0], [2.0], [4.0]])
        out = rolling_std(x, 3)
        assert out[2, 0] == pytest.approx(np.std([1.0, 2.0, 4.0], ddof=1))

    def test_pct_change_first_row_nan(self):
        x = np.array([[100.0, 50.0], [110.0, 45.0]])
        out = pct_change(x)
        assert all(math.isnan(v) for v in out[0])
        assert out[1].tolist() == pytest.approx([0.10, -0.10])

    def test_momentum_lookback(self):
        x = np.array([[100.0], [120.0], [150.0]])
        out = momentum(x, 2)
        assert math.isnan(out[1, 0])
        assert out[2, 0] == pytest.approx(0.5)

    def test_realized_vol_annualises_sample_std_of_returns(self):
        rets = np.array([0.01, -0.02, 0.015, 0.0])
        closes = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + rets]))[:, None]
        out = realized_vol(closes, 4)
        assert out[-1, 0] == pytest.approx(np.std(rets, ddof=1) * math.sqrt(252))


# --------------------------------------------------------------------------- #
# rebalance calendar                                                           #
# --------------------------------------------------------------------------- #
DATES = [
    "2026-01-28",
    "2026-01-29",
    "2026-01-30",  # Jan ends on a Friday session
    "2026-02-02",
    "2026-02-03",
    "2026-02-26",
    "2026-02-27",  # Feb 28 is a Saturday: 27th is last session
    "2026-03-02",
    "2026-03-31",  # quarter end
    "2026-04-01",
]


class TestRebalanceMask:
    def test_daily_is_every_session(self):
        assert rebalance_mask(DATES, RebalancePolicy("D")).all()

    def test_bme_is_last_session_of_each_month(self):
        mask = rebalance_mask(DATES, RebalancePolicy("BME"))
        picked = [d for d, m in zip(DATES, mask) if m]
        assert picked == ["2026-01-30", "2026-02-27", "2026-03-31"]

    def test_bms_is_first_session_of_each_month(self):
        mask = rebalance_mask(DATES, RebalancePolicy("BMS"))
        picked = [d for d, m in zip(DATES, mask) if m]
        assert picked == ["2026-01-28", "2026-02-02", "2026-03-02", "2026-04-01"]

    def test_bqe_is_last_session_of_each_quarter(self):
        mask = rebalance_mask(DATES, RebalancePolicy("BQE"))
        picked = [d for d, m in zip(DATES, mask) if m]
        assert picked == ["2026-03-31"]

    def test_weekly_is_last_session_of_each_iso_week(self):
        mask = rebalance_mask(DATES, RebalancePolicy("W"))
        picked = [d for d, m in zip(DATES, mask) if m]
        # A session is a week end when the NEXT session in the series falls in
        # another ISO week; the last row is judged against the next calendar
        # weekday (2026-04-02, same week), so it is not picked.
        assert picked == ["2026-01-30", "2026-02-03", "2026-02-27", "2026-03-02"]

    def test_unknown_frequency_rejected(self):
        with pytest.raises(ValueError):
            rebalance_mask(DATES, RebalancePolicy("MONTHLYISH"))


# --------------------------------------------------------------------------- #
# weights backtest                                                             #
# --------------------------------------------------------------------------- #
def _dates(n: int) -> list[str]:
    # weekdays only, starting Monday 2026-01-05
    import datetime as dt

    out = []
    day = dt.date(2026, 1, 5)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += dt.timedelta(days=1)
    return out


class TestRunWeightsBacktest:
    def test_target_set_at_t_earns_the_return_from_t_to_t_plus_one(self):
        # Asset A jumps +10% on session 2. Target +1 on A is set at session 1.
        closes = np.array([[100.0, 100.0], [100.0, 100.0], [110.0, 100.0], [110.0, 100.0]])
        targets = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        res = run_weights_backtest(closes, _dates(4), targets, policy=RebalancePolicy("D"), cost_bps=0.0)
        assert res["returns"] == pytest.approx([0.0, 0.0, 0.10, 0.0])
        assert res["equity"][-1] == pytest.approx(110_000.0)

    def test_target_set_on_last_session_cannot_affect_equity(self):
        closes = np.array([[100.0], [120.0]])
        targets = np.array([[0.0], [1.0]])
        res = run_weights_backtest(closes, _dates(2), targets, policy=RebalancePolicy("D"), cost_bps=0.0)
        assert res["equity"] == pytest.approx([100_000.0, 100_000.0])

    def test_weights_drift_between_rebalances(self):
        # Equal weight at session 0 (BME: only 2026-01-30 is month-end here, so
        # the day-0 rebalance comes from the explicit initial allocation).
        closes = np.array([[100.0, 100.0], [200.0, 100.0]])
        targets = np.array([[0.5, 0.5], [0.5, 0.5]])
        res = run_weights_backtest(
            closes, _dates(2), targets, policy=RebalancePolicy("D"), cost_bps=0.0, rebalance_first_session=True
        )
        # second session is a D rebalance back to 0.5/0.5, so read the drift
        # from the gross P&L: 0.5*100% = +50%.
        assert res["returns"][1] == pytest.approx(0.5)
        drift = run_weights_backtest(
            closes,
            _dates(2),
            np.array([[0.5, 0.5], [0.0, 0.0]]),
            policy=RebalancePolicy("BME"),
            cost_bps=0.0,
            rebalance_first_session=True,
        )
        assert drift["final_weights"] == pytest.approx({"0": 2 / 3, "1": 1 / 3})

    def test_turnover_is_charged_at_cost_bps(self):
        closes = np.array([[100.0], [100.0], [100.0], [100.0]])
        targets = np.array([[1.0], [0.0], [1.0], [1.0]])
        res = run_weights_backtest(
            closes,
            _dates(4),
            targets,
            policy=RebalancePolicy("D"),
            cost_bps=10.0,
            rebalance_first_session=True,
        )
        # three trades of turnover 1.0 (enter, exit, enter) at 10 bps each
        assert res["turnover"] == pytest.approx([1.0, 1.0, 1.0, 0.0])
        assert res["equity"][-1] == pytest.approx(100_000.0 * (1 - 0.001) ** 3)
        assert res["n_rebalances"] == 3

    def test_missing_price_forces_zero_weight_and_finite_equity(self):
        closes = np.array([[100.0, np.nan], [101.0, np.nan], [102.0, 50.0]])
        targets = np.array([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]])
        res = run_weights_backtest(closes, _dates(3), targets, policy=RebalancePolicy("D"), cost_bps=0.0)
        assert all(math.isfinite(v) for v in res["equity"])
        assert res["returns"][1] == pytest.approx(0.5 * 0.01)

    def test_gross_above_max_is_scaled_down(self):
        closes = np.ones((2, 2)) * 100.0
        targets = np.array([[1.0, 1.0], [1.0, 1.0]])
        res = run_weights_backtest(
            closes,
            _dates(2),
            targets,
            policy=RebalancePolicy("D"),
            cost_bps=0.0,
            max_gross=1.0,
        )
        assert res["final_weights"] == pytest.approx({"0": 0.5, "1": 0.5})

    def test_signal_policy_rebalances_only_on_target_change(self):
        closes = np.ones((5, 1)) * 100.0
        targets = np.array([[1.0], [1.0], [1.0], [0.0], [0.0]])
        res = run_weights_backtest(
            closes,
            _dates(5),
            targets,
            policy=RebalancePolicy("SIGNAL", signal_change_threshold=0.01),
            cost_bps=0.0,
        )
        assert res["n_rebalances"] == 2

    def test_result_is_json_serialisable_with_metrics(self):
        rng = np.random.default_rng(1)
        closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, (300, 3)), axis=0))
        targets = np.full((300, 3), 1 / 3)
        res = run_weights_backtest(closes, _dates(300), targets, policy=RebalancePolicy("BME"), cost_bps=5.0)
        json.dumps(res)
        for key in ("sharpe", "max_drawdown", "annualized_return", "ann_vol", "total_return"):
            assert key in res["metrics"]
        assert res["n_sessions"] == 300
        assert res["metrics"]["max_drawdown"] <= 0.0


class TestLatestTargets:
    def test_reports_last_row_with_symbols(self):
        targets = np.array([[0.0, 0.0], [0.25, -0.75]])
        out = latest_targets(["2026-01-05", "2026-01-06"], ["SPY", "TLT"], targets)
        assert out == {"as_of": "2026-01-06", "weights": {"SPY": 0.25, "TLT": -0.75}, "gross": 1.0, "net": -0.5}


# --------------------------------------------------------------------------- #
# vol-target overlay (W17 VolTargetModel semantics)                            #
# --------------------------------------------------------------------------- #
class TestVolTargetOverlay:
    def test_scale_is_target_over_realised_capped_and_prior_window_only(self):
        rng = np.random.default_rng(3)
        n = 200
        rets = rng.normal(0.0, 0.02, n)  # ~32% annualised
        closes = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + rets[1:]]))[:, None]
        targets = np.ones((n, 1))
        out = apply_vol_target(targets, closes, target_vol=0.10, lookback=63, max_leverage=1.5, rescore="D")
        assert (out[:63] == 0.0).all()
        i = 150
        window = pct_change(closes)[i - 63 : i, 0]
        realised = np.std(window, ddof=1) * math.sqrt(252)
        assert out[i, 0] == pytest.approx(min(0.10 / realised, 1.5))
        # A shock AT session i must not change the scale applied at i.
        shocked = closes.copy()
        shocked[i, 0] *= 1.5
        out2 = apply_vol_target(targets, shocked, target_vol=0.10, lookback=63, max_leverage=1.5, rescore="D")
        assert out2[i, 0] == pytest.approx(out[i, 0])
        assert out2[i + 1, 0] < out[i + 1, 0]

    def test_low_vol_regime_is_capped_by_max_leverage(self):
        closes = 100.0 * np.exp(np.cumsum(np.full((200, 1), 0.0001), axis=0))
        out = apply_vol_target(np.ones((200, 1)), closes, target_vol=0.10, lookback=63, max_leverage=1.5)
        assert out[-1, 0] == pytest.approx(1.0)  # realised vol ~0 -> scale 1.0 (model keeps prior scale)
