"""Backtest harness tests — pure math + walk-forward, NO torch / chronos.

The chronos path is exercised only via a monkeypatched ``is_available`` so the
whole file runs on a host without torch installed.
"""

from __future__ import annotations

import math

import pytest

from forecasting.backtest import (
    baseline_forecaster,
    calibration_table,
    empirical_coverage,
    pinball_loss,
    pit_of_actual,
    run_backtest,
    walk_forward,
)
from forecasting.chronos_engine import QuantileForecast


# --------------------------------------------------------------------------- #
# (a) pinball_loss                                                             #
# --------------------------------------------------------------------------- #
def test_pinball_loss_median_symmetric():
    # level=0.5 -> half the absolute error in either direction.
    assert pinball_loss(10.0, 8.0, 0.5) == pytest.approx(1.0)  # actual above pred
    assert pinball_loss(6.0, 8.0, 0.5) == pytest.approx(1.0)  # actual below pred
    assert pinball_loss(8.0, 8.0, 0.5) == pytest.approx(0.0)


def test_pinball_loss_high_level_asymmetric():
    # level=0.9 penalises under-prediction (actual above pred) heavily.
    assert pinball_loss(10.0, 8.0, 0.9) == pytest.approx(0.9 * 2.0)
    # over-prediction (actual below pred) penalised lightly.
    assert pinball_loss(6.0, 8.0, 0.9) == pytest.approx(0.1 * 2.0)


# --------------------------------------------------------------------------- #
# (b) pit_of_actual                                                           #
# --------------------------------------------------------------------------- #
def _pit_fixture():
    levels = [0.1, 0.5, 0.9]
    values = [1.0, 5.0, 9.0]
    return levels, values


def test_pit_below_all_is_zero():
    levels, values = _pit_fixture()
    assert pit_of_actual(0.0, levels, values) == 0.0


def test_pit_above_all_is_one():
    levels, values = _pit_fixture()
    assert pit_of_actual(100.0, levels, values) == 1.0


def test_pit_at_median_value_is_median_level():
    levels, values = _pit_fixture()
    assert pit_of_actual(5.0, levels, values) == pytest.approx(0.5)


def test_pit_is_monotonic():
    levels, values = _pit_fixture()
    samples = [1.0, 2.0, 3.0, 5.0, 7.0, 9.0]
    pits = [pit_of_actual(s, levels, values) for s in samples]
    assert pits == sorted(pits)
    assert all(0.0 <= p <= 1.0 for p in pits)


def test_pit_interpolates_between_levels():
    levels, values = _pit_fixture()
    # Halfway between value 1.0 (level 0.1) and 5.0 (level 0.5).
    assert pit_of_actual(3.0, levels, values) == pytest.approx(0.3)


# --------------------------------------------------------------------------- #
# (c) empirical_coverage                                                      #
# --------------------------------------------------------------------------- #
def test_empirical_coverage_hand_set():
    actuals = [1.0, 5.0, 9.0, 11.0]
    lowers = [0.0, 0.0, 0.0, 0.0]
    uppers = [10.0, 10.0, 10.0, 10.0]
    # 1, 5, 9 covered; 11 out -> 3/4.
    assert empirical_coverage(actuals, lowers, uppers) == pytest.approx(0.75)


def test_empirical_coverage_inclusive_bounds():
    assert empirical_coverage([0.0, 10.0], [0.0, 0.0], [10.0, 10.0]) == 1.0


# --------------------------------------------------------------------------- #
# (d) calibration_table — perfectly calibrated synthetic set                  #
# --------------------------------------------------------------------------- #
def test_calibration_table_perfectly_calibrated():
    # Actuals are 0..99. For a perfectly calibrated forecast the level-quantile
    # pred is constant = the empirical quantile of the actuals, so fraction
    # (actual <= pred) equals the level.
    actuals = [float(i) for i in range(100)]
    levels = [0.1, 0.25, 0.5, 0.75, 0.9]

    # pred at level p: the value v such that fraction(actual <= v) == p.
    # With actuals 0..99, fraction(actual <= k) = (k+1)/100, so v = 100*p - 1.
    per_level_preds = {
        level: [100.0 * level - 1.0] * len(actuals) for level in levels
    }

    table = calibration_table(actuals, per_level_preds, levels)
    assert [row["level"] for row in table] == levels
    for row in table:
        assert row["empirical"] == pytest.approx(row["level"])
        assert row["calibration_error"] == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# (e) baseline_forecaster                                                     #
