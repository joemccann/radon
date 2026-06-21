"""Chronos-2 quantile forecast CLI for a ticker flow-history metric.

stdout is reserved for the result JSON (`--json`). All progress / logging
goes to stderr. The DB dual-write is best-effort and never affects the
returned payload.

Usage:
    python3 scripts/chronos_forecast.py --ticker AAPL --metric flow_strength \
        --horizon 10 --lookback 120 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_forecast_output(ticker: str, metric: str, horizon: int, lookback: int) -> dict:
    """Assemble the forecast payload for one ticker/metric.

    Returns a status-tagged dict. Never raises for the expected control-flow
    cases (insufficient history, engine unavailable); the model itself may
    raise ValueError on malformed input, which the caller surfaces.
    """
    from forecasting import flow_history

    dates, values = flow_history.flow_series_for(
        ticker, metric=metric, lookback_days=lookback
    )

    if len(values) < 8:
        return {
            "ticker": ticker,
            "metric": metric,
            "status": "insufficient_history",
            "points": len(values),
        }

    from forecasting import chronos_engine

    if not chronos_engine.is_available():
        return {
            "ticker": ticker,
            "metric": metric,
            "status": "engine_unavailable",
            "points": len(values),
        }

    fc = chronos_engine.forecast_quantiles(
        values, horizon, model_id=chronos_engine.DEFAULT_MODEL_ID
    )

    return {
        "ticker": ticker,
        "metric": metric,
        "status": "ok",
        "scan_time": _iso_now(),
        "horizon": horizon,
        "model_id": fc.model_id,
        "latest_actual": values[-1],
        "history_points": len(values),
        "forecast": fc.to_dict(),
    }


def _dual_write(out: dict) -> None:
    """Best-effort snapshot persist. Stderr-only on failure; never raises."""
    try:
        from db.service_cycle import service_cycle
        from db.writer import upsert_forecast_snapshot

        with service_cycle("chronos-forecast", market_hours_class="on-demand"):
            upsert_forecast_snapshot(
                out["ticker"],
                out["metric"],
                out["scan_time"],
                out["horizon"],
                out["model_id"],
                out,
            )
    except Exception as exc:  # noqa: BLE001 — telemetry must not affect the payload
        print(f"[chronos-forecast] dual-write failed: {exc}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--metric", default="flow_strength")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout")
    args = parser.parse_args(argv)

    ticker = args.ticker.upper()
    out = build_forecast_output(ticker, args.metric, args.horizon, args.lookback)

    if out.get("status") == "ok":
        _dual_write(out)

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
