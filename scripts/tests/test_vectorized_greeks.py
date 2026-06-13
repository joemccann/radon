"""Tests for vectorized portfolio Greeks calculation.

All expected values are derived independently from first principles.
The formula is:
  moneyness    = (spot - strike) / strike  [call]  or  (strike - spot) / strike  [put]
  time_factor  = max(0.1, sqrt(dte / 365))
  adjusted     = moneyness / (0.2 * time_factor)
  call_delta   = 0.5 + 0.5 * tanh(adjusted * 2)
  raw_delta    = call_delta            [call]
               = call_delta - 1        [put]
  leg_delta    = sign * raw_delta * contracts * 100

Tests marked with # KILLS: list which deadly mutations they are designed to catch.
"""
import math
import numpy as np
import pytest
from scripts.utils.vectorized_greeks import portfolio_greeks_vectorized


# ---------------------------------------------------------------------------
# Independent reference function — NOT a copy of production code.
# Uses only stdlib math so a mutation in the NumPy production path is caught.
# ---------------------------------------------------------------------------

def _ref_delta(spot: float, strike: float, dte: float, is_call: bool) -> float:
    """Hand-implemented reference delta using only stdlib math.

    This is an independent derivation of the formula, not a mirror of
    vectorized_greeks.py.  A mutation in the NumPy production path does not
    affect this function, so cross-validation against it is meaningful.
    """
    if spot <= 0 or strike <= 0 or dte <= 0:
        return 0.5 if is_call else -0.5
    moneyness = (spot - strike) / strike if is_call else (strike - spot) / strike
    time_factor = max(0.1, math.sqrt(dte / 365.0))
    adjusted = moneyness / (0.2 * time_factor)
    call_delta = 0.5 + 0.5 * math.tanh(adjusted * 2.0)
    return call_delta if is_call else call_delta - 1.0


def _ref_leg_delta(spot, strike, dte, is_call, sign, contracts):
    """Reference leg_delta = sign * raw_delta * contracts * 100."""
    raw = _ref_delta(spot, strike, dte, is_call)
    return sign * raw * contracts * 100.0


