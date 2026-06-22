"""Flow-surprise residual — Feature 2 proper.

The EDGE is the residual: forecast one step ahead from history EXCLUDING today,
then measure where today's actual flow falls inside that predicted distribution.
A PIT near 1.0 means today's flow blew past what the model expected (EXCESS); a
PIT near 0.0 means it came in far below (DEFICIT).

No torch / chronos imports at module load. The chronos forecaster is reached
only through ``chronos_engine.is_available()`` at call time, so this module
imports and runs on the lean fleet. numpy is allowed.

Public surface:
  - ``compute_flow_surprise`` — single ticker/metric residual.
  - ``rank_watchlist_surprise`` — watchlist ranked by surprise extremity.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

from forecasting.chronos_engine import MIN_CONTEXT_LEN

PROJECT_DIR = Path(__file__).resolve().parents[2]
CACHE_PATH = PROJECT_DIR / "data" / "flow_surprise.json"

EXCESS_PIT = 0.9
DEFICIT_PIT = 0.1


def write_cache(payload: dict) -> None:
    """Persist the ranked payload to ``data/flow_surprise.json`` atomically.

    Lives in the library (not the CLI) so BOTH the CLI and the nightly runner
    write the dashboard cache through the same helper. json.dump to a sibling
    temp file then os.replace so a reader never sees a half-written file.
    Best-effort: any failure logs to stderr and is swallowed.
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CACHE_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, CACHE_PATH)
    except Exception as exc:  # noqa: BLE001 — cache write must not affect the payload
        print(f"[flow-surprise] cache write failed: {exc}", file=sys.stderr)


def _pick_forecaster():
    """Return (engine_name, forecaster_callable).

    Chronos when the engine is installed, otherwise the deterministic baseline.
    Heavy modules (chronos_engine, backtest) are imported lazily so the module
    stays torch-free at import time.
    """
    from forecasting import chronos_engine

    if chronos_engine.is_available():
        from forecasting.chronos_engine import forecast_quantiles

        def chronos_forecaster(train, horizon, levels):
            return forecast_quantiles(train, horizon, quantile_levels=levels)

        return "chronos", chronos_forecaster

    from forecasting import backtest

    return "baseline", backtest.baseline_forecaster


def compute_flow_surprise(
    ticker: str,
    *,
    metric: str = "flow_strength",
    lookback_days: int = 250,
    quantile_levels: Optional[Sequence[float]] = None,
) -> dict:
    """Where does today's actual flow fall in a forecast built from history
    excluding today?

    Forecasts one step ahead from ``values[:-1]`` and scores ``values[-1]``
    against the predicted quantiles via the PIT. Flags EXCESS / DEFICIT /
    NORMAL on the PIT extremes.
    """
    from forecasting import backtest
    from forecasting.flow_history import flow_series_for

    dates, values = flow_series_for(
        ticker, metric=metric, lookback_days=lookback_days
    )

    if len(values) < MIN_CONTEXT_LEN + 1:
        return {
            "ticker": ticker,
            "metric": metric,
            "status": "insufficient_history",
            "points": len(values),
        }

    engine, forecaster = _pick_forecaster()

    train = values[:-1]
    actual = values[-1]

    fc = forecaster(train, 1, quantile_levels) if quantile_levels else forecaster(
        train, 1, _default_levels()
    )

    expected_median = fc.median[0]
    p10 = _quantile_at(fc, 0.1, fallback="min")
    p90 = _quantile_at(fc, 0.9, fallback="max")

    surprise_pit = backtest.pit_of_actual(
        actual,
        fc.quantile_levels,
        [fc.quantiles[str(level)][0] for level in fc.quantile_levels],
    )

    surprise_ratio = actual / p90 if p90 not in (0, None) else None

    if surprise_pit >= EXCESS_PIT:
        flag = "EXCESS"
    elif surprise_pit <= DEFICIT_PIT:
        flag = "DEFICIT"
    else:
        flag = "NORMAL"

    return {
        "ticker": ticker,
        "metric": metric,
        "status": "ok",
        "engine": engine,
        "actual": actual,
        "expected_median": expected_median,
        "expected_p10": p10,
        "expected_p90": p90,
        "surprise_pit": surprise_pit,
        "surprise_ratio": surprise_ratio,
        "flag": flag,
        "history_points": len(values),
        "as_of": dates[-1],
    }


def rank_watchlist_surprise(
    tickers: Optional[Sequence[str]] = None,
    *,
    metric: str = "flow_strength",
    top: int = 20,
    lookback_days: int = 250,
) -> dict:
    """Rank tickers by surprise extremity (most extreme PIT first).

    Extremity is ``abs(surprise_pit - 0.5)`` so both EXCESS and DEFICIT bubble
    to the top. Per-ticker failures are skipped (logged to stderr) so one bad
    symbol never sinks the scan.
    """
    if tickers is None:
        tickers = _load_watchlist_tickers()

    results: list[dict] = []
    skipped = 0
    for ticker in tickers:
        try:
            row = compute_flow_surprise(
                ticker,
                metric=metric,
                lookback_days=lookback_days,
            )
        except Exception as exc:  # noqa: BLE001 — one bad ticker can't sink the scan
            print(
                f"[flow-surprise] skip {ticker}: {exc}",
                file=sys.stderr,
            )
            skipped += 1
            continue

        if row.get("status") != "ok":
            skipped += 1
            continue
        results.append(row)

    results.sort(key=lambda r: abs(r["surprise_pit"] - 0.5), reverse=True)

    return {
        "scan_time": _iso_now(),
        "metric": metric,
        "count_ok": len(results),
        "results": results[:top],
        "skipped": skipped,
    }


def _default_levels() -> list:
    from forecasting.chronos_engine import DEFAULT_QUANTILE_LEVELS

    return list(DEFAULT_QUANTILE_LEVELS)


def _quantile_at(fc, level: float, *, fallback: str) -> float:
    """Step-0 quantile at ``level``, falling back to the min/max present level."""
    key = str(level)
    if key in fc.quantiles:
        return fc.quantiles[key][0]
    levels = fc.quantile_levels
    chosen = levels[0] if fallback == "min" else levels[-1]
    return fc.quantiles[str(chosen)][0]


def _load_watchlist_tickers() -> list:
    watchlist_path = PROJECT_DIR / "data" / "watchlist.json"
    with open(watchlist_path) as handle:
        watchlist = json.load(handle)
    return [entry["ticker"] for entry in watchlist.get("tickers", [])]


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
