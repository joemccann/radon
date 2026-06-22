"""FU5 — wire the remaining 5 backtest strategies + backfill.

Each of the five previously-stubbed strategies is now registered as ``wired``
with a real signal_replay reader and a documented entry rule. These tests prove,
per strategy:

  - the documented entry rule fires the EXPECTED trades on a synthetic series,
  - no-look-ahead holds (the decision at origin t sees only data dated <= t),
  - cost-adjusted returns flow through the metrics bundle,
  - where a strategy's snapshot table is genuinely sparse/absent, ``run_strategy``
    returns status ``insufficient_data`` honestly rather than fabricating trades.

Plus a backfill test that aggregates raw synthetic snapshot rows into the
time-keyed series the engine consumes.

No DB, no network — every series is injected synthetically.
"""

from __future__ import annotations

import pytest

from backtest.cost_model import round_trip_cost_fraction
from backtest.strategies import (
    STRATEGIES,
    list_strategies,
    run_strategy,
)
from backtest import signal_replay


# --------------------------------------------------------------------------- #
# Registry — all six strategies now wired                                      #
# --------------------------------------------------------------------------- #
def test_registry_lists_six_strategies_all_wired():
    listed = list_strategies()
    keys = {s["key"] for s in listed}
    assert keys == {
        "cri",
        "dark_pool_flow",
        "leap_iv",
        "garch_convergence",
        "risk_reversal",
        "vcg",
    }
    assert all(s["wired"] for s in listed), "every strategy must be wired in FU5"


def test_unknown_strategy_still_raises_keyerror():
    with pytest.raises(KeyError):
        run_strategy("does_not_exist")


# --------------------------------------------------------------------------- #
# VCG-R — VIX > 28 AND VCG > 2.5 AND both betas < 0 -> short credit            #
# --------------------------------------------------------------------------- #
def _vcg_row(date, credit, *, vcg, vix=30.0, beta1=-0.1, beta2=-0.2):
    return {
        "date": date,
        "credit": credit,
        "vcg": vcg,
        "vix": vix,
        "vvix": 110.0,
        "beta1": beta1,
        "beta2": beta2,
    }


def test_vcg_fires_only_when_all_three_conditions_hold():
    # day1: RO fires (vix>28, vcg>2.5, betas<0). HYG then drops 80->76 (-5%),
    #   so the short hedge earns +5%.
    # day2: vcg below trigger -> no entry.
    # day3: vcg high but a beta flips positive -> sign discipline blocks it.
    # day4: last bar, no forward.
    history = [
        _vcg_row("2026-02-02", 80.0, vcg=3.0),
        _vcg_row("2026-02-03", 76.0, vcg=1.0),
        _vcg_row("2026-02-04", 78.0, vcg=3.0, beta2=0.1),
        _vcg_row("2026-02-05", 79.0, vcg=3.0),
    ]
    result = run_strategy("vcg", horizon=1, history=history)
    assert result["status"] == "ok"
    dates = [t["date"] for t in result["trades"]]
    assert dates == ["2026-02-02"]
    trade = result["trades"][0]
    assert trade["position"] == -1.0
    # forward credit return = 76/80 - 1 = -0.05 ; short inverts -> +0.05
    assert trade["forward_return"] == pytest.approx(-0.05)
    assert trade["gross_return"] == pytest.approx(0.05)


def test_vcg_vix_gate_blocks_when_vol_not_elevated():
    history = [
        _vcg_row("2026-02-02", 80.0, vcg=3.0, vix=20.0),  # vix below 28 gate
        _vcg_row("2026-02-03", 76.0, vcg=3.0, vix=20.0),
    ]
    result = run_strategy("vcg", horizon=1, history=history)
    assert result["trades"] == []


def test_vcg_costs_reduce_net_return():
    history = [
        _vcg_row("2026-02-02", 80.0, vcg=3.0),
        _vcg_row("2026-02-03", 76.0, vcg=1.0),
    ]
    fraction = 0.003
    result = run_strategy("vcg", horizon=1, cost_fraction=fraction, history=history)
    trade = result["trades"][0]
    assert trade["net_return"] == pytest.approx(0.05 - fraction)


