#!/usr/bin/env python3
"""
Scan watchlist for dark pool flow signals.
Ranks tickers by flow strength and filters for actionable signals.

API Reference: docs/unusual_whales_api.md
Full Spec: docs/unusual_whales_api_spec.yaml

Uses fetch_flow.py internally which calls:
  - GET /api/darkpool/{ticker} - Dark pool flow data
"""
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from clients.uw_client import UWRateLimitError
from db.readers import read_portfolio_positions, read_watchlist_items
from fetch_flow import fetch_flow as fetch_flow_module

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover — DB layer optional
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

try:
    from alerts import run_alerts_for_results  # type: ignore
except Exception:  # pragma: no cover — alerts layer optional
    def run_alerts_for_results(*args, **kwargs):  # type: ignore
        return 0

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent

try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

DEFAULT_MAX_WORKERS = 24


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        parsed = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))

def get_open_positions():
    """Get list of tickers with open positions."""
    return {
        str(p.get("ticker") or "").upper()
        for p in read_portfolio_positions()
        if p.get("ticker")
    }


def get_watchlist_items() -> list[dict]:
    """Get watchlist rows from Turso."""
    return read_watchlist_items()

def fetch_flow_data(
    ticker: str,
    days: int = 5,
    *,
    fetch_missing_history: bool = False,
    retry_transient: bool = False,
) -> dict:
    """Fetch flow data for a single ticker via the shared wrapper seam."""
    try:
        return fetch_flow_module(
            ticker,
            lookback_days=days,
            skip_options_flow=False,
            fetch_missing_history=fetch_missing_history,
            retry_transient=retry_transient,
        )
    except UWRateLimitError:
        raise
    except Exception as e:
        return {"error": str(e)}

# Keep old name as alias so existing call sites work
fetch_flow = fetch_flow_data

def analyze_signal(flow_data: dict) -> dict:
    """Extract key metrics from flow data."""
    if "error" in flow_data:
        return {"score": -1, "signal": "ERROR", "error": flow_data["error"]}

    dp = flow_data.get("dark_pool", {})
    agg = dp.get("aggregate", {})
    daily = dp.get("daily", [])

    direction = agg.get("flow_direction", "UNKNOWN")
    strength = agg.get("flow_strength", 0)
    buy_ratio = agg.get("dp_buy_ratio")
    num_prints = agg.get("num_prints", 0)

    # Check for sustained direction (3+ consecutive days)
    sustained = 0
    if daily:
        current_dir = daily[0].get("flow_direction")
        for d in daily[1:]:
            if d.get("flow_direction") == current_dir and current_dir in ("ACCUMULATION", "DISTRIBUTION"):
                sustained += 1
            else:
                break

    # Check most recent day's direction and strength
    recent_dir = daily[0].get("flow_direction") if daily else "UNKNOWN"
    recent_strength = daily[0].get("flow_strength", 0) if daily else 0

    # Score: higher = more actionable
    # Base score from aggregate strength
    score = strength

    # Bonus for sustained direction
    if sustained >= 2:
        score += 20
    if sustained >= 4:
        score += 20

    # Bonus if recent day confirms aggregate
    if recent_dir == direction and recent_strength > 50:
        score += 15

    # Penalty if recent day contradicts aggregate
    if recent_dir != direction and recent_dir in ("ACCUMULATION", "DISTRIBUTION"):
        score -= 30

    # Penalty for low print count (statistically unreliable)
    if num_prints < 50:
        score -= 20
    elif num_prints < 100:
        score -= 10

    # Check options flow for conflict (matches evaluate.py edge criteria)
    options_conflict = False
    options_flow = flow_data.get("options_flow", {})
    if options_flow:
        combined_bias = options_flow.get("bias", "NO_DATA")
        bias_map = {
            "BULLISH": "ACCUMULATION", "STRONGLY_BULLISH": "ACCUMULATION",
            "BEARISH": "DISTRIBUTION", "STRONGLY_BEARISH": "DISTRIBUTION",
        }
        expected_dp = bias_map.get(combined_bias)
        if expected_dp and expected_dp != direction:
            options_conflict = True
            score -= 25  # Penalty for conflict

    # Determine signal quality
    if score >= 60 and direction in ("ACCUMULATION", "DISTRIBUTION"):
        signal = "STRONG"
    elif score >= 40 and direction in ("ACCUMULATION", "DISTRIBUTION"):
        signal = "MODERATE"
    elif direction in ("ACCUMULATION", "DISTRIBUTION"):
        signal = "WEAK"
    else:
        signal = "NONE"

    return {
        "score": round(score, 1),
        "signal": signal,
        "direction": direction,
        "strength": strength,
        "buy_ratio": buy_ratio,
        "options_conflict": options_conflict,
        "num_prints": num_prints,
        "sustained_days": sustained + 1 if sustained > 0 else 0,
        "recent_direction": recent_dir,
        "recent_strength": recent_strength,
    }