class TestPortfolioGreeksVectorized:

    # ------------------------------------------------------------------
    # Sign / direction tests (catch sign-flip mutations on raw_delta)
    # ------------------------------------------------------------------

    def test_long_call_delta_positive(self):
        """Long call must have positive raw delta."""
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([True]),
        )
        assert result["raw_deltas"][0] > 0

    def test_short_call_delta_negative(self):
        """Short call must have negative raw delta (sign * delta)."""
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([-1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([True]),
        )
        assert result["raw_deltas"][0] > 0  # raw delta itself is positive
        assert result["leg_deltas"][0] < 0  # sign-adjusted is negative

    def test_long_put_delta_negative(self):
        """Long put must have negative raw delta."""
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([False]),
        )
        assert result["raw_deltas"][0] < 0

    def test_short_put_delta_positive(self):
        """Short put: raw delta negative, sign=-1 → leg_delta positive."""
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([-1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([False]),
        )
        assert result["raw_deltas"][0] < 0
        assert result["leg_deltas"][0] > 0

    # ------------------------------------------------------------------
    # Pinned exact-value tests — derived from first principles.
    # These kill multiplier, contracts-factor, aggregation, and
    # formula-constant mutations that purely directional tests cannot catch.
    # ------------------------------------------------------------------

    def test_atm_call_leg_delta_exact(self):
        """ATM call leg_delta is exactly +50.0.

        # KILLS: ×100 multiplier swap (100→1, 100→10), contracts-factor drop,
        #        tanh sign-flip (0.5+→0.5-), sign-flip in leg_delta.

        Arithmetic:
          moneyness = (100-100)/100 = 0
          time_factor = max(0.1, sqrt(30/365)) = 0.286691…  [> 0.1, no floor]
          adjusted = 0 / (0.2 * tf) = 0
          call_delta = 0.5 + 0.5 * tanh(0) = 0.5 + 0 = 0.5
          raw_delta = 0.5  (call)
          leg_delta = +1 * 0.5 * 1 * 100 = 50.0
        """
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([True]),
        )
        assert result["raw_deltas"][0] == pytest.approx(0.5, abs=1e-12)
        assert result["leg_deltas"][0] == pytest.approx(50.0, abs=1e-10)

    def test_atm_put_leg_delta_exact(self):
        """ATM put leg_delta is exactly -50.0.

        # KILLS: put-delta formula (call_delta-1 → call_delta+1),
        #        ×100 multiplier, contracts-factor.

        Arithmetic:
          moneyness = (100-100)/100 = 0  [put: (K-S)/K = 0]
          adjusted = 0
          call_delta = 0.5
          raw_delta = call_delta - 1 = 0.5 - 1 = -0.5
          leg_delta = +1 * (-0.5) * 1 * 100 = -50.0
        """
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([False]),
        )
        assert result["raw_deltas"][0] == pytest.approx(-0.5, abs=1e-12)
        assert result["leg_deltas"][0] == pytest.approx(-50.0, abs=1e-10)

    def test_itm_call_n3_leg_delta_exact(self):
        """ITM call with N=3 contracts pins both the ×100 and contracts factor.

        # KILLS: ×100 multiplier mutation (100→1, 100→10),
        #        contracts factor drop (N treated as 1 when it is 3),
        #        net_delta negation.

        Arithmetic (spot=110, K=100, dte=30, call, sign=+1, N=3):
          moneyness = (110-100)/100 = 0.1
          time_factor = max(0.1, sqrt(30/365)) = sqrt(30/365) = 0.28669108953…
          adjusted = 0.1 / (0.2 * 0.28669108953) = 1.74403746142…
          tanh(2 * 1.74403746142) = tanh(3.48807492284) = 0.99813395879…
          call_delta = 0.5 + 0.5 * 0.99813395879 = 0.99906697939…
          raw_delta = 0.99906697939…  (call)
          leg_delta = +1 * 0.99906697939 * 3 * 100 = 299.72009382…
        """
        result = portfolio_greeks_vectorized(
            spots=np.array([110.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([3.0]),
            is_call=np.array([True]),
        )
        expected_raw = _ref_delta(110.0, 100.0, 30.0, True)
        expected_leg = _ref_leg_delta(110.0, 100.0, 30.0, True, 1.0, 3.0)
        # expected_raw ≈ 0.9990669794, expected_leg ≈ 299.720094
        assert result["raw_deltas"][0] == pytest.approx(expected_raw, abs=1e-10)
        assert result["leg_deltas"][0] == pytest.approx(expected_leg, abs=1e-6)
        # Also assert against literal to catch a wrong reference function
        assert result["leg_deltas"][0] == pytest.approx(299.720094, abs=1e-4)

    def test_net_delta_multi_leg_exact(self):
        """net_delta is the exact sum of independently-computed leg deltas.

        # KILLS: net_delta negation, net_delta → mean, contracts-factor drop,
        #        ×100 multiplier mutations.

        Setup: long 3 ATM calls + short 2 ATM puts, all spot=100, K=100, dte=30.
          ATM call raw_delta = 0.5
          ATM put  raw_delta = -0.5

          ld1 = +1 * 0.5 * 3 * 100 = +150.0   (long 3 calls)
          ld2 = -1 * (-0.5) * 2 * 100 = +100.0  (short 2 puts)
          net_delta = 150.0 + 100.0 = 250.0
        """
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0, 100.0]),
            strikes=np.array([100.0, 100.0]),
            dtes=np.array([30.0, 30.0]),
            signs=np.array([1.0, -1.0]),
            contracts=np.array([3.0, 2.0]),
            is_call=np.array([True, False]),
        )
        # Independent leg values
        ld1 = _ref_leg_delta(100.0, 100.0, 30.0, True, 1.0, 3.0)   # +150.0
        ld2 = _ref_leg_delta(100.0, 100.0, 30.0, False, -1.0, 2.0)  # +100.0
        expected_net = ld1 + ld2  # 250.0

        assert result["leg_deltas"][0] == pytest.approx(150.0, abs=1e-10)
        assert result["leg_deltas"][1] == pytest.approx(100.0, abs=1e-10)
        assert result["net_delta"] == pytest.approx(expected_net, abs=1e-10)
        # Pin the literal to catch a wrong reference function
        assert result["net_delta"] == pytest.approx(250.0, abs=1e-10)

    def test_dollar_delta_mixed_spots_exact(self):
        """dollar_delta = sum(leg_delta * spot) with different spot prices per leg.

        # KILLS: dollar_delta drops *spots (sum(leg_deltas) only),
        #        ×100 multiplier, contracts-factor drop.

        Setup:
          Leg1: ATM call, spot=100, sign=+1, N=2 → ld=+1*0.5*2*100=100.0
          Leg2: ATM put,  spot=200, sign=-1, N=1 → ld=-1*(-0.5)*1*100=+50.0

          net_delta = 100 + 50 = 150.0
          dollar_delta = 100 * 100 + 50 * 200 = 10000 + 10000 = 20000.0
        """
        spots = np.array([100.0, 200.0])
        result = portfolio_greeks_vectorized(
            spots=spots,
            strikes=np.array([100.0, 200.0]),
            dtes=np.array([30.0, 30.0]),
            signs=np.array([1.0, -1.0]),
            contracts=np.array([2.0, 1.0]),
            is_call=np.array([True, False]),
        )
        ld1 = _ref_leg_delta(100.0, 100.0, 30.0, True, 1.0, 2.0)   # 100.0
        ld2 = _ref_leg_delta(200.0, 200.0, 30.0, False, -1.0, 1.0)  # +50.0
        expected_dd = ld1 * 100.0 + ld2 * 200.0  # 20000.0

        assert result["net_delta"] == pytest.approx(150.0, abs=1e-10)
        assert result["dollar_delta"] == pytest.approx(expected_dd, abs=1e-8)
        # Pin literal to catch wrong reference function
        assert result["dollar_delta"] == pytest.approx(20000.0, abs=1e-8)

    def test_time_factor_floor_0_1_exact(self):
        """Time-factor floor of 0.1 is enforced for very short DTE.

        # KILLS: max(0.1,...) → max(0.0,...) and max(0.1,...) → max(0.2,...).

        When dte=1: sqrt(1/365) ≈ 0.05234, which is below 0.1.
        The floor clamps time_factor to 0.1.

        Arithmetic (spot=101, K=100, dte=1, call, sign=+1, N=1):
          sqrt(1/365) = 0.052342... → floor to 0.1
          moneyness = (101-100)/100 = 0.01
          time_factor = 0.1   (floor applies)
          adjusted = 0.01 / (0.2 * 0.1) = 0.01 / 0.02 = 0.5
          call_delta = 0.5 + 0.5 * tanh(1.0) = 0.5 + 0.5*0.76159... = 0.88079707...
          leg_delta = +1 * 0.88079707... * 1 * 100 = 88.079707...

        Compare: WITHOUT floor (0.0 mutant):
          time_factor = 0.052342 → adjusted = 0.01/(0.2*0.052342) = 0.9552...
          call_delta = 0.5 + 0.5*tanh(1.9105) ≈ 0.9786
          leg_delta ≈ 97.86  (differs by ~9.8 — mutation caught)

        Compare: WITH 0.2 floor (mutant): [only relevant when sqrt(dte/365) ∈ (0.1,0.2)]
          For dte=1, sqrt(1/365)=0.0523 < 0.1 < 0.2, so 0.2 floor also gives tf=0.2
          adjusted = 0.01/(0.2*0.2) = 0.25
          call_delta = 0.5 + 0.5*tanh(0.5) = 0.5 + 0.5*0.4621 = 0.7311
          leg_delta ≈ 73.1  (differs by ~15 — mutation caught)
        """
        result = portfolio_greeks_vectorized(
            spots=np.array([101.0]),
            strikes=np.array([100.0]),
            dtes=np.array([1.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([True]),
        )
        # 0.5 + 0.5*tanh(1.0) = 0.8807970779778823
        expected_raw = 0.5 + 0.5 * math.tanh(1.0)  # = 0.88079707797788...
        expected_leg = expected_raw * 100.0

        assert result["raw_deltas"][0] == pytest.approx(expected_raw, abs=1e-10)
        assert result["leg_deltas"][0] == pytest.approx(expected_leg, abs=1e-8)
        # Pin literal: 88.07970... — catches both 0.0 floor and 0.2 floor mutants
        assert result["leg_deltas"][0] == pytest.approx(88.079707797, abs=1e-5)

    # ------------------------------------------------------------------
    # Cross-validation against independent stdlib reference.
    # The reference function (_ref_delta) uses only math.tanh, NOT NumPy,
    # so a mutation in the NumPy path is caught even if the test-side
    # mirror had been missed.  This replaces the old ts_approx_delta
    # self-mirror which was a copy of the production code.
    # ------------------------------------------------------------------

    def test_cross_validate_independent_reference(self):
        """All raw_deltas must match an independent stdlib implementation.

        # KILLS: sign of tanh term, put-delta formula, moneyness sign,
        #        d1 term swap, time_factor denominator.

        The reference function (_ref_delta defined in this module) uses
        only stdlib math — it is NOT derived from vectorized_greeks.py,
        so a mutation to the NumPy production path will produce a mismatch.
        """
        cases = [
            # (spot, strike, dte, is_call, description)
            (150.0, 140.0,  30.0, True,  "ITM call, 30 DTE"),
            (150.0, 160.0,  30.0, True,  "OTM call, 30 DTE"),
            (150.0, 150.0,  90.0, True,  "ATM call, 90 DTE"),
            (150.0, 140.0,  30.0, False, "OTM put, 30 DTE"),
            (150.0, 160.0,  30.0, False, "ITM put, 30 DTE"),
            (50.0,   50.0,   7.0, True,  "ATM call, 7 DTE"),
            (300.0, 250.0, 180.0, True,  "deep ITM call, 180 DTE"),
            (300.0, 350.0, 180.0, False, "deep ITM put, 180 DTE"),
            # Extra: deep OTM (near-zero delta)
            (100.0, 200.0,  30.0, True,  "deep OTM call"),
            # Near-1 delta
            (200.0, 100.0,  30.0, True,  "deep ITM call"),
        ]
        spots = np.array([c[0] for c in cases])
        strikes = np.array([c[1] for c in cases])
        dtes = np.array([c[2] for c in cases])
        is_call = np.array([c[3] for c in cases])
        signs = np.ones(len(cases))
        contracts = np.ones(len(cases))

        result = portfolio_greeks_vectorized(
            spots, strikes, dtes, signs, contracts, is_call
        )

        for i, (sp, st, dt, ic, desc) in enumerate(cases):
            expected = _ref_delta(sp, st, dt, ic)
            np.testing.assert_allclose(
                result["raw_deltas"][i],
                expected,
                atol=1e-12,
                err_msg=f"raw_delta mismatch for {desc}: "
                        f"spot={sp} strike={st} dte={dt} call={ic}",
            )

    # ------------------------------------------------------------------
    # Edge-case / fallback tests
    # ------------------------------------------------------------------

    def test_edge_case_spot_zero(self):
        """spot=0 → fallback delta (0.5 for call, -0.5 for put)."""
        result = portfolio_greeks_vectorized(
            spots=np.array([0.0]),
            strikes=np.array([100.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([True]),
        )
        assert result["raw_deltas"][0] == pytest.approx(0.5)

    def test_edge_case_strike_zero(self):
        """strike=0 → fallback delta."""
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([0.0]),
            dtes=np.array([30.0]),
            signs=np.array([1.0]),
            contracts=np.array([False]),
            is_call=np.array([False]),
        )
        assert result["raw_deltas"][0] == pytest.approx(-0.5)

    def test_edge_case_dte_zero(self):
        """dte=0 → fallback delta."""
        result = portfolio_greeks_vectorized(
            spots=np.array([100.0]),
            strikes=np.array([100.0]),
            dtes=np.array([0.0]),
            signs=np.array([1.0]),
            contracts=np.array([1.0]),
            is_call=np.array([True]),
        )
        assert result["raw_deltas"][0] == pytest.approx(0.5)

    def test_empty_arrays(self):
        """Empty input returns empty arrays and zero aggregates."""
        result = portfolio_greeks_vectorized(
            spots=np.array([]),
            strikes=np.array([]),
            dtes=np.array([]),
            signs=np.array([]),
            contracts=np.array([]),
            is_call=np.array([], dtype=bool),
        )
        assert result["raw_deltas"].shape == (0,)
        assert result["leg_deltas"].shape == (0,)
        assert result["net_delta"] == 0.0
        assert result["dollar_delta"] == 0.0

    def test_net_delta_sums_correctly(self):
        """net_delta = sum of all leg deltas (self-referential sanity check).

        Note: this test is kept for regression, but test_net_delta_multi_leg_exact
        provides the pinned-value check that catches more mutations.
        """
        result = portfolio_greeks_vectorized(
            spots=np.array([150.0, 150.0]),
            strikes=np.array([145.0, 155.0]),
            dtes=np.array([45.0, 45.0]),
            signs=np.array([1.0, -1.0]),
            contracts=np.array([5.0, 5.0]),
            is_call=np.array([True, True]),
        )
        expected_net = float(np.sum(result["leg_deltas"]))
        assert result["net_delta"] == pytest.approx(expected_net)

    def test_dollar_delta(self):
        """dollar_delta = sum(leg_delta * spot) (self-referential sanity check).

        Note: test_dollar_delta_mixed_spots_exact provides the pinned-value check.
        """
        spots = np.array([200.0, 200.0])
        result = portfolio_greeks_vectorized(
            spots=spots,
            strikes=np.array([190.0, 210.0]),
            dtes=np.array([30.0, 30.0]),
            signs=np.array([1.0, -1.0]),
            contracts=np.array([2.0, 2.0]),
            is_call=np.array([True, True]),
        )
        expected_dd = float(np.sum(result["leg_deltas"] * spots))
        assert result["dollar_delta"] == pytest.approx(expected_dd)
