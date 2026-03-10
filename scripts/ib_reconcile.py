#!/usr/bin/env python3
"""
IB Trade Reconciliation Script

Fetches trading history from IB and reconciles with local trade_log.json and portfolio.json.
Designed to run asynchronously at startup without blocking the UI.

Actions detected:
- BUY: Opening long stock/option position
- SELL: Closing long position (realized P&L)
- SHORT: Opening short stock position
- COVER: Closing short position (realized P&L)
- New positions not in portfolio.json
- Closed positions still in portfolio.json
"""

import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT


def log(msg: str, level: str = "info"):
    """Print log message with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {"info": "ℹ", "warn": "⚠", "error": "✗", "success": "✓"}.get(level, "•")
    print(f"[{timestamp}] {prefix} {msg}")


def connect_ib(port: int = DEFAULT_GATEWAY_PORT, client_id: int = CLIENT_IDS["ib_reconcile"]) -> Optional[IBClient]:
    """Connect to IB Gateway/TWS."""
    try:
        client = IBClient()
        client.connect(host=DEFAULT_HOST, port=port, client_id=client_id)
        return client
    except Exception as e:
        log(f"IB connection failed: {e}", "error")
        return None

def load_json(filepath: str) -> dict:
    """Load JSON file, return empty dict if not found."""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_json(filepath: str, data: dict):
    """Save data to JSON file."""
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2, default=str)

def get_trade_log_trades(trade_log: dict) -> set:
    """Extract set of (ticker, date, structure_type) tuples from trade log."""
    trades = set()
    for trade in trade_log.get("trades", []):
        ticker = trade.get("ticker")
        date = trade.get("date") or trade.get("close_date")
        structure = trade.get("structure", "")
        trades.add((ticker, date, structure))
    return trades

def fetch_ib_executions(client: IBClient, lookback_days: int = 7) -> list:
    """Fetch executions from IB for the last N days."""
    executions = []
    fills = client.get_fills()
    
    for fill in fills:
        e = fill.execution
        c = fill.contract
        cr = fill.commissionReport
        
        executions.append({
            "time": e.time,
            "symbol": c.symbol,
            "sec_type": c.secType,
            "side": e.side,  # BOT or SLD
            "shares": e.shares,
            "price": e.price,
            "exchange": e.exchange,
            "commission": cr.commission if cr else 0,
            "realized_pnl": cr.realizedPNL if cr and cr.realizedPNL else 0,
            "strike": c.strike if c.secType == "OPT" else None,
            "expiry": c.lastTradeDateOrContractMonth if c.secType == "OPT" else None,
            "right": c.right if c.secType == "OPT" else None,
        })
    
    return executions

def fetch_ib_positions(client: IBClient) -> list:
    """Fetch current positions from IB."""
    positions = []
    for p in client.get_positions():
        positions.append({
            "symbol": p.contract.symbol,
            "sec_type": p.contract.secType,
            "quantity": p.position,
            "avg_cost": p.avgCost,
            "strike": p.contract.strike if p.contract.secType == "OPT" else None,
            "expiry": p.contract.lastTradeDateOrContractMonth if p.contract.secType == "OPT" else None,
            "right": p.contract.right if p.contract.secType == "OPT" else None,
        })
    return positions

def _contract_key(e: dict) -> str:
    """Build a grouping key: symbol only for stocks, symbol+strike+expiry+right for options."""
    if e["sec_type"] in ("OPT", "BAG"):
        return f"{e['symbol']}|{e['sec_type']}|{e.get('strike')}|{e.get('expiry')}|{e.get('right')}"
    return f"{e['symbol']}|{e['sec_type']}"


def group_executions_by_symbol(executions: list) -> dict:
    """Group executions by contract (symbol + strike/expiry/right for options) and determine net action."""
    grouped = {}

    for e in executions:
        key = _contract_key(e)
        if key not in grouped:
            grouped[key] = {
                "symbol": e["symbol"],
                "sec_type": e["sec_type"],
                "strike": e.get("strike"),
                "expiry": e.get("expiry"),
                "right": e.get("right"),
                "executions": [],
                "net_quantity": 0,
                "total_value": 0,
                "total_commission": 0,
                "total_realized_pnl": 0,
            }

        g = grouped[key]
        g["executions"].append(e)

        qty = e["shares"] if e["side"] == "BOT" else -e["shares"]
        g["net_quantity"] += qty
        g["total_value"] += e["shares"] * e["price"]
        g["total_commission"] += e["commission"]
        g["total_realized_pnl"] += e["realized_pnl"]

    # Determine action for each group
    for key, g in grouped.items():
        if g["net_quantity"] > 0:
            g["action"] = "BUY" if g["sec_type"] == "STK" else "BUY_OPTION"
        elif g["net_quantity"] < 0:
            # Check if it's a close (has realized P&L) or short opening
            if g["total_realized_pnl"] != 0:
                g["action"] = "SELL" if g["sec_type"] == "STK" else "SELL_OPTION"
            else:
                g["action"] = "SHORT" if g["sec_type"] == "STK" else "SELL_TO_OPEN"
        else:
            # Net zero - could be a day trade or covered
            if g["total_realized_pnl"] != 0:
                g["action"] = "CLOSED"
            else:
                g["action"] = "NEUTRAL"

    return grouped

def find_new_trades(executions: list, trade_log: dict) -> list:
    """Find executions that aren't in the trade log."""
    existing = get_trade_log_trades(trade_log)
    grouped = group_executions_by_symbol(executions)

    new_trades = []
    for key, g in grouped.items():
        symbol = g["symbol"]
        # Get the date from first execution
        if g["executions"]:
            trade_date = g["executions"][0]["time"].strftime("%Y-%m-%d")

            # Check if this trade exists in log
            found = False
            for ticker, date, structure in existing:
                if ticker == symbol and date == trade_date:
                    found = True
                    break

            if not found and g["action"] not in ["NEUTRAL"]:
                entry = {
                    "symbol": symbol,
                    "date": trade_date,
                    "action": g["action"],
                    "net_quantity": g["net_quantity"],
                    "avg_price": g["total_value"] / sum(e["shares"] for e in g["executions"]) if g["executions"] else 0,
                    "commission": g["total_commission"],
                    "realized_pnl": g["total_realized_pnl"],
                    "sec_type": g["sec_type"],
                }
                # Include contract details for options
                if g["sec_type"] in ("OPT", "BAG") and g.get("strike"):
                    entry["strike"] = g["strike"]
                    entry["expiry"] = g.get("expiry")
                    entry["right"] = g.get("right")
                new_trades.append(entry)

    return new_trades

