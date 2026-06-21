"""Calibration backtest CLI for a ticker flow-history metric.

Offline analysis tool: it does NOT register a service-health writer (no
service_cycle) because it is operator-invoked, not a scheduled service.

stdout is reserved for the result JSON; all progress goes to stderr.

Usage:
    python3 scripts/forecast_backtest.py --ticker AAPL --metric flow_strength \
        --horizon 1 --lookback 250
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--metric", default="flow_strength")
    parser.add_argument("--horizon", type=int, default=1)
    parser.add_argument("--lookback", type=int, default=250)
    args = parser.parse_args(argv)

    # Lazy imports so --help works without torch / chronos / db deps installed.
    from forecasting.backtest import run_backtest
    from forecasting.chronos_engine import MIN_CONTEXT_LEN
    from forecasting.flow_history import flow_series_for

    ticker = args.ticker.upper()

    _dates, values = flow_series_for(
        ticker, metric=args.metric, lookback_days=args.lookback
    )

    if len(values) < MIN_CONTEXT_LEN + 2:
        out = {
            "ticker": ticker,
            "metric": args.metric,
            "status": "insufficient_history",
            "points": len(values),
        }
        print(json.dumps(out))
        return 0

    out = run_backtest(values, horizon=args.horizon)
    out["ticker"] = ticker
    out["metric"] = args.metric
    out["scan_time"] = _iso_now()

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
