"""History backfill — seed ``ticker_flow_history`` from the dark-pool disk cache.

Chronos-2 forecasting needs a daily flow series, but ``ticker_flow_history`` only
starts accruing once the scanner records each new day going forward. The
dark-pool print cache (``utils.darkpool_cache``) already holds up to ~15 days of
immutable closed-session prints per ticker. This script replays that cache
through the same flow analyser the scanner uses and upserts the resulting daily
rows, so the forecast surface has context immediately instead of waiting weeks.

Heavy/db modules are imported lazily inside the functions so this module stays
import-light (no torch, no DB connection at import time). Run directly:

    python scripts/forecasting/backfill_flow_history.py --days 20

stdout is reserved for the result JSON; progress goes to stderr.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Import root is scripts/ (parents[1] of this file). Inserting it lets the
# script run as ``python scripts/forecasting/backfill_flow_history.py`` outside
# of pytest's configured pythonpath.
_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def aggregate_cached_day(ticker: str, date: str) -> dict | None:
    """Aggregate one cached (ticker, date) into upsert-ready flow fields.

    Returns ``None`` when no cached prints exist for that day. Otherwise maps
    ``fetch_flow.analyze_darkpool`` output to the ``upsert_ticker_flow_history``
    keyword fields.
    """
    from utils import darkpool_cache

    trades = darkpool_cache.get_cached_darkpool(ticker, date)
    if not trades:
        return None

    import fetch_flow

    agg = fetch_flow.analyze_darkpool(trades)
    return {
        "flow_strength": agg["flow_strength"],
        "buy_ratio": agg["dp_buy_ratio"],
        "dp_direction": agg["flow_direction"],
        "total_premium": agg["total_premium"],
        "total_volume": agg["total_volume"],
        "num_prints": len(trades),
    }


def _parse_cache_filename(name: str) -> tuple[str, str] | None:
    """``{TICKER}_{YYYY-MM-DD}.json`` -> (ticker, date), splitting on the LAST
    underscore so multi-part tickers (e.g. ``BRK_B``) survive. Returns None when
    the name doesn't parse into a ticker + ISO date.
    """
    stem = name[:-5] if name.endswith(".json") else name
    if "_" not in stem:
        return None
    ticker, date = stem.rsplit("_", 1)
    if not ticker or len(date) != 10:
        return None
    return ticker.upper(), date


def _cutoff_date(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def backfill_from_cache(*, tickers=None, days: int = 20) -> dict:
    """Replay the dark-pool cache into ``ticker_flow_history``.

    Globs ``darkpool_cache.CACHE_DIR`` for ``*.json``, keeps pairs within the
    last ``days``, optionally filters to ``tickers`` (upper-cased), and upserts
    each non-empty day. Per-pair upsert failures are swallowed (logged to stderr)
    so one bad row never aborts the run.
    """
    from utils import darkpool_cache

    wanted = {t.upper() for t in tickers} if tickers else None
    cutoff = _cutoff_date(days)

    scanned_files = 0
    written = 0
    skipped_empty = 0
    errors = 0
    touched: set[str] = set()

    try:
        cache_files = sorted(Path(darkpool_cache.CACHE_DIR).glob("*.json"))
    except OSError as exc:
        print(f"[backfill] cannot list CACHE_DIR: {exc}", file=sys.stderr)
        cache_files = []

    for path in cache_files:
        parsed = _parse_cache_filename(path.name)
        if parsed is None:
            continue
        ticker, date = parsed
        scanned_files += 1

        if wanted is not None and ticker not in wanted:
            continue
        if date < cutoff:
            continue

        agg = aggregate_cached_day(ticker, date)
        if agg is None:
            skipped_empty += 1
            continue

        try:
            from db import writer

            writer.upsert_ticker_flow_history(ticker, date, **agg)
            written += 1
            touched.add(ticker)
        except Exception as exc:  # best-effort per pair
            errors += 1
            print(
                f"[backfill] upsert failed for {ticker} {date}: {exc}",
                file=sys.stderr,
            )

    return {
        "scanned_files": scanned_files,
        "written": written,
        "skipped_empty": skipped_empty,
        "errors": errors,
        "tickers_touched": sorted(touched),
    }


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Backfill ticker_flow_history from the dark-pool disk cache."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=20,
        help="Only backfill cached days within the last N days (default 20).",
    )
    parser.add_argument(
        "--ticker",
        action="append",
        default=None,
        help="Restrict to this ticker (repeatable). Default: all cached tickers.",
    )
    args = parser.parse_args(argv)

    out = backfill_from_cache(tickers=args.ticker, days=args.days)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
