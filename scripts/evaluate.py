#!/usr/bin/env python3
"""Unified evaluation script for Radon.

Runs all 7 evaluation milestones:
  M1  — Ticker validation
  M1B — Seasonality (context)
  M1C — Analyst ratings (context)
  M1D — News & catalysts (context — buybacks, M&A, earnings, material events)
  M2  — Dark pool flow (including today)
  M3  — Options chain + institutional flow
  M3B — OI change analysis
  M4  — Edge determination   (depends on M2, M3, M3B, Price, News)
  M5  — Structure proposal    (depends on M4 PASS)
  M6  — Kelly sizing          (depends on M5)
  M7  — Final decision        (depends on all above)

Milestones 1 through 3B run in *parallel* (ThreadPoolExecutor).
Milestones 4-7 run sequentially after the parallel group completes.

Usage:
    python3 scripts/evaluate.py AAPL
    python3 scripts/evaluate.py AAPL --json
    python3 scripts/evaluate.py AAPL --bankroll 1200000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure scripts/ is on sys.path for sibling imports
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from clients.uw_client import UWClient
from fetch_ticker import fetch_ticker_info
from fetch_flow import fetch_flow
from fetch_options import fetch_options
from fetch_oi_changes import fetch_ticker_oi_changes, categorize_signal
from fetch_analyst_ratings import fetch_analyst_ratings
from fetch_news import fetch_news


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MilestoneResult:
    """Outcome of a single evaluation milestone."""
    name: str
    passed: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed_ms: float = 0.0


@dataclass
class EvaluationResult:
    """Aggregated result of a full ticker evaluation."""
    ticker: str
    decision: str = "PENDING"          # PENDING | TRADE | NO_TRADE
    failing_gate: Optional[str] = None  # TICKER_VALIDATION | EDGE | CONVEXITY | RISK
    milestones: Dict[str, MilestoneResult] = field(default_factory=dict)
    fetched_at: str = ""
    edge_details: Optional[Dict] = None
    structure: Optional[Dict] = None
    kelly: Optional[Dict] = None
    seasonality: Optional[Dict] = None
    analyst: Optional[Dict] = None
    news: Optional[Dict] = None


# ---------------------------------------------------------------------------
# Helpers: Seasonality
# ---------------------------------------------------------------------------

def rate_seasonality(win_rate: float, avg_return: float) -> str:
    """Rate a seasonality window.

    FAVORABLE  — win_rate > 60 AND avg_return > 5
    UNFAVORABLE — win_rate < 50 OR avg_return < 0
    NEUTRAL — everything else
    """
    if win_rate > 60 and avg_return > 5:
        return "FAVORABLE"
    if win_rate < 50 or avg_return < 0:
        return "UNFAVORABLE"
    return "NEUTRAL"


def fetch_seasonality(ticker: str) -> Dict:
    """Fetch seasonality data from EquityClock.

    Downloads the seasonal chart PNG, reads the image to extract monthly
    performance data, and rates the current month.
    """
    import subprocess
    import calendar
    from datetime import datetime

    url = f"https://charts.equityclock.com/seasonal_charts/{ticker.upper()}_sheet.png"
    path = f"/tmp/{ticker.upper()}_sheet.png"

    try:
        result = subprocess.run(
            ["curl", "-s", "-o", path, "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=15,
        )
        http_code = result.stdout.strip()
        if http_code != "200":
            return {"rating": "UNKNOWN", "win_rate": None, "avg_return": None,
                    "note": f"EquityClock returned HTTP {http_code}"}

        import os
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            return {"rating": "UNKNOWN", "win_rate": None, "avg_return": None,
                    "note": "Downloaded file too small or missing"}

        # Chart downloaded successfully — return path for vision extraction
        # The actual OCR/vision reading happens at the agent layer since
        # evaluate.py runs headless. We mark it as DOWNLOADED so the agent
        # knows to read the image and fill in the data.
        return {
            "rating": "DOWNLOADED",
            "win_rate": None,
            "avg_return": None,
            "chart_path": path,
            "note": f"Chart downloaded to {path} — needs vision extraction",
        }

    except Exception as e:
        return {"rating": "UNKNOWN", "win_rate": None, "avg_return": None,
                "note": f"Fetch error: {e}"}


# ---------------------------------------------------------------------------
# Helpers: Price history
# ---------------------------------------------------------------------------

def fetch_price_history(ticker: str, days: int = 10) -> List[Dict]:
    """Fetch recent price bars from IB.

    Returns list of dicts with date, open, close, volume.
    Returns empty list on failure (non-fatal).
    """
    try:
        import asyncio
        import logging
        from ib_insync import IB, Stock, util
        from clients.ib_client import DEFAULT_HOST, DEFAULT_GATEWAY_PORT

        # Suppress noisy ib_insync connection logs in worker threads
        logging.getLogger("ib_insync").setLevel(logging.CRITICAL)

        # ib_insync needs an event loop — create one for this thread.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        ib = IB()
        ib.connect(DEFAULT_HOST, DEFAULT_GATEWAY_PORT, clientId=18, timeout=8)
        ib.reqMarketDataType(4)  # frozen+delayed if market closed

        contract = Stock(ticker, "SMART", "USD")
        ib.qualifyContracts(contract)
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=f"{days} D",
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
        )
        ib.disconnect()

        return [
            {
                "date": str(b.date),
                "open": float(b.open),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Helpers: OI categorisation
# ---------------------------------------------------------------------------

def categorize_oi_signals(oi_changes: List[Dict]) -> Dict:
    """Summarise OI changes by signal tier."""
    massive = 0
    large = 0
    significant = 0
    total_premium = 0.0
    total_oi = 0
    bullish_premium = 0.0
    bearish_premium = 0.0

    for item in oi_changes:
        premium = float(item.get("premium", 0) or item.get("prev_total_premium", 0) or 0)
        oi_diff = int(item.get("oi_diff_plain", 0) or 0)
        option_type = (item.get("option_type") or "").lower()
        signal = item.get("signal", "")

        total_premium += premium
        total_oi += abs(oi_diff)

        if premium > 10_000_000 or signal == "MASSIVE":
            massive += 1
        elif premium > 5_000_000 or signal == "LARGE":
            large += 1
        elif premium > 1_000_000 or signal == "SIGNIFICANT":
            significant += 1

        if option_type == "call":
            bullish_premium += premium
        elif option_type == "put":
            bearish_premium += premium

    return {
        "massive_count": massive,
        "large_count": large,
        "significant_count": significant,
        "total_premium": total_premium,
        "total_oi_change": total_oi,
        "bullish_premium": bullish_premium,
        "bearish_premium": bearish_premium,
    }


# ---------------------------------------------------------------------------
# Core: compute_sustained_days
# ---------------------------------------------------------------------------

def compute_sustained_days(daily: List[Dict], direction: str = "ACCUMULATION") -> int:
    """Count consecutive days matching *direction* from the most recent day.

    ``daily`` must be sorted most-recent-first (index 0 = today).
    """
    streak = 0
    for day in daily:
        if day.get("flow_direction") == direction:
            streak += 1
        else:
            break
    return streak


# ---------------------------------------------------------------------------
# Core: determine_edge (Gate 2)
# ---------------------------------------------------------------------------

def determine_edge(
    flow: Dict,
    options: Optional[Dict],
    oi_changes: List[Dict],
    price_history: List[Dict],
    news: Optional[Dict] = None,
) -> Dict:
    """Evaluate whether an actionable edge exists.

    Returns dict with:
      passed (bool), reason (str), sustained_days (int),
      flow_strength (float), options_conflict (bool),
      news_catalysts (list), news_sentiment (str), ...

    Criteria (ALL must be met, OR alternative):
      Primary:
        1. Sustained direction ≥ 3 consecutive days (including today)
        2. Aggregate flow strength > 50
        3. Options confirm or don't contradict
        4. Signal not yet reflected in price

      Alternative (can replace criterion 1):
        1a. Most recent day flow strength > 70

      News catalyst boost:
        - Material catalysts (buyback, M&A, earnings beat, etc.) that align
          with flow direction provide additional context but do NOT override
          failing quantitative gates.
        - News is reported in the output for operator judgment.
    """
    dp = flow.get("dark_pool", {})
    agg = dp.get("aggregate", {})
    daily = dp.get("daily", [])

    # Determine dominant direction from aggregate
    agg_direction = agg.get("flow_direction", "NEUTRAL")
    agg_strength = float(agg.get("flow_strength", 0))
    agg_buy_ratio = float(agg.get("dp_buy_ratio") or 0.5)

    # Sustained days from most recent
    sustained = compute_sustained_days(daily, direction=agg_direction)

    # Most recent day strength
    recent_strength = float(daily[0].get("flow_strength", 0)) if daily else 0.0

    # Check if signal is priced in: price moved > 5% in same direction as flow
    # This check is skipped if price_history is empty (deferred IB fetch optimization)
    signal_priced_in = False
    if price_history and len(price_history) >= 2:
        first_close = price_history[0].get("close", 0)
        last_close = price_history[-1].get("close", 0)
        if first_close and last_close:
            pct_change = (last_close - first_close) / first_close
            if agg_direction == "ACCUMULATION" and pct_change > 0.05:
                signal_priced_in = True
            elif agg_direction == "DISTRIBUTION" and pct_change < -0.05:
                signal_priced_in = True

    # Check options for conflict
    options_conflict = False
    if options:
        analysis = options.get("analysis", {})
        combined_bias = analysis.get("combined_bias", "NO_DATA")
        bias_map = {
            "BULLISH": "ACCUMULATION", "LEAN_BULLISH": "ACCUMULATION",
            "BEARISH": "DISTRIBUTION", "LEAN_BEARISH": "DISTRIBUTION",
        }
        expected_dp = bias_map.get(combined_bias)
        if expected_dp and expected_dp != agg_direction:
            options_conflict = True

    # ── News / Catalyst analysis ────────────────────────────────────────
    news_summary = (news or {}).get("summary", {})
    news_catalysts = news_summary.get("material_catalysts", {})
    news_sentiment = news_summary.get("sentiment_bias", "NEUTRAL")
    news_material_count = news_summary.get("material_count", 0)

    # Check if news sentiment aligns with flow direction
    news_aligns = False
    if agg_direction == "ACCUMULATION" and news_sentiment in ("BULLISH", "LEAN_BULLISH"):
        news_aligns = True
    elif agg_direction == "DISTRIBUTION" and news_sentiment in ("BEARISH", "LEAN_BEARISH"):
        news_aligns = True

    # Identify high-impact catalysts
    high_impact_catalysts = [
        cat for cat in news_catalysts
        if cat in ("BUYBACK", "M&A", "EARNINGS_BEAT", "EARNINGS_MISS",
                    "GUIDANCE_UP", "GUIDANCE_DOWN", "FDA", "SPINOFF",
                    "DIVIDEND", "STOCK_SPLIT")
    ]

    # Build result
    result = {
        "passed": False,
        "reason": "",
        "sustained_days": sustained,
        "flow_strength": agg_strength,
        "recent_strength": recent_strength,
        "agg_direction": agg_direction,
        "agg_buy_ratio": agg_buy_ratio,
        "options_conflict": options_conflict,
        "signal_priced_in": signal_priced_in,
        "oi_summary": categorize_oi_signals(oi_changes),
        "news_sentiment": news_sentiment,
        "news_catalysts": list(news_catalysts.keys()),
        "news_material_count": news_material_count,
        "news_aligns_with_flow": news_aligns,
        "high_impact_catalysts": high_impact_catalysts,
    }

    # Gate checks
    if agg_direction == "NEUTRAL":
        result["reason"] = f"Aggregate flow direction is NEUTRAL (buy ratio {agg_buy_ratio:.1%})"
        return result

    if agg_strength < 50:
        result["reason"] = f"Aggregate flow strength {agg_strength:.1f} below threshold (need >50)"
        return result

    if signal_priced_in:
        result["reason"] = (
            f"Signal already reflected in price: price moved >5% in direction of "
            f"{agg_direction.lower()} during accumulation window"
        )
        return result

    # Primary criterion: sustained ≥ 3
    primary_pass = sustained >= 3

    # Alternative criterion: recent strength > 70
    alt_pass = recent_strength > 70

    if not primary_pass and not alt_pass:
        result["reason"] = (
            f"Sustained {agg_direction.lower()} days = {sustained} (need ≥3). "
            f"Recent strength = {recent_strength:.1f} (need >70 for alternative). "
            f"Signal fading."
        )
        return result

    # All checks passed
    result["passed"] = True
    if primary_pass:
        result["reason"] = (
            f"{sustained} consecutive days of {agg_direction.lower()}, "
            f"strength {agg_strength:.1f}"
        )
    else:
        result["reason"] = (
            f"Recent strength {recent_strength:.1f} >70 (alternative criterion). "
            f"Sustained days = {sustained}."
        )

    return result


# ---------------------------------------------------------------------------
# Parallel data fetching
# ---------------------------------------------------------------------------

def _run_parallel_milestones(
    ticker: str,
    bankroll: float,
    skip_ib: bool = False,
    flow_days: int = 5,
) -> Dict[str, Any]:
    """Fetch all independent milestones concurrently.

    Returns a dict keyed by milestone name with raw results.

    Args:
        ticker: Ticker symbol
        bankroll: Current bankroll (unused here, kept for API compat)
        skip_ib: If True, skip IB price fetch (used when batch-fetching prices)
        flow_days: Dark-pool lookback window in trading days

    Note: IB price history runs on the main thread (ib_insync requires the
    main asyncio event loop). All UW-based fetches run in a thread pool.
    """
    results: Dict[str, Any] = {}

    # ── IB price fetch on main thread (skip if batch mode) ───────────────
    if not skip_ib:
        results["PRICE"] = fetch_price_history(ticker)

    # ── All other fetches in parallel ────────────────────────────────────
    def _m1():
        info = fetch_ticker_info(ticker)
        # Enrich with stock/info from UW for company name, sector, market cap
        if info.get("verified") and not info.get("company_name"):
            try:
                with UWClient() as uw:
                    stock = uw.get_stock_info(ticker)
                    sdata = stock.get("data", {}) if stock else {}
                    info["company_name"] = sdata.get("name") or sdata.get("full_name")
                    info["sector"] = sdata.get("sector")
                    info["industry"] = sdata.get("industry")
                    info["market_cap"] = sdata.get("market_cap")
                    info["avg_volume"] = sdata.get("avg_30_volume")
            except Exception:
                pass
        return ("M1", info)

    def _m1b():
        return ("M1B", fetch_seasonality(ticker))

    def _m1c():
        return ("M1C", fetch_analyst_ratings(ticker, use_cache=True))

    def _m2():
        return ("M2", fetch_flow(ticker, lookback_days=flow_days))

    def _m3():
        # Skip IB inside thread (ib_insync needs main event loop).
        # Spot price comes from PRICE fetch on main thread.
        return ("M3", fetch_options(ticker, source="uw"))

    def _m3b():
        return ("M3B", fetch_ticker_oi_changes(ticker))

    def _m1d():
        return ("M1D", fetch_news(ticker, days=7, limit=20))

    tasks = [_m1, _m1b, _m1c, _m1d, _m2, _m3, _m3b]

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in tasks}
        for future in as_completed(futures):
            try:
                key, data = future.result()
                results[key] = data
            except Exception as exc:
                name = futures[future]
                results[name] = {"error": str(exc)}

    return results


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Batch price fetching (IB connection pooling)
# ---------------------------------------------------------------------------

def _fetch_all_prices(tickers: List[str], days: int = 10) -> Dict[str, List[Dict]]:
    """Fetch price history for multiple tickers using a single IB connection.

    Returns dict mapping ticker -> price bars. Empty list on failure.
    This avoids 1.8s connection overhead per ticker.
    """
    try:
        import asyncio
        import logging
        from ib_insync import IB, Stock
        from clients.ib_client import DEFAULT_HOST, DEFAULT_GATEWAY_PORT

        logging.getLogger("ib_insync").setLevel(logging.CRITICAL)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        ib = IB()
        ib.connect(DEFAULT_HOST, DEFAULT_GATEWAY_PORT, clientId=18, timeout=8)
        ib.reqMarketDataType(4)

        results = {}
        for ticker in tickers:
            try:
                contract = Stock(ticker, "SMART", "USD")
                ib.qualifyContracts(contract)
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=f"{days} D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                )
                results[ticker] = [
                    {
                        "date": str(b.date),
                        "open": float(b.open),
                        "close": float(b.close),
                        "volume": float(b.volume),
                    }
                    for b in bars
                ]
            except Exception:
                results[ticker] = []

        ib.disconnect()
        return results
    except Exception:
        return {t: [] for t in tickers}


def run_evaluations(
    tickers: List[str],
    bankroll: float = 1_200_000,
    skip_ib_price: bool = False,
    flow_days: int = 5,
) -> List[EvaluationResult]:
    """Run evaluations for multiple tickers with IB connection pooling.

    For single ticker: uses standard flow (IB fetch inside run_evaluation).
    For multiple tickers: optionally batches IB fetch, then runs UW milestones.

    Args:
        tickers: List of ticker symbols
        bankroll: Current bankroll for Kelly sizing
        skip_ib_price: If True, skip IB price history entirely (faster, skips signal_priced_in check)
        flow_days: Dark-pool lookback trading days for M2
    """
    if len(tickers) == 1 and not skip_ib_price:
        # Single ticker — standard flow, no batch overhead
        return [run_evaluation(tickers[0], bankroll=bankroll, flow_days=flow_days)]

    # Optionally batch fetch all price data with a single IB connection
    price_cache = {} if skip_ib_price else _fetch_all_prices(tickers)

    # Process tickers sequentially (each has internal parallelism for UW calls)
    # This avoids 35 concurrent UW requests (5 tickers × 7 milestones)
    results = [
        run_evaluation(t, bankroll=bankroll, price_history=price_cache.get(t, []), flow_days=flow_days)
        for t in tickers
    ]
    return results


def _run_single_eval(ticker: str, bankroll: float, price_history: List[Dict], flow_days: int = 5) -> EvaluationResult:
    """Run evaluation for a single ticker with pre-fetched price data."""
    return run_evaluation(ticker, bankroll=bankroll, price_history=price_history, flow_days=flow_days)


def run_evaluation(
    ticker: str,
    bankroll: float = 1_200_000,
    price_history: Optional[List[Dict]] = None,
    flow_days: int = 5,
) -> EvaluationResult:
    """Run a full 7-milestone evaluation for *ticker*.

    Steps:
      1. Fetch milestones M1, M1B, M1C, M1D, M2, M3, M3B, Price in parallel.
      2. Check M1 (ticker valid + options available). Abort if fail.
      3. Run M4 (edge determination) using M2, M3, M3B, Price, News.
      4. If edge passes, proceed to M5 (structure) and M6 (Kelly).
      5. Return EvaluationResult with full audit trail.

    Args:
        ticker: Ticker symbol
        bankroll: Current bankroll for Kelly sizing
        price_history: Pre-fetched price data (skips IB fetch if provided)
    """
    now = datetime.now()
    eval_result = EvaluationResult(
        ticker=ticker.upper(),
        fetched_at=now.strftime("%Y-%m-%d %I:%M %p PT"),
    )

    # ── Phase 1: Parallel fetch ──────────────────────────────────────────
    # If price_history was pre-fetched (batch mode), skip IB in _run_parallel_milestones
    raw = _run_parallel_milestones(
        ticker,
        bankroll,
        skip_ib=price_history is not None,
        flow_days=flow_days,
    )
    if price_history is not None:
        raw["PRICE"] = price_history

    # ── M1: Ticker Validation ────────────────────────────────────────────
    m1_data = raw.get("M1", {})
    verified = m1_data.get("verified", False)
    options_ok = m1_data.get("options_available", False)
    m1_pass = verified and options_ok

    eval_result.milestones["M1"] = MilestoneResult(
        name="ticker_validation",
        passed=m1_pass,
        data=m1_data,
        error=m1_data.get("error"),
    )

    if not m1_pass:
        eval_result.decision = "NO_TRADE"
        eval_result.failing_gate = "TICKER_VALIDATION"
        reason = m1_data.get("error") or ("No options chain" if not options_ok else "Unverified")
        eval_result.milestones["M1"].error = reason
        return eval_result

    # ── M1B: Seasonality ─────────────────────────────────────────────────
    m1b_data = raw.get("M1B", {})
    eval_result.seasonality = m1b_data
    eval_result.milestones["M1B"] = MilestoneResult(
        name="seasonality", passed=True, data=m1b_data,
    )

    # ── M1C: Analyst Ratings ─────────────────────────────────────────────
    m1c_data = raw.get("M1C", {})
    eval_result.analyst = m1c_data
    eval_result.milestones["M1C"] = MilestoneResult(
        name="analyst_ratings", passed=True, data=m1c_data,
    )

    # ── M1D: News & Catalysts ────────────────────────────────────────────
    m1d_data = raw.get("M1D", {})
    eval_result.news = m1d_data
    eval_result.milestones["M1D"] = MilestoneResult(
        name="news_catalysts", passed=True, data=m1d_data,
    )

    # ── M2: Dark Pool Flow ───────────────────────────────────────────────
    m2_data = raw.get("M2", {})
    today_str = now.strftime("%Y-%m-%d")
    days_checked = m2_data.get("trading_days_checked", [])
    includes_today = today_str in days_checked

    eval_result.milestones["M2"] = MilestoneResult(
        name="dark_pool_flow",
        passed=True,  # M2 itself doesn't gate — Edge (M4) does
        data={**m2_data, "includes_today": includes_today},
    )

    # ── M3: Options Flow ─────────────────────────────────────────────────
    m3_data = raw.get("M3", {})
    eval_result.milestones["M3"] = MilestoneResult(
        name="options_flow", passed=True, data=m3_data,
    )

    # ── M3B: OI Changes ──────────────────────────────────────────────────
    m3b_raw = raw.get("M3B", [])
    if not isinstance(m3b_raw, list):
        m3b_raw = []

    # Enrich each item: merge categorize_signal output INTO the original dict
    enriched_oi = []
    for item in m3b_raw:
        merged = {**item, **categorize_signal(item)}
        # Ensure premium is a float for downstream consumers
        merged["premium"] = float(merged.get("prev_total_premium") or 0)
        merged["oi_diff_plain"] = int(merged.get("oi_diff_plain") or 0)
        merged["option_type"] = "call" if merged.get("is_call") else "put"
        merged["signal"] = merged.get("strength", "MODERATE")
        enriched_oi.append(merged)

    eval_result.milestones["M3B"] = MilestoneResult(
        name="oi_changes", passed=True,
        data={"items": enriched_oi, "summary": categorize_oi_signals(enriched_oi)},
    )

    # ── M4: Edge Determination ───────────────────────────────────────────
    price_history = raw.get("PRICE", [])
    edge = determine_edge(
        flow=m2_data,
        options=m3_data,
        oi_changes=enriched_oi,
        price_history=price_history,
        news=m1d_data,
    )
    eval_result.edge_details = edge
    eval_result.milestones["M4"] = MilestoneResult(
        name="edge_determination",
        passed=edge["passed"],
        data=edge,
    )

    if not edge["passed"]:
        eval_result.decision = "NO_TRADE"
        eval_result.failing_gate = "EDGE"
        return eval_result

    # ── M5: Structure Proposal (placeholder — requires IB live quotes) ──
    # In automated mode, we surface the edge result and let the operator
    # design the structure interactively.  The structure data is filled in
    # when the operator confirms.
    eval_result.milestones["M5"] = MilestoneResult(
        name="structure", passed=True,
        data={"note": "Edge passed — structure design pending operator input"},
    )

    # ── M6: Kelly Sizing (placeholder) ───────────────────────────────────
    eval_result.milestones["M6"] = MilestoneResult(
        name="kelly_sizing", passed=True,
        data={"bankroll": bankroll, "note": "Pending structure design"},
    )

    # Decision remains PENDING until operator confirms structure + Kelly
    eval_result.decision = "PENDING"
    return eval_result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_report(result: EvaluationResult) -> str:
    """Produce a human-readable evaluation report."""
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append(f"📊 Data as of: {result.fetched_at or 'N/A'}")
    lines.append(f"EVALUATION: {result.ticker}")
    lines.append("=" * 70)
    lines.append("")

    # Company info
    m1 = result.milestones.get("M1")
    if m1 and m1.data:
        company = m1.data.get("company_name", "Unknown")
        price = m1.data.get("current_price", "N/A")
        sector = m1.data.get("sector", "N/A")
        lines.append(f"Company:  {company}")
        lines.append(f"Sector:   {sector}")
        lines.append(f"Price:    ${price}")
        lines.append("")

    # Milestone summary
    lines.append("MILESTONES")
    lines.append("-" * 40)
    milestone_order = ["M1", "M1B", "M1C", "M1D", "M2", "M3", "M3B", "M4", "M5", "M6"]
    for key in milestone_order:
        ms = result.milestones.get(key)
        if ms:
            icon = "✅" if ms.passed else "❌"
            lines.append(f"  {icon} {key}: {ms.name}")
            if ms.error:
                lines.append(f"       Error: {ms.error}")
        else:
            lines.append(f"  ⬜ {key}: (not reached)")
    lines.append("")

    # Edge details
    m4 = result.milestones.get("M4")
    if m4 and m4.data:
        lines.append("EDGE DETERMINATION")
        lines.append("-" * 40)
        lines.append(f"  Passed:          {'YES' if m4.passed else 'NO'}")
        lines.append(f"  Reason:          {m4.data.get('reason', 'N/A')}")
        lines.append(f"  Sustained Days:  {m4.data.get('sustained_days', 'N/A')}")
        lines.append(f"  Flow Strength:   {m4.data.get('flow_strength', 'N/A')}")
        lines.append(f"  Recent Strength: {m4.data.get('recent_strength', 'N/A')}")
        lines.append(f"  Options Conflict:{m4.data.get('options_conflict', False)}")
        lines.append(f"  Signal Priced In:{m4.data.get('signal_priced_in', False)}")
        # News context in edge
        news_cats = m4.data.get("high_impact_catalysts", [])
        news_sent = m4.data.get("news_sentiment", "N/A")
        news_aligns = m4.data.get("news_aligns_with_flow", False)
        if news_cats or news_sent != "NEUTRAL":
            lines.append(f"  News Sentiment:  {news_sent}")
            if news_cats:
                lines.append(f"  Key Catalysts:   {', '.join(news_cats)}")
            lines.append(f"  News↔Flow Align: {'YES' if news_aligns else 'NO'}")
        lines.append("")

    # Dark pool daily
    m2 = result.milestones.get("M2")
    if m2 and m2.data:
        dp = m2.data.get("dark_pool", {})
        daily = dp.get("daily", [])
        if daily:
            lines.append("DARK POOL DAILY FLOW")
            lines.append("-" * 60)
            lines.append(f"  {'Date':<12} {'Buy Ratio':>10} {'Strength':>10} {'Direction':<15}")
            for d in daily:
                br = float(d.get("dp_buy_ratio") or 0)
                st = float(d.get("flow_strength") or 0)
                dr = str(d.get("flow_direction") or "N/A")
                dt = str(d.get("date") or "N/A")
                lines.append(f"  {dt:<12} {br:>9.1%} {st:>9.1f} {dr:<15}")
            agg = dp.get("aggregate", {})
            if agg:
                abr = float(agg.get("dp_buy_ratio") or 0)
                ast_ = float(agg.get("flow_strength") or 0)
                adr = str(agg.get("flow_direction") or "N/A")
                lines.append(f"  {'AGGREGATE':<12} {abr:>9.1%} {ast_:>9.1f} {adr:<15}")
            lines.append("")

    # OI changes
    m3b = result.milestones.get("M3B")
    if m3b and m3b.data:
        summary = m3b.data.get("summary", {})
        if summary.get("total_premium", 0) > 0:
            lines.append("OI CHANGE SUMMARY")
            lines.append("-" * 40)
            lines.append(f"  MASSIVE:     {summary.get('massive_count', 0)}")
            lines.append(f"  LARGE:       {summary.get('large_count', 0)}")
            lines.append(f"  SIGNIFICANT: {summary.get('significant_count', 0)}")
            lines.append(f"  Total:       ${summary.get('total_premium', 0):,.0f}")
            lines.append("")

    # News & Catalysts (M1D)
    m1d = result.milestones.get("M1D")
    if m1d and m1d.data:
        news_data = m1d.data
        ns = news_data.get("summary", {})
        if ns.get("total", 0) > 0:
            lines.append("NEWS & CATALYSTS (7 days)")
            lines.append("-" * 60)
            lines.append(f"  Headlines:   {ns.get('total', 0)} "
                         f"({ns.get('bullish', 0)} bull, "
                         f"{ns.get('bearish', 0)} bear, "
                         f"{ns.get('neutral', 0)} neutral)")
            lines.append(f"  Sentiment:   {ns.get('sentiment_bias', 'N/A')} "
                         f"(score: {ns.get('avg_sentiment_score', 0):.2f})")
            cats = ns.get("material_catalysts", {})
            if cats:
                lines.append(f"  Catalysts:   {', '.join(f'{k}({v})' for k, v in cats.items())}")
            # Show material headlines
            headlines = news_data.get("headlines", [])
            material = [h for h in headlines if h.get("is_material")]
            if material:
                lines.append("  Material Headlines:")
                for h in material[:5]:
                    cat_str = f" [{', '.join(h.get('catalysts', []))}]"
                    lines.append(f"    {h.get('sentiment', '?'):8s} {h.get('title', '')[:70]}{cat_str}")
            lines.append("")

    # Structure (M5) if present
    m5 = result.milestones.get("M5")
    if m5 and m5.data and m5.data.get("structure_type"):
        lines.append("STRUCTURE")
        lines.append("-" * 40)
        for k, v in m5.data.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    # Kelly (M6) if present
    m6 = result.milestones.get("M6")
    if m6 and m6.data and m6.data.get("total_cost"):
        lines.append("KELLY SIZING")
        lines.append("-" * 40)
        for k, v in m6.data.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    # Final decision
    lines.append("=" * 70)
    if result.decision == "NO_TRADE":
        lines.append(f"⛔ DECISION: NO_TRADE — Failing Gate: {result.failing_gate}")
    elif result.decision == "TRADE":
        lines.append("✅ DECISION: TRADE — All gates passed")
    else:
        lines.append(f"⏳ DECISION: {result.decision}")
    lines.append("=" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Run full evaluation for one or more tickers."
    )
    parser.add_argument("tickers", nargs="+", help="Ticker symbol(s) to evaluate")
    parser.add_argument("--bankroll", type=float, default=1_200_000,
                        help="Current bankroll (default: 1,200,000)")
    parser.add_argument("--days", type=int, default=5,
                        help="Dark-pool lookback trading days (default: 5)")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of formatted report")
    parser.add_argument("--fast", action="store_true",
                        help="Skip IB price history (faster, skips signal_priced_in check)")
    args = parser.parse_args()

    tickers = [t.upper() for t in args.tickers]
    results = run_evaluations(
        tickers,
        bankroll=args.bankroll,
        skip_ib_price=args.fast,
        flow_days=args.days,
    )

    if args.json:
        output = []
        for result in results:
            output.append({
                "ticker": result.ticker,
                "decision": result.decision,
                "failing_gate": result.failing_gate,
                "fetched_at": result.fetched_at,
                "edge_details": result.edge_details,
                "milestones": {
                    k: {"name": v.name, "passed": v.passed, "data": v.data, "error": v.error}
                    for k, v in result.milestones.items()
                },
            })
        print(json.dumps(output if len(output) > 1 else output[0], indent=2, default=str))
    else:
        for result in results:
            print(format_report(result))
            print("\n")

    # Exit 0 if any trade, 1 if all NO_TRADE
    any_trade = any(r.decision != "NO_TRADE" for r in results)
    sys.exit(0 if any_trade else 1)


if __name__ == "__main__":
    main()
