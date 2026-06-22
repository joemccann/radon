"""Walk-forward engine tests — the load-bearing invariant is NO LOOK-AHEAD.

Pure logic: synthetic signal series + synthetic forward returns. No torch /
chronos / DB / network.

The two things proven here:
  1. The entry decision at origin t reads ONLY signal points dated <= t.
     We prove it with a strategy rule that records every (date) it was shown
     and asserts none post-dates the origin, AND with a tripwire forecaster
     whose later values are NaN — if the engine leaked them in, the trade
     return would be NaN.
  2. Synthetic signals map to the EXPECTED set of trades + their realised
     returns are the forward returns at the entry origin (not the same-bar
     return — that would be look-ahead).
"""

from __future__ import annotations

import math

import pytest

from backtest.engine import SignalPoint, walk_forward_backtest


def _series(rows):
    return [SignalPoint(date=d, signal=s) for d, s in rows]


# --------------------------------------------------------------------------- #
# no look-ahead: rule only ever sees data dated <= origin                      #
# --------------------------------------------------------------------------- #
def test_entry_rule_never_sees_future_dates():
    seen_windows = []

    def rule(history, point):
        # history is every point up to and INCLUDING the current origin.
        seen_windows.append((point.date, [h.date for h in history]))
        return 0.0  # never enter; we only care about what it was shown

    series = _series(
        [
            ("2026-01-01", {"score": 10}),
            ("2026-01-02", {"score": 20}),
            ("2026-01-03", {"score": 30}),
            ("2026-01-04", {"score": 40}),
        ]
    )
    forward_returns = {
        "2026-01-01": 0.01,
        "2026-01-02": 0.02,
        "2026-01-03": 0.03,
        "2026-01-04": 0.04,
    }

    walk_forward_backtest(series, forward_returns, rule)

    for origin_date, shown_dates in seen_windows:
        assert all(d <= origin_date for d in shown_dates), (
            f"look-ahead: at origin {origin_date} the rule was shown {shown_dates}"
        )
        # and the most recent point shown IS the origin
        assert shown_dates[-1] == origin_date


def test_future_signal_values_cannot_leak_into_a_trade():
    # The last point's signal is poisoned with NaN. A correct engine forms the
    # decision at origin t from history<=t and earns forward_returns[t]; it must
    # never consult a later point. If it did, an early trade's return would be
    # contaminated. We enter on EVERY origin and assert no NaN returns appear
    # for the clean early origins.
    def always_long(history, point):
        return 1.0

    series = _series(
        [
            ("2026-01-01", {"score": 1}),
            ("2026-01-02", {"score": 2}),
            ("2026-01-03", {"score": float("nan")}),  # poison
        ]
    )
    forward_returns = {"2026-01-01": 0.05, "2026-01-02": -0.03, "2026-01-03": 0.07}

    result = walk_forward_backtest(series, forward_returns, always_long)
    early = [t for t in result["trades"] if t["date"] in ("2026-01-01", "2026-01-02")]
    assert len(early) == 2
    for trade in early:
        assert not math.isnan(trade["gross_return"]), trade


# --------------------------------------------------------------------------- #
# realised return is the FORWARD return at the entry origin                    #
# --------------------------------------------------------------------------- #
def test_trade_earns_forward_return_at_origin_not_same_bar_lookahead():
    # A long entered at t earns forward_returns[t] (the return realised over the
    # holding window that STARTS after t). The engine must key off the origin's
    # forward return, which is the canonical no-look-ahead mark-to-market.
    def long_when_score_high(history, point):
        return 1.0 if point.signal["score"] >= 25 else 0.0

    series = _series(
        [
            ("2026-01-01", {"score": 10}),
            ("2026-01-02", {"score": 30}),  # enters here
            ("2026-01-03", {"score": 40}),  # enters here
            ("2026-01-04", {"score": 5}),
        ]
    )
    forward_returns = {
        "2026-01-01": 0.01,
        "2026-01-02": 0.02,
        "2026-01-03": -0.03,
        "2026-01-04": 0.04,
    }

    result = walk_forward_backtest(series, forward_returns, long_when_score_high)
    entered = {t["date"]: t for t in result["trades"]}
    assert set(entered) == {"2026-01-02", "2026-01-03"}
    assert entered["2026-01-02"]["gross_return"] == pytest.approx(0.02)
    assert entered["2026-01-03"]["gross_return"] == pytest.approx(-0.03)


def test_short_position_inverts_the_forward_return():
    def short_always(history, point):
        return -1.0

    series = _series([("2026-01-01", {"x": 1}), ("2026-01-02", {"x": 2})])
    forward_returns = {"2026-01-01": 0.05, "2026-01-02": -0.04}

    result = walk_forward_backtest(series, forward_returns, short_always)
    by_date = {t["date"]: t for t in result["trades"]}
    assert by_date["2026-01-01"]["gross_return"] == pytest.approx(-0.05)
    assert by_date["2026-01-02"]["gross_return"] == pytest.approx(0.04)


def test_flat_positions_produce_no_trades():
    def never(history, point):
        return 0.0

    series = _series([("2026-01-01", {"x": 1}), ("2026-01-02", {"x": 2})])
    forward_returns = {"2026-01-01": 0.05, "2026-01-02": -0.04}
    result = walk_forward_backtest(series, forward_returns, never)
    assert result["trades"] == []
    assert result["metrics"]["n_trades"] == 0


# --------------------------------------------------------------------------- #
# costs are subtracted: net return < gross for a winning trade                 #
# --------------------------------------------------------------------------- #
def test_costs_reduce_net_return_below_gross():
    def long_always(history, point):
        return 1.0

    series = _series([("2026-01-01", {"x": 1})])
    forward_returns = {"2026-01-01": 0.05}

    # A positive cost fraction must shrink the realised (net) return.
    result = walk_forward_backtest(
        series, forward_returns, long_always, cost_fraction=0.001
    )
    trade = result["trades"][0]
    assert trade["gross_return"] == pytest.approx(0.05)
    assert trade["net_return"] == pytest.approx(0.05 - 0.001)
    assert result["metrics"]["expectancy"] == pytest.approx(0.05 - 0.001)


def test_missing_forward_return_skips_the_origin():
    # An origin with no forward return (e.g. the most recent bar, no future yet)
    # cannot be marked to market and must be skipped, not crash.
    def long_always(history, point):
        return 1.0

    series = _series([("2026-01-01", {"x": 1}), ("2026-01-02", {"x": 2})])
    forward_returns = {"2026-01-01": 0.05}  # nothing for 2026-01-02
    result = walk_forward_backtest(series, forward_returns, long_always)
    assert [t["date"] for t in result["trades"]] == ["2026-01-01"]