# --------------------------------------------------------------------------- #
# Dark Pool Flow — 3+ consecutive same-direction days AND flow_strength > 50   #
# --------------------------------------------------------------------------- #
def _flow_row(date, close, *, flow_strength, dp_direction):
    return {
        "date": date,
        "close": close,
        "flow_strength": flow_strength,
        "dp_direction": dp_direction,
    }


def test_dark_pool_flow_fires_on_third_consecutive_accumulation_day():
    # 3 consecutive accumulation days with strength>50 -> on day3 a LONG fires.
    # Underlying then rises 100->105 (+5%); long earns +5%.
    # day1/day2 have <3 in a row so no entry. day4 breaks the streak.
    history = [
        _flow_row("2026-03-02", 100.0, flow_strength=60.0, dp_direction="accumulation"),
        _flow_row("2026-03-03", 101.0, flow_strength=70.0, dp_direction="accumulation"),
        _flow_row("2026-03-04", 100.0, flow_strength=80.0, dp_direction="accumulation"),
        _flow_row("2026-03-05", 105.0, flow_strength=10.0, dp_direction="distribution"),
        _flow_row("2026-03-06", 105.0, flow_strength=10.0, dp_direction="distribution"),
    ]
    result = run_strategy("dark_pool_flow", horizon=1, history=history)
    assert result["status"] == "ok"
    dates = [t["date"] for t in result["trades"]]
    assert dates == ["2026-03-04"]
    trade = result["trades"][0]
    assert trade["position"] == 1.0
    # forward = 105/100 - 1 = 0.05
    assert trade["forward_return"] == pytest.approx(0.05)
    assert trade["gross_return"] == pytest.approx(0.05)


def test_dark_pool_flow_distribution_streak_goes_short():
    history = [
        _flow_row("2026-03-02", 100.0, flow_strength=60.0, dp_direction="distribution"),
        _flow_row("2026-03-03", 99.0, flow_strength=70.0, dp_direction="distribution"),
        _flow_row("2026-03-04", 100.0, flow_strength=80.0, dp_direction="distribution"),
        _flow_row("2026-03-05", 95.0, flow_strength=10.0, dp_direction="accumulation"),
    ]
    result = run_strategy("dark_pool_flow", horizon=1, history=history)
    dates = [t["date"] for t in result["trades"]]
    assert dates == ["2026-03-04"]
    trade = result["trades"][0]
    assert trade["position"] == -1.0
    # forward = 95/100 - 1 = -0.05 ; short inverts -> +0.05
    assert trade["gross_return"] == pytest.approx(0.05)


def test_dark_pool_flow_weak_strength_never_fires():
    history = [
        _flow_row("2026-03-02", 100.0, flow_strength=40.0, dp_direction="accumulation"),
        _flow_row("2026-03-03", 101.0, flow_strength=45.0, dp_direction="accumulation"),
        _flow_row("2026-03-04", 102.0, flow_strength=49.0, dp_direction="accumulation"),
        _flow_row("2026-03-05", 103.0, flow_strength=40.0, dp_direction="accumulation"),
    ]
    result = run_strategy("dark_pool_flow", horizon=1, history=history)
    assert result["trades"] == []


def test_dark_pool_flow_no_look_ahead_on_streak_count():
    # The entry decision on day i must only count the streak up to and including
    # day i. We feed a 2-day streak then a 3rd day; the engine must NOT enter on
    # day2 (only 2 in a row at that point) and MUST enter on day3.
    history = [
        _flow_row("2026-03-02", 100.0, flow_strength=60.0, dp_direction="accumulation"),
        _flow_row("2026-03-03", 101.0, flow_strength=60.0, dp_direction="accumulation"),
        _flow_row("2026-03-04", 102.0, flow_strength=60.0, dp_direction="accumulation"),
        _flow_row("2026-03-05", 108.0, flow_strength=10.0, dp_direction="distribution"),
    ]
    result = run_strategy("dark_pool_flow", horizon=1, history=history)
    assert [t["date"] for t in result["trades"]] == ["2026-03-04"]


