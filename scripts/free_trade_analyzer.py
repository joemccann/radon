#!/usr/bin/env python3
"""
Free Trade Analyzer

Analyzes multi-leg option positions to determine:
1. Current effective cost of the "core" leg after hedge leg P&L
2. Price to close hedge leg to make core leg "free" (zero net cost)
3. Whether the position is already "free" or close to it

Supports:
- Risk Reversals (Long Call + Short Put, Long Put + Short Call)
- Vertical Spreads (Bull Call, Bear Put, Bull Put, Bear Call)
- Synthetic Long/Short
- Straddles/Strangles
- Collars (with stock)

Usage:
    python3 scripts/free_trade_analyzer.py
    python3 scripts/free_trade_analyzer.py --json
    python3 scripts/free_trade_analyzer.py --ticker EWY
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

# Project paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
PORTFOLIO_FILE = DATA_DIR / "portfolio.json"


@dataclass
class Leg:
    """Single leg of an option position."""
    direction: str  # LONG or SHORT
    leg_type: str   # Call, Put, Stock
    strike: Optional[float]
    contracts: int
    entry_price: float  # per contract
    current_price: float  # per contract
    multiplier: int = 100
    
    @property
    def entry_cost(self) -> float:
        """Total entry cost (positive = paid, negative = received)."""
        sign = 1 if self.direction == "LONG" else -1
        return sign * self.entry_price * self.contracts * self.multiplier
    
    @property
    def current_value(self) -> float:
        """Current market value (to close position)."""
        # To close: LONG sells (receives), SHORT buys (pays)
        sign = 1 if self.direction == "LONG" else -1
        return sign * self.current_price * self.contracts * self.multiplier
    
    @property
    def pnl(self) -> float:
        """Current P&L for this leg."""
        return self.current_value - self.entry_cost
    
    @property
    def close_cost(self) -> float:
        """Cost to close this leg (positive = pay, negative = receive)."""
        # LONG: sell to close (receive premium)
        # SHORT: buy to close (pay premium)
        if self.direction == "LONG":
            return -self.current_price * self.contracts * self.multiplier
        else:
            return self.current_price * self.contracts * self.multiplier


@dataclass
class FreeTradeSuggestion:
    """Suggestion for making a leg free."""
    core_leg: Leg
    hedge_leg: Leg
    
    # Calculated values
    core_entry_cost: float = 0.0
    hedge_current_pnl: float = 0.0
    effective_core_cost: float = 0.0
    breakeven_close_price: float = 0.0
    current_hedge_price: float = 0.0
    
    # Status
    is_free: bool = False
    pct_to_free: float = 0.0  # How close to free (100% = free)
    
    def __post_init__(self):
        self.calculate()
    
    def calculate(self):
        """Calculate free trade metrics."""
        # Core leg cost (what we paid for the directional bet)
        self.core_entry_cost = abs(self.core_leg.entry_cost)
        
        # Hedge leg P&L
        self.hedge_current_pnl = self.hedge_leg.pnl
        
        # Effective cost = core cost - hedge profit
        self.effective_core_cost = self.core_entry_cost - self.hedge_current_pnl
        
        # Current hedge price
        self.current_hedge_price = self.hedge_leg.current_price
        
        # Breakeven close price for hedge (to make core free)
        # For SHORT hedge: profit = (entry - close) * contracts * mult
        # Need profit = core_cost
        # entry - close = core_cost / (contracts * mult)
        # close = entry - core_cost / (contracts * mult)
        
        contracts_mult = self.hedge_leg.contracts * self.hedge_leg.multiplier
        if self.hedge_leg.direction == "SHORT":
            # Sold hedge, need to buy back
            # Profit when buyback < entry
            self.breakeven_close_price = self.hedge_leg.entry_price - (self.core_entry_cost / contracts_mult)
        else:
            # Bought hedge, need to sell
            # Profit when sell > entry
            self.breakeven_close_price = self.hedge_leg.entry_price + (self.core_entry_cost / contracts_mult)
        
        # Clamp to zero (can't have negative prices)
        self.breakeven_close_price = max(0, self.breakeven_close_price)
        
        # Is it already free?
        self.is_free = self.effective_core_cost <= 0
        
        # Percentage to free
        if self.core_entry_cost > 0:
            self.pct_to_free = min(100, (self.hedge_current_pnl / self.core_entry_cost) * 100)
        else:
            self.pct_to_free = 100.0


@dataclass
class PositionAnalysis:
    """Full analysis of a multi-leg position."""
    ticker: str
    structure: str
    structure_type: str
    expiry: str
    contracts: int
    legs: list
    
    # Analysis results
    suggestions: list = field(default_factory=list)
    total_entry_cost: float = 0.0
    total_current_pnl: float = 0.0
    has_free_trade_opportunity: bool = False
    best_opportunity_pct: float = 0.0


def classify_position(legs: list) -> tuple[str, Optional[Leg], Optional[Leg]]:
    """
    Classify position type and identify core vs hedge legs.
    
    Returns: (structure_type, core_leg, hedge_leg)
    """
    if len(legs) == 1:
        return ("single_leg", legs[0], None)
    
    if len(legs) == 2:
        leg1, leg2 = legs
        
        # Risk Reversal: Long Call + Short Put OR Long Put + Short Call
        if leg1.leg_type == "Call" and leg2.leg_type == "Put":
            if leg1.direction == "LONG" and leg2.direction == "SHORT":
                return ("risk_reversal_bullish", leg1, leg2)  # Core: Call, Hedge: Put
            elif leg1.direction == "SHORT" and leg2.direction == "LONG":
                return ("risk_reversal_bearish", leg2, leg1)  # Core: Put, Hedge: Call
        
        if leg1.leg_type == "Put" and leg2.leg_type == "Call":
            if leg1.direction == "LONG" and leg2.direction == "SHORT":
                return ("risk_reversal_bearish", leg1, leg2)  # Core: Put, Hedge: Call
            elif leg1.direction == "SHORT" and leg2.direction == "LONG":
                return ("risk_reversal_bullish", leg2, leg1)  # Core: Call, Hedge: Put
        
        # Vertical Spreads (same type, different strikes)
        if leg1.leg_type == leg2.leg_type and leg1.strike != leg2.strike:
            # Bull Call Spread: Long lower strike, Short higher strike
            if leg1.leg_type == "Call":
                long_leg = leg1 if leg1.direction == "LONG" else leg2
                short_leg = leg2 if leg2.direction == "SHORT" else leg1
                if long_leg.strike < short_leg.strike:
                    return ("bull_call_spread", long_leg, short_leg)
                else:
                    return ("bear_call_spread", short_leg, long_leg)
            
            # Put Spreads
            if leg1.leg_type == "Put":
                long_leg = leg1 if leg1.direction == "LONG" else leg2
                short_leg = leg2 if leg2.direction == "SHORT" else leg1
                if long_leg.strike > short_leg.strike:
                    return ("bear_put_spread", long_leg, short_leg)
                else:
                    return ("bull_put_spread", short_leg, long_leg)
        
        # Straddle/Strangle: Both long or both short, Call + Put
        types = {leg1.leg_type, leg2.leg_type}
        directions = {leg1.direction, leg2.direction}
        if types == {"Call", "Put"} and len(directions) == 1:
            if "LONG" in directions:
                # Long straddle/strangle - can't really make "free"
                return ("long_straddle_strangle", None, None)
            else:
                # Short straddle/strangle
                return ("short_straddle_strangle", None, None)
    
    # Complex or unrecognized
    return ("complex", None, None)


def parse_portfolio_position(pos: dict) -> Optional[PositionAnalysis]:
    """Parse a portfolio position into analysis format."""
    if pos.get("structure_type") == "Stock":
        return None
    
    legs_data = pos.get("legs", [])
    if len(legs_data) < 2:
        return None  # Single leg, no free trade analysis
    
    # Parse legs
    legs = []
    for leg in legs_data:
        leg_type = leg.get("type", "")
        if leg_type == "Stock":
            continue  # Skip stock legs for now
        
        # Calculate entry price per contract
        entry_cost_raw = leg.get("entry_cost")
        entry_cost = abs(entry_cost_raw) if entry_cost_raw is not None else 0
        contracts = leg.get("contracts", 0) or 0
        multiplier = 100 if leg_type in ("Call", "Put") else 1
        entry_price = entry_cost / (contracts * multiplier) if contracts > 0 else 0
        
        # Current price
        market_price = leg.get("market_price")
        current_price = abs(market_price) if market_price is not None else 0
        
        legs.append(Leg(
            direction=leg.get("direction", ""),
            leg_type=leg_type,
            strike=leg.get("strike"),
            contracts=contracts,
            entry_price=entry_price,
            current_price=current_price,
            multiplier=multiplier,
        ))
    
    if len(legs) < 2:
        return None
    
    # Classify and analyze
    structure_type, core_leg, hedge_leg = classify_position(legs)
    
    analysis = PositionAnalysis(
        ticker=pos.get("ticker", ""),
        structure=pos.get("structure", ""),
        structure_type=structure_type,
        expiry=pos.get("expiry", ""),
        contracts=pos.get("contracts", 0),
        legs=legs,
    )
    
    # Calculate totals
    analysis.total_entry_cost = sum(abs(leg.entry_cost) for leg in legs if leg.direction == "LONG")
    analysis.total_current_pnl = sum(leg.pnl for leg in legs)
    
    # Generate suggestions if we identified core/hedge
    if core_leg and hedge_leg:
        suggestion = FreeTradeSuggestion(core_leg=core_leg, hedge_leg=hedge_leg)
        analysis.suggestions.append(suggestion)
        analysis.has_free_trade_opportunity = suggestion.pct_to_free >= 50
        analysis.best_opportunity_pct = suggestion.pct_to_free
    
    return analysis


def load_portfolio() -> list:
    """Load portfolio from JSON file."""
    if not PORTFOLIO_FILE.exists():
        return []
    
    with open(PORTFOLIO_FILE) as f:
        data = json.load(f)
    
    return data.get("positions", [])


def analyze_portfolio(ticker_filter: Optional[str] = None) -> list[PositionAnalysis]:
    """Analyze all multi-leg positions in portfolio."""
    positions = load_portfolio()
    results = []
    
    for pos in positions:
        if ticker_filter and pos.get("ticker") != ticker_filter:
            continue
        
        analysis = parse_portfolio_position(pos)
        if analysis:
            results.append(analysis)
    
    # Sort by opportunity percentage (highest first)
    results.sort(key=lambda x: x.best_opportunity_pct, reverse=True)
    
    return results


def format_currency(value: float) -> str:
    """Format value as currency."""
    if value >= 0:
        return f"${value:,.2f}"
    else:
        return f"-${abs(value):,.2f}"


def format_price(value: float) -> str:
    """Format price."""
    return f"${value:.2f}"


def print_analysis(analyses: list[PositionAnalysis], json_output: bool = False):
    """Print analysis results."""
    if json_output:
        output = []
        for a in analyses:
            item = {
                "ticker": a.ticker,
                "structure": a.structure,
                "structure_type": a.structure_type,
                "expiry": a.expiry,
                "contracts": a.contracts,
                "total_entry_cost": a.total_entry_cost,
                "total_current_pnl": a.total_current_pnl,
                "has_free_trade_opportunity": a.has_free_trade_opportunity,
                "best_opportunity_pct": a.best_opportunity_pct,
                "suggestions": []
            }
            for s in a.suggestions:
                item["suggestions"].append({
                    "core_leg": {
                        "type": s.core_leg.leg_type,
                        "direction": s.core_leg.direction,
                        "strike": s.core_leg.strike,
                        "entry_price": s.core_leg.entry_price,
                        "current_price": s.core_leg.current_price,
                    },
                    "hedge_leg": {
                        "type": s.hedge_leg.leg_type,
                        "direction": s.hedge_leg.direction,
                        "strike": s.hedge_leg.strike,
                        "entry_price": s.hedge_leg.entry_price,
                        "current_price": s.hedge_leg.current_price,
                    },
                    "core_entry_cost": s.core_entry_cost,
                    "hedge_current_pnl": s.hedge_current_pnl,
                    "effective_core_cost": s.effective_core_cost,
                    "breakeven_close_price": s.breakeven_close_price,
                    "current_hedge_price": s.current_hedge_price,
                    "is_free": s.is_free,
                    "pct_to_free": s.pct_to_free,
                })
            output.append(item)
        print(json.dumps(output, indent=2))
        return
    
    if not analyses:
        print("No multi-leg positions found for analysis.")
        return
    
    print("=" * 80)
    print("FREE TRADE ANALYZER")
    print("=" * 80)
    
    # Summary of opportunities
    free_positions = [a for a in analyses if any(s.is_free for s in a.suggestions)]
    near_free = [a for a in analyses if a.best_opportunity_pct >= 70 and a not in free_positions]
    
    if free_positions:
        print(f"\n🎉 {len(free_positions)} POSITION(S) ALREADY FREE:")
        for a in free_positions:
            print(f"   • {a.ticker} {a.structure}")
    
    if near_free:
        print(f"\n⚡ {len(near_free)} POSITION(S) NEAR FREE (≥70%):")
        for a in near_free:
            print(f"   • {a.ticker} {a.structure} ({a.best_opportunity_pct:.0f}% to free)")
    
    print("\n" + "-" * 80)
    
    for analysis in analyses:
        print(f"\n📊 {analysis.ticker} — {analysis.structure}")
        print(f"   Type: {analysis.structure_type.replace('_', ' ').title()}")
        print(f"   Expiry: {analysis.expiry}")
        print(f"   Contracts: {analysis.contracts}")
        
        for suggestion in analysis.suggestions:
            core = suggestion.core_leg
            hedge = suggestion.hedge_leg
            
            print(f"\n   CORE LEG (keep): {core.direction} {core.contracts}x {core.leg_type} ${core.strike}")
            print(f"      Entry: {format_price(core.entry_price)} | Current: {format_price(core.current_price)}")
            print(f"      Entry Cost: {format_currency(suggestion.core_entry_cost)}")
            
            print(f"\n   HEDGE LEG (close): {hedge.direction} {hedge.contracts}x {hedge.leg_type} ${hedge.strike}")
            print(f"      Entry: {format_price(hedge.entry_price)} | Current: {format_price(hedge.current_price)}")
            print(f"      Current P&L: {format_currency(suggestion.hedge_current_pnl)}")
            
            print(f"\n   📈 FREE TRADE ANALYSIS:")
            print(f"      Effective Core Cost: {format_currency(suggestion.effective_core_cost)}")
            print(f"      Progress to Free: {suggestion.pct_to_free:.1f}%")
            
            if suggestion.is_free:
                print(f"      ✅ POSITION IS FREE! Hedge profit exceeds core cost.")
            else:
                print(f"      Breakeven Close Price: {format_price(suggestion.breakeven_close_price)}")
                price_diff = suggestion.current_hedge_price - suggestion.breakeven_close_price
                if suggestion.breakeven_close_price <= 0:
                    print(f"      ⚠️  Would need hedge to expire worthless for free trade")
                elif price_diff > 0:
                    print(f"      📉 Need hedge to drop {format_price(price_diff)} more")
                else:
                    print(f"      📈 Hedge already below breakeven!")
        
        print("\n" + "-" * 80)


def get_startup_summary(analyses: list[PositionAnalysis], threshold: float = 50.0) -> Optional[str]:
    """Get a brief summary for startup notification.
    
    Args:
        analyses: List of position analyses
        threshold: Minimum percentage to consider "near free" (default 50%)
    """
    free_positions = [a for a in analyses if any(s.is_free for s in a.suggestions)]
    near_free = [a for a in analyses if a.best_opportunity_pct >= threshold and a not in free_positions]
    
    if not free_positions and not near_free:
        return None
    
    parts = []
    if free_positions:
        tickers = ", ".join(a.ticker for a in free_positions)
        parts.append(f"🎉 FREE: {tickers}")
    
    if near_free:
        items = [f"{a.ticker} ({a.best_opportunity_pct:.0f}%)" for a in near_free]
        parts.append(f"⚡ Near free: {', '.join(items)}")
    
    return " | ".join(parts)


def format_table(analyses: list[PositionAnalysis]) -> str:
    """Format analyses as an ASCII table for terminal output.
    
    Shows ALL qualifying multi-leg positions regardless of progress percentage.
    """
    if not analyses:
        return "No multi-leg positions found."
    
    from datetime import datetime, date
    
    # Calculate DTE for each position
    today = date.today()
    
    lines = []
    lines.append("💰 FREE TRADE PROGRESS")
    lines.append("=" * 70)
    lines.append(f"{'Ticker':<8} {'Structure':<28} {'Expiry':<8} {'DTE':>4} {'Progress':>10} {'Status':<10}")
    lines.append("-" * 70)
    
    for a in analyses:
        if not a.suggestions:
            continue
        
        s = a.suggestions[0]
        
        # Calculate DTE
        try:
            expiry_date = datetime.strptime(a.expiry, "%Y-%m-%d").date()
            dte = (expiry_date - today).days
        except:
            dte = 0
        
        # Format structure (truncate if needed)
        structure_short = a.structure[:26] + ".." if len(a.structure) > 28 else a.structure
        
        # Format expiry (just month/day)
        try:
            expiry_short = datetime.strptime(a.expiry, "%Y-%m-%d").strftime("%b %d")
        except:
            expiry_short = a.expiry[:8]
        
        # Determine status icon
        if s.is_free:
            status = "🎉 FREE"
            pct_str = "100%"
        elif s.pct_to_free >= 50:
            status = "⚡ Near"
            pct_str = f"{s.pct_to_free:.0f}%"
        elif s.pct_to_free >= 25:
            status = "🔄 Progress"
            pct_str = f"{s.pct_to_free:.0f}%"
        else:
            status = "⏳ Early"
            pct_str = f"{s.pct_to_free:.0f}%"
        
        lines.append(f"{a.ticker:<8} {structure_short:<28} {expiry_short:<8} {dte:>4} {pct_str:>10} {status:<10}")
    
    lines.append("-" * 70)
    
    # Add legend
    free_count = sum(1 for a in analyses if any(s.is_free for s in a.suggestions))
    near_count = sum(1 for a in analyses if 50 <= a.best_opportunity_pct < 100)
    
    if free_count > 0 or near_count > 0:
        lines.append(f"🎉 {free_count} FREE | ⚡ {near_count} Near (≥50%)")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Analyze positions for free trade opportunities")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--ticker", type=str, help="Filter by ticker")
    parser.add_argument("--summary", action="store_true", help="Brief summary for notifications")
    parser.add_argument("--table", action="store_true", help="Compact table format (for startup)")
    args = parser.parse_args()
    
    analyses = analyze_portfolio(ticker_filter=args.ticker)
    
    if args.summary:
        summary = get_startup_summary(analyses)
        if summary:
            print(summary)
        else:
            print("No free trade opportunities found.")
        return
    
    if args.table:
        print(format_table(analyses))
        return
    
    print_analysis(analyses, json_output=args.json)


if __name__ == "__main__":
    main()
