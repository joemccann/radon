"""Tests for scripts/costs.py — shared transaction-cost + slippage model.

This is the Python mirror of web/lib/order/costs.ts. Both files model the
SAME numbers so a backtest (Python) and the live order builder (TS) agree on
what a fill costs. Pinned values are derived from first principles in the
comments below — never trust head-arithmetic for expected values.

The model has three pieces:
  1. per-leg commission (IB-style flat per contract, with a per-order floor)
  2. half-spread slippage (function of bid/ask, or an estimated fallback)
  3. a combo fill penalty (extra slippage for multi-leg BAG orders)

All functions are pure; no I/O, no network, no DB.
"""
import pytest

from costs import (
    COMMISSION_PER_CONTRACT,
    COMMISSION_MIN_PER_ORDER,
    COMBO_LEG_SLIPPAGE_PENALTY,
    ESTIMATED_HALF_SPREAD_PER_CONTRACT,
    combo_fill_penalty,
    commission_cost,
    estimate_round_trip_cost,
    half_spread_slippage,
    total_entry_cost,
)


class TestCommission:
    """IB-style flat per-contract commission with a per-order floor."""

    def test_commission_scales_per_contract(self):
        # 10 contracts × COMMISSION_PER_CONTRACT, above the floor.
        expected = 10 * COMMISSION_PER_CONTRACT
        # Sanity: must exceed the floor for this test to exercise the scaling.
        assert expected > COMMISSION_MIN_PER_ORDER
        assert commission_cost(10) == pytest.approx(expected)

    def test_commission_applies_min_floor(self):
        # 1 contract × per-contract is below the floor → floor wins.
        assert 1 * COMMISSION_PER_CONTRACT < COMMISSION_MIN_PER_ORDER
        assert commission_cost(1) == pytest.approx(COMMISSION_MIN_PER_ORDER)

    def test_commission_zero_contracts_is_zero(self):
        # No contracts → no order → no commission (floor must not apply).
        assert commission_cost(0) == 0.0

    def test_commission_negative_treated_as_magnitude(self):
        # A SELL leg may pass a negative contract count; cost is by magnitude.
        assert commission_cost(-10) == pytest.approx(commission_cost(10))


class TestHalfSpreadSlippage:
    """Half the quoted bid/ask spread, per contract, × 100 multiplier."""

    def test_half_spread_from_quote(self):
        # bid=1.00 ask=1.20 → spread=0.20 → half=0.10 per share.
        # 5 contracts × 0.10 × 100 = $50.00
        cost = half_spread_slippage(bid=1.00, ask=1.20, contracts=5)
        assert cost == pytest.approx(0.10 * 5 * 100)

    def test_half_spread_falls_back_to_estimate_when_quote_missing(self):
        # No bid/ask → use the documented estimated half-spread parameter.
        # 3 contracts × ESTIMATED_HALF_SPREAD_PER_CONTRACT (per-share) × 100
        cost = half_spread_slippage(bid=None, ask=None, contracts=3)
        expected = ESTIMATED_HALF_SPREAD_PER_CONTRACT * 3 * 100
        assert cost == pytest.approx(expected)

    def test_half_spread_falls_back_on_crossed_or_zero_spread(self):
        # ask <= bid is a stale/crossed quote → fall back to the estimate
        # rather than returning zero (which would understate true cost).
        cost = half_spread_slippage(bid=1.20, ask=1.00, contracts=2)
        expected = ESTIMATED_HALF_SPREAD_PER_CONTRACT * 2 * 100
        assert cost == pytest.approx(expected)

    def test_half_spread_negative_contracts_by_magnitude(self):
        a = half_spread_slippage(bid=1.00, ask=1.20, contracts=-4)
        b = half_spread_slippage(bid=1.00, ask=1.20, contracts=4)
        assert a == pytest.approx(b)


class TestComboFillPenalty:
    """Multi-leg BAG orders pay extra slippage; single legs pay none."""

    def test_single_leg_has_no_penalty(self):
        assert combo_fill_penalty(num_legs=1, contracts=10) == 0.0

    def test_zero_or_negative_legs_has_no_penalty(self):
        assert combo_fill_penalty(num_legs=0, contracts=10) == 0.0

    def test_two_leg_penalty(self):
        # Penalty applies to (num_legs - 1) extra legs:
        # (2 - 1) × COMBO_LEG_SLIPPAGE_PENALTY × contracts × 100
        cost = combo_fill_penalty(num_legs=2, contracts=5)
        expected = 1 * COMBO_LEG_SLIPPAGE_PENALTY * 5 * 100
        assert cost == pytest.approx(expected)

    def test_four_leg_penalty_scales(self):
        # Iron condor: 4 legs → 3 extra legs.
        cost = combo_fill_penalty(num_legs=4, contracts=2)
        expected = 3 * COMBO_LEG_SLIPPAGE_PENALTY * 2 * 100
        assert cost == pytest.approx(expected)


class TestTotalEntryCost:
    """One-way cost = commission + half-spread + combo penalty."""

    def test_single_leg_entry(self):
        contracts = 5
        commission = commission_cost(contracts)
        slip = half_spread_slippage(bid=1.00, ask=1.20, contracts=contracts)
        penalty = combo_fill_penalty(num_legs=1, contracts=contracts)
        expected = commission + slip + penalty
        got = total_entry_cost(
            contracts=contracts, num_legs=1, bid=1.00, ask=1.20,
        )
        assert got == pytest.approx(expected)

    def test_combo_entry_includes_penalty(self):
        contracts = 5
        got = total_entry_cost(
            contracts=contracts, num_legs=2, bid=1.00, ask=1.20,
        )
        expected = (
            commission_cost(contracts)
            + half_spread_slippage(bid=1.00, ask=1.20, contracts=contracts)
            + combo_fill_penalty(num_legs=2, contracts=contracts)
        )
        assert got == pytest.approx(expected)


class TestRoundTrip:
    """Round trip = entry + exit. Exit quote optional → estimate fallback."""

    def test_round_trip_is_double_when_symmetric(self):
        # Same quote in and out → round trip is exactly twice one-way.
        one_way = total_entry_cost(
            contracts=5, num_legs=1, bid=1.00, ask=1.20,
        )
        rt = estimate_round_trip_cost(
            contracts=5,
            num_legs=1,
            entry_bid=1.00,
            entry_ask=1.20,
            exit_bid=1.00,
            exit_ask=1.20,
        )
        assert rt == pytest.approx(2 * one_way)

    def test_round_trip_exit_quote_defaults_to_estimate(self):
        # No exit quote → exit leg uses the estimated half-spread fallback.
        entry = total_entry_cost(
            contracts=5, num_legs=1, bid=1.00, ask=1.20,
        )
        exit_est = total_entry_cost(
            contracts=5, num_legs=1, bid=None, ask=None,
        )
        rt = estimate_round_trip_cost(
            contracts=5, num_legs=1, entry_bid=1.00, entry_ask=1.20,
        )
        assert rt == pytest.approx(entry + exit_est)
