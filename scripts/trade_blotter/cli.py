#!/usr/bin/env python3
"""
Trade Blotter CLI - Fetch and reconcile trades from Interactive Brokers.

Usage:
    python3 cli.py                    # Today's trades
    python3 cli.py --port 7497        # Custom IB port
    python3 cli.py --json             # JSON output
    python3 cli.py --summary          # P&L summary only
"""
import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal

# Add parent directory to path for imports
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blotter_service import create_blotter_service, IBFetcher
from clients.ib_client import DEFAULT_HOST
from formatting import format_currency, format_pnl
from models import TradeBlotter, Trade


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def print_trade(trade: Trade, show_executions: bool = True):
    """Print a single trade with details."""
    status = "CLOSED" if trade.is_closed else "OPEN"
    status_icon = "🔒" if trade.is_closed else "📂"
    
    print(f"\n{status_icon} {trade.contract_desc} [{status}]")
    print(f"   Net Qty: {trade.net_quantity}")
    print(f"   Commissions: {format_currency(trade.total_commission)}")
    
    if trade.is_closed:
        print(f"   Realized P&L: {format_pnl(trade.realized_pnl)}")
    else:
        print(f"   Cost Basis: {format_currency(trade.cost_basis)}")
        print(f"   Cash Flow: {format_currency(trade.total_cash_flow)}")
    
    if show_executions:
        print("   Executions:")
        for e in trade.executions:
            side_icon = "🟢" if e.side.value == "BOT" else "🔴"
            print(f"      {side_icon} {e.time.strftime('%H:%M:%S')} | "
                  f"{e.side.value} {e.quantity}x @ ${e.price:.2f} | "
                  f"Fee: ${e.commission:.2f}")


