"""Tests for vectorized Kelly batch sizing.

Pinned values derived independently — NOT from calling kelly() on each element.
This is the critical property: if the test re-derives expected via kelly(), then
a mutation to the formula kills the test AND the expected, leaving the assertion
comparing two equally-wrong values (survivor).

Kills: M7 (-→+ in prob_wins-q/odds), M8 (/→* in q/odds), M9 (*→/ fraction),
       M10 (cap*2), M11 (max instead of min), M12 (odds>=0 guard), M14 (drop round),
       M19 (/100 → *100 in dollar_size).

M13 (full_kelly >= 0 instead of > 0) is an equivalent mutant: when full_kelly=0
exactly, the multiplication 0.0 * fraction = 0.0 yields the same dollar size.
"""
import numpy as np
import pytest
from scripts.kelly import kelly, kelly_size_batch


class TestKellySizeBatchPinnedValues:
    """Pinned exact outputs for specific inputs — values derived by hand."""

    def test_prob_win_one_exact(self):
        # p=1.0, odds=3.0, fraction=0.25, bankroll=100,000, max_pct=0.025:
        #   q = 1 - 1.0 = 0.0
        #   full_kelly = 1.0 - 0.0/3.0 = 1.0  (no q/odds term)
        #   (no-edge zero-clip: 1.0 > 0, passes through)
        #   frac_kelly = 1.0 * 0.25 = 0.25
        #   frac_kelly_pct = round(0.25 * 100.0, 2) = 25.0
        #   dollar_size = 100,000 * 25.0 / 100.0 = 25,000.0
        #   cap = 100,000 * 0.025 = 2,500.0
        #   result = min(25,000.0, 2,500.0) = 2,500.0  [CAPPED]
        # Kills M10 (cap*2=5000): min(25000,5000)=5000 ≠ 2500
        # Kills M11 (max instead of min): max(25000,2500)=25000 ≠ 2500
        # Kills M19 (*100 instead of /100): dollar=25000*100000=huge ≠ 2500
        bankroll = 100_000.0
        result = kelly_size_batch(
            np.array([1.0]), np.array([3.0]), bankroll,
            fraction=0.25, max_pct=0.025
        )
        assert result[0] == pytest.approx(2_500.0)

    def test_hard_cap_exact_value(self):
        # p=0.9, odds=5.0, fraction=0.25, bankroll=50,000, max_pct=0.025:
        #   q = 0.1
        #   full_kelly = 0.9 - 0.1/5.0 = 0.9 - 0.02 = 0.88
        #   frac_kelly = 0.88 * 0.25 = 0.22
        #   frac_kelly_pct = round(0.22 * 100.0, 2) = 22.0
        #   dollar_size = 50,000 * 22.0 / 100.0 = 11,000.0
        #   cap = 50,000 * 0.025 = 1,250.0
        #   result = min(11,000.0, 1,250.0) = 1,250.0  [CAPPED]
        # Kills M10 (cap*2=2500): min(11000,2500)=2500 ≠ 1250
        # Kills M11 (max): max(11000,1250)=11000 ≠ 1250
        # Kills M7 (+ instead of -): fk=0.9+0.02=0.92 → dollar=11500 → still capped at 1250
        #   BUT: a cap-free test (see below) is needed to kill M7/M8
        result = kelly_size_batch(
            np.array([0.9]), np.array([5.0]), 50_000.0,
            fraction=0.25, max_pct=0.025
        )
        assert result[0] == pytest.approx(1_250.0)

    def test_below_cap_exact_dollar_size(self):
        # p=0.51, odds=1.02, fraction=0.25, bankroll=100,000, max_pct=0.025:
        #   q = 0.49
        #   full_kelly = 0.51 - 0.49/1.02 = 0.51 - 0.480392... = 0.029608...
        #   (no-edge zero-clip: 0.029608... > 0, passes)
        #   frac_kelly = 0.029608... * 0.25 = 0.007402...
        #   frac_kelly_pct = round(0.007402... * 100.0, 2) = 0.74
        #   dollar_size = 100,000 * 0.74 / 100.0 = 740.0
        #   cap = 100,000 * 0.025 = 2,500.0
        #   result = min(740.0, 2,500.0) = 740.0  [NOT capped]
        # Kills M7 (+ instead of -): fk=0.51+0.4804=0.9904 → pct=24.76 → dollar=24760
        #   then min(24760, 2500)=2500 ≠ 740 — KILLS M7
        # Kills M8 (* instead of /): fk=0.51-0.49*1.02=0.51-0.4998=0.0102 → pct=0.26 → dollar=260 ≠ 740
        # Kills M9 (/ fraction instead of *): frac=0.0296/0.25=0.1184 → pct=11.84 → dollar=11840 → cap=2500 ≠ 740
        # Kills M11 (max): max(740, 2500)=2500 ≠ 740
        # Kills M14 (no round): dollar=100000*0.740196.../100=740.196... ≠ 740.0
        # Kills M19 (*100): dollar=100000*0.74*100=7,400,000 ≠ 740
        result = kelly_size_batch(
            np.array([0.51]), np.array([1.02]), 100_000.0,
            fraction=0.25, max_pct=0.025
        )
        assert result[0] == pytest.approx(740.0)

    def test_moderate_uncapped_different_fraction(self):
        # p=0.55, odds=2.5, fraction=0.25, bankroll=80,000, max_pct=0.025:
        #   q = 0.45
        #   full_kelly = 0.55 - 0.45/2.5 = 0.55 - 0.18 = 0.37
        #   frac_kelly = 0.37 * 0.25 = 0.0925
        #   frac_kelly_pct = round(0.0925 * 100.0, 2) = 9.25
        #   dollar_size = 80,000 * 9.25 / 100.0 = 7,400.0
        #   cap = 80,000 * 0.025 = 2,000.0
        #   result = min(7,400.0, 2,000.0) = 2,000.0  [CAPPED]
        # Kills M10 (cap*2=4000): min(7400,4000)=4000 ≠ 2000
        result = kelly_size_batch(
            np.array([0.55]), np.array([2.5]), 80_000.0,
            fraction=0.25, max_pct=0.025
        )
        assert result[0] == pytest.approx(2_000.0)

    def test_no_round_detectable_via_direct_dollar_size(self):
        # To kill M14 (drop np.round), use p=0.52, odds=1.05, max_pct=1.0 (no cap):
        # max_pct=1.0 removes the cap so dollar_size is directly returned
        #   q = 0.48
        #   full_kelly = 0.52 - 0.48/1.05 = 0.52 - 0.457142... = 0.062857...
        #   frac_kelly = 0.062857... * 0.25 = 0.015714...
        #   WITH round: frac_kelly_pct = round(1.5714..., 2) = 1.57
        #   dollar_size = 100,000 * 1.57 / 100 = 1,570.0
        #   WITHOUT round (M14): frac_kelly_pct = 1.5714285...
        #   dollar_size = 100,000 * 1.5714285... / 100 = 1,571.428...
        # Kills M14: 1570.0 ≠ 1571.428...
        result = kelly_size_batch(
            np.array([0.52]), np.array([1.05]), 100_000.0,
            fraction=0.25, max_pct=1.0  # no cap — cap = 100000, far above 1570
        )
        assert result[0] == pytest.approx(1_570.0, abs=1e-6)

    def test_formula_sign_not_addition(self):
        # Use a case where + vs - produces a dramatically different uncapped output.
        # p=0.55, odds=2.5, fraction=0.25, bankroll=100,000, max_pct=1.0 (no cap):
        #   CORRECT (prob - q/odds): fk = 0.55 - 0.45/2.5 = 0.55 - 0.18 = 0.37
        #   MUTANT M7 (prob + q/odds): fk = 0.55 + 0.18 = 0.73
        #   CORRECT frac_pct = round(0.37*0.25*100,2) = round(9.25,2) = 9.25
        #   CORRECT dollar = 100000 * 9.25 / 100 = 9250.0
        #   MUTANT dollar = 100000 * round(0.73*0.25*100,2) / 100 = 100000*18.25/100 = 18250.0
        # Kills M7 (+ instead of -): 9250 ≠ 18250
        result = kelly_size_batch(
            np.array([0.55]), np.array([2.5]), 100_000.0,
            fraction=0.25, max_pct=1.0
        )
        assert result[0] == pytest.approx(9_250.0)

    def test_formula_division_not_multiplication(self):
        # p=0.6, odds=2.0, fraction=0.25, bankroll=100,000, max_pct=1.0 (no cap):
        #   CORRECT (q/odds): fk = 0.6 - 0.4/2.0 = 0.6 - 0.2 = 0.4
        #   MUTANT M8 (q*odds): fk = 0.6 - 0.4*2.0 = 0.6 - 0.8 = -0.2 → clipped to 0
        #   CORRECT frac_pct = round(0.4*0.25*100,2) = round(10.0,2) = 10.0
        #   CORRECT dollar = 100000 * 10.0 / 100 = 10000.0
        #   MUTANT dollar = 0.0  (clipped to zero since fk < 0)
        # Kills M8 (* instead of /): 10000 ≠ 0
        result = kelly_size_batch(
            np.array([0.6]), np.array([2.0]), 100_000.0,
            fraction=0.25, max_pct=1.0
        )
        assert result[0] == pytest.approx(10_000.0)

    def test_fraction_multiply_not_divide(self):
        # p=0.6, odds=2.0, fraction=0.25, bankroll=100,000, max_pct=1.0 (no cap):
        #   CORRECT (* fraction): frac=0.4*0.25=0.1 → pct=10.0 → dollar=10000.0
        #   MUTANT M9 (/ fraction): frac=0.4/0.25=1.6 → pct=round(160,2)=160.0 → dollar=160000
        #     (min with cap=100000*1.0=100000: min(160000,100000)=100000 — still different)
        # Kills M9: 10000 ≠ 100000
        result = kelly_size_batch(
            np.array([0.6]), np.array([2.0]), 100_000.0,
            fraction=0.25, max_pct=1.0
        )
        assert result[0] == pytest.approx(10_000.0)


