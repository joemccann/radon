"""Metrics-engine tests — Sharpe / Sortino / Calmar / maxDD / hit-rate /
expectancy / equity-curve against HAND-DERIVED values on known return series.

Pure math, no torch / chronos / DB. numpy allowed.

Every expected value is derived explicitly in the test (or in a comment that
shows the arithmetic) — never eyeballed.
"""

from __future__ import annotations

import math

import pytest

from backtest.metrics import (
    calmar,
    compute_metrics,
    equity_curve,
    expectancy,
    hit_rate,
    max_drawdown,
    sharpe,
    sortino,
)


# --------------------------------------------------------------------------- #
# equity_curve                                                                 #
# --------------------------------------------------------------------------- #
def test_equity_curve_compounds_from_start():
    # start 1.0, returns +10%, -50%, +100% -> 1.1, 0.55, 1.1
    curve = equity_curve([0.10, -0.50, 1.00], start=1.0)
    assert curve == pytest.approx([1.1, 0.55, 1.1])


def test_equity_curve_empty_is_just_start():
    assert equity_curve([], start=1.0) == [1.0]


# --------------------------------------------------------------------------- #
# max_drawdown                                                                 #
# --------------------------------------------------------------------------- #
def test_max_drawdown_known_curve():
    # equity curve from returns above: peak 1.1, trough 0.55 -> dd = 0.55/1.1 - 1
    curve = equity_curve([0.10, -0.50, 1.00], start=1.0)
    # max drawdown is the worst peak-to-trough: 0.55/1.1 - 1 = -0.5
    assert max_drawdown(curve) == pytest.approx(-0.5)


def test_max_drawdown_monotonic_up_is_zero():
    assert max_drawdown([1.0, 1.2, 1.5, 2.0]) == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# sharpe                                                                       #
# --------------------------------------------------------------------------- #
def test_sharpe_hand_derived():
    # returns r = [0.01, -0.02, 0.03, 0.00]
    # mean = 0.005 ; population variance (ddof=0):
    #   deviations: 0.005, -0.025, 0.025, -0.005
    #   squares: 0.000025, 0.000625, 0.000625, 0.000025 ; sum = 0.0013
    #   var = 0.0013 / 4 = 0.000325 ; std = sqrt(0.000325)
    r = [0.01, -0.02, 0.03, 0.00]
    mean = 0.005
    std = math.sqrt(0.000325)
    ppy = 252
    expected = (mean / std) * math.sqrt(ppy)
    assert sharpe(r, periods_per_year=ppy) == pytest.approx(expected)


def test_sharpe_with_risk_free_subtracts_per_period_rate():
    # rf annual 2.52% over 252 periods -> per-period 0.0001
    r = [0.01, -0.02, 0.03, 0.00]
    excess_mean = 0.005 - 0.0001
    std = math.sqrt(0.000325)  # std of excess == std of raw (constant shift)
    expected = (excess_mean / std) * math.sqrt(252)
    assert sharpe(r, periods_per_year=252, risk_free_annual=0.0252) == pytest.approx(
        expected
    )


def test_sharpe_zero_std_is_zero():
    assert sharpe([0.01, 0.01, 0.01]) == 0.0


# --------------------------------------------------------------------------- #
# sortino                                                                      #
# --------------------------------------------------------------------------- #
def test_sortino_hand_derived():
    # returns r = [0.01, -0.02, 0.03, 0.00]
    # mean = 0.005
    # downside deviation uses min(r,0)^2 averaged over ALL n periods:
    #   negatives squared: (-0.02)^2 = 0.0004 ; others contribute 0
    #   sum = 0.0004 ; / 4 = 0.0001 ; downside_std = 0.01
    r = [0.01, -0.02, 0.03, 0.00]
    mean = 0.005
    downside_std = 0.01
    expected = (mean / downside_std) * math.sqrt(252)
    assert sortino(r, periods_per_year=252) == pytest.approx(expected)


def test_sortino_no_downside_is_zero():
    # no negative returns -> downside deviation 0 -> guard returns 0.0
    assert sortino([0.01, 0.02, 0.0]) == 0.0


# --------------------------------------------------------------------------- #
# calmar                                                                       #
# --------------------------------------------------------------------------- #
def test_calmar_hand_derived():
    # returns +10%, -50%, +100% -> equity 1.1, 0.55, 1.1
    # total return over 3 periods = 1.1/1.0 - 1 = 0.10
    # annualized = (1.10)^(252/3) - 1  (ppy=252, n=3)
    # max_dd magnitude = 0.5
    r = [0.10, -0.50, 1.00]
    ann = (1.10) ** (252 / 3) - 1.0
    expected = ann / 0.5
    assert calmar(r, periods_per_year=252) == pytest.approx(expected)


def test_calmar_zero_drawdown_is_zero():
    # no drawdown -> undefined ratio, guard returns 0.0
    assert calmar([0.01, 0.02, 0.03]) == 0.0


# --------------------------------------------------------------------------- #
# hit_rate + expectancy                                                        #
# --------------------------------------------------------------------------- #
def test_hit_rate_counts_strictly_positive():
    # 2 of 4 strictly positive (0.0 is not a hit)
    assert hit_rate([0.01, -0.02, 0.03, 0.00]) == pytest.approx(0.5)


def test_hit_rate_empty_is_zero():
    assert hit_rate([]) == 0.0


def test_expectancy_is_mean_return():
    assert expectancy([0.01, -0.02, 0.03, 0.00]) == pytest.approx(0.005)


# --------------------------------------------------------------------------- #
# compute_metrics — the bundle                                                 #
# --------------------------------------------------------------------------- #
def test_compute_metrics_bundle_matches_components():
    r = [0.01, -0.02, 0.03, 0.00]
    m = compute_metrics(r, periods_per_year=252)
    assert m["sharpe"] == pytest.approx(sharpe(r, periods_per_year=252))
    assert m["sortino"] == pytest.approx(sortino(r, periods_per_year=252))
    assert m["calmar"] == pytest.approx(calmar(r, periods_per_year=252))
    assert m["max_drawdown"] == pytest.approx(max_drawdown(equity_curve(r)))
    assert m["hit_rate"] == pytest.approx(0.5)
    assert m["expectancy"] == pytest.approx(0.005)
    assert m["n_trades"] == 4
    assert m["equity_curve"][-1] == pytest.approx(equity_curve(r)[-1])


def test_compute_metrics_empty_returns_safe_zeros():
    m = compute_metrics([], periods_per_year=252)
    assert m["n_trades"] == 0
    assert m["sharpe"] == 0.0
    assert m["max_drawdown"] == 0.0
    assert m["equity_curve"] == [1.0]
