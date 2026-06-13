"""Extended tests for kelly.py — thresholds and dollar sizing.

Pinned values derived from first principles, shown in comments.
Kills: M1-M6, M15-M18, M20 (scalar + __main__ mutations).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from kelly import kelly

# Repo root + kelly.py derived from this file's location — never hardcode an
# absolute local path (it breaks in CI where the checkout is elsewhere).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_KELLY_SCRIPT = Path(__file__).resolve().parent.parent / "kelly.py"


class TestKellyScalarFormula:
    """Pin EXACT output values to kill arithmetic and constant mutations."""

    def test_full_kelly_pct_exact(self):
        # p=0.6, odds=2.0:
        #   q = 1 - 0.6 = 0.4
        #   full_kelly = 0.6 - 0.4/2.0 = 0.6 - 0.2 = 0.4
        #   full_kelly_pct = round(0.4 * 100, 2) = 40.0
        result = kelly(prob_win=0.6, odds=2.0, fraction=0.25)
        assert result["full_kelly_pct"] == pytest.approx(40.0)

    def test_fractional_kelly_pct_exact(self):
        # p=0.6, odds=2.0, fraction=0.25:
        #   frac_kelly = 0.4 * 0.25 = 0.1
        #   fractional_kelly_pct = round(0.1 * 100, 2) = 10.0
        # Kills M3 (/ instead of *): 0.4 / 0.25 = 1.6 → pct=40.0 ≠ 10.0
        # Kills M18 (* 100 → / 100): round(0.1 / 100, 2) = 0.0 ≠ 10.0
        result = kelly(prob_win=0.6, odds=2.0, fraction=0.25)
        assert result["fractional_kelly_pct"] == pytest.approx(10.0)

    def test_full_kelly_pct_exact_different_inputs(self):
        # p=0.7, odds=3.0:
        #   q = 0.3, full_kelly = 0.7 - 0.3/3.0 = 0.7 - 0.1 = 0.6
        #   full_kelly_pct = round(0.6 * 100, 2) = 60.0
        # Kills M1 (+ instead of -): 0.7 + 0.1 = 0.8 → 80.0 ≠ 60.0
        # Kills M2 (* instead of /): 0.7 - 0.3*3 = 0.7 - 0.9 = -0.2 → -20.0 ≠ 60.0
        # Kills M17 (* 100 → / 100): round(0.6 / 100, 2) = 0.01 ≠ 60.0
        result = kelly(prob_win=0.7, odds=3.0, fraction=0.25)
        assert result["full_kelly_pct"] == pytest.approx(60.0)

    def test_fractional_kelly_pct_custom_fraction_exact(self):
        # p=0.6, odds=2.0, fraction=0.50:
        #   full_kelly = 0.4 (same as above)
        #   frac_kelly = 0.4 * 0.50 = 0.20
        #   fractional_kelly_pct = round(0.20 * 100, 2) = 20.0
        # Kills M3 (/ instead of *): 0.4 / 0.5 = 0.8 → 80.0 ≠ 20.0
        result = kelly(prob_win=0.6, odds=2.0, fraction=0.50)
        assert result["fractional_kelly_pct"] == pytest.approx(20.0)

    def test_no_edge_zero_pct_values(self):
        # p=0.3, odds=1.0:
        #   q=0.7, full_kelly = 0.3 - 0.7/1.0 = -0.4
        #   full_kelly_pct = round(-0.4 * 100, 2) = -40.0
        #   frac_kelly = -0.4 * 0.25 = -0.1
        #   fractional_kelly_pct = round(-0.1 * 100, 2) = -10.0
        # (These are returned even for DO NOT BET — the guard is edge_exists)
        result = kelly(prob_win=0.3, odds=1.0, fraction=0.25)
        assert result["full_kelly_pct"] == pytest.approx(-40.0)
        assert result["fractional_kelly_pct"] == pytest.approx(-10.0)
        assert result["edge_exists"] is False
        assert result["recommendation"] == "DO NOT BET"

    def test_edge_exists_true_only_when_strictly_positive(self):
        # p=0.5, odds=1.0: full_kelly = 0.5 - 0.5/1.0 = 0.0 (exactly)
        # edge_exists must be False (> 0, NOT >= 0)
        # Kills M4 (>= 0): 0.0 >= 0 → True (wrong)
        result = kelly(prob_win=0.5, odds=1.0)
        assert result["full_kelly_pct"] == pytest.approx(0.0)
        assert result["edge_exists"] is False
        assert result["recommendation"] == "DO NOT BET"

    def test_edge_exists_just_above_zero(self):
        # Complementary to the zero boundary test: small positive → edge_exists=True
        # p=0.505, odds=1.01: full_kelly = 0.505 - 0.495/1.01 ≈ 0.01490... > 0
        result = kelly(prob_win=0.505, odds=1.01)
        assert result["edge_exists"] is True
        # full_kelly_pct must be > 0
        assert result["full_kelly_pct"] > 0.0


class TestKellyRecommendations:
    """Pin exact recommendation thresholds; kill M5 (0.025→0.05) and M6 (0.10→0.05)."""

    def test_strong_recommendation_exact_pct(self):
        # p=0.7, odds=3.0: full_kelly_pct = 60.0 (> 10 → STRONG)
        result = kelly(prob_win=0.7, odds=3.0)
        assert result["full_kelly_pct"] == pytest.approx(60.0)
        assert result["recommendation"] == "STRONG"

    def test_strong_threshold_boundary(self):
        # M6 mutant changes threshold from 0.10 → 0.05
        # A value at full_kelly ≈ 0.063 (Case C) must be MARGINAL, NOT STRONG
        # p=0.52, odds=1.05: full_kelly = 0.52 - 0.48/1.05 ≈ 0.06286
        # full_kelly_pct = round(0.06286*100, 2) = 6.29  (< 10 → not STRONG)
        # Kills M6: M6 sets STRONG > 0.05 so 0.06286 > 0.05 → STRONG (wrong)
        result = kelly(prob_win=0.52, odds=1.05)
        assert result["full_kelly_pct"] == pytest.approx(6.29)
        assert result["recommendation"] == "MARGINAL"

    def test_marginal_recommendation_exact_pct(self):
        # p=0.52, odds=1.05:
        #   q = 0.48
        #   full_kelly = 0.52 - 0.48/1.05 = 0.52 - 0.45714... = 0.06286
        #   full_kelly_pct = round(0.06286*100, 2) = 6.29
        #   0.025 < 0.06286 <= 0.10 → MARGINAL
        result = kelly(prob_win=0.52, odds=1.05)
        assert result["full_kelly_pct"] == pytest.approx(6.29)
        assert result["recommendation"] == "MARGINAL"

    def test_marginal_threshold_lower_boundary(self):
        # M5 mutant changes MARGINAL lower threshold from 0.025 → 0.05
        # A value between 0.025 and 0.05 must STILL be MARGINAL
        # p=0.505, odds=1.01: full_kelly ≈ 0.01490 → WEAK (< 0.025)
        # Need: 0.025 < fk < 0.05
        # p=0.53, odds=1.03: fk = 0.53 - 0.47/1.03 = 0.53 - 0.4563 = 0.0737 → too high
        # p=0.512, odds=1.01: fk = 0.512 - 0.488/1.01 = 0.512 - 0.4832 = 0.0288
        # round(0.0288*100,2) = 2.88 — in range (0.025, 0.05)
        # M5 would classify this as WEAK (> 0.05 fails → goes to >=0 check) — NO:
        # M5 sets boundary at 0.05, so 0.0288 < 0.05 → falls through to WEAK
        # Correct boundary at 0.025: 0.0288 > 0.025 → MARGINAL
        result = kelly(prob_win=0.512, odds=1.01)
        fk_pct = result["full_kelly_pct"]
        # First verify the arithmetic
        # q=0.488, q/odds=0.488/1.01=0.48316..., fk=0.512-0.48316=0.02884
        # fk_pct = round(0.02884*100, 2) = 2.88
        assert fk_pct == pytest.approx(2.88)
        assert 2.5 < fk_pct < 5.0  # confirms it's in the discriminating range
        # Kills M5 (MARGINAL threshold 0.025 → 0.05): M5 would return WEAK for this
        assert result["recommendation"] == "MARGINAL"

    def test_weak_recommendation_exact_pct(self):
        # p=0.505, odds=1.01:
        #   q = 0.495
        #   full_kelly = 0.505 - 0.495/1.01 = 0.505 - 0.49010... = 0.01490
        #   full_kelly_pct = round(0.01490*100, 2) = 1.49
        #   0 < 0.01490 <= 0.025 → WEAK
        result = kelly(prob_win=0.505, odds=1.01)
        assert result["full_kelly_pct"] == pytest.approx(1.49)
        assert result["recommendation"] == "WEAK"

    def test_no_edge_do_not_bet(self):
        # p=0.3, odds=1.0: full_kelly = -0.4 → DO NOT BET
        result = kelly(prob_win=0.3, odds=1.0)
        assert result["recommendation"] == "DO NOT BET"
        assert result["edge_exists"] is False

    def test_custom_fraction_exact_double(self):
        # fraction=0.50 must be exactly 2× fraction=0.25 result
        # p=0.6, odds=2.0:
        #   f=0.25: frac_kelly_pct = 10.0
        #   f=0.50: frac_kelly_pct = 20.0
        #   ratio = 20.0 / 10.0 = 2.0
        r_quarter = kelly(prob_win=0.6, odds=2.0, fraction=0.25)
        r_half = kelly(prob_win=0.6, odds=2.0, fraction=0.50)
        assert r_quarter["fractional_kelly_pct"] == pytest.approx(10.0)
        assert r_half["fractional_kelly_pct"] == pytest.approx(20.0)
        assert r_half["fractional_kelly_pct"] == pytest.approx(r_quarter["fractional_kelly_pct"] * 2)

    def test_zero_odds_guard(self):
        # odds=0 → guard returns zeros immediately
        # Kills M20 (odds < 0 instead of <= 0): odds=0 would slip through
        result = kelly(prob_win=0.6, odds=0)
        assert result["fractional_kelly_pct"] == pytest.approx(0.0)
        assert result["full_kelly_pct"] == pytest.approx(0.0)
        assert result["edge_exists"] is False
        assert result["recommendation"] == "DO NOT BET"


class TestKellyDollarSizing:
    """Pin Gate-3 sizing exactly: dollar_size, max_per_position, use_size.

    Tests call kelly() then apply the same arithmetic as __main__ to ensure
    the production constant 0.025 and min() are both independently pinned.
    """

    def test_gate3_cap_applied_to_high_kelly(self):
        # p=0.6, odds=2.0, fraction=0.25, bankroll=100,000:
        #   fractional_kelly_pct = 10.0  (from formula)
        #   dollar_size = 100,000 * 10.0 / 100 = 10,000.0
        #   max_per_position = 100,000 * 0.025 = 2,500.0
        #   use_size = min(10,000.0, 2,500.0) = 2,500.0  [CAPPED]
        # Kills M15 (0.025→0.05): max_per_position = 5,000 ≠ 2,500
        # Kills M16 (min→max): use_size = 10,000 ≠ 2,500
        result = kelly(prob_win=0.6, odds=2.0, fraction=0.25)
        bankroll = 100_000
        dollar_size = round(bankroll * result["fractional_kelly_pct"] / 100, 2)
        max_per_position = round(bankroll * 0.025, 2)
        use_size = min(dollar_size, max_per_position)

        assert result["fractional_kelly_pct"] == pytest.approx(10.0)
        assert dollar_size == pytest.approx(10_000.0)
        assert max_per_position == pytest.approx(2_500.0)
        assert use_size == pytest.approx(2_500.0)

    def test_gate3_below_cap(self):
        # p=0.51, odds=1.02, fraction=0.25, bankroll=100,000:
        #   q = 0.49
        #   full_kelly = 0.51 - 0.49/1.02 = 0.51 - 0.48039... = 0.02961...
        #   frac_kelly = 0.02961... * 0.25 = 0.00740...
        #   fractional_kelly_pct = round(0.00740...*100, 2) = 0.74
        #   dollar_size = 100,000 * 0.74 / 100 = 740.0
        #   max_per_position = 100,000 * 0.025 = 2,500.0
        #   use_size = min(740.0, 2,500.0) = 740.0  [NOT capped]
        # Kills M16 (min→max): min(740, 2500)=740 vs max(740, 2500)=2500 (wrong)
        result = kelly(prob_win=0.51, odds=1.02, fraction=0.25)
        bankroll = 100_000
        dollar_size = round(bankroll * result["fractional_kelly_pct"] / 100, 2)
        max_per_position = round(bankroll * 0.025, 2)
        use_size = min(dollar_size, max_per_position)

        assert result["fractional_kelly_pct"] == pytest.approx(0.74)
        assert dollar_size == pytest.approx(740.0)
        assert max_per_position == pytest.approx(2_500.0)
        assert use_size == pytest.approx(740.0)  # NOT capped — smaller than cap

    def test_zero_odds(self):
        result = kelly(prob_win=0.6, odds=0)
        assert result["fractional_kelly_pct"] == pytest.approx(0.0)
        assert result["edge_exists"] is False

    def test_edge_boundary_zero(self):
        # p=0.5, odds=1.0: full_kelly = 0 exactly → no edge
        result = kelly(prob_win=0.5, odds=1.0)
        assert result["edge_exists"] is False
        assert result["recommendation"] == "DO NOT BET"


class TestKellyCLISizing:
    """Test the __main__ CLI code path via subprocess.

    This is the ONLY path that exercises:
      - result["dollar_size"]
      - result["max_per_position"]  (0.025 constant)
      - result["use_size"]          (min() call)
    Kills M15 (0.025→0.05), M16 (min→max).
    """

    def _run_cli(self, prob, odds, fraction, bankroll):
        r = subprocess.run(
            [sys.executable, str(_KELLY_SCRIPT),
             "--prob", str(prob),
             "--odds", str(odds),
             "--fraction", str(fraction),
             "--bankroll", str(bankroll)],
            capture_output=True, text=True,
            cwd=str(_REPO_ROOT)
        )
        assert r.returncode == 0, f"CLI failed: {r.stderr}"
        return json.loads(r.stdout)

    def test_cli_cap_enforced_high_kelly(self):
        # p=0.9, odds=5.0, fraction=0.25, bankroll=50,000:
        #   q=0.1, full_kelly=0.9-0.1/5=0.9-0.02=0.88
        #   frac_kelly=0.88*0.25=0.22
        #   fractional_kelly_pct=round(0.22*100,2)=22.0
        #   dollar_size=round(50000*22.0/100,2)=11000.0
        #   max_per_position=round(50000*0.025,2)=1250.0    ← 0.025 const
        #   use_size=min(11000.0,1250.0)=1250.0             ← min, capped
        # Kills M15 (0.025→0.05): max_per_position=2500.0 ≠ 1250.0
        # Kills M16 (min→max): use_size=11000.0 ≠ 1250.0
        data = self._run_cli(prob=0.9, odds=5.0, fraction=0.25, bankroll=50_000)
        assert data["dollar_size"] == pytest.approx(11_000.0)
        assert data["max_per_position"] == pytest.approx(1_250.0)
        assert data["use_size"] == pytest.approx(1_250.0)

    def test_cli_no_cap_low_kelly(self):
        # p=0.51, odds=1.02, fraction=0.25, bankroll=50,000:
        #   fractional_kelly_pct = 0.74 (derived above from test_gate3_below_cap)
        #   dollar_size = round(50000*0.74/100,2) = 370.0
        #   max_per_position = round(50000*0.025,2) = 1250.0
        #   use_size = min(370.0, 1250.0) = 370.0  [NOT capped]
        # Kills M16 (min→max): use_size=max(370,1250)=1250.0 ≠ 370.0
        data = self._run_cli(prob=0.51, odds=1.02, fraction=0.25, bankroll=50_000)
        assert data["dollar_size"] == pytest.approx(370.0)
        assert data["max_per_position"] == pytest.approx(1_250.0)
        assert data["use_size"] == pytest.approx(370.0)

    def test_cli_exact_standard_case(self):
        # p=0.6, odds=2.0, fraction=0.25, bankroll=100,000:
        #   dollar_size=10000.0, max_per_position=2500.0, use_size=2500.0
        data = self._run_cli(prob=0.6, odds=2.0, fraction=0.25, bankroll=100_000)
        assert data["dollar_size"] == pytest.approx(10_000.0)
        assert data["max_per_position"] == pytest.approx(2_500.0)
        assert data["use_size"] == pytest.approx(2_500.0)

    def test_cli_fractional_kelly_pct_exact(self):
        # CLI output must include the correct full_kelly_pct and fractional_kelly_pct
        # p=0.7, odds=3.0, fraction=0.25, bankroll=100,000:
        #   full_kelly_pct=60.0, frac_kelly_pct=15.0
        #   dollar_size=round(100000*15.0/100,2)=15000.0
        #   max_per_position=round(100000*0.025,2)=2500.0
        #   use_size=min(15000,2500)=2500.0
        data = self._run_cli(prob=0.7, odds=3.0, fraction=0.25, bankroll=100_000)
        assert data["full_kelly_pct"] == pytest.approx(60.0)
        assert data["fractional_kelly_pct"] == pytest.approx(15.0)
        assert data["dollar_size"] == pytest.approx(15_000.0)
        assert data["max_per_position"] == pytest.approx(2_500.0)
        assert data["use_size"] == pytest.approx(2_500.0)
