"""Chronos-2 quantile forecasting engine.

Thin, dependency-light wrapper around the optional ``chronos-forecasting``
package. Heavy deps (torch, chronos, pandas) are imported lazily so the core
fleet never needs them; only the dedicated forecasting host installs
``requirements-forecasting.txt``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

DEFAULT_QUANTILE_LEVELS = [
    0.01,
    0.05,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.8,
    0.9,
    0.95,
    0.99,
]
DEFAULT_MODEL_ID = "amazon/chronos-2"

MIN_CONTEXT_LEN = 8

_PIPELINE_CACHE: dict = {}


class ChronosUnavailable(RuntimeError):
    """Raised when the optional chronos-forecasting package is not installed."""


@dataclass
class QuantileForecast:
    model_id: str
    horizon: int
    context_len: int
    quantile_levels: list
    quantiles: dict
    median: list

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "horizon": self.horizon,
            "context_len": self.context_len,
            "quantile_levels": list(self.quantile_levels),
            "quantiles": {k: list(v) for k, v in self.quantiles.items()},
            "median": list(self.median),
        }


def is_available() -> bool:
    """Return True if the chronos package is importable. Never raises."""
    try:
        import importlib.util

        return importlib.util.find_spec("chronos") is not None
    except Exception:
        return False


def _validate_series(series) -> list:
    try:
        values = [float(x) for x in series]
    except (TypeError, ValueError):
        raise ValueError("series must be numeric")
    if len(values) < MIN_CONTEXT_LEN:
        raise ValueError(
            f"series must have at least {MIN_CONTEXT_LEN} points, got {len(values)}"
        )
    return values


def _validate_quantile_levels(quantile_levels) -> list:
    levels = [float(q) for q in quantile_levels]
    if len(set(levels)) != len(levels):
        raise ValueError("quantile_levels must be unique")
    if any(not (0.0 < q < 1.0) for q in levels):
        raise ValueError("quantile_levels must each be strictly within (0, 1)")
    if 0.5 not in levels:
        raise ValueError("quantile_levels must include 0.5 (median)")
    return sorted(levels)


def forecast_quantiles(
    series: Sequence[float],
    horizon: int,
    *,
    quantile_levels: Sequence[float] = DEFAULT_QUANTILE_LEVELS,
    future_covariates=None,
    past_covariates=None,
    model_id: str = DEFAULT_MODEL_ID,
    device: Optional[str] = None,
) -> QuantileForecast:
    values = _validate_series(series)

    if not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0:
        raise ValueError("horizon must be a positive integer")

    levels = _validate_quantile_levels(quantile_levels)

    if not is_available():
        raise ChronosUnavailable(
            "chronos-forecasting is not installed; "
            "install requirements-forecasting.txt on the forecasting host"
        )

    pipeline = _load_pipeline(model_id, device)
    raw = _raw_quantile_forecast(
        pipeline,
        values,
        horizon,
        levels,
        future_covariates,
        past_covariates,
    )

    import numpy as np

    matrix = np.asarray(raw, dtype=float)
    if matrix.shape != (len(levels), horizon):
        raise ValueError(
            f"raw forecast shape {matrix.shape} does not match "
            f"(quantiles={len(levels)}, horizon={horizon})"
        )

    # Enforce non-crossing: at each horizon step the quantile values must be
    # monotonically non-decreasing in the quantile level, so low <= mid <= high.
    non_crossing = np.sort(matrix, axis=0)

    quantiles = {
        str(level): non_crossing[idx].tolist() for idx, level in enumerate(levels)
    }
    median = quantiles["0.5"]

    return QuantileForecast(
        model_id=model_id,
        horizon=horizon,
        context_len=len(values),
        quantile_levels=levels,
        quantiles=quantiles,
        median=median,
    )


def _load_pipeline(model_id: str, device: Optional[str]):
    resolved_device = device or "cpu"
    cache_key = (model_id, resolved_device)
    cached = _PIPELINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        from chronos import Chronos2Pipeline
    except ImportError as exc:
        raise ChronosUnavailable(
            "chronos-forecasting is not installed; "
            "install requirements-forecasting.txt on the forecasting host"
        ) from exc

    pipeline = Chronos2Pipeline.from_pretrained(model_id, device_map=resolved_device)
    _PIPELINE_CACHE[cache_key] = pipeline
    return pipeline


def _raw_quantile_forecast(
    pipeline,
    series,
    horizon,
    quantile_levels,
    future_covariates,
    past_covariates,
):
    """Call the chronos-2 pipeline and return a (len(quantile_levels), horizon) array.

    Verified live against chronos-forecasting 2.3.0 / ``amazon/chronos-2`` on
    2026-06-20 (CPU). Two paths:

      - Univariate (no covariates): ``predict_quantiles([series], ...)`` returns
        ``(quantiles, mean)`` where ``quantiles[0]`` has shape
        ``(n_variates, prediction_length, len(quantile_levels))``. We take
        variate 0 and transpose to ``(len(levels), horizon)``.
      - Covariate path: ``predict_df`` (long format) — its quantile columns are
        named ``str(level)`` (e.g. ``"0.5"``) and the id column defaults to
        ``item_id`` (NOT ``id``).
    """
    import numpy as np

    levels = list(quantile_levels)

    if future_covariates is None and past_covariates is None:
        quantiles, _mean = pipeline.predict_quantiles(
            [np.asarray(series, dtype="float32")],
            prediction_length=horizon,
            quantile_levels=levels,
        )
        # quantiles[0]: (n_variates, horizon, len(levels)); univariate -> variate 0.
        per_step = np.asarray(quantiles[0])[0]  # (horizon, len(levels))
        return per_step.T  # (len(levels), horizon)

    import pandas as pd

    n = len(series)
    context = pd.DataFrame(
        {
            "item_id": ["series"] * n,
            "timestamp": pd.RangeIndex(n),
            "target": list(series),
        }
    )
    if past_covariates is not None:
        past = pd.DataFrame(past_covariates)
        for col in past.columns:
            if col not in ("item_id", "timestamp"):
                context[col] = list(past[col])[:n]

    future_df = None
    if future_covariates is not None:
        future_df = pd.DataFrame(future_covariates)
        if "item_id" not in future_df.columns:
            future_df.insert(0, "item_id", "series")

    out = pipeline.predict_df(
        context,
        future_df=future_df,
        id_column="item_id",
        timestamp_column="timestamp",
        target="target",
        prediction_length=horizon,
        quantile_levels=levels,
    )
    columns = [str(level) for level in levels]
    return np.asarray(out[columns].to_numpy().T, dtype=float)