def _process_ticker(
    item: dict,
    client=None,
    *,
    fetch_missing_history: bool = False,
    retry_transient: bool = False,
    include_forecast: bool = False,
) -> dict:
    """Process a single ticker: fetch flow and analyze signal.

    Returns a result dict or None on error.
    Designed to run inside a ThreadPoolExecutor worker.
    
    Args:
        item: Watchlist item with 'ticker' key
        client: Optional shared UWClient (passed via functools.partial)
    """
    ticker = item["ticker"]
    try:
        # Use the wrapper seam so tests and callers can patch scanner.fetch_flow_data
        # without needing to know the internal fetch_flow import path.
        flow = fetch_flow_data(
            ticker,
            days=5,
            fetch_missing_history=fetch_missing_history,
            retry_transient=retry_transient,
        )
        analysis = analyze_signal(flow)
        result = {
            "ticker": ticker,
            "sector": item.get("sector", "Unknown"),
            **analysis
        }
        try:
            from forecasting.flow_history import record_daily_flow

            today = datetime.now(timezone.utc).date().isoformat()
            record_daily_flow(ticker, today, result)
        except Exception as exc:  # best-effort accrual, never break the scan
            print(f"  {ticker} - flow-history accrual skipped ({exc})", file=sys.stderr)
        if include_forecast:
            try:
                from forecasting import forecast_score as fs

                fs.attach_forecast_score(ticker, result)
            except Exception as exc:  # forecasting must never break the scan
                print(f"  {ticker} - forecast scoring skipped ({exc})", file=sys.stderr)
        return result
    except UWRateLimitError:
        logger.warning("Rate limited on %s — skipping", ticker)
        print(f"  {ticker} - SKIP (rate limited)", file=sys.stderr)
        return None
    except Exception as exc:
        logger.warning("Error processing %s: %s", ticker, exc)
        print(f"  {ticker} - ERROR ({exc})", file=sys.stderr)
        return None


def scan(
    top_n: int = 20,
    min_score: float = 0,
    max_workers: int = DEFAULT_MAX_WORKERS,
    *,
    fetch_missing_history: bool = False,
    retry_transient: bool = False,
    include_forecast: bool = False,
):
    """Scan all watchlist tickers and rank by signal strength.

    Uses ThreadPoolExecutor to process tickers concurrently.

    Args:
        top_n: Number of top signals to return.
        min_score: Minimum score threshold.
        max_workers: Maximum concurrent workers.
        fetch_missing_history: Live-backfill prior closed days when the
            dark-pool disk cache is cold. Disabled by default for interactive
            scan speed and UW quota protection.
        retry_transient: Retry UW 429/5xx responses. Disabled by default so
            rate-limited names are skipped quickly in on-demand scans.
        include_forecast: Attach optional Chronos forecast metadata. Disabled
            by default because the core scan does not require it and the
            forecasting package is not installed on lean hosts.
    """
    open_positions = get_open_positions()
    tickers = get_watchlist_items()

    # Filter out open positions before dispatching to workers
    items_to_scan = [
        item for item in tickers
        if item["ticker"] not in open_positions
    ]
    skipped = len(tickers) - len(items_to_scan)
    if skipped:
        print(f"Skipping {skipped} tickers with open positions", file=sys.stderr)

    print(f"Scanning {len(items_to_scan)} tickers ({max_workers} workers)...", file=sys.stderr)

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                _process_ticker,
                item,
                fetch_missing_history=fetch_missing_history,
                retry_transient=retry_transient,
                include_forecast=include_forecast,
            ): item
            for item in items_to_scan
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            item = futures[future]
            ticker = item["ticker"]
            try:
                result = future.result()
            except Exception as exc:
                logger.warning("Unhandled error for %s: %s", ticker, exc)
                print(f"  [{done}/{len(items_to_scan)}] {ticker} - ERROR ({exc})", file=sys.stderr)
                continue
            if result is not None:
                print(f"  [{done}/{len(items_to_scan)}] {ticker}... {result['signal']} ({result['score']})", file=sys.stderr)
                results.append(result)

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    # Filter by min_score and take top_n
    filtered = [r for r in results if r["score"] >= min_score][:top_n]

    output = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "tickers_scanned": len(results),
        "signals_found": len([r for r in results if r["signal"] in ("STRONG", "MODERATE")]),
        "top_signals": filtered
    }

    mirror_scan_snapshot("scanner", output)

    try:
        run_alerts_for_results(filtered)
    except Exception as exc:  # pragma: no cover — alerts never break the scan
        logger.warning("alert evaluation failed: %s", exc)
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Scan watchlist for flow signals")
    p.add_argument("--top", type=int, default=20, help="Number of top signals to show")
    p.add_argument("--min-score", type=float, default=0, help="Minimum score threshold")
    p.add_argument(
        "--workers",
        type=int,
        default=_env_int("RADON_SCANNER_WORKERS", DEFAULT_MAX_WORKERS),
        help=f"Max concurrent workers (default {DEFAULT_MAX_WORKERS}; override RADON_SCANNER_WORKERS)",
    )
    p.add_argument(
        "--backfill-history",
        action="store_true",
        help="Fetch missing closed-session dark-pool history live instead of using cache-only history.",
    )
    p.add_argument(
        "--forecast",
        action="store_true",
        help="Attach optional Chronos forecast metadata to scanner rows.",
    )
    p.add_argument(
        "--retry-transient",
        action="store_true",
        help="Retry UW 429/5xx responses instead of skipping transient failures quickly.",
    )
    args = p.parse_args()

    scan(
        top_n=args.top,
        min_score=args.min_score,
        max_workers=args.workers,
        fetch_missing_history=args.backfill_history,
        retry_transient=args.retry_transient,
        include_forecast=args.forecast,
    )
