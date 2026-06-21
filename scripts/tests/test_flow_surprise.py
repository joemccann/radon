"""Function-level tests for the flow-surprise residual (no subprocess, no torch).

``compute_flow_surprise`` / ``rank_watchlist_surprise`` are exercised by
monkeypatching the flow-history reader and forcing the deterministic baseline
forecaster (``chronos_engine.is_available -> False``). Imports stay lazy inside
the functions under test, so this passes without torch/chronos.
"""

from __future__ import annotations

import pytest

from forecasting import flow_surprise


def _patch_baseline(monkeypatch):
    """Force the deterministic baseline forecaster (no chronos)."""
    from forecasting import chronos_engine

    monkeypatch.setattr(chronos_engine, "is_available", lambda: False)


def _patch_series(monkeypatch, dates, values):
    from forecasting import flow_history

    monkeypatch.setattr(
        flow_history, "flow_series_for", lambda *a, **k: (dates, values)
    )


def _dates(n):
    return [f"2026-06-{d:02d}" for d in range(1, n + 1)]


def test_excess_flag_when_actual_far_above_history(monkeypatch):
    _patch_baseline(monkeypatch)
    history = [float(v) for v in range(1, 13)]  # 1..12
    values = history + [1000.0]  # today blows past the distribution
    _patch_series(monkeypatch, _dates(len(values)), values)

    out = flow_surprise.compute_flow_surprise("AAPL")

    assert out["status"] == "ok"
    assert out["engine"] == "baseline"
    assert out["actual"] == 1000.0
    assert out["surprise_pit"] >= 0.9
    assert out["flag"] == "EXCESS"


def test_normal_flag_when_actual_near_median(monkeypatch):
    _patch_baseline(monkeypatch)
    history = [float(v) for v in range(1, 13)]  # 1..12, median ~6.5
    values = history + [6.5]  # today lands at the centre
    _patch_series(monkeypatch, _dates(len(values)), values)

    out = flow_surprise.compute_flow_surprise("AAPL")

    assert out["status"] == "ok"
    assert out["flag"] == "NORMAL"
    assert 0.1 < out["surprise_pit"] < 0.9


def test_deficit_flag_when_actual_far_below_history(monkeypatch):
    _patch_baseline(monkeypatch)
    history = [float(v) for v in range(1, 13)]  # 1..12
    values = history + [-1000.0]  # today far below the distribution
    _patch_series(monkeypatch, _dates(len(values)), values)

    out = flow_surprise.compute_flow_surprise("AAPL")

    assert out["status"] == "ok"
    assert out["surprise_pit"] <= 0.1
    assert out["flag"] == "DEFICIT"


def test_insufficient_history_for_short_series(monkeypatch):
    _patch_baseline(monkeypatch)
    values = [1.0, 2.0, 3.0]  # < MIN_CONTEXT_LEN + 1
    _patch_series(monkeypatch, _dates(len(values)), values)

    out = flow_surprise.compute_flow_surprise("AAPL")

    assert out["status"] == "insufficient_history"
    assert out["points"] == 3
    assert "surprise_pit" not in out


def test_rank_watchlist_sorted_by_extremity_and_capped(monkeypatch):
    _patch_baseline(monkeypatch)

    history = [float(v) for v in range(1, 13)]
    per_ticker = {
        "EXCESS": history + [1000.0],   # pit ~1.0  -> extremity ~0.5
        "NORMAL": history + [6.5],      # pit ~0.5  -> extremity ~0.0
        "DEFICIT": history + [-1000.0], # pit ~0.0  -> extremity ~0.5
        "MILD": history + [9.0],        # pit mid-high -> moderate extremity
    }

    from forecasting import flow_history

    def fake_series(ticker, **kwargs):
        return _dates(len(per_ticker[ticker])), per_ticker[ticker]

    monkeypatch.setattr(flow_history, "flow_series_for", fake_series)

    out = flow_surprise.rank_watchlist_surprise(
        tickers=["NORMAL", "MILD", "EXCESS", "DEFICIT"],
        top=3,
    )

    assert out["count_ok"] == 4
    assert len(out["results"]) <= 3
    assert len(out["results"]) == 3

    extremities = [abs(r["surprise_pit"] - 0.5) for r in out["results"]]
    assert extremities == sorted(extremities, reverse=True)
    # NORMAL is the least extreme, so it must be dropped from the top 3.
    assert "NORMAL" not in [r["ticker"] for r in out["results"]]
