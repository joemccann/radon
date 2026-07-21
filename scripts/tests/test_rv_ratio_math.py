"""RV-ratio scan math — align_sessions / compute_ratio / compute_ratio_stats /
classify_divergence / build_payload (rv-ratio plan step 3, sections 4 + 6).

All expected numeric values are derived in-test with numpy, never head
arithmetic. Fixture dates are window-relative (today-minus-N).
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta

import numpy as np
import pytest

import rv_ratio_scan as rv


# ── fixtures ─────────────────────────────────────────────────────────

def _dates(n: int, gap_from_today: int = 0) -> list[str]:
    """n window-relative ascending ISO dates ending gap_from_today days ago."""
    return [
        (date.today() - timedelta(days=gap_from_today + n - 1 - i)).isoformat()
        for i in range(n)
    ]


def _gbm_closes(n: int, seed: int, drift: float = 0.0003, vol: float = 0.012) -> np.ndarray:
    rng = np.random.default_rng(seed)
    log_returns = rng.normal(drift, vol, n - 1)
    return np.concatenate(([100.0], 100.0 * np.exp(np.cumsum(log_returns))))


def _series(dates: list[str], closes: np.ndarray) -> dict[str, float]:
    return {d: float(c) for d, c in zip(dates, closes)}


def _rv_series(closes: np.ndarray, window: int) -> np.ndarray:
    """Reference rolling RV, derived independently with numpy."""
    out = np.full(len(closes), np.nan)
    for t in range(window, len(closes)):
        seg = closes[t - window : t + 1]
        log_returns = np.log(seg[1:] / seg[:-1])
        out[t] = float(np.std(log_returns, ddof=1) * np.sqrt(252) * 100)
    return out


SCAN_TIME = "2026-07-17T22:31:04Z"
SOURCES = {"asset": ["yahoo"], "benchmark": ["yahoo"]}


def _payload_for(n_closes: int, seed_a: int = 31, seed_b: int = 32) -> dict:
    dates = _dates(n_closes)
    return rv.build_payload(
        symbol="SMH",
        dates=dates,
        asset_closes=_gbm_closes(n_closes, seed=seed_a),
        bench_closes=_gbm_closes(n_closes, seed=seed_b, vol=0.008),
        scan_time=SCAN_TIME,
        sources=SOURCES,
    )


# ── constants pinned to the plan ─────────────────────────────────────

class TestConstants:
    def test_window_and_targets(self):
        assert rv.RV_WINDOW == 252
        assert rv.RATIO_SESSIONS_TARGET == 2520
        assert rv.CLOSES_TARGET == rv.RATIO_SESSIONS_TARGET + rv.RV_WINDOW
        assert rv.MIN_RATIO_SESSIONS == 1
        assert rv.BENCHMARK == "SPY"


# ── align_sessions ───────────────────────────────────────────────────

class TestAlignSessions:
    def test_intersects_mismatched_calendars(self):
        dates = _dates(10)
        asset_dates = [d for d in dates if d != dates[3]]  # asset halt day
        bench_dates = [d for d in dates if d != dates[6]]  # bench holiday
        asset = _series(asset_dates, np.arange(100.0, 100.0 + len(asset_dates)))
        bench = _series(bench_dates, np.arange(500.0, 500.0 + len(bench_dates)))

        aligned_dates, a, b = rv.align_sessions(asset, bench)

        expected_dates = sorted(set(asset_dates) & set(bench_dates))
        assert aligned_dates == expected_dates
        assert list(a) == [asset[d] for d in expected_dates]
        assert list(b) == [bench[d] for d in expected_dates]

    def test_returns_float_arrays_in_date_order(self):
        dates = _dates(5)
        shuffled = {dates[i]: 100.0 + i for i in (3, 0, 4, 1, 2)}
        bench = {d: 500.0 for d in dates}
        aligned_dates, a, b = rv.align_sessions(shuffled, bench)
        assert aligned_dates == dates
        assert isinstance(a, np.ndarray) and isinstance(b, np.ndarray)
        # a follows ascending date order, not insertion order:
        assert list(a) == [shuffled[d] for d in dates]

    def test_truncates_to_trailing_closes_target(self):
        n = rv.CLOSES_TARGET + 30
        dates = _dates(n)
        closes = np.arange(float(n))
        asset = _series(dates, closes)
        bench = _series(dates, closes + 1000.0)

        aligned_dates, a, b = rv.align_sessions(asset, bench)

        assert len(aligned_dates) == rv.CLOSES_TARGET
        assert aligned_dates == dates[-rv.CLOSES_TARGET :]
        assert a[0] == asset[dates[30]]
        assert b[-1] == bench[dates[-1]]

    def test_empty_intersection(self):
        asset = _series(_dates(3, gap_from_today=10), np.array([1.0, 2.0, 3.0]))
        bench = _series(_dates(3), np.array([4.0, 5.0, 6.0]))
        aligned_dates, a, b = rv.align_sessions(asset, bench)
        assert aligned_dates == []
        assert len(a) == 0 and len(b) == 0


# ── compute_ratio ────────────────────────────────────────────────────

class TestComputeRatio:
    def test_elementwise_ratio(self):
        asset_rv = np.array([20.0, 30.0, 45.0])
        bench_rv = np.array([10.0, 15.0, 18.0])
        ratio = rv.compute_ratio(asset_rv, bench_rv)
        np.testing.assert_allclose(ratio, asset_rv / bench_rv, rtol=1e-12)

    def test_drops_degenerate_benchmark_never_inf(self):
        asset_rv = np.array([np.nan, 20.0, 30.0, 40.0, 50.0])
        bench_rv = np.array([10.0, 0.0, np.nan, -5.0, 20.0])
        ratio = rv.compute_ratio(asset_rv, bench_rv)
        assert not np.any(np.isinf(ratio))
        assert np.all(np.isnan(ratio[:4]))
        assert ratio[4] == pytest.approx(50.0 / 20.0, rel=1e-12)


# ── classify_divergence (boundaries at exactly +/-1σ and +/-2σ) ──────

class TestClassifyDivergence:
    AVG, SD = 1.20, 0.10

    def test_in_band_at_exactly_plus_one_sigma(self):
        assert rv.classify_divergence(self.AVG + self.SD, self.AVG, self.SD) == "in_band"

    def test_elevated_just_above_one_sigma(self):
        assert rv.classify_divergence(self.AVG + self.SD * 1.001, self.AVG, self.SD) == "elevated"

    def test_elevated_at_exactly_plus_two_sigma(self):
        assert rv.classify_divergence(self.AVG + 2 * self.SD, self.AVG, self.SD) == "elevated"

    def test_decoupled_just_above_two_sigma(self):
        assert rv.classify_divergence(self.AVG + 2 * self.SD * 1.001, self.AVG, self.SD) == "decoupled"

    def test_in_band_at_exactly_minus_one_sigma(self):
        assert rv.classify_divergence(self.AVG - self.SD, self.AVG, self.SD) == "in_band"

    def test_compressed_just_below_minus_one_sigma(self):
        assert rv.classify_divergence(self.AVG - self.SD * 1.001, self.AVG, self.SD) == "compressed"

    def test_in_band_inside_the_band(self):
        assert rv.classify_divergence(self.AVG, self.AVG, self.SD) == "in_band"

    def test_zero_stddev_is_in_band(self):
        assert rv.classify_divergence(self.AVG, self.AVG, 0.0) == "in_band"


# ── compute_ratio_stats ──────────────────────────────────────────────

class TestComputeRatioStats:
    def test_stats_block_derived_with_numpy(self):
        rng = np.random.default_rng(41)
        ratio = 1.2 + 0.1 * rng.standard_normal(500)
        stats = rv.compute_ratio_stats(ratio)

        avg = float(np.mean(ratio))
        sd = float(np.std(ratio, ddof=1))
        assert stats["high"] == pytest.approx(round(float(np.max(ratio)), 4))
        assert stats["low"] == pytest.approx(round(float(np.min(ratio)), 4))
        assert stats["avg"] == pytest.approx(round(avg, 4))
        assert stats["last"] == pytest.approx(round(float(ratio[-1]), 4))
        assert stats["stddev"] == pytest.approx(round(sd, 4))
        assert stats["last_percentile"] == pytest.approx(
            round(float(np.mean(ratio <= ratio[-1])), 4)
        )
        assert stats["band_upper"] == pytest.approx(round(avg + sd, 4))
        assert stats["band_lower"] == pytest.approx(round(avg - sd, 4))
        assert stats["divergence"] == rv.classify_divergence(float(ratio[-1]), avg, sd)

    def test_single_point_stddev_zero(self):
        stats = rv.compute_ratio_stats(np.array([1.37]))
        assert stats["stddev"] == 0.0
        assert stats["high"] == stats["low"] == stats["avg"] == stats["last"] == 1.37
        assert stats["last_percentile"] == 1.0
        assert stats["divergence"] == "in_band"

    def test_last_at_max_percentile_is_one(self):
        ratio = np.array([1.0, 1.1, 1.2, 1.5])
        assert rv.compute_ratio_stats(ratio)["last_percentile"] == 1.0


# ── build_payload (section 6 contract) ───────────────────────────────

class TestBuildPayloadContract:
    def test_top_level_keys_match_section_6(self):
        payload = _payload_for(300)
        assert set(payload.keys()) == {
            "schema_version", "symbol", "benchmark", "window_sessions",
            "scan_time", "sources", "dates", "ratio", "asset_rv",
            "benchmark_rv", "stats", "coverage", "units", "missing",
        }
        assert payload["schema_version"] == 1
        assert payload["symbol"] == "SMH"
        assert payload["benchmark"] == "SPY"
        assert payload["window_sessions"] == 252
        assert payload["scan_time"] == SCAN_TIME
        assert payload["sources"] == SOURCES
        assert payload["units"] == "x SPY realized vol"
        assert payload["missing"] is False

    def test_stats_and_coverage_keys(self):
        payload = _payload_for(300)
        assert set(payload["stats"].keys()) == {
            "high", "low", "avg", "last", "stddev", "last_percentile",
            "band_upper", "band_lower", "divergence",
        }
        assert set(payload["coverage"].keys()) == {
            "first_date", "last_date", "sessions", "complete",
        }

    def test_parallel_arrays_and_dates(self):
        n = 300
        payload = _payload_for(n)
        expected_sessions = n - rv.RV_WINDOW
        assert len(payload["dates"]) == expected_sessions
        assert len(payload["ratio"]) == expected_sessions
        assert len(payload["asset_rv"]) == expected_sessions
        assert len(payload["benchmark_rv"]) == expected_sessions
        # First ratio point sits at the first index with a full 252-return window.
        assert payload["dates"][0] == _dates(n)[rv.RV_WINDOW]
        assert payload["dates"][-1] == _dates(n)[-1]
        assert payload["coverage"]["first_date"] == payload["dates"][0]
        assert payload["coverage"]["last_date"] == payload["dates"][-1]
        assert payload["coverage"]["sessions"] == expected_sessions
        assert payload["coverage"]["complete"] is False

    def test_values_match_numpy_derivation(self):
        n = 300
        asset_closes = _gbm_closes(n, seed=31)
        bench_closes = _gbm_closes(n, seed=32, vol=0.008)
        payload = _payload_for(n)

        asset_rv = _rv_series(asset_closes, rv.RV_WINDOW)
        bench_rv = _rv_series(bench_closes, rv.RV_WINDOW)
        valid = ~np.isnan(asset_rv) & ~np.isnan(bench_rv) & (bench_rv > 0)
        expected_ratio = asset_rv[valid] / bench_rv[valid]

        np.testing.assert_allclose(
            payload["ratio"], np.round(expected_ratio, 4), atol=1e-9
        )
        np.testing.assert_allclose(
            payload["asset_rv"], np.round(asset_rv[valid], 2), atol=1e-9
        )
        np.testing.assert_allclose(
            payload["benchmark_rv"], np.round(bench_rv[valid], 2), atol=1e-9
        )
        # Stats are computed on the unrounded ratio series, then rounded 4dp.
        avg = float(np.mean(expected_ratio))
        sd = float(np.std(expected_ratio, ddof=1))
        assert payload["stats"]["avg"] == pytest.approx(round(avg, 4))
        assert payload["stats"]["stddev"] == pytest.approx(round(sd, 4))
        assert payload["stats"]["last"] == pytest.approx(round(float(expected_ratio[-1]), 4))

    def test_rounding_ratio_4dp_rv_2dp(self):
        payload = _payload_for(300)
        assert all(r == round(r, 4) for r in payload["ratio"])
        assert all(v == round(v, 2) for v in payload["asset_rv"])
        assert all(v == round(v, 2) for v in payload["benchmark_rv"])

    def test_json_serializable_pure_python_types(self):
        payload = _payload_for(300)
        json.dumps(payload)  # numpy scalars would raise
        assert all(isinstance(r, float) for r in payload["ratio"])
        assert isinstance(payload["coverage"]["sessions"], int)

    def test_complete_at_full_history_target(self):
        payload = _payload_for(rv.CLOSES_TARGET)
        assert payload["coverage"]["sessions"] == rv.RATIO_SESSIONS_TARGET
        assert payload["coverage"]["complete"] is True

    def test_single_ratio_point_is_served_not_missing(self):
        payload = _payload_for(rv.RV_WINDOW + 1)  # 253 closes -> 1 ratio point
        assert payload["missing"] is False
        assert payload["coverage"]["sessions"] == 1
        assert payload["coverage"]["complete"] is False
        assert payload["stats"]["stddev"] == 0.0

    def test_insufficient_history_missing_variant(self):
        payload = _payload_for(rv.RV_WINDOW)  # 252 closes -> 0 ratio points
        assert payload == {
            "schema_version": 1,
            "symbol": "SMH",
            "missing": True,
            "reason": "insufficient_history",
            "scan_time": SCAN_TIME,
        }

    def test_degenerate_benchmark_everywhere_is_missing(self):
        n = 300
        dates = _dates(n)
        payload = rv.build_payload(
            symbol="SMH",
            dates=dates,
            asset_closes=_gbm_closes(n, seed=31),
            bench_closes=np.full(n, 500.0),  # constant closes -> zero RV
            scan_time=SCAN_TIME,
            sources=SOURCES,
        )
        assert payload["missing"] is True
        assert payload["reason"] == "insufficient_history"

    def test_degenerate_points_dropped_from_arrays(self):
        n = 300
        dates = _dates(n)
        bench_closes = _gbm_closes(n, seed=32, vol=0.008)
        # Freeze the benchmark's last 253 closes: zero RV at the tail.
        bench_closes[-253:] = bench_closes[-254]
        payload = rv.build_payload(
            symbol="SMH",
            dates=dates,
            asset_closes=_gbm_closes(n, seed=31),
            bench_closes=bench_closes,
            scan_time=SCAN_TIME,
            sources=SOURCES,
        )
        assert payload["missing"] is False
        assert payload["dates"][-1] < dates[-1]  # tail points dropped
        assert all(r > 0 for r in payload["ratio"])
