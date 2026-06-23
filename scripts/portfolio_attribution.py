#!/usr/bin/env python3
"""Portfolio Performance Attribution Engine.

Decomposes trade P&L into actionable dimensions:
- By strategy (6 Radon strategies + unclassified)
- By edge type (dark pool, IV mispricing, thesis, none)
- By ticker
- By risk profile (defined vs undefined)
- Kelly calibration accuracy (predicted vs actual win rates)

Reads: Turso journal rows, data/strategies.json
Outputs: JSON attribution payload
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
STRATEGIES_PATH = ROOT / "data" / "strategies.json"


# ── Strategy Classification ─────────────────────────────────────────────


STRATEGY_IDS = [
    "dark-pool-flow",
    "leap-iv-mispricing",
    "garch-convergence",
    "risk-reversal",
    "vcg",
    "cri",
]


def classify_trade(trade: Dict[str, Any]) -> str:
    """Classify a trade into one of the 6 strategies or 'unclassified'.

    Priority order matters — first match wins. A trade with
    "IV_MISPRICING + FLOW_CONFLUENCE" matches leap-iv-mispricing because
    IV_MISPRICING appears first in the classification chain.
    """
    edge = trade.get("edge_analysis") or {}
    edge_type = str(edge.get("edge_type", "")).upper()
    structure = str(trade.get("structure", "")).upper()
    risk_profile = str(trade.get("risk_profile", "")).upper()
    gates = trade.get("gates_passed") or []
    gates_str = " ".join(str(g).upper() for g in gates)
    ticker = str(trade.get("ticker", "")).upper()

    # 1. Dark Pool Flow — explicit edge type or gate reference with strong DP
    if "DARK_POOL" in edge_type:
        return "dark-pool-flow"
    dp_strength = edge.get("dp_strength")
    if dp_strength is not None and float(dp_strength) > 50 and "EDGE" in gates_str:
        return "dark-pool-flow"

    # 2. LEAP IV Mispricing — IV_MISPRICING in edge or LEAP in structure
    if "IV_MISPRICING" in edge_type:
        return "leap-iv-mispricing"
    if "LEAP" in structure:
        return "leap-iv-mispricing"

    # 3. GARCH Convergence
    if "GARCH" in edge_type:
        return "garch-convergence"

    # 4. Risk Reversal — structure name or undefined risk with put+call legs
    if "RISK REVERSAL" in structure:
        return "risk-reversal"
    if risk_profile == "UNDEFINED":
        legs = trade.get("legs") or []
        has_short_put = any(
            str(l.get("type", "")).upper() in ("SHORT PUT", "SHORT_PUT")
            for l in legs
        )
        has_long_call = any(
            str(l.get("type", "")).upper() in ("LONG CALL", "LONG_CALL")
            for l in legs
        )
        if has_short_put and has_long_call:
            return "risk-reversal"

    # 5. VCG
    if "VCG" in edge_type:
        return "vcg"

    # 6. CRI — explicit edge or SPXU/SPY put structures
    if "CRI" in edge_type:
        return "cri"
    if ticker in ("SPXU", "SPY") and "PUT" in structure:
        return "cri"

    return "unclassified"


# ── Edge Type Classification ─────────────────────────────────────────────


def classify_edge_type(trade: Dict[str, Any]) -> str:
    """Classify the edge source of a trade."""
    edge = trade.get("edge_analysis") or {}
    edge_type_raw = str(edge.get("edge_type", "")).upper()

    if "DARK_POOL" in edge_type_raw:
        return "dark_pool"
    if "IV_MISPRICING" in edge_type_raw:
        return "iv_mispricing"
    if "GARCH" in edge_type_raw:
        return "garch"
    if "VCG" in edge_type_raw:
        return "vcg"
    if "CRI" in edge_type_raw:
        return "cri"

    # Check for thesis trades
    if edge.get("type") == "thesis_trade" or "THESIS" in edge_type_raw:
        return "thesis"

    # Check gates for edge mention
    gates = trade.get("gates_passed") or []
    for g in gates:
        g_upper = str(g).upper()
        if "EDGE" in g_upper:
            if "DP" in g_upper or "DARK" in g_upper:
                return "dark_pool"
            if "IV" in g_upper:
                return "iv_mispricing"
            return "other_edge"

    return "none"


# ── Attribution Data Structures ──────────────────────────────────────────


@dataclass
class StrategyAttribution:
    strategy_id: str
    strategy_name: str
    trade_count: int = 0
    closed_count: int = 0
    open_count: int = 0
    winners: int = 0
    losers: int = 0
    realized_pnl: float = 0.0
    total_cost: float = 0.0
    win_rate: Optional[float] = None
    avg_win: Optional[float] = None
    avg_loss: Optional[float] = None
    expected_win_rate: Optional[float] = None  # From Kelly estimates
    kelly_accuracy: Optional[float] = None


@dataclass
class TickerAttribution:
    ticker: str
    trade_count: int = 0
    realized_pnl: float = 0.0
    strategies: List[str] = field(default_factory=list)


@dataclass
class EdgeAttribution:
    edge_type: str
    trade_count: int = 0
    closed_count: int = 0
    realized_pnl: float = 0.0
    win_rate: Optional[float] = None
    winners: int = 0
    losers: int = 0


@dataclass
class RiskAttribution:
    risk_type: str  # "defined", "undefined", "equity", "unknown"
    trade_count: int = 0
    closed_count: int = 0
    realized_pnl: float = 0.0
    win_rate: Optional[float] = None
    winners: int = 0
    losers: int = 0


# ── Core Attribution Logic ───────────────────────────────────────────────


def _is_closed(trade: Dict[str, Any]) -> bool:
    """Determine if a trade is closed (has realized P&L)."""
    pnl = trade.get("realized_pnl")
    decision = str(trade.get("decision", "")).upper()
    action = str(trade.get("action", "")).upper()
    return (
        pnl is not None
        and pnl != 0
    ) or decision == "CLOSED" or "CLOSED" in action


def _get_pnl(trade: Dict[str, Any]) -> float:
    """Extract realized P&L from a trade, defaulting to 0."""
    pnl = trade.get("realized_pnl")
    if pnl is None:
        return 0.0
    try:
        return float(pnl)
    except (TypeError, ValueError):
        return 0.0


def _get_risk_type(trade: Dict[str, Any]) -> str:
    """Classify the risk type of a trade."""
    rp = str(trade.get("risk_profile", "")).upper()
    if rp in ("DEFINED", "DEFINED (NOW)"):
        return "defined"
    if rp == "UNDEFINED":
        return "undefined"
    if rp == "EQUITY":
        return "equity"

    # Infer from structure
    structure = str(trade.get("structure", "")).upper()
    if any(k in structure for k in ("SPREAD", "LONG CALL", "LONG PUT", "BULL CALL", "BEAR PUT")):
        return "defined"
    if "RISK REVERSAL" in structure or "SHORT PUT" in structure or "SHORT CALL" in structure:
        return "undefined"
    if "STOCK" in structure:
        return "equity"

    return "unknown"


def load_strategy_names() -> Dict[str, str]:
    """Load strategy ID → display name mapping."""
    try:
        strategies = json.loads(STRATEGIES_PATH.read_text())
        return {s["id"]: s["name"] for s in strategies}
    except Exception:
        return {}


def build_attribution(
    trades: List[Dict[str, Any]],
    strategy_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Build the full attribution payload from a list of trades."""
    if strategy_names is None:
        strategy_names = load_strategy_names()

    # Add 'unclassified' to names
    all_names = {**strategy_names, "unclassified": "Unclassified"}

    # ── Classify all trades ──
    classified: List[Dict[str, Any]] = []
    for trade in trades:
        classified.append({
            **trade,
            "_strategy": classify_trade(trade),
            "_edge_type": classify_edge_type(trade),
            "_risk_type": _get_risk_type(trade),
            "_is_closed": _is_closed(trade),
            "_pnl": _get_pnl(trade),
        })

    # ── M1+M2+M3: Strategy Attribution ──
    strategy_map: Dict[str, StrategyAttribution] = {}
    kelly_estimates: Dict[str, List[float]] = {}  # strategy → list of predicted win probs

    for t in classified:
        sid = t["_strategy"]
        if sid not in strategy_map:
            strategy_map[sid] = StrategyAttribution(
                strategy_id=sid,
                strategy_name=all_names.get(sid, sid),
            )
        sa = strategy_map[sid]
        sa.trade_count += 1
        pnl = t["_pnl"]

        if t["_is_closed"]:
            sa.closed_count += 1
            sa.realized_pnl += pnl
            if pnl > 0:
                sa.winners += 1
            elif pnl < 0:
                sa.losers += 1
        else:
            sa.open_count += 1

        cost = t.get("total_cost")
        if cost is not None:
            try:
                sa.total_cost += abs(float(cost))
            except (TypeError, ValueError):
                pass

        # Collect Kelly predicted win probabilities
        kelly = t.get("kelly_calculation") or {}
        p_itm = kelly.get("p_itm_estimate") or kelly.get("probability")
        if p_itm is not None:
            try:
                kelly_estimates.setdefault(sid, []).append(float(p_itm))
            except (TypeError, ValueError):
                pass

    # Compute win rates and Kelly calibration
    for sid, sa in strategy_map.items():
        if sa.closed_count > 0:
            sa.win_rate = sa.winners / sa.closed_count

            # Average win/loss
            wins = [t["_pnl"] for t in classified if t["_strategy"] == sid and t["_is_closed"] and t["_pnl"] > 0]
            losses = [t["_pnl"] for t in classified if t["_strategy"] == sid and t["_is_closed"] and t["_pnl"] < 0]
            if wins:
                sa.avg_win = sum(wins) / len(wins)
            if losses:
                sa.avg_loss = sum(losses) / len(losses)

        # Kelly calibration: compare predicted P(ITM) to actual win rate
        if sid in kelly_estimates and kelly_estimates[sid] and sa.win_rate is not None:
            sa.expected_win_rate = sum(kelly_estimates[sid]) / len(kelly_estimates[sid])
            if sa.expected_win_rate > 0:
                # Accuracy = 1 - |predicted - actual| / predicted
                # Clamped to [0, 1]
                error = abs(sa.expected_win_rate - sa.win_rate)
                sa.kelly_accuracy = max(0.0, 1.0 - error / sa.expected_win_rate)

    # ── M5: Ticker Attribution ──
    ticker_map: Dict[str, TickerAttribution] = {}
    for t in classified:
        ticker = t.get("ticker", "UNKNOWN")
        if ticker not in ticker_map:
            ticker_map[ticker] = TickerAttribution(ticker=ticker)
        ta = ticker_map[ticker]
        ta.trade_count += 1
        ta.realized_pnl += t["_pnl"]
        sid = t["_strategy"]
        if sid not in ta.strategies:
            ta.strategies.append(sid)

    # Sort by P&L descending
    tickers_sorted = sorted(ticker_map.values(), key=lambda x: x.realized_pnl, reverse=True)

    # ── M6: Edge Type Attribution ──
    edge_map: Dict[str, EdgeAttribution] = {}
    for t in classified:
        et = t["_edge_type"]
        if et not in edge_map:
            edge_map[et] = EdgeAttribution(edge_type=et)
        ea = edge_map[et]
        ea.trade_count += 1
        if t["_is_closed"]:
            ea.closed_count += 1
            ea.realized_pnl += t["_pnl"]
            if t["_pnl"] > 0:
                ea.winners += 1
            elif t["_pnl"] < 0:
                ea.losers += 1

    for ea in edge_map.values():
        if ea.closed_count > 0:
            ea.win_rate = ea.winners / ea.closed_count

    # ── M7: Risk Profile Attribution ──
    risk_map: Dict[str, RiskAttribution] = {}
    for t in classified:
        rt = t["_risk_type"]
        if rt not in risk_map:
            risk_map[rt] = RiskAttribution(risk_type=rt)
        ra = risk_map[rt]
        ra.trade_count += 1
        if t["_is_closed"]:
            ra.closed_count += 1
            ra.realized_pnl += t["_pnl"]
            if t["_pnl"] > 0:
                ra.winners += 1
            elif t["_pnl"] < 0:
                ra.losers += 1

    for ra in risk_map.values():
        if ra.closed_count > 0:
            ra.win_rate = ra.winners / ra.closed_count

    # ── Assemble payload ──
    total_realized = sum(t["_pnl"] for t in classified if t["_is_closed"])
    total_trades = len(classified)
    closed_trades = sum(1 for t in classified if t["_is_closed"])

    return {
        "total_trades": total_trades,
        "closed_trades": closed_trades,
        "open_trades": total_trades - closed_trades,
        "total_realized_pnl": round(total_realized, 2),
        "by_strategy": [asdict(strategy_map[sid]) for sid in
                        sorted(strategy_map.keys(), key=lambda s: strategy_map[s].realized_pnl, reverse=True)],
        "by_ticker": [asdict(t) for t in tickers_sorted],
        "by_edge": [asdict(edge_map[et]) for et in
                    sorted(edge_map.keys(), key=lambda e: edge_map[e].realized_pnl, reverse=True)],
        "by_risk": [asdict(risk_map[rt]) for rt in
                   sorted(risk_map.keys(), key=lambda r: risk_map[r].realized_pnl, reverse=True)],
        "best_ticker": tickers_sorted[0].ticker if tickers_sorted else None,
        "worst_ticker": tickers_sorted[-1].ticker if tickers_sorted else None,
        "kelly_calibration": {
            sid: {
                "expected_win_rate": round(sa.expected_win_rate, 4) if sa.expected_win_rate is not None else None,
                "actual_win_rate": round(sa.win_rate, 4) if sa.win_rate is not None else None,
                "accuracy": round(sa.kelly_accuracy, 4) if sa.kelly_accuracy is not None else None,
                "sample_size": sa.closed_count,
            }
            for sid, sa in strategy_map.items()
            if sa.expected_win_rate is not None
        },
    }


def load_and_build() -> Dict[str, Any]:
    """Load journal trades and build attribution from real data."""
    from db.readers import read_journal_trades

    trades = read_journal_trades()
    strategy_names = load_strategy_names()
    return build_attribution(trades, strategy_names)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Portfolio performance attribution")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args(argv)

    result = load_and_build()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Portfolio Attribution — {result['total_trades']} trades ({result['closed_trades']} closed)")
        print(f"Total Realized P&L: ${result['total_realized_pnl']:,.2f}")
        print()
        print("By Strategy:")
        for s in result["by_strategy"]:
            wr = f"{s['win_rate']*100:.0f}%" if s["win_rate"] is not None else "N/A"
            print(f"  {s['strategy_name']:25s}  {s['trade_count']:3d} trades  ${s['realized_pnl']:>12,.2f}  WR {wr}")
        print()
        print("By Edge:")
        for e in result["by_edge"]:
            print(f"  {e['edge_type']:15s}  {e['trade_count']:3d} trades  ${e['realized_pnl']:>12,.2f}")
        print()
        print(f"Best: {result['best_ticker']}  |  Worst: {result['worst_ticker']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
