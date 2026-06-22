"""Flow-surprise residual CLI — Feature 2 proper.

Forecast one step ahead from history excluding today, then measure where
today's actual flow falls in that predicted distribution. With ``--ticker`` it
emits a single residual; otherwise it ranks the watchlist by surprise
extremity.

stdout is reserved for the result JSON. All progress / logging goes to stderr.
The DB dual-write is best-effort and never affects the returned payload.

Usage:
    python3 scripts/flow_surprise.py --metric flow_strength --top 20
    python3 scripts/flow_surprise.py --ticker AAPL
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional


def _dual_write() -> None:
    """Best-effort service-health heartbeat. Stderr-only on failure."""
    try:
        from db.service_cycle import service_cycle

        with service_cycle("flow-surprise", market_hours_class="on-demand"):
            pass
    except Exception as exc:  # noqa: BLE001 — telemetry must not affect the payload
        print(f"[flow-surprise] dual-write failed: {exc}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--metric", default="flow_strength")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--lookback", type=int, default=250)
    parser.add_argument("--ticker", default=None, help="Single ticker (optional)")
    args = parser.parse_args(argv)

    from forecasting import flow_surprise

    if args.ticker:
        out = flow_surprise.compute_flow_surprise(
            args.ticker.upper(),
            metric=args.metric,
            lookback_days=args.lookback,
        )
    else:
        out = flow_surprise.rank_watchlist_surprise(
            metric=args.metric,
            top=args.top,
            lookback_days=args.lookback,
        )
        flow_surprise.write_cache(out)

    _dual_write()

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
