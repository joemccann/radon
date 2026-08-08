#!/usr/bin/env python3
"""Generate a single-ticker flow report.

Combines `fetch_flow.fetch_flow` (dark pool + options flow data) with
`scanner.analyze_signal` (scoring), then derives a directional bias
(BULLISH / NEUTRAL / BEARISH) for the UI to render.

Usage: python3 scripts/flow_report.py AAPL
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict

from fetch_flow import fetch_flow
from scanner import analyze_signal


_BULLISH_OPTION_BIAS = {"BULLISH", "STRONGLY_BULLISH"}
_BEARISH_OPTION_BIAS = {"BEARISH", "STRONGLY_BEARISH"}

# Daily Dark Pool History window for the per-ticker flow report UI.
# Scanner / batch paths keep their own shorter defaults for rate budget.
DEFAULT_LOOKBACK_DAYS = 20


def _classify_direction(flow: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Pick BULLISH / NEUTRAL / BEARISH and a confidence score.

    Mirrors `web/lib/flowSignal.ts:classifyFlowSignal` so server and client
    agree on the verdict. Keep the two implementations in sync.
    """
    dp_direction = (analysis.get("direction") or "UNKNOWN").upper()
    dp_strength = max(0.0, min(100.0, float(analysis.get("strength") or 0)))
    options_bias = (flow.get("options_flow", {}).get("bias") or "NO_DATA").upper()
    combined = (flow.get("combined_signal") or "NO_SIGNAL").upper()

    if dp_direction == "ACCUMULATION":
        dp_label = "BULLISH"
    elif dp_direction == "DISTRIBUTION":
        dp_label = "BEARISH"
    else:
        dp_label = "NEUTRAL"

    if options_bias in _BULLISH_OPTION_BIAS:
        options_label = "BULLISH"
    elif options_bias in _BEARISH_OPTION_BIAS:
        options_label = "BEARISH"
    else:
        options_label = "NEUTRAL"

    if combined == "STRONG_BULLISH_CONFLUENCE":
        direction = "BULLISH"
    elif combined == "STRONG_BEARISH_CONFLUENCE":
        direction = "BEARISH"
    elif dp_label != "NEUTRAL":
        direction = dp_label
    elif options_label != "NEUTRAL":
        direction = options_label
    else:
        direction = "NEUTRAL"

    if direction == "NEUTRAL":
        confidence = 0
    else:
        confidence = dp_strength if dp_label == direction else 0
        if options_label == direction:
            confidence = min(100, confidence + 15)
        elif options_label != "NEUTRAL":
            confidence = max(0, confidence - 10)

    return {
        "direction": direction,
        "confidence": round(confidence),
    }


def build_report(ticker: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> Dict[str, Any]:
    """Run the flow fetch + analysis pipeline for a single ticker."""
    ticker = ticker.upper()
    flow = fetch_flow(ticker, lookback_days=lookback_days)
    analysis = analyze_signal(flow)
    verdict = _classify_direction(flow, analysis)

    return {
        "ticker": ticker,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "lookback_days": lookback_days,
        "verdict": verdict,
        "analysis": analysis,
        "dark_pool": flow.get("dark_pool", {}),
        "options_flow": flow.get("options_flow", {}),
        "combined_signal": flow.get("combined_signal"),
        "market_status": flow.get("market_status"),
        "trading_day_progress": flow.get("trading_day_progress"),
        "trading_days_checked": flow.get("trading_days_checked", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run flow report for a single ticker")
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help=f"Lookback trading days (default {DEFAULT_LOOKBACK_DAYS})",
    )
    args = parser.parse_args()

    try:
        report = build_report(args.ticker, lookback_days=args.days)
    except Exception as exc:  # noqa: BLE001 - surface root cause to caller
        print(json.dumps({"error": str(exc), "ticker": args.ticker.upper()}))
        sys.exit(1)

    print(json.dumps(report, default=str))


if __name__ == "__main__":
    main()
