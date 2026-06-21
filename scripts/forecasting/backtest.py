"""Calibration backtest harness — pure scoring math + walk-forward.

No torch / chronos imports at module load. The chronos forecaster is reached
only through ``chronos_engine.is_available()`` / ``forecast_quantiles`` at call
time, so this module imports and runs on the lean fleet. numpy is allowed.

Public surface:
  - ``pinball_loss`` / ``pit_of_actual`` / ``empirical_coverage`` /
    ``calibration_table`` — pure scoring primitives.
  - ``baseline_forecaster`` — the dumb flat empirical-quantile forecaster.
  - ``walk_forward`` — expanding-window evaluation for any forecaster.
  - ``run_backtest`` — baseline + (optional) chronos, with a verdict.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from forecasting.chronos_engine import (
    DEFAULT_QUANTILE_LEVELS,
    MIN_CONTEXT_LEN,
    QuantileForecast,
)

CENTRAL_INTERVALS = {
    "0.8": (0.1, 0.9),
    "0.98": (0.01, 0.99),
}


def pinball_loss(actual: float, pred: float, level: float) -> float:
    """Standard quantile (pinball) loss for one prediction at ``level``."""
    actual = float(actual)
    pred = float(pred)
    if actual >= pred:
        return level * (actual - pred)
    return (1.0 - level) * (pred - actual)


def pit_of_actual(
    actual: float,
    quantile_levels: Sequence[float],
    quantile_values: Sequence[float],
) -> float:
    """Probability-integral-transform position of ``actual`` in one step's
    predicted quantiles.

    ``quantile_levels`` and ``quantile_values`` are the matching sorted lists
    (ascending level, hence ascending value for a non-crossing forecast). The
    level at which ``actual`` falls is linearly interpolated; below the lowest
    predicted value returns 0.0, above the highest returns 1.0.
    """
    actual = float(actual)
    levels = [float(x) for x in quantile_levels]
    values = [float(x) for x in quantile_values]

    if not values:
        return 0.0
    if actual <= values[0]:
        return 0.0
    if actual >= values[-1]:
        return 1.0

    for idx in range(1, len(values)):
        low_value = values[idx - 1]
        high_value = values[idx]
        if actual <= high_value:
            span = high_value - low_value
            if span <= 0.0:
                return _clamp_unit(levels[idx])
            weight = (actual - low_value) / span
            interpolated = levels[idx - 1] + weight * (levels[idx] - levels[idx - 1])
            return _clamp_unit(interpolated)

    return 1.0


def empirical_coverage(
    actuals: Sequence[float],
    lowers: Sequence[float],
    uppers: Sequence[float],
) -> float:
    """Fraction of actuals that fall within [lower, upper] inclusive."""
    if not actuals:
        return 0.0
    covered = sum(
        1
        for actual, low, high in zip(actuals, lowers, uppers)
        if low <= actual <= high
    )
    return covered / len(actuals)


def calibration_table(
    actuals: Sequence[float],
    per_level_preds: dict,
    levels: Sequence[float],
) -> list:
    """Per-level empirical coverage of a one-sided quantile prediction.

    For each level the empirical value is the fraction of origins whose actual
    is at or below the predicted level-quantile; calibration_error is the
    absolute gap to the nominal level.
    """
    n = len(actuals)
    table = []
    for level in levels:
        preds = per_level_preds.get(level)
        if preds is None or n == 0:
            empirical = 0.0
        else:
            empirical = sum(
                1 for actual, pred in zip(actuals, preds) if actual <= pred
            ) / n
        table.append(
            {
                "level": level,
                "empirical": empirical,
                "calibration_error": abs(empirical - level),
            }
        )
    return table


def baseline_forecaster(
    history: Sequence[float],
    horizon: int,
    quantile_levels: Sequence[float],
) -> QuantileForecast:
    """Flat forecast: every horizon step equals the empirical quantile of
    ``history`` at each level.

    np.quantile is monotonic in level for sorted levels, so the forecast is
    non-crossing by construction.
    """
    import numpy as np

    values = np.asarray([float(x) for x in history], dtype=float)
    levels = sorted(float(q) for q in quantile_levels)

    quantiles = {
        str(level): [float(np.quantile(values, level))] * horizon for level in levels
    }
    median = quantiles["0.5"] if "0.5" in quantiles else quantiles[str(levels[0])]

    return QuantileForecast(
        model_id="baseline-empirical-quantile",
        horizon=horizon,
        context_len=len(values),
        quantile_levels=levels,
        quantiles=quantiles,
        median=list(median),
    )


def walk_forward(
    series: Sequence[float],
    *,
    horizon: int = 1,
    min_train: Optional[int] = None,
    step: int = 1,
    forecaster: Callable[..., QuantileForecast],
    quantile_levels: Optional[Sequence[float]] = None,
) -> dict:
    """Expanding-window evaluation of ``forecaster`` over ``series``.

    For each origin t in [min_train, len(series) - horizon) (stride ``step``):
    train on series[:t], forecast ``horizon`` steps, score every step against
    the realised values. Aggregates pinball loss per level (over all
    origin*step pairs), a calibration table on the first horizon step, and
    central interval coverage where the bounding levels are present.
    """
    if min_train is None:
        min_train = MIN_CONTEXT_LEN
    if quantile_levels is None:
        quantile_levels = DEFAULT_QUANTILE_LEVELS

    values = [float(x) for x in series]
    levels = sorted(float(q) for q in quantile_levels)

    if len(values) <= min_train:
        return {"n_origins": 0, "status": "insufficient_history"}

    pinball_sums = {level: 0.0 for level in levels}
    pinball_counts = {level: 0 for level in levels}

    first_step_actuals: list = []
    first_step_preds_by_level: dict = {level: [] for level in levels}

    interval_actuals: list = []
    interval_lowers: dict = {name: [] for name in CENTRAL_INTERVALS}
    interval_uppers: dict = {name: [] for name in CENTRAL_INTERVALS}

    n_origins = 0
    last_origin = len(values) - horizon
    for t in range(min_train, last_origin + 1, step):
        train = values[:t]
        actual = values[t : t + horizon]
        if len(actual) < horizon:
            break

        fc = forecaster(train, horizon, levels)
        n_origins += 1

        for step_idx in range(horizon):
            realised = actual[step_idx]
            for level in levels:
                predicted = fc.quantiles[str(level)][step_idx]
                pinball_sums[level] += pinball_loss(realised, predicted, level)
                pinball_counts[level] += 1

        first_realised = actual[0]
        first_step_actuals.append(first_realised)
        for level in levels:
            first_step_preds_by_level[level].append(fc.quantiles[str(level)][0])

        interval_actuals.append(first_realised)
        for name, (low_level, high_level) in CENTRAL_INTERVALS.items():
            if low_level in levels and high_level in levels:
                interval_lowers[name].append(fc.quantiles[str(low_level)][0])
                interval_uppers[name].append(fc.quantiles[str(high_level)][0])

    pinball_by_level = {
        str(level): (pinball_sums[level] / pinball_counts[level])
        if pinball_counts[level]
        else 0.0
        for level in levels
    }
    mean_pinball = (
        sum(pinball_by_level.values()) / len(pinball_by_level)
        if pinball_by_level
        else 0.0
    )

    calibration = calibration_table(
        first_step_actuals, first_step_preds_by_level, levels
    )

    interval_coverage = {}
    for name, (low_level, high_level) in CENTRAL_INTERVALS.items():
        if low_level in levels and high_level in levels:
            interval_coverage[name] = empirical_coverage(
                interval_actuals, interval_lowers[name], interval_uppers[name]
            )

    return {
        "n_origins": n_origins,
        "horizon": horizon,
        "levels": levels,
        "pinball_by_level": pinball_by_level,
        "mean_pinball": mean_pinball,
        "calibration": calibration,
        "interval_coverage": interval_coverage,
    }


def run_backtest(
    series: Sequence[float],
    *,
    horizon: int = 1,
    quantile_levels: Optional[Sequence[float]] = None,
    min_train: Optional[int] = None,
) -> dict:
    """Run the baseline always; run chronos only when the engine is available.

    Returns both walk-forward results plus a short verdict comparing
    mean_pinball (chronos wins iff its mean_pinball is strictly lower).
    """
    if quantile_levels is None:
        quantile_levels = DEFAULT_QUANTILE_LEVELS

    baseline = walk_forward(
        series,
        horizon=horizon,
        min_train=min_train,
        forecaster=baseline_forecaster,
        quantile_levels=quantile_levels,
    )

    from forecasting import chronos_engine

    if chronos_engine.is_available():
        def chronos_forecaster(train, h, levels):
            return chronos_engine.forecast_quantiles(train, h, quantile_levels=levels)

        chronos = walk_forward(
            series,
            horizon=horizon,
            min_train=min_train,
            forecaster=chronos_forecaster,
            quantile_levels=quantile_levels,
        )
    else:
        chronos = {"status": "engine_unavailable"}

    return {
        "series_len": len(series),
        "baseline": baseline,
        "chronos": chronos,
        "verdict": _verdict(baseline, chronos),
    }


def _verdict(baseline: dict, chronos: dict) -> str:
    """Short human-readable comparison of mean_pinball between the two runs."""
    baseline_mp = baseline.get("mean_pinball")
    chronos_mp = chronos.get("mean_pinball")

    if chronos.get("status") == "engine_unavailable":
        return "chronos engine unavailable; baseline only"
    if baseline_mp is None or chronos_mp is None:
        return "insufficient history to compare"
    if baseline_mp <= 0.0:
        if chronos_mp < baseline_mp:
            return "chronos beats baseline"
        return "baseline ties or beats chronos"

    fraction = (baseline_mp - chronos_mp) / baseline_mp
    if chronos_mp < baseline_mp:
        return f"chronos beats baseline by {fraction:.1%}"
    return f"baseline beats chronos by {-fraction:.1%}"


def _clamp_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