def print_blotter(blotter: TradeBlotter, verbose: bool = False):
    """Print full blotter report."""
    print("=" * 70)
    print("TRADE BLOTTER")
    print(f"As of: {blotter.as_of.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # Closed trades
    if blotter.closed_trades:
        print(f"\n📊 CLOSED TRADES ({len(blotter.closed_trades)})")
        print("-" * 50)
        for trade in blotter.closed_trades:
            print_trade(trade, show_executions=verbose)
    
    # Open positions
    if blotter.open_trades:
        print(f"\n📈 OPEN POSITIONS ({len(blotter.open_trades)})")
        print("-" * 50)
        for trade in blotter.open_trades:
            print_trade(trade, show_executions=verbose)
    
    # Spreads
    print_spreads(blotter)
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Closed Trades:    {len(blotter.closed_trades)}")
    print(f"  Open Positions:   {len(blotter.open_trades)}")
    print(f"  Total Commissions: {format_currency(blotter.total_commissions)}")
    print(f"  Realized P&L:     {format_pnl(blotter.total_realized_pnl)}")
    
    # Spread summary
    spreads = blotter.get_spreads()
    if spreads:
        total_spread_flow = sum(s.total_cash_flow for s in spreads)
        print(f"  Net Spread Flow:  {format_pnl(total_spread_flow)}")


def print_spreads(blotter: TradeBlotter):
    """Print spread summary with combined P&L."""
    spreads = blotter.get_spreads()
    
    if not spreads:
        return
    
    print(f"\n📊 SPREAD POSITIONS ({len(spreads)})")
    print("-" * 60)
    
    for spread in spreads:
        status = "CLOSED" if spread.is_closed else "OPEN"
        status_icon = "🔒" if spread.is_closed else "📂"
        
        print(f"\n{status_icon} {spread.name} (exp: {spread.expiry}) [{status}]")
        print(f"   Legs: {len(spread.legs)}")
        
        for leg in spread.legs:
            qty_str = f"{leg.net_quantity:+}"
            print(f"      • {leg.contract_desc}: {qty_str}")
        
        print(f"   Commissions: {format_currency(spread.total_commission)}")
        print(f"   Net Cash Flow: {format_pnl(spread.total_cash_flow)}")
        
        if spread.is_closed:
            print(f"   Realized P&L: {format_pnl(spread.realized_pnl)}")


def print_summary(blotter: TradeBlotter):
    """Print P&L summary only."""
    print("=" * 50)
    print("TODAY'S P&L SUMMARY")
    print("=" * 50)
    print(f"  Closed Trades:     {len(blotter.closed_trades)}")
    print(f"  Open Positions:    {len(blotter.open_trades)}")
    print(f"  Total Commissions: {format_currency(blotter.total_commissions)}")
    print(f"  Realized P&L:      {format_pnl(blotter.total_realized_pnl)}")
    
    # Add spread summary
    spreads = blotter.get_spreads()
    if spreads:
        print("-" * 50)
        print("  SPREAD SUMMARY")
        total_spread_flow = sum(s.total_cash_flow for s in spreads)
        print(f"  Spreads:           {len(spreads)}")
        print(f"  Net Spread Flow:   {format_pnl(total_spread_flow)}")
    
    print("=" * 50)


def blotter_to_dict(blotter: TradeBlotter) -> dict:
    """Convert blotter to JSON-serializable dict."""
    def trade_to_dict(trade: Trade) -> dict:
        return {
            "symbol": trade.symbol,
            "contract_desc": trade.contract_desc,
            "sec_type": trade.sec_type.value,
            "is_closed": trade.is_closed,
            "net_quantity": trade.net_quantity,
            "total_quantity": trade.total_quantity,
            "total_commission": trade.total_commission,
            "realized_pnl": trade.realized_pnl,
            "cost_basis": trade.cost_basis,
            "proceeds": trade.proceeds,
            "total_cash_flow": trade.total_cash_flow,
            "executions": [
                {
                    "exec_id": e.exec_id,
                    "time": e.time,
                    "side": e.side.value,
                    "quantity": e.quantity,
                    "price": e.price,
                    "commission": e.commission,
                    "notional_value": e.notional_value,
                    "net_cash_flow": e.net_cash_flow,
                }
                for e in trade.executions
            ]
        }
    
    def spread_to_dict(spread) -> dict:
        return {
            "name": spread.name,
            "symbol": spread.symbol,
            "expiry": spread.expiry,
            "is_closed": spread.is_closed,
            "legs": [trade_to_dict(leg) for leg in spread.legs],
            "total_commission": spread.total_commission,
            "total_cash_flow": spread.total_cash_flow,
            "realized_pnl": spread.realized_pnl,
        }
    
    spreads = blotter.get_spreads()
    total_spread_flow = sum(s.total_cash_flow for s in spreads)
    
    return {
        "as_of": blotter.as_of,
        "summary": {
            "closed_trades": len(blotter.closed_trades),
            "open_trades": len(blotter.open_trades),
            "total_commissions": blotter.total_commissions,
            "realized_pnl": blotter.total_realized_pnl,
            "spread_count": len(spreads),
            "net_spread_cash_flow": total_spread_flow,
        },
        "spreads": [spread_to_dict(s) for s in spreads],
        "closed_trades": [trade_to_dict(t) for t in blotter.closed_trades],
        "open_trades": [trade_to_dict(t) for t in blotter.open_trades],
    }


def main():
    parser = argparse.ArgumentParser(description="Trade Blotter - IB Trade Reconciliation")
    parser.add_argument("--host", default=DEFAULT_HOST, help="IB Gateway/TWS host")
    parser.add_argument("--port", type=int, default=4001, help="IB Gateway/TWS port")
    parser.add_argument("--client-id", type=int, default=88, help="IB client ID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--summary", action="store_true", help="Show P&L summary only")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show execution details")
    
    args = parser.parse_args()
    
    try:
        # Create service and fetch data
        service = create_blotter_service(
            source="ib",
            host=args.host,
            port=args.port,
            client_id=args.client_id,
        )
        
        blotter = service.build_blotter()
        
        if args.json:
            print(json.dumps(blotter_to_dict(blotter), cls=DecimalEncoder, indent=2))
        elif args.summary:
            print_summary(blotter)
        else:
            print_blotter(blotter, verbose=args.verbose)
        
        return 0
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
