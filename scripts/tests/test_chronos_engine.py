"""Unit tests for the Chronos-2 quantile forecasting engine.

These MUST pass without torch / chronos installed: the engine imports those
lazily and the model-API-specific call is monkeypatched out here.
"""

import numpy as np
import pytest

from forecasting.chronos_engine import (
    DEFAULT_MODEL_ID,
    DEFAULT_QUANTILE_LEVELS,
    ChronosUnavailable,
    QuantileForecast,
    forecast_quantiles,
    is_available,
)

SERIES = [10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0, 13.5, 15.0, 14.5]


def _patch_raw(monkeypatch, matrix):
    """Force the engine to use a deterministic raw forecast and to think the
    chronos package is available (without it actually being installed)."""
    import forecasting.chronos_engine as engine

    monkeypatch.setattr(engine, "is_available", lambda: True)
    monkeypatch.setattr(engine, "_load_pipeline", lambda model_id, device: object())
    monkeypatch.setattr(
        engine,
        "_raw_quantile_forecast",
        lambda pipeline, series, horizon, levels, fut, past: np.asarray(matrix, float),
    )


def _unsorted_matrix(levels, horizon):
    """Build a (len(levels), horizon) matrix whose columns are intentionally
    NOT sorted in quantile order, to prove the non-crossing fix reorders them."""
    n = len(levels)
    base = np.arange(n * horizon, dtype=float).reshape(n, horizon)
    # Reverse the rows so each horizon column is strictly descending (worst case
    # for non-crossing: the lowest quantile currently holds the largest value).
    return base[::-1]


def test_forecast_shape_and_keys(monkeypatch):
    levels = sorted(DEFAULT_QUANTILE_LEVELS)
    horizon = 5
    _patch_raw(monkeypatch, _unsorted_matrix(levels, horizon))

    fc = forecast_quantiles(SERIES, horizon)

    assert isinstance(fc, QuantileForecast)
    assert fc.model_id == DEFAULT_MODEL_ID
    assert fc.horizon == horizon
    assert fc.context_len == len(SERIES)
    assert set(fc.quantiles.keys()) == {str(level) for level in levels}
    for values in fc.quantiles.values():
        assert len(values) == horizon


def test_median_equals_half_quantile(monkeypatch):
    levels = sorted(DEFAULT_QUANTILE_LEVELS)
    horizon = 4
    _patch_raw(monkeypatch, _unsorted_matrix(levels, horizon))

    fc = forecast_quantiles(SERIES, horizon)

    assert fc.median == fc.quantiles["0.5"]
    assert len(fc.median) == horizon


def test_to_dict_round_trip(monkeypatch):
    levels = sorted(DEFAULT_QUANTILE_LEVELS)
    horizon = 3
    _patch_raw(monkeypatch, _unsorted_matrix(levels, horizon))

    fc = forecast_quantiles(SERIES, horizon)
    payload = fc.to_dict()

    assert payload["model_id"] == DEFAULT_MODEL_ID
    assert payload["horizon"] == horizon
    assert payload["median"] == fc.quantiles["0.5"]
    assert set(payload["quantiles"].keys()) == {str(level) for level in levels}


def test_non_crossing_enforced(monkeypatch):
    levels = sorted(DEFAULT_QUANTILE_LEVELS)
    horizon = 5
    _patch_raw(monkeypatch, _unsorted_matrix(levels, horizon))

    fc = forecast_quantiles(SERIES, horizon)

    ordered_keys = [str(level) for level in levels]
    for step in range(horizon):
        column = [fc.quantiles[k][step] for k in ordered_keys]
        assert column == sorted(column), f"crossing at step {step}: {column}"


def test_custom_quantile_levels_sorted(monkeypatch):
    levels_in = [0.9, 0.1, 0.5]
    levels_sorted = sorted(levels_in)
    horizon = 3
    _patch_raw(monkeypatch, _unsorted_matrix(levels_sorted, horizon))

    fc = forecast_quantiles(SERIES, horizon, quantile_levels=levels_in)

    assert fc.quantile_levels == levels_sorted


def test_raises_on_empty_series():
    with pytest.raises(ValueError):
        forecast_quantiles([], 5)


def test_raises_on_short_series():
    with pytest.raises(ValueError):
        forecast_quantiles([1.0, 2.0, 3.0], 5)


def test_raises_on_non_positive_horizon():
    with pytest.raises(ValueError):
        forecast_quantiles(SERIES, 0)
    with pytest.raises(ValueError):
        forecast_quantiles(SERIES, -3)


def test_raises_when_quantiles_missing_median():
    with pytest.raises(ValueError):
        forecast_quantiles(SERIES, 5, quantile_levels=[0.1, 0.9])


def test_raises_on_level_at_or_above_one():
    with pytest.raises(ValueError):
        forecast_quantiles(SERIES, 5, quantile_levels=[0.1, 0.5, 1.0])


def test_raises_on_non_numeric_series():
    with pytest.raises(ValueError):
        forecast_quantiles(["a", "b", "c", "d", "e", "f", "g", "h"], 5)


def test_raises_chronos_unavailable_when_package_missing(monkeypatch):
    import forecasting.chronos_engine as engine

    monkeypatch.setattr(engine, "is_available", lambda: False)
    with pytest.raises(ChronosUnavailable):
        forecast_quantiles(SERIES, 5)


def test_is_available_returns_bool():
    assert isinstance(is_available(), bool)