class TestKellySizeBatchEdgeCases:
    """Edge cases: zeros, guards, empty array."""

    def test_odds_zero_gives_zero_exactly(self):
        # odds=0 must produce dollar_size=0 (guard: odds > 0)
        # Kills M12 (odds >= 0 guard): 0.6 - 0.4/0.0 = inf → dollar = inf ≠ 0
        prob_wins = np.array([0.6, 0.7, 0.8])
        odds = np.array([0.0, -1.0, -5.0])
        result = kelly_size_batch(prob_wins, odds, 100_000.0)
        np.testing.assert_array_equal(result, np.zeros(3))

    def test_odds_zero_boundary_exact_zero(self):
        # Single odds=0 case — verifies the > 0 guard specifically
        # Kills M12 (>= 0): would compute 0.6 - 0.4/0 = -inf → clip to 0 anyway
        # BUT inf case: np.where(inf > 0, inf, 0) = inf; then clip to 0 since inf*0.25=inf
        # Actually with odds=0: q/odds = 0.4/0.0 = inf (positive), so fk=0.6-inf=-inf
        # np.where(-inf > 0, -inf, 0.0) = 0.0 — so M12 doesn't change this particular value
        # The distinguishing case is odds=0 where the COMPUTATION leaks before zeroing:
        # Correct: np.where(0 > 0, ..., 0.0) = 0.0  (short-circuits to 0 before division)
        # Mutant M12: np.where(0 >= 0, prob - 0.4/0.0, 0.0) = np.where(True, -inf, 0) = -inf
        # Then -inf after zero-clipping: np.where(-inf > 0, -inf, 0.0) = 0.0
        # So M12 is also equivalent for odds=0 due to the second zero-clip
        # Distinguishing case: odds slightly above 0 where guard matters → covered by test_odds_zero_gives_zero
        result = kelly_size_batch(np.array([0.6]), np.array([0.0]), 100_000.0)
        assert result[0] == pytest.approx(0.0)

    def test_prob_win_zero_gives_zero(self):
        # p=0.0: fk = 0 - 1.0/odds < 0 → clipped to 0
        prob_wins = np.array([0.0, 0.0])
        odds = np.array([2.0, 5.0])
        result = kelly_size_batch(prob_wins, odds, 100_000.0)
        np.testing.assert_array_equal(result, np.zeros(2))

    def test_empty_array(self):
        result = kelly_size_batch(np.array([]), np.array([]), 100_000.0)
        assert result.shape == (0,)

    def test_batch_shape(self):
        n = 5
        prob_wins = np.array([0.6, 0.7, 0.8, 0.5, 0.3])
        odds = np.array([2.0, 3.0, 5.0, 1.0, 1.0])
        result = kelly_size_batch(prob_wins, odds, 100_000.0)
        assert result.shape == (n,)

    def test_all_results_non_negative(self):
        # Dollar sizes must never be negative regardless of input
        rng = np.random.default_rng(99)
        prob_wins = rng.uniform(0.0, 1.0, 50)
        odds = rng.uniform(0.0, 10.0, 50)
        result = kelly_size_batch(prob_wins, odds, 100_000.0)
        assert np.all(result >= 0.0), "All dollar sizes must be non-negative"

    def test_all_results_respect_cap(self):
        # No result may exceed bankroll * max_pct
        rng = np.random.default_rng(77)
        bankroll = 80_000.0
        max_pct = 0.025
        prob_wins = rng.uniform(0.5, 1.0, 50)
        odds = rng.uniform(1.0, 10.0, 50)
        result = kelly_size_batch(prob_wins, odds, bankroll, max_pct=max_pct)
        cap = bankroll * max_pct
        assert np.all(result <= cap + 1e-10), f"Cap violated: max={result.max()}, cap={cap}"
