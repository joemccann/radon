"""Chronos-2 quantile forecasting engine.

Thin, dependency-light wrapper around the optional ``chronos-forecasting``
package. Heavy deps (torch, chronos, pandas) are imported lazily so the core
fleet never needs them; only the dedicated forecasting host installs
``requirements-forecasting.txt``.
"""

from __future__ import annotations
from model_gateway.client import request as gateway_request

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
    """The serving gateway advertises whether its optional Chronos runtime exists."""
    try:
        status = gateway_request("status", {}, timeout_seconds=10)
        return status.get("capabilities", {}).get("forecast") is True
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


def forecast_quantiles(series, horizon: int, *, quantile_levels=DEFAULT_QUANTILE_LEVELS,
                       future_covariates=None, past_covariates=None,
                       model_id: str = DEFAULT_MODEL_ID, device=None) -> QuantileForecast:
    values = _validate_series(series)
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("horizon must be a positive integer")
    levels = _validate_quantile_levels(quantile_levels)
    def wire(value):
        return value.to_dict(orient="list") if hasattr(value, "to_dict") else value
    try:
        reply = gateway_request("forecast", {"series": values, "horizon": horizon,
            "quantile_levels": levels, "future_covariates": wire(future_covariates),
            "past_covariates": wire(past_covariates), "model_id": model_id,
            "device": device}, timeout_seconds=240)
    except Exception as exc:
        raise ChronosUnavailable("Forecast gateway unavailable") from exc
    data = reply.get("forecast")
    if not isinstance(data, dict):
        raise ChronosUnavailable("Forecast gateway returned no forecast")
    result = QuantileForecast(**data)
    if result.horizon != horizon or len(result.median) != horizon:
        raise ValueError("forecast gateway shape mismatch")
    return result