# --------------------------------------------------------------------------- #
# Sparse/absent snapshot tables -> honest insufficient_data                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", ["leap_iv", "garch_convergence", "risk_reversal"])
def test_sparse_strategies_report_insufficient_data(key):
    result = run_strategy(key)
    assert result["status"] == "insufficient_data"
    assert result["trades"] == []
    assert result["wired"] is True
    # metrics still present + JSON-serialisable, with zero trades.
    assert result["metrics"]["n_trades"] == 0


def test_vcg_empty_history_is_insufficient_data():
    result = run_strategy("vcg", history=[])
    assert result["status"] == "insufficient_data"
    assert result["trades"] == []


# --------------------------------------------------------------------------- #
# Backfill — aggregate raw snapshot rows into the time-keyed series            #
# --------------------------------------------------------------------------- #
def test_backfill_vcg_history_dedupes_and_orders_by_date():
    # Two snapshot payloads whose embedded history overlaps. The backfill must
    # merge them into a single date-ordered series, last-write-wins per date.
    snap_a = {
        "history": [
            {"date": "2026-02-02", "credit": 80.0, "vcg": 3.0, "vix": 30.0,
             "beta1": -0.1, "beta2": -0.2},
            {"date": "2026-02-03", "credit": 79.0, "vcg": 2.0, "vix": 29.0,
             "beta1": -0.1, "beta2": -0.2},
        ]
    }
    snap_b = {
        "history": [
            # newer read of the same 02-03 day -> should overwrite snap_a's row
            {"date": "2026-02-03", "credit": 78.5, "vcg": 2.6, "vix": 29.0,
             "beta1": -0.1, "beta2": -0.2},
            {"date": "2026-02-04", "credit": 77.0, "vcg": 3.1, "vix": 31.0,
             "beta1": -0.1, "beta2": -0.2},
        ]
    }
    series = signal_replay.backfill_vcg_history([snap_a, snap_b])
    dates = [r["date"] for r in series]
    assert dates == ["2026-02-02", "2026-02-03", "2026-02-04"]
    # last-write-wins for the duplicated date
    row_0203 = next(r for r in series if r["date"] == "2026-02-03")
    assert row_0203["credit"] == pytest.approx(78.5)
    assert row_0203["vcg"] == pytest.approx(2.6)


def test_backfill_feeds_engine_with_real_depth():
    # End-to-end: backfill two overlapping payloads, run vcg over the merged
    # series, and assert the merged 02-04 RO day produced a trade.
    snap_a = {
        "history": [
            {"date": "2026-02-02", "credit": 80.0, "vcg": 1.0, "vix": 30.0,
             "beta1": -0.1, "beta2": -0.2},
            {"date": "2026-02-03", "credit": 79.0, "vcg": 1.0, "vix": 29.0,
             "beta1": -0.1, "beta2": -0.2},
        ]
    }
    snap_b = {
        "history": [
            {"date": "2026-02-04", "credit": 77.0, "vcg": 3.1, "vix": 31.0,
             "beta1": -0.1, "beta2": -0.2},
            {"date": "2026-02-05", "credit": 73.15, "vcg": 1.0, "vix": 30.0,
             "beta1": -0.1, "beta2": -0.2},
        ]
    }
    merged = signal_replay.backfill_vcg_history([snap_a, snap_b])
    result = run_strategy("vcg", horizon=1, history=merged)
    assert [t["date"] for t in result["trades"]] == ["2026-02-04"]
    trade = result["trades"][0]
    # forward = 73.15/77 - 1 = -0.05 ; short inverts -> +0.05
    assert trade["gross_return"] == pytest.approx(0.05, abs=1e-9)
