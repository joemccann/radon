"""Tests for cri_scan.py — Crash Risk Index scanner.

All tests are pure computation — no network calls.
"""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from cri_scan import (
    _extract_ib_quote_value,
    cor1m_level_and_change,
    score_vix_component,
    score_vvix_component,
    score_correlation_component,
    score_momentum_component,
    compute_cri,
    cri_level,
    cta_exposure_model,
    crash_trigger,
    run_analysis,
    select_cor1m_current_quote,
)


# ══════════════════════════════════════════════════════════════════
# 1. COR1M Implied Correlation
# ══════════════════════════════════════════════════════════════════

class TestCor1mSignal:
    """Tests for COR1M level + 5d change extraction."""

    def test_returns_current_level_and_5d_change(self):
        """COR1M is a percentage index, e.g. 31.1 means 31.1% implied corr."""
        cor1m = np.array([24.5, 25.1, 26.8, 27.2, 28.4, 30.3, 31.1])
        level, change = cor1m_level_and_change(cor1m)
        assert level == 31.1
        assert change == 6.0

    def test_short_series_returns_nan_change(self):
        """Need 6 observations for a 5-session change."""
        cor1m = np.array([28.0, 29.0, 30.0, 31.0, 32.0])
        level, change = cor1m_level_and_change(cor1m)
        assert level == 32.0
        assert np.isnan(change)

    def test_current_override_replaces_last_bar_for_level_and_change(self):
        """Current quote should win when the latest daily bar is wrong."""
        cor1m = np.array([24.0, 25.0, 26.0, 27.0, 28.0, 29.0, 31.1])
        level, change = cor1m_level_and_change(cor1m, current_override=28.97)
        assert level == 28.97
        assert change == pytest.approx(3.97)

    def test_all_nan_series_returns_nan(self):
        """All-NaN COR1M series is invalid."""
        level, change = cor1m_level_and_change(np.full(10, np.nan))
        assert np.isnan(level)
        assert np.isnan(change)


class TestIBCurrentQuoteExtraction:
    """Tests for COR1M snapshot quote selection."""

    def test_ignores_close_only_snapshot_for_current_quote(self):
        snapshot = SimpleNamespace(last=None, close=31.1, bid=None, ask=None)
        assert _extract_ib_quote_value(snapshot) is None

    def test_uses_last_trade_when_available(self):
        snapshot = SimpleNamespace(last=28.97, close=31.1, bid=None, ask=None)
        assert _extract_ib_quote_value(snapshot) == 28.97


class TestCor1mCurrentQuoteSelection:
    """Tests for reconciling IB and Yahoo current COR1M quotes."""

    def test_prefers_yahoo_when_quotes_diverge_materially(self):
        assert select_cor1m_current_quote(ib_quote=31.1, yahoo_quote=28.97) == 28.97

    def test_keeps_ib_when_quotes_are_consistent(self):
        assert select_cor1m_current_quote(ib_quote=28.9, yahoo_quote=28.97) == 28.9

    def test_uses_ib_when_yahoo_missing(self):
        assert select_cor1m_current_quote(ib_quote=28.97, yahoo_quote=None) == 28.97

    def test_uses_yahoo_when_ib_missing(self):
        assert select_cor1m_current_quote(ib_quote=None, yahoo_quote=28.97) == 28.97


# ══════════════════════════════════════════════════════════════════
# 2. CRI Score Components (each scored 0-25)
# ══════════════════════════════════════════════════════════════════

