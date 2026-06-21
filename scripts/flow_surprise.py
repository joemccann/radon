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
import os
import sys
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).resolve().parents[1]
CACHE_PATH = PROJECT_DIR / "data" / "flow_surprise.json"


def write_cache(payload: dict) -> None:
    """Persist the ranked payload to ``data/flow_surprise.json`` atomically.

    json.dump to a sibling temp file then os.replace so a reader never sees a
    half-written file. Best-effort: any failure logs to stderr and is swallowed
    so the CLI still emits its result JSON on stdout.
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CACHE_PATH.with_suffix(".json.tmp")
        with open(tmp_path, "w") as handle:
            json.dump(payload, handle)
        os.replace(tmp_path, CACHE_PATH)
    except Exception as exc:  # noqa: BLE001 — cache write must not affect the payload
        print(f"[flow-surprise] cache write failed: {exc}", file=sys.stderr)


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
        write_cache(out)

    _dual_write()

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
