"""BOUNCE SETUP scanner. Red tests against docs/bounce-setup.md.

Pure functions only: every expectation is computed from inline synthetic series,
never from network data.
"""

from __future__ import annotations

from datetime import date

import pytest

from bounce_setup_scanner import (
    IV_OFF_PEAK_MIN,
    IV_RUNUP_MIN,
    SKEW_EASE_MIN,
    SLOPE_SESSIONS,
    STAGE2_TOP_N,
    STRETCH_PCTL_MAX,
    WINDOW,
    classify,
    occ_symbol,
    pick_expiry,
    rank_stretch,
    select_fixed_strike,
    skew_easing,
    stage2_tickers,
    vol_plateau,
)


class TestConstants:
    def test_spec_defaults(self):
        assert WINDOW == 20
        assert STAGE2_TOP_N == 30
        assert STRETCH_PCTL_MAX == 10.0
        assert IV_RUNUP_MIN == 2.0
        assert IV_OFF_PEAK_MIN == 0.25
        assert SKEW_EASE_MIN == 0.3
        assert SLOPE_SESSIONS == 3


class TestOccSymbol:
    def test_bac_reference_contract(self):
        assert occ_symbol("bac", "2026-10-16", "P", 55.0) == "BAC261016P00055000"

    def test_fractional_strike(self):
        assert occ_symbol("F", "2026-10-16", "P", 12.5) == "F261016P00012500"


class TestPickExpiry:
    AS_OF = date(2026, 9, 18)

    def test_prefers_a_monthly_in_the_30_to_45_dte_window(self):
        # From 2026-09-10: 2026-10-16 (October third Friday) is 36 DTE. 2026-10-09
        # is a weekly at 29 DTE and 2026-10-23 a weekly at 43 DTE: both skipped.
        expiries = ["2026-10-09", "2026-10-16", "2026-10-23", "2026-11-20"]
        assert pick_expiry(expiries, date(2026, 9, 10)) == "2026-10-16"

    def test_falls_back_to_nearest_monthly_with_at_least_21_dte(self):
        expiries = ["2026-09-25", "2026-10-02", "2026-11-20", "2026-12-18"]
        assert pick_expiry(expiries, self.AS_OF) == "2026-11-20"

    def test_none_when_no_monthly_qualifies(self):
        assert pick_expiry(["2026-09-25", "2026-10-02"], self.AS_OF) is None


class TestSelectFixedStrike:
    def test_nearest_strike_to_window_start_close(self):
        assert select_fixed_strike(54.2, [50.0, 52.5, 55.0, 57.5]) == 55.0

    def test_tie_goes_to_the_lower_strike(self):
        assert select_fixed_strike(53.75, [52.5, 55.0]) == 52.5

    def test_none_without_strikes(self):
        assert select_fixed_strike(54.0, []) is None


def _series(values):
    return [float(v) for v in values]


class TestVolPlateau:
    def test_bac_shape_passes(self):
        # Rises to +4.5, gives back most of it, and is still falling.
        fs = _series([30, 31, 31.2, 31.1, 31, 31.3, 30.8, 32, 32.5, 32.3,
                      33, 33, 34.2, 34.5, 34.3, 33.7, 32.8, 33.1, 32.9, 30.9])
        out = vol_plateau(fs)
        assert out["runup"] == pytest.approx(4.5)
        assert out["off_peak"] == pytest.approx((34.5 - 30.9) / 4.5)
        assert out["slope"] == pytest.approx(30.9 - (32.8 + 33.1 + 32.9) / 3)
        assert out["pass"] is True

    def test_no_runup_fails(self):
        fs = _series([30 + 0.05 * i for i in range(20)])
        assert vol_plateau(fs)["pass"] is False

    def test_still_rising_fails_on_slope(self):
        fs = _series([30] * 10 + [31, 32, 33, 34, 35, 34, 33, 33.5, 33.8, 34.0])
        out = vol_plateau(fs)
        assert out["slope"] > 0
        assert out["pass"] is False

    def test_flat_slope_passes_boundary(self):
        # slope == 0 is a plateau (<= 0); off_peak exactly 0.25 passes (>=).
        fs = _series([30] * 13 + [34, 33, 33, 33, 33, 33, 33])
        out = vol_plateau(fs)
        assert out["runup"] == pytest.approx(4.0)
        assert out["off_peak"] == pytest.approx(0.25)
        assert out["slope"] == pytest.approx(0.0)
        assert out["pass"] is True

    def test_short_series_is_unavailable(self):
        assert vol_plateau(_series([30, 31, 32]))["pass"] is None


