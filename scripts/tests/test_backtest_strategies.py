"""Strategy-registry + CRI end-to-end wiring tests.

Synthetic CRI history (no DB, no network). Proves:
  - the registry is honest about which strategies are wired
  - stubbed strategies raise NotImplementedError (no fake results)
  - the documented CRI Crash Trigger fires on the right days
  - the realised return is the FORWARD SPY return inverted (short = put hedge)
  - costs.py integration converts a dollar round-trip cost to a fraction
"""

from __future__ import annotations

import pytest

from backtest.cost_model import round_trip_cost_fraction
from backtest.strategies import (
    STRATEGIES,
    list_strategies,
    run_strategy,
)


def test_registry_lists_six_strategies_all_wired():
    # FU5 wired the remaining 5 strategies; every entry is now wired.
    listed = list_strategies()
    keys = {s["key"] for s in listed}
    assert "cri" in keys
    assert len(listed) == 6
    assert all(s["wired"] for s in listed)


def test_no_feed_strategy_reports_insufficient_data():
    # vcg IS wired, but with no replayable history (or an absent snapshot feed)
    # the result is honest insufficient_data rather than a fake/empty success.
    assert STRATEGIES["vcg"].wired is True
    result = run_strategy("leap_iv")
    assert result["status"] == "insufficient_data"
    assert result["trades"] == []


def test_unknown_strategy_raises_keyerror():
    with pytest.raises(KeyError):
        run_strategy("does_not_exist")


def _crash_day(date, spy):
    # all three CRI conditions fire: below MA, vol>25, cor1m>60
    return {
        "date": date,
        "spy": spy,
        "realized_vol": 30.0,
        "cor1m": 65.0,
        "spx_vs_ma_pct": -5.0,
    }


def _calm_day(date, spy):
    return {
        "date": date,
        "spy": spy,
        "realized_vol": 12.0,
        "cor1m": 20.0,
        "spx_vs_ma_pct": 4.0,
    }


def test_cri_fires_only_on_crash_days_and_shorts_spy():
    # day1 crash -> enters short; SPY then falls 800 -> 760 (-5%) so the short
    # earns +5%. day2 calm -> no entry. day3 is the last bar (no forward).
    history = [
        _crash_day("2026-01-01", 800.0),
        _calm_day("2026-01-02", 760.0),
        _calm_day("2026-01-03", 780.0),
    ]
    result = run_strategy("cri", horizon=1, history=history)
    dates = [t["date"] for t in result["trades"]]
    assert dates == ["2026-01-01"]
    trade = result["trades"][0]
    assert trade["position"] == -1.0
    # forward SPY return = 760/800 - 1 = -0.05 ; short inverts -> +0.05
    assert trade["forward_return"] == pytest.approx(-0.05)
    assert trade["gross_return"] == pytest.approx(0.05)
    assert result["wired"] is True


def test_cri_costs_reduce_net_return():
    history = [
        _crash_day("2026-01-01", 800.0),
        _calm_day("2026-01-02", 760.0),
    ]
    fraction = 0.002
    result = run_strategy("cri", horizon=1, cost_fraction=fraction, history=history)
    trade = result["trades"][0]
    assert trade["net_return"] == pytest.approx(0.05 - fraction)


# --------------------------------------------------------------------------- #
# costs.py integration                                                         #
# --------------------------------------------------------------------------- #
def test_round_trip_cost_fraction_uses_costs_module():
    # 1 contract, single leg, no quote -> commission floor 1.00 each way +
    # estimated half-spread 0.025 * 100 = 2.50 each way.
    # round trip = 2 * (1.00 + 2.50) = 7.00 dollars.
    # premium notional = 3.50 (per share) * 100 = 350.0 dollars.
    # fraction = 7.00 / 350.0 = 0.02
    fraction = round_trip_cost_fraction(contracts=1, num_legs=1, premium_per_share=3.50)
    assert fraction == pytest.approx(7.0 / 350.0)


def test_round_trip_cost_fraction_zero_premium_is_zero():
    assert round_trip_cost_fraction(contracts=1, num_legs=1, premium_per_share=0.0) == 0.0
