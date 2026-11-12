"""Backtests charge the real round-trip cost from ``scripts/costs.py`` by default.

Each trade is charged the costs.py round-trip dollar cost of the strategy's
documented structure (one contract per leg), as a fraction of the notional the
engine marks the trade on: the underlying close at the origin. An explicit
``cost_fraction`` overrides the default, including an explicit zero.

Expected values are derived from ``costs.py`` itself, never hand arithmetic.
No DB, no network: every series is injected synthetically.
"""

from __future__ import annotations

import pytest

import backtest_run
from backtest.engine import SignalPoint, walk_forward_backtest
from backtest.strategies import STRATEGIES, run_strategy
from costs import OPTION_MULTIPLIER, estimate_round_trip_cost


def _real_cost_fraction(strategy_key: str, close: float) -> float:
    legs = STRATEGIES[strategy_key].num_legs
    return estimate_round_trip_cost(1, legs) / (close * OPTION_MULTIPLIER)


def _crash_day(date, spy):
    return {
        "date": date,
        "spy": spy,
        "realized_vol": 30.0,
        "cor1m": 65.0,
        "spx_vs_ma_pct": -5.0,
    }


def _vcg_row(date, credit, *, vcg):
    return {
        "date": date,
        "credit": credit,
        "vcg": vcg,
        "vix": 30.0,
        "vvix": 110.0,
        "beta1": -0.1,
        "beta2": -0.2,
    }


def _flow_row(date, close):
    return {
        "date": date,
        "close": close,
        "flow_strength": 80.0,
        "dp_direction": "accumulation",
    }


def test_cri_default_net_return_is_gross_minus_real_cost():
    history = [_crash_day("2026-01-01", 800.0), _crash_day("2026-01-02", 760.0)]
    result = run_strategy("cri", horizon=1, history=history)
    trade = result["trades"][0]
    expected_cost = _real_cost_fraction("cri", 800.0)
    assert expected_cost > 0.0
    assert trade["cost_fraction"] == pytest.approx(expected_cost)
    assert trade["net_return"] == pytest.approx(trade["gross_return"] - expected_cost)
    assert result["cost_source"] == "costs.py"


def test_vcg_default_cost_is_per_trade_from_each_origin_close():
    history = [
        _vcg_row("2026-02-02", 80.0, vcg=3.0),
        _vcg_row("2026-02-03", 40.0, vcg=3.0),
        _vcg_row("2026-02-04", 41.0, vcg=1.0),
    ]
    trades = run_strategy("vcg", horizon=1, history=history)["trades"]
    by_date = {t["date"]: t for t in trades}
    first = by_date["2026-02-02"]
    second = by_date["2026-02-03"]
    assert first["cost_fraction"] == pytest.approx(_real_cost_fraction("vcg", 80.0))
    assert second["cost_fraction"] == pytest.approx(_real_cost_fraction("vcg", 40.0))
    assert second["cost_fraction"] > first["cost_fraction"]
    for trade in trades:
        assert trade["net_return"] == pytest.approx(
            trade["gross_return"] - trade["cost_fraction"]
        )


def test_dark_pool_flow_default_charges_real_cost():
    history = [
        _flow_row("2026-03-02", 100.0),
        _flow_row("2026-03-03", 101.0),
        _flow_row("2026-03-04", 102.0),
        _flow_row("2026-03-05", 107.1),
    ]
    trade = run_strategy("dark_pool_flow", horizon=1, history=history)["trades"][0]
    assert trade["date"] == "2026-03-04"
    expected_cost = _real_cost_fraction("dark_pool_flow", 102.0)
    assert trade["net_return"] == pytest.approx(trade["gross_return"] - expected_cost)


def test_explicit_cost_fraction_overrides_real_cost():
    history = [_crash_day("2026-01-01", 800.0), _crash_day("2026-01-02", 760.0)]
    result = run_strategy("cri", horizon=1, cost_fraction=0.002, history=history)
    trade = result["trades"][0]
    assert trade["net_return"] == pytest.approx(trade["gross_return"] - 0.002)
    assert result["cost_source"] == "override"


def test_explicit_zero_cost_fraction_is_honoured_not_replaced():
    history = [_crash_day("2026-01-01", 800.0), _crash_day("2026-01-02", 760.0)]
    trade = run_strategy("cri", horizon=1, cost_fraction=0.0, history=history)[
        "trades"
    ][0]
    assert trade["net_return"] == pytest.approx(trade["gross_return"])


def test_engine_accepts_a_per_trade_cost_rule():
    series = [
        SignalPoint(date="2026-01-01", signal={"c": 0.01}),
        SignalPoint(date="2026-01-02", signal={"c": 0.02}),
    ]
    forward = {"2026-01-01": 0.05, "2026-01-02": 0.05}
    result = walk_forward_backtest(
        series,
        forward,
        lambda history, point: 1.0,
        cost_fraction=lambda point: point.signal["c"],
    )
    nets = [t["net_return"] for t in result["trades"]]
    assert nets == pytest.approx([0.04, 0.03])


@pytest.mark.parametrize(
    ("argv", "expected"),
    [([], None), (["--cost-fraction", "0.004"], 0.004), (["--cost-fraction", "0"], 0.0)],
)
def test_cli_defaults_to_real_cost_and_keeps_override(monkeypatch, argv, expected):
    seen: dict = {}

    def fake_run_strategy(key, *, horizon, cost_fraction):
        seen["cost_fraction"] = cost_fraction
        return {"status": "ok", "trades": []}

    monkeypatch.setattr("backtest.strategies.run_strategy", fake_run_strategy)
    assert backtest_run.main(["--strategy", "cri", *argv]) == 0
    assert seen["cost_fraction"] == expected