class TestSkewEasing:
    def test_bac_shape_passes(self):
        skew = _series([2.62, 2.68, 2.57, 1.75, 2.3, 1.8, 3.0, 2.97, 2.68, 2.63,
                        2.63, 2.6, 2.7, 3.2, 2.58, 3.5, 2.82, 3.47, 3.2, 2.98])
        out = skew_easing(skew)
        assert out["ease"] == pytest.approx(3.5 - 2.98)
        assert out["slope"] == pytest.approx(2.98 - (2.82 + 3.47 + 3.2) / 3)
        assert out["pass"] is True

    def test_flat_slope_fails_strict(self):
        skew = _series([3.0] * 15 + [3.5, 3.0, 3.0, 3.0, 3.0])
        out = skew_easing(skew)
        assert out["slope"] == pytest.approx(0.0)
        assert out["pass"] is False

    def test_small_ease_fails(self):
        skew = _series([3.0] * 16 + [3.2, 3.15, 3.1, 3.0])
        assert skew_easing(skew)["pass"] is False

    def test_short_series_is_unavailable(self):
        assert skew_easing(_series([3.0, 2.9]))["pass"] is None


class TestRankStretch:
    def test_lowest_combined_percentile_ranks_first(self):
        scores = {
            "AAA": {"rsi": 70.0, "pct_b": 1.1, "ret_z": 2.0},
            "BAC": {"rsi": 22.0, "pct_b": -0.2, "ret_z": -2.5},
            "CCC": {"rsi": 45.0, "pct_b": 0.4, "ret_z": -0.1},
            "DDD": {"rsi": 30.0, "pct_b": 0.05, "ret_z": -1.2},
        }
        ranked = rank_stretch(scores)
        assert [row["ticker"] for row in ranked] == ["BAC", "DDD", "CCC", "AAA"]
        assert ranked[0]["stretch_rank"] == 1
        assert ranked[0]["stretch_pctl"] == pytest.approx(0.0)
        assert ranked[-1]["stretch_pctl"] == pytest.approx(100.0)

    def test_names_missing_a_metric_are_excluded(self):
        ranked = rank_stretch({
            "BAC": {"rsi": 22.0, "pct_b": -0.2, "ret_z": -2.5},
            "NEW": {"rsi": 40.0, "pct_b": 0.1, "ret_z": None},
        })
        assert [row["ticker"] for row in ranked] == ["BAC"]

    def test_stage2_takes_only_the_top_n(self):
        scores = {f"T{i:03d}": {"rsi": float(i), "pct_b": i / 100, "ret_z": i / 10} for i in range(80)}
        picked = stage2_tickers(rank_stretch(scores))
        assert len(picked) == STAGE2_TOP_N
        assert picked[0] == "T000"


class TestClassify:
    PASS = {"pass": True}
    FAIL = {"pass": False}
    NA = {"pass": None}

    def test_verdict_table(self):
        assert classify(5.0, self.PASS, self.PASS) == "BOUNCE_SETUP"
        assert classify(5.0, self.PASS, self.FAIL) == "WATCH"
        assert classify(5.0, self.FAIL, self.PASS) == "WATCH"
        assert classify(5.0, self.FAIL, self.FAIL) == "STRETCHED"

    def test_unavailable_leg_is_never_a_pass(self):
        assert classify(5.0, self.NA, self.PASS) == "WATCH"
        assert classify(5.0, self.NA, self.NA) == "STRETCHED"

    def test_stretch_boundary_is_inclusive(self):
        assert classify(10.0, self.PASS, self.PASS) == "BOUNCE_SETUP"
        assert classify(10.1, self.PASS, self.PASS) is None
