"""Function-level tests for the Chronos forecast CLI (no subprocess, no torch).

`build_forecast_output` is exercised across its three status paths by
monkeypatching the flow-history reader and the engine surface. Imports stay
lazy inside the function under test, so this passes without torch/chronos.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import chronos_forecast


@dataclass
class _FakeForecast:
    """Stand-in for QuantileForecast — only the attrs the CLI reads."""

    model_id: str
    quantiles: dict
    median: list

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "quantiles": self.quantiles,
            "median": self.median,
        }


def _patch_series(monkeypatch, dates, values):
    from forecasting import flow_history

    monkeypatch.setattr(
        flow_history, "flow_series_for", lambda *a, **k: (dates, values)
    )


def _patch_engine(monkeypatch, *, available=True, forecast=None):
    from forecasting import chronos_engine

    monkeypatch.setattr(chronos_engine, "is_available", lambda: available)
    monkeypatch.setattr(chronos_engine, "DEFAULT_MODEL_ID", "amazon/chronos-2")
    if forecast is not None:
        monkeypatch.setattr(
            chronos_engine, "forecast_quantiles", lambda *a, **k: forecast
        )


def test_build_forecast_output_ok(monkeypatch):
    dates = [f"2026-06-{d:02d}" for d in range(1, 13)]
    values = [float(v) for v in range(1, 13)]
    _patch_series(monkeypatch, dates, values)

    fc = _FakeForecast(
        model_id="amazon/chronos-2",
        quantiles={"0.5": [13.0, 14.0, 15.0]},
        median=[13.0, 14.0, 15.0],
    )
    _patch_engine(monkeypatch, available=True, forecast=fc)

    out = chronos_forecast.build_forecast_output("AAPL", "flow_strength", 3, 120)

    assert out["status"] == "ok"
    assert out["ticker"] == "AAPL"
    assert out["metric"] == "flow_strength"
    assert out["horizon"] == 3
    assert out["model_id"] == "amazon/chronos-2"
    assert out["latest_actual"] == 12.0
    assert out["history_points"] == 12
    assert "scan_time" in out
    assert out["forecast"] == fc.to_dict()
    assert out["forecast"]["median"] == [13.0, 14.0, 15.0]


def test_build_forecast_output_insufficient_history(monkeypatch):
    _patch_series(monkeypatch, ["2026-06-01"] * 3, [1.0, 2.0, 3.0])

    out = chronos_forecast.build_forecast_output("AAPL", "flow_strength", 10, 120)

    assert out["status"] == "insufficient_history"
    assert out["points"] == 3
    assert "forecast" not in out


def test_build_forecast_output_engine_unavailable(monkeypatch):
    dates = [f"2026-06-{d:02d}" for d in range(1, 13)]
    values = [float(v) for v in range(1, 13)]
    _patch_series(monkeypatch, dates, values)
    _patch_engine(monkeypatch, available=False)

    out = chronos_forecast.build_forecast_output("AAPL", "flow_strength", 10, 120)

    assert out["status"] == "engine_unavailable"
    assert out["points"] == 12
    assert "forecast" not in out