def find_position_discrepancies(ib_positions: list, portfolio: dict) -> dict:
    """Find positions that differ between IB and local portfolio."""
    discrepancies = {
        "missing_locally": [],  # In IB but not in portfolio.json
        "missing_in_ib": [],    # In portfolio.json but not in IB (closed)
        "quantity_mismatch": [],
    }
    
    # Build lookup of local tickers (just by symbol for simplicity)
    local_tickers = set()
    local_positions = {}
    for pos in portfolio.get("positions", []):
        ticker = pos.get("ticker")
        if ticker:
            local_tickers.add(ticker)
            # Use ticker as key, store the position
            if ticker not in local_positions:
                local_positions[ticker] = []
            local_positions[ticker].append(pos)
    
    # Build lookup of IB tickers
    ib_tickers = set()
    ib_positions_by_symbol = {}
    for p in ib_positions:
        symbol = p["symbol"]
        ib_tickers.add(symbol)
        if symbol not in ib_positions_by_symbol:
            ib_positions_by_symbol[symbol] = []
        ib_positions_by_symbol[symbol].append(p)
    
    # Find positions in IB not locally (new positions)
    for symbol in ib_tickers - local_tickers:
        for p in ib_positions_by_symbol[symbol]:
            discrepancies["missing_locally"].append(p)
    
    # Find positions locally not in IB (closed positions)
    for ticker in local_tickers - ib_tickers:
        for p in local_positions[ticker]:
            discrepancies["missing_in_ib"].append(p)
    
    return discrepancies

def generate_reconciliation_report(new_trades: list, discrepancies: dict) -> dict:
    """Generate a report of what needs to be reconciled."""
    report = {
        "timestamp": datetime.now().isoformat(),
        "new_trades": new_trades,
        "positions_missing_locally": discrepancies["missing_locally"],
        "positions_closed": discrepancies["missing_in_ib"],
        "needs_attention": len(new_trades) > 0 or len(discrepancies["missing_locally"]) > 0 or len(discrepancies["missing_in_ib"]) > 0,
    }
    return report

def main():
    """Main reconciliation routine."""
    log("Starting IB trade reconciliation...")
    
    # Paths
    project_root = Path(__file__).parent.parent
    trade_log_path = project_root / "data" / "trade_log.json"
    portfolio_path = project_root / "data" / "portfolio.json"
    reconcile_path = project_root / "data" / "reconciliation.json"
    
    # Connect to IB
    client = connect_ib()
    if not client:
        log("Cannot connect to IB Gateway - skipping reconciliation", "warn")
        return

    try:
        # Load local data
        trade_log = load_json(str(trade_log_path))
        portfolio = load_json(str(portfolio_path))

        # Fetch IB data
        log("Fetching executions from IB...")
        executions = fetch_ib_executions(client)
        log(f"Found {len(executions)} executions")

        log("Fetching positions from IB...")
        positions = fetch_ib_positions(client)
        log(f"Found {len(positions)} positions")
        
        # Find discrepancies
        log("Checking for new trades...")
        new_trades = find_new_trades(executions, trade_log)
        
        log("Checking position discrepancies...")
        discrepancies = find_position_discrepancies(positions, portfolio)
        
        # Generate report
        report = generate_reconciliation_report(new_trades, discrepancies)
        
        # Save reconciliation report
        save_json(str(reconcile_path), report)
        
        # Log summary
        if report["needs_attention"]:
            log(f"⚠️  Reconciliation needed:", "warn")
            if new_trades:
                log(f"   • {len(new_trades)} new trades to log", "warn")
                for t in new_trades:
                    log(f"     - {t['action']} {t['symbol']}: {t['net_quantity']} @ ${t['avg_price']:.2f}", "info")
            if discrepancies["missing_locally"]:
                log(f"   • {len(discrepancies['missing_locally'])} positions missing locally", "warn")
            if discrepancies["missing_in_ib"]:
                log(f"   • {len(discrepancies['missing_in_ib'])} positions may be closed", "warn")
        else:
            log("✓ Trade log and portfolio are in sync", "success")
        
    finally:
        client.disconnect()
        log("Disconnected from IB")

if __name__ == "__main__":
    main()
