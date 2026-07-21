"""Shared realized-vol math — scripts/utils/realized_vol.py.

Pins the extraction of compute_realized_vol out of cri_scan.py (rv-ratio
plan step 1): scalar parity with the historical cri implementation on
fixed numpy series, NaN below window+1 prices, the window=252 case, and
the series-vs-scalar parity property at every index on synthetic GBM
data (windows 20 and 252).

Expected values are derived in-test with numpy, never head arithmetic.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from utils.realized_vol import compute_realized_vol, compute_realized_vol_series


def _expected_scalar_rv(prices: np.ndarray, window: int) -> float:
    """Reference implementation: the historical cri_scan math, verbatim."""
    log_returns = np.log(prices[-window:] / prices[-window - 1:-1])
    return float(np.std(log_returns, ddof=1) * np.sqrt(252) * 100)


def _gbm_closes(n_returns: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(0.0003, 0.012, n_returns)
    return np.concatenate(([100.0], 100.0 * np.exp(np.cumsum(log_returns))))


class TestComputeRealizedVol:
    def test_matches_historical_cri_math_window_20(self):
        closes = _gbm_closes(60, seed=7)
        assert compute_realized_vol(closes, 20) == pytest.approx(
            _expected_scalar_rv(closes, 20), rel=1e-12
        )

    def test_matches_historical_cri_math_window_252(self):
        closes = _gbm_closes(400, seed=11)
        assert compute_realized_vol(closes, 252) == pytest.approx(
            _expected_scalar_rv(closes, 252), rel=1e-12
        )

    def test_uses_only_trailing_window(self):
        closes = _gbm_closes(120, seed=3)
        full = compute_realized_vol(closes, 20)
        truncated = compute_realized_vol(closes[-21:], 20)
        assert full == pytest.approx(truncated, rel=1e-12)

    def test_nan_below_window_plus_one_prices(self):
        closes = _gbm_closes(19, seed=5)  # 20 closes < 20 + 1
        assert math.isnan(compute_realized_vol(closes, 20))

    def test_nan_below_window_plus_one_prices_252(self):
        closes = _gbm_closes(251, seed=5)  # 252 closes < 252 + 1
        assert math.isnan(compute_realized_vol(closes, 252))

    def test_exactly_window_plus_one_prices_is_finite(self):
        closes = _gbm_closes(20, seed=9)  # 21 closes == 20 + 1
        assert math.isfinite(compute_realized_vol(closes, 20))

    def test_constant_prices_zero_vol(self):
        closes = np.full(30, 100.0)
        assert compute_realized_vol(closes, 20) == pytest.approx(0.0, abs=1e-12)


class TestComputeRealizedVolSeries:
    @pytest.mark.parametrize("window,n_returns,seed", [(20, 300, 21), (252, 600, 22)])
    def test_series_vs_scalar_parity_at_every_index(self, window, n_returns, seed):
        closes = _gbm_closes(n_returns, seed=seed)
        series = compute_realized_vol_series(closes, window)
        assert len(series) == len(closes)
        expected = np.array(
            [compute_realized_vol(closes[: t + 1], window) for t in range(len(closes))]
        )
        np.testing.assert_allclose(series, expected, rtol=1e-12, equal_nan=True)

    def test_nan_prefix_before_window(self):
        window = 20
        closes = _gbm_closes(100, seed=13)
        series = compute_realized_vol_series(closes, window)
        assert np.all(np.isnan(series[:window]))
        assert np.all(np.isfinite(series[window:]))

    def test_all_nan_when_history_too_short(self):
        closes = _gbm_closes(10, seed=17)
        series = compute_realized_vol_series(closes, 20)
        assert np.all(np.isnan(series))


class TestCriScanRePoint:
    def test_cri_scan_uses_the_shared_function(self):
        import cri_scan

        assert cri_scan.compute_realized_vol is compute_realized_vol
