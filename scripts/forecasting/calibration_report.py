"""Calibration report harness — run the backtest over a watchlist and persist
a per-ticker calibration + verdict row.

Offline analysis tool: NO service_cycle / service-health writer (operator- or
cron-invoked, not a scheduled service). stdout is reserved for the result JSON;
all progress goes to stderr.

No torch / chronos imports at module load. The chronos forecaster is reached
only through ``backtest.run_backtest`` at call time (which itself probes
``chronos_engine.is_available()``), so this module imports and runs on the lean
fleet. numpy is allowed.

Public surface:
  - ``build_calibration_report`` — the repeatable harness.
  - ``main`` — argparse CLI wrapper.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

PROJECT_DIR = Path(__file__).resolve().parents[2]


def build_calibration_report(
    tickers: Optional[Sequence[str]] = None,
    *,
    metric: str = "flow_strength",
    lookback_days: int = 250,
    horizon: int = 1,
    persist: bool = False,
) -> dict:
    """Run ``run_backtest`` over each ticker and aggregate per-ticker verdicts.

    Tickers default to the Turso watchlist. Each ticker is
    independently scored; a too-short series yields an ``insufficient_history``
    row rather than sinking the scan. When ``persist`` is set each ``ok`` row's
    full backtest payload is written via ``upsert_forecast_calibration``
    best-effort.
    """
    # Lazy heavy imports so the module stays torch-free at import time.
    from forecasting import backtest, flow_history
    from forecasting.chronos_engine import MIN_CONTEXT_LEN

    if tickers is None:
        tickers = _load_watchlist_tickers()

    rows: list[dict] = []
    n_ok = 0
    n_insufficient = 0
    chronos_available = False
    chronos_beats_baseline = 0

    for ticker in tickers:
        symbol = ticker.upper()
        _dates, values = flow_history.flow_series_for(
            symbol, metric=metric, lookback_days=lookback_days
        )

        if len(values) < MIN_CONTEXT_LEN + 2:
            n_insufficient += 1
            rows.append(
                {
                    "ticker": symbol,
                    "status": "insufficient_history",
                    "points": len(values),
                }
            )
            continue

        bt = backtest.run_backtest(values, horizon=horizon)
        row = _row_from_backtest(symbol, bt)
        rows.append(row)
        n_ok += 1

        if row["mean_pinball_chronos"] is not None:
            chronos_available = True
            if (
                row["mean_pinball_baseline"] is not None
                and row["mean_pinball_chronos"] < row["mean_pinball_baseline"]
            ):
                chronos_beats_baseline += 1

        if persist:
            _persist_row(symbol, metric, row, bt)

    return {
        "scan_time": _iso_now(),
        "metric": metric,
        "horizon": horizon,
        "n_ok": n_ok,
        "n_insufficient": n_insufficient,
        "chronos_available": chronos_available,
        "chronos_beats_baseline": chronos_beats_baseline,
        "tickers": rows,
    }


def _row_from_backtest(ticker: str, bt: dict) -> dict:
    """Lift the headline numbers out of a ``run_backtest`` payload."""
    baseline = bt.get("baseline") or {}
    chronos = bt.get("chronos") or {}

    mean_pinball_baseline = baseline.get("mean_pinball")
    mean_pinball_chronos = chronos.get("mean_pinball")

    engine = "chronos" if mean_pinball_chronos is not None else "baseline"

    return {
        "ticker": ticker,
        "status": "ok",
        "engine": engine,
        "series_len": bt.get("series_len"),
        "mean_pinball_baseline": mean_pinball_baseline,
        "mean_pinball_chronos": mean_pinball_chronos,
        "verdict": bt.get("verdict"),
        "max_calibration_error": _worst_calibration_error(baseline),
    }


def _worst_calibration_error(baseline: dict) -> Optional[float]:
    """Worst per-level calibration_error from the baseline calibration table."""
    table = baseline.get("calibration") or []
    errors = [
        entry["calibration_error"]
        for entry in table
        if entry.get("calibration_error") is not None
    ]
    return max(errors) if errors else None


def _persist_row(ticker: str, metric: str, row: dict, payload: dict) -> None:
    """Best-effort persist of one calibration row; never sinks the scan."""
    try:
        from db import writer

        writer.upsert_forecast_calibration(
            ticker,
            metric,
            _iso_now(),
            engine=row["engine"],
            series_len=row["series_len"],
            mean_pinball_chronos=row["mean_pinball_chronos"],
            mean_pinball_baseline=row["mean_pinball_baseline"],
            verdict=row["verdict"],
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — persistence must not break the report
        print(
            f"[calibration-report] persist failed for {ticker}: {exc}",
            file=sys.stderr,
        )


def _load_watchlist_tickers() -> list[str]:
    from db.readers import read_watchlist_tickers

    return read_watchlist_tickers()


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--metric", default="flow_strength")
    parser.add_argument("--lookback", type=int, default=250)
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--ticker", default=None, help="single ticker (defaults to watchlist)")
    args = parser.parse_args(argv)

    tickers = [args.ticker] if args.ticker else None

    out = build_calibration_report(
        tickers,
        metric=args.metric,
        lookback_days=args.lookback,
        horizon=args.horizon,
        persist=args.persist,
    )
    print(json.dumps(out))
    return 0


if __name__ == "__main__":  # pragma: no cover — sys.path bootstrap for direct run
    _SCRIPTS_DIR = Path(__file__).resolve().parents[1]
    if str(_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS_DIR))
    sys.exit(main())