class TestCRIScoreComponents:
    """Tests for individual component scoring functions."""

    # ── VIX ──
    def test_vix_low_calm(self):
        """VIX < 15, flat → score near 0."""
        score = score_vix_component(vix=12.0, vix_5d_roc=0.0)
        assert 0 <= score <= 5, f"Expected low score for calm VIX, got {score}"

    def test_vix_high_crisis(self):
        """VIX > 40, rising fast → score near 25."""
        score = score_vix_component(vix=50.0, vix_5d_roc=80.0)
        assert score >= 20, f"Expected high score for crisis VIX, got {score}"

    def test_vix_moderate(self):
        """VIX ~25, moderate rise → mid-range score."""
        score = score_vix_component(vix=25.0, vix_5d_roc=20.0)
        assert 5 <= score <= 20, f"Expected mid-range, got {score}"

    def test_vix_clamped_to_25(self):
        """Score never exceeds 25."""
        score = score_vix_component(vix=100.0, vix_5d_roc=200.0)
        assert score <= 25.0

    def test_vix_floor_at_0(self):
        """Score never goes below 0."""
        score = score_vix_component(vix=5.0, vix_5d_roc=-50.0)
        assert score >= 0.0

    # ── VVIX ──
    def test_vvix_low_calm(self):
        """VVIX < 90 → score near 0."""
        score = score_vvix_component(vvix=80.0, vvix_vix_ratio=5.0)
        assert 0 <= score <= 5, f"Expected low score for calm VVIX, got {score}"

    def test_vvix_high_crisis(self):
        """VVIX > 140, high ratio → score near 25."""
        score = score_vvix_component(vvix=160.0, vvix_vix_ratio=10.0)
        assert score >= 20, f"Expected high score, got {score}"

    def test_vvix_clamped(self):
        """Score clamped to [0, 25]."""
        score = score_vvix_component(vvix=200.0, vvix_vix_ratio=20.0)
        assert 0 <= score <= 25

    # ── Correlation ──
    def test_correlation_calm(self):
        """Low correlation → score near 0."""
        score = score_correlation_component(corr=15.0, corr_5d_change=0.0)
        assert 0 <= score <= 5, f"Expected low score, got {score}"

    def test_correlation_crisis(self):
        """High correlation + spiking → score near 25."""
        score = score_correlation_component(corr=80.0, corr_5d_change=30.0)
        assert score >= 20, f"Expected high score, got {score}"

    def test_correlation_clamped(self):
        """Score clamped to [0, 25]."""
        score = score_correlation_component(corr=100.0, corr_5d_change=100.0)
        assert 0 <= score <= 25

    # ── Momentum ──
    def test_momentum_above_ma(self):
        """SPX above 100d MA → score near 0."""
        score = score_momentum_component(spx_distance_pct=2.0)
        assert 0 <= score <= 5, f"Expected low score above MA, got {score}"

    def test_momentum_below_ma_deep(self):
        """SPX 10%+ below MA → score near 25."""
        score = score_momentum_component(spx_distance_pct=-12.0)
        assert score >= 20, f"Expected high score deep below MA, got {score}"

    def test_momentum_at_ma(self):
        """SPX at MA → score near 0."""
        score = score_momentum_component(spx_distance_pct=0.0)
        assert 0 <= score <= 5

    def test_momentum_clamped(self):
        """Score clamped to [0, 25]."""
        score = score_momentum_component(spx_distance_pct=-30.0)
        assert 0 <= score <= 25

    # ── NaN inputs ──
    def test_nan_vix_returns_zero(self):
        score = score_vix_component(vix=float("nan"), vix_5d_roc=0.0)
        assert score == 0.0

    def test_nan_correlation_returns_zero(self):
        score = score_correlation_component(corr=float("nan"), corr_5d_change=0.0)
        assert score == 0.0


# ══════════════════════════════════════════════════════════════════
# 3. CRI Levels
# ══════════════════════════════════════════════════════════════════

class TestCRILevels:
    """Tests for cri_level() classification."""

    def test_low(self):
        assert cri_level(0) == "LOW"
        assert cri_level(24) == "LOW"

    def test_elevated(self):
        assert cri_level(25) == "ELEVATED"
        assert cri_level(49) == "ELEVATED"

    def test_high(self):
        assert cri_level(50) == "HIGH"
        assert cri_level(74) == "HIGH"

    def test_critical(self):
        assert cri_level(75) == "CRITICAL"
        assert cri_level(100) == "CRITICAL"

    def test_boundary_25(self):
        """25 is ELEVATED, not LOW."""
        assert cri_level(25) == "ELEVATED"

    def test_boundary_50(self):
        """50 is HIGH, not ELEVATED."""
        assert cri_level(50) == "HIGH"

    def test_boundary_75(self):
        """75 is CRITICAL, not HIGH."""
        assert cri_level(75) == "CRITICAL"


# ══════════════════════════════════════════════════════════════════
# 4. CTA Exposure Model
# ══════════════════════════════════════════════════════════════════

