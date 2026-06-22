"""F11 — forecast-driven scan scoring.

Covers the pure scoring math (upside skew + interval width) and the
graceful-degradation contract: when chronos is unavailable on the lean
fleet the scan must still return opportunities with no forecast attached.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from forecasting import forecast_score as fs


def _band(median, lo, hi, *, horizon=3):
    """Build a minimal forecast-like object: flat per-step bands."""

    class _F:
        pass

    f = _F()
    f.horizon = horizon
    f.median = [median] * horizon
    f.quantiles = {
        "0.1": [lo] * horizon,
        "0.5": [median] * horizon,
        "0.9": [hi] * horizon,
    }
    return f


# --- pure scoring math -------------------------------------------------------

def test_score_rewards_upside_skew():
    """A band skewed up (more room above median than below) scores higher
    than a symmetric band of the same width."""
    skewed_up = fs.forecast_score(_band(median=50, lo=45, hi=70))
    symmetric = fs.forecast_score(_band(median=50, lo=40, hi=60))
    assert skewed_up["score"] > symmetric["score"]


def test_score_penalises_downside_skew():
    skewed_down = fs.forecast_score(_band(median=50, lo=20, hi=55))
    symmetric = fs.forecast_score(_band(median=50, lo=40, hi=60))
    assert skewed_down["score"] < symmetric["score"]


def test_score_exposes_band_for_rendering():
    out = fs.forecast_score(_band(median=50, lo=45, hi=70))
    assert out["band"]["lo"] == pytest.approx(45.0)
    assert out["band"]["median"] == pytest.approx(50.0)
    assert out["band"]["hi"] == pytest.approx(70.0)
    assert out["interval_width"] == pytest.approx(25.0)
    assert "upside_skew" in out


def test_wide_upside_band_flags_convexity():
    """Gate 1 reinforcement: a wide upside band sets convex_reinforced True."""
    out = fs.forecast_score(_band(median=50, lo=49, hi=90))
    assert out["convex_reinforced"] is True
    tight = fs.forecast_score(_band(median=50, lo=49.5, hi=50.5))
    assert tight["convex_reinforced"] is False


# --- attach + ranking effect -------------------------------------------------

def test_attach_forecast_score_adds_field(monkeypatch):
    series_dates = [f"2026-01-{d:02d}" for d in range(1, 13)]
    series_vals = [float(v) for v in range(1, 13)]
    monkeypatch.setattr(
        fs, "_flow_series_for", lambda ticker: (series_dates, series_vals)
    )
    monkeypatch.setattr(
        fs,
        "_forecast_quantiles",
        lambda values, horizon: _band(median=50, lo=45, hi=70, horizon=horizon),
    )

    result = {"ticker": "NVDA", "score": 60.0}
    fs.attach_forecast_score("NVDA", result)
    assert "forecast" in result
    assert result["forecast"]["score"] > 0
    assert result["forecast"]["band"]["hi"] == pytest.approx(70.0)


def test_attach_reorders_when_blended(monkeypatch):
    """Two tickers tied on base score rank by forecast score once blended."""

    def fake_series(ticker):
        dates = [f"2026-01-{d:02d}" for d in range(1, 13)]
        return dates, [float(v) for v in range(1, 13)]

    monkeypatch.setattr(fs, "_flow_series_for", fake_series)

    def fake_fc(values, horizon):
        # caller passes ticker via closure-free path; differentiate by length
        return _band(median=50, lo=45, hi=70, horizon=horizon)

    monkeypatch.setattr(fs, "_forecast_quantiles", fake_fc)

    strong = {"ticker": "AAA", "score": 60.0}
    weak = {"ticker": "BBB", "score": 60.0}
    fs.attach_forecast_score("AAA", strong)
    # weak gets a downside-skewed band -> lower forecast score
    monkeypatch.setattr(
        fs,
        "_forecast_quantiles",
        lambda values, horizon: _band(median=50, lo=20, hi=55, horizon=horizon),
    )
    fs.attach_forecast_score("BBB", weak)

    assert strong["forecast"]["score"] > weak["forecast"]["score"]


# --- graceful degradation ----------------------------------------------------

def test_attach_short_history_is_noop(monkeypatch):
    """Below MIN_CONTEXT_LEN history -> no forecast, no exception."""
    monkeypatch.setattr(
        fs, "_flow_series_for", lambda ticker: (["2026-01-01"], [1.0])
    )
    called = {"n": 0}

    def boom(values, horizon):
        called["n"] += 1
        raise AssertionError("must not forecast on short history")

    monkeypatch.setattr(fs, "_forecast_quantiles", boom)
    result = {"ticker": "TSLA", "score": 30.0}
    fs.attach_forecast_score("TSLA", result)
    assert "forecast" not in result
    assert called["n"] == 0


def test_attach_swallows_chronos_unavailable(monkeypatch):
    """Lean fleet: chronos raises -> scan result is untouched, no raise."""
    series_dates = [f"2026-01-{d:02d}" for d in range(1, 13)]
    series_vals = [float(v) for v in range(1, 13)]
    monkeypatch.setattr(
        fs, "_flow_series_for", lambda ticker: (series_dates, series_vals)
    )

    def boom(values, horizon):
        raise RuntimeError("chronos-forecasting is not installed")

    monkeypatch.setattr(fs, "_forecast_quantiles", boom)
    result = {"ticker": "AMD", "score": 42.0}
    fs.attach_forecast_score("AMD", result)  # must not raise
    assert "forecast" not in result
    assert result["score"] == 42.0


def test_scanner_process_ticker_degrades_without_chronos(monkeypatch):
    """End-to-end seam: _process_ticker still returns a result when the
    forecast attach helper blows up."""
    import scanner

    monkeypatch.setattr(
        scanner,
        "fetch_flow_data",
        lambda ticker, days=5: {"total_prints": 5, "buy_ratio": 0.7},
    )
    monkeypatch.setattr(
        scanner,
        "analyze_signal",
        lambda flow: {
            "score": 55.0,
            "signal": "STRONG",
            "direction": "ACCUMULATION",
            "strength": 40.0,
        },
    )

    import forecasting.flow_history as fh

    monkeypatch.setattr(fh, "record_daily_flow", lambda *a, **k: None)

    def boom(ticker, result):
        raise RuntimeError("chronos blew up")

    monkeypatch.setattr(fs, "attach_forecast_score", boom)

    out = scanner._process_ticker({"ticker": "NVDA", "sector": "Tech"})
    assert out is not None
    assert out["ticker"] == "NVDA"
    assert out["score"] == 55.0
    assert "forecast" not in out


def test_scanner_attaches_forecast_when_available(monkeypatch):
    import scanner

    monkeypatch.setattr(
        scanner,
        "fetch_flow_data",
        lambda ticker, days=5: {"total_prints": 5, "buy_ratio": 0.7},
    )
    monkeypatch.setattr(
        scanner,
        "analyze_signal",
        lambda flow: {
            "score": 55.0,
            "signal": "STRONG",
            "direction": "ACCUMULATION",
            "strength": 40.0,
        },
    )

    import forecasting.flow_history as fh

    monkeypatch.setattr(fh, "record_daily_flow", lambda *a, **k: None)

    def fake_attach(ticker, result):
        result["forecast"] = {
            "score": 0.8,
            "band": {"lo": 45.0, "median": 50.0, "hi": 70.0},
            "interval_width": 25.0,
            "upside_skew": 0.4,
            "convex_reinforced": True,
            "horizon": 3,
        }

    monkeypatch.setattr(fs, "attach_forecast_score", fake_attach)

    out = scanner._process_ticker({"ticker": "NVDA", "sector": "Tech"})
    assert out["forecast"]["convex_reinforced"] is True
    assert out["forecast"]["band"]["hi"] == 70.0