# --------------------------------------------------------------------------- #
def test_baseline_forecaster_flat_and_non_crossing():
    history = [float(i) for i in range(1, 21)]
    levels = [0.1, 0.5, 0.9]
    horizon = 4

    fc = baseline_forecaster(history, horizon, levels)

    assert isinstance(fc, QuantileForecast)
    assert fc.model_id == "baseline-empirical-quantile"
    assert fc.horizon == horizon
    assert fc.quantile_levels == levels

    for level in levels:
        series = fc.quantiles[str(level)]
        assert len(series) == horizon
        # Flat: every horizon step identical.
        assert len(set(series)) == 1

    # Non-crossing at each step: low <= mid <= high.
    for step in range(horizon):
        lo = fc.quantiles["0.1"][step]
        mid = fc.quantiles["0.5"][step]
        hi = fc.quantiles["0.9"][step]
        assert lo <= mid <= hi


# --------------------------------------------------------------------------- #
# (f) walk_forward with a deterministic fake forecaster                       #
# --------------------------------------------------------------------------- #
def _perfect_forecaster_factory(series, levels):
    """Return a forecaster that perfectly predicts the known future.

    It looks up the realised values from the full series by matching the train
    length, then emits every quantile equal to the actual -> zero pinball.
    """

    def forecaster(train, horizon, fc_levels):
        t = len(train)
        future = series[t : t + horizon]
        quantiles = {str(level): list(future) for level in fc_levels}
        median = quantiles["0.5"]
        return QuantileForecast(
            model_id="perfect",
            horizon=horizon,
            context_len=t,
            quantile_levels=list(fc_levels),
            quantiles=quantiles,
            median=median,
        )

    return forecaster


def test_walk_forward_perfect_forecaster_zero_pinball():
    series = [float(i) for i in range(1, 21)]  # len 20
    levels = [0.1, 0.5, 0.9]
    min_train = 8
    horizon = 1

    forecaster = _perfect_forecaster_factory(series, levels)
    out = walk_forward(
        series,
        horizon=horizon,
        min_train=min_train,
        forecaster=forecaster,
        quantile_levels=levels,
    )

    # origins t in [8, 19] inclusive -> 12 origins.
    assert out["n_origins"] == len(series) - horizon - min_train + 1
    assert out["mean_pinball"] == pytest.approx(0.0, abs=1e-12)
    for value in out["pinball_by_level"].values():
        assert value == pytest.approx(0.0, abs=1e-12)


def test_walk_forward_insufficient_history():
    out = walk_forward(
        [1.0, 2.0, 3.0],
        forecaster=baseline_forecaster,
        min_train=8,
    )
    assert out["n_origins"] == 0
    assert out["status"] == "insufficient_history"


def test_walk_forward_interval_coverage_present_when_levels_present():
    series = [float(i) for i in range(1, 21)]
    levels = [0.01, 0.1, 0.5, 0.9, 0.99]
    forecaster = _perfect_forecaster_factory(series, levels)
    out = walk_forward(
        series,
        horizon=1,
        min_train=8,
        forecaster=forecaster,
        quantile_levels=levels,
    )
    # Perfect forecast -> actual equals every bound -> covered inclusively.
    assert out["interval_coverage"]["0.8"] == pytest.approx(1.0)
    assert out["interval_coverage"]["0.98"] == pytest.approx(1.0)


def test_walk_forward_omits_intervals_without_levels():
    series = [float(i) for i in range(1, 21)]
    levels = [0.2, 0.5, 0.8]  # neither 0.1/0.9 nor 0.01/0.99
    forecaster = _perfect_forecaster_factory(series, levels)
    out = walk_forward(
        series,
        horizon=1,
        min_train=8,
        forecaster=forecaster,
        quantile_levels=levels,
    )
    assert out["interval_coverage"] == {}


# --------------------------------------------------------------------------- #
# (g) run_backtest — engine_unavailable path                                  #
# --------------------------------------------------------------------------- #
def test_run_backtest_engine_unavailable(monkeypatch):
    from forecasting import chronos_engine

    monkeypatch.setattr(chronos_engine, "is_available", lambda: False)

    series = [float(i) for i in range(1, 21)]
    levels = [0.1, 0.5, 0.9]

    out = run_backtest(series, horizon=1, quantile_levels=levels, min_train=8)

    assert out["series_len"] == len(series)
    assert out["chronos"] == {"status": "engine_unavailable"}
    assert out["baseline"]["n_origins"] > 0
    assert "mean_pinball" in out["baseline"]
    assert "engine unavailable" in out["verdict"]
