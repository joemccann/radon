"""F11 — forecast-driven scan scoring.

Turns a Chronos-2 quantile band over a ticker's recent ``flow_strength``
series into a single forecast score plus a renderable band, and attaches it
to a scan/discover result dict.

Two concerns, kept separate:

  - ``forecast_score`` is pure math over an already-computed quantile band:
    it rewards an upside-skewed band (more room above the median than below,
    which reinforces Gate 1 convexity) and reports the interval width.
  - ``attach_forecast_score`` is the best-effort integration seam: it reads
    the flow history, runs the (optional) chronos engine, and folds the score
    onto the result. It MUST degrade gracefully — on the lean fleet chronos
    isn't installed, and a forecasting failure must never break a scan.

The two thin wrappers ``_flow_series_for`` / ``_forecast_quantiles`` exist so
tests can patch the history read and the model call without importing torch.
"""

from __future__ import annotations

import sys
from typing import Any

# Convexity reinforcement: an upside band at least this many median-multiples
# wide above the median is a structurally convex setup (Gate 1).
CONVEX_UPSIDE_RATIO = 0.30

# Quantile levels we read off the band. The engine always includes 0.5; the
# 0.1 / 0.9 pair is the widest non-tail interval present in DEFAULT levels.
LOWER_LEVEL = "0.1"
MEDIAN_LEVEL = "0.5"
UPPER_LEVEL = "0.9"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _band_from_forecast(forecast: Any) -> dict[str, float]:
    """Collapse a per-step quantile forecast into a single lo/median/hi band
    by averaging across the horizon."""
    quantiles = forecast.quantiles
    lo = _mean([float(x) for x in quantiles[LOWER_LEVEL]])
    median = _mean([float(x) for x in quantiles[MEDIAN_LEVEL]])
    hi = _mean([float(x) for x in quantiles[UPPER_LEVEL]])
    return {"lo": lo, "median": median, "hi": hi}


def forecast_score(forecast: Any) -> dict[str, Any]:
    """Score a quantile forecast for convex upside.

    upside_skew = (hi - median) - (median - lo), normalised by interval width.
    Range roughly [-1, 1]: positive means the band leans up. The headline
    ``score`` maps that onto [0, 1] so a symmetric band sits at 0.5.
    """
    band = _band_from_forecast(forecast)
    lo, median, hi = band["lo"], band["median"], band["hi"]

    interval_width = hi - lo
    upside_room = hi - median
    downside_room = median - lo

    upside_skew = (
        (upside_room - downside_room) / interval_width
        if interval_width > 0
        else 0.0
    )
    score = max(0.0, min(1.0, 0.5 + upside_skew / 2.0))

    convex_reinforced = (
        median > 0 and upside_room >= CONVEX_UPSIDE_RATIO * abs(median)
    )

    return {
        "score": round(score, 4),
        "band": {
            "lo": round(lo, 4),
            "median": round(median, 4),
            "hi": round(hi, 4),
        },
        "interval_width": round(interval_width, 4),
        "upside_skew": round(upside_skew, 4),
        "convex_reinforced": bool(convex_reinforced),
        "horizon": int(getattr(forecast, "horizon", 0)),
    }


def _flow_series_for(ticker: str) -> tuple[list[str], list[float]]:
    from forecasting.flow_history import flow_series_for

    return flow_series_for(ticker, metric="flow_strength")


def _forecast_quantiles(values: list[float], horizon: int):
    from forecasting.chronos_engine import forecast_quantiles

    return forecast_quantiles(values, horizon)


def _has_enough_history(values: list[float]) -> bool:
    from forecasting.chronos_engine import MIN_CONTEXT_LEN

    return len(values) >= MIN_CONTEXT_LEN


def attach_forecast_score(
    ticker: str,
    result: dict[str, Any],
    *,
    horizon: int = 3,
) -> None:
    """Best-effort: attach ``result["forecast"]`` from the flow-strength band.

    Silently no-ops when history is too short or chronos is unavailable, so
    the scan keeps working on the lean fleet. Never raises.
    """
    try:
        _dates, values = _flow_series_for(ticker)
        if not _has_enough_history(values):
            return
        forecast = _forecast_quantiles(values, horizon)
        result["forecast"] = forecast_score(forecast)
    except Exception as exc:  # forecasting must never break a scan
        print(
            f"attach_forecast_score: skipped {ticker} ({exc})",
            file=sys.stderr,
        )