class TestCTAExposureModel:
    """Tests for cta_exposure_model()."""

    def test_normal_vol_full_exposure(self):
        """10% realized vol → 100% exposure (10%/10% = 1)."""
        result = cta_exposure_model(realized_vol=10.0)
        assert abs(result["exposure_pct"] - 100.0) < 1.0

    def test_doubled_vol_half_exposure(self):
        """20% realized vol → 50% exposure."""
        result = cta_exposure_model(realized_vol=20.0)
        assert abs(result["exposure_pct"] - 50.0) < 1.0

    def test_quad_vol_quarter_exposure(self):
        """40% realized vol → 25% exposure."""
        result = cta_exposure_model(realized_vol=40.0)
        assert abs(result["exposure_pct"] - 25.0) < 1.0

    def test_low_vol_capped_at_200(self):
        """5% realized vol → exposure would be 200%, capped at 200%."""
        result = cta_exposure_model(realized_vol=5.0)
        assert result["exposure_pct"] <= 200.0

    def test_zero_vol_safe(self):
        """Zero vol should not cause division by zero."""
        result = cta_exposure_model(realized_vol=0.0)
        assert "exposure_pct" in result
        assert not math.isnan(result["exposure_pct"])

    def test_forced_reduction_positive_when_vol_high(self):
        """High vol → forced_reduction > 0."""
        result = cta_exposure_model(realized_vol=30.0)
        assert result["forced_reduction_pct"] > 0

    def test_forced_reduction_zero_when_vol_normal(self):
        """Normal vol → no forced reduction."""
        result = cta_exposure_model(realized_vol=10.0)
        assert result["forced_reduction_pct"] == 0.0

    def test_estimated_selling_scales(self):
        """Estimated selling should increase with higher vol."""
        low = cta_exposure_model(realized_vol=15.0)
        high = cta_exposure_model(realized_vol=40.0)
        assert high["est_selling_bn"] >= low["est_selling_bn"]


# ══════════════════════════════════════════════════════════════════
# 5. Crash Trigger
# ══════════════════════════════════════════════════════════════════

class TestCrashTrigger:
    """Tests for crash_trigger() — all three conditions must fire."""

    def test_all_conditions_met(self):
        """All three: SPX < 100d MA, vol > 25%, COR1M > 60 → TRIGGERED."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=30.0,
            cor1m=70.0,
        )
        assert result["triggered"] is True

    def test_spx_above_ma_fails(self):
        """SPX above MA → not triggered."""
        result = crash_trigger(
            spx_below_ma=False,
            realized_vol=30.0,
            cor1m=70.0,
        )
        assert result["triggered"] is False

    def test_low_vol_fails(self):
        """Vol < 25% → not triggered."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=15.0,
            cor1m=70.0,
        )
        assert result["triggered"] is False

    def test_low_correlation_fails(self):
        """COR1M < 60 → not triggered."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=30.0,
            cor1m=40.0,
        )
        assert result["triggered"] is False

    def test_march_2020_scenario(self):
        """March 2020 conditions: deep below MA, vol 80%+, COR1M 85+."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=80.0,
            cor1m=85.0,
        )
        assert result["triggered"] is True
        assert result["conditions"]["spx_below_100d_ma"] is True
        assert result["conditions"]["realized_vol_gt_25"] is True
        assert result["conditions"]["cor1m_gt_60"] is True

    def test_boundary_vol_exactly_25(self):
        """Vol exactly 25% is borderline — should pass (> 25)."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=25.0,
            cor1m=70.0,
        )
        # 25.0 is NOT > 25.0, so should NOT trigger
        assert result["triggered"] is False

    def test_boundary_corr_exactly_060(self):
        """COR1M exactly 60 is borderline — should NOT pass (> 60)."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=30.0,
            cor1m=60.0,
        )
        assert result["triggered"] is False


# ══════════════════════════════════════════════════════════════════
# 6. Empty / NaN Data
# ══════════════════════════════════════════════════════════════════

class TestEmptyData:
    """Edge cases: NaN inputs, insufficient data."""

    def test_nan_vol_cta_model(self):
        """NaN vol → safe output."""
        result = cta_exposure_model(realized_vol=float("nan"))
        assert "exposure_pct" in result

    def test_nan_correlation_trigger(self):
        """NaN correlation → not triggered."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=30.0,
            cor1m=float("nan"),
        )
        assert result["triggered"] is False

    def test_nan_vol_trigger(self):
        """NaN vol → not triggered."""
        result = crash_trigger(
            spx_below_ma=True,
            realized_vol=float("nan"),
            cor1m=70.0,
        )
        assert result["triggered"] is False

    def test_compute_cri_with_nans(self):
        """compute_cri should handle NaN inputs gracefully."""
        result = compute_cri(
            vix=float("nan"), vix_5d_roc=0.0,
            vvix=float("nan"), vvix_vix_ratio=0.0,
            corr=float("nan"), corr_5d_change=0.0,
            spx_distance_pct=0.0,
        )
        assert 0 <= result["score"] <= 100
        assert result["level"] in ("LOW", "ELEVATED", "HIGH", "CRITICAL")


# ══════════════════════════════════════════════════════════════════
# 7. Composite CRI (integration)
# ══════════════════════════════════════════════════════════════════

class TestComputeCRI:
    """Integration tests for the full compute_cri function."""

    def test_calm_market(self):
        """All inputs calm → low score."""
        result = compute_cri(
            vix=13.0, vix_5d_roc=0.0,
            vvix=80.0, vvix_vix_ratio=6.0,
            corr=20.0, corr_5d_change=0.0,
            spx_distance_pct=3.0,
        )
        assert result["score"] < 25
        assert result["level"] == "LOW"

    def test_crisis_market(self):
        """All inputs at crisis levels → high score."""
        result = compute_cri(
            vix=55.0, vix_5d_roc=100.0,
            vvix=160.0, vvix_vix_ratio=3.0,
            corr=85.0, corr_5d_change=40.0,
            spx_distance_pct=-15.0,
        )
        assert result["score"] >= 75
        assert result["level"] == "CRITICAL"

    def test_score_is_sum_of_components(self):
        """Total score = sum of 4 components."""
        result = compute_cri(
            vix=25.0, vix_5d_roc=20.0,
            vvix=120.0, vvix_vix_ratio=5.0,
            corr=50.0, corr_5d_change=10.0,
            spx_distance_pct=-3.0,
        )
        expected = (
            result["components"]["vix"]
            + result["components"]["vvix"]
            + result["components"]["correlation"]
            + result["components"]["momentum"]
        )
        assert abs(result["score"] - expected) < 0.5  # rounding tolerance

    def test_result_has_all_fields(self):
        """Output dict has required keys."""
        result = compute_cri(
            vix=20.0, vix_5d_roc=5.0,
            vvix=100.0, vvix_vix_ratio=5.0,
            corr=30.0, corr_5d_change=2.0,
            spx_distance_pct=1.0,
        )
        assert "score" in result
        assert "level" in result
        assert "components" in result
        assert set(result["components"].keys()) == {"vix", "vvix", "correlation", "momentum"}


# ══════════════════════════════════════════════════════════════════
# 8. Full Analysis Output
# ══════════════════════════════════════════════════════════════════

class TestRunAnalysis:
    """Integration tests for run_analysis() using COR1M input."""

    def test_emits_cor1m_fields_and_trigger_key(self):
        n = 140
        dates = [f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]

        aligned = {
            "VIX": np.linspace(18.0, 30.0, n),
            "VVIX": np.linspace(90.0, 120.0, n),
            "SPY": np.linspace(600.0, 560.0, n),
            "COR1M": np.concatenate([np.full(n - 6, 28.0), np.array([30.0, 31.5, 33.0, 35.0, 37.0, 40.0])]),
        }

        result = run_analysis(aligned, dates)

        assert result["cor1m"] == 40.0
        assert result["cor1m_5d_change"] == 10.0
        assert "avg_sector_correlation" not in result
        assert result["crash_trigger"]["conditions"]["cor1m_gt_60"] is False

    def test_prefers_cor1m_current_quote_over_last_history_bar(self):
        n = 140
        dates = [f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]

        aligned = {
            "VIX": np.linspace(18.0, 30.0, n),
            "VVIX": np.linspace(90.0, 120.0, n),
            "SPY": np.linspace(600.0, 560.0, n),
            "COR1M": np.concatenate([np.full(n - 6, 24.0), np.array([25.0, 26.0, 27.0, 28.0, 29.0, 31.1])]),
        }

        result = run_analysis(aligned, dates, current_quotes={"COR1M": 28.97})

        assert result["cor1m"] == 28.97
        assert result["cor1m_5d_change"] == pytest.approx(3.97)
        assert result["history"][-1]["cor1m"] == 28.97

    def test_caches_enough_spy_closes_to_rebuild_20_session_rvol_history(self):
        n = 140
        dates = [f"2026-02-{(i % 28) + 1:02d}" for i in range(n)]

        aligned = {
            "VIX": np.linspace(18.0, 30.0, n),
            "VVIX": np.linspace(90.0, 120.0, n),
            "SPY": np.linspace(600.0, 560.0, n),
            "COR1M": np.linspace(24.0, 40.0, n),
        }

        result = run_analysis(aligned, dates)

        assert len(result["history"]) == 20
        assert all(entry["realized_vol"] is not None for entry in result["history"])
        assert len(result["spy_closes"]) == 40
