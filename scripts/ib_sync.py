#!/usr/bin/env python3
"""
Interactive Brokers Portfolio Sync

Connects to TWS/IB Gateway and syncs live positions to portfolio.json

Requirements:
  pip install ib_insync

Usage:
  python3 scripts/ib_sync.py              # Display portfolio
  python3 scripts/ib_sync.py --sync       # Sync to portfolio.json
  python3 scripts/ib_sync.py --port 7497  # Custom port (7497=TWS paper, 7496=TWS live, 4001=Gateway)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from ib_insync import IB, util
except ImportError:
    print("ERROR: ib_insync not installed")
    print("Install with: pip install ib_insync")
    sys.exit(1)


# Default connection settings
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7497  # TWS paper trading (7496 for live, 4001/4002 for Gateway)
DEFAULT_CLIENT_ID = 1

PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "portfolio.json"


def connect_ib(host: str, port: int, client_id: int) -> IB:
    """Connect to TWS/IB Gateway"""
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id)
        print(f"✓ Connected to IB on {host}:{port}")
        return ib
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Ensure TWS or IB Gateway is running")
        print("  2. Enable API connections in TWS: Configure > API > Settings")
        print("  3. Check 'Enable ActiveX and Socket Clients'")
        print("  4. Verify port matches (TWS Paper=7497, TWS Live=7496, Gateway=4001)")
        sys.exit(1)


def get_account_summary(ib: IB) -> dict:
    """Fetch account summary (cash, net liquidation, etc.)"""
    account_values = ib.accountSummary()
    
    summary = {}
    for av in account_values:
        if av.tag in ['NetLiquidation', 'TotalCashValue', 'AvailableFunds', 'BuyingPower']:
            if av.currency == 'USD':
                summary[av.tag] = float(av.value)
    
    return summary


def format_option_structure(contract, position) -> str:
    """Format option contract into readable structure string"""
    if contract.secType == 'OPT':
        right = 'Call' if contract.right == 'C' else 'Put'
        return f"{right} ${contract.strike} ({contract.lastTradeDateOrContractMonth})"
    elif contract.secType == 'STK':
        return f"Stock ({position} shares)"
    else:
        return contract.secType


def detect_structure_type(legs: list) -> tuple[str, str]:
    """
    Detect multi-leg structure type from component legs.
    Returns (structure_name, risk_profile)
    """
    if len(legs) == 1:
        leg = legs[0]
        if leg['secType'] == 'STK':
            return "Stock", "equity"
        direction = "Long" if leg['position'] > 0 else "Short"
        right = "Call" if leg.get('right') == 'C' else "Put"
        return f"{direction} {right}", "defined" if direction == "Long" else "undefined"
    
    # Sort legs by strike for consistent ordering
    opt_legs = [l for l in legs if l['secType'] == 'OPT']
    if not opt_legs:
        return "Mixed", "unknown"
    
    # Analyze leg composition
    calls = [l for l in opt_legs if l.get('right') == 'C']
    puts = [l for l in opt_legs if l.get('right') == 'P']
    long_legs = [l for l in opt_legs if l['position'] > 0]
    short_legs = [l for l in opt_legs if l['position'] < 0]
    
    # Risk Reversal: Short Put + Long Call (same expiry)
    if len(puts) == 1 and len(calls) == 1:
        if puts[0]['position'] < 0 and calls[0]['position'] > 0:
            return "Risk Reversal", "undefined"
        if puts[0]['position'] > 0 and calls[0]['position'] < 0:
            return "Reverse Risk Reversal", "undefined"
        if puts[0]['position'] > 0 and calls[0]['position'] > 0:
            return "Strangle" if puts[0].get('strike') != calls[0].get('strike') else "Straddle", "defined"
    
    # Vertical Spreads: Same type, different strikes, opposite directions
    if len(calls) == 2 and len(puts) == 0:
        if len(long_legs) == 1 and len(short_legs) == 1:
            long_strike = long_legs[0].get('strike', 0)
            short_strike = short_legs[0].get('strike', 0)
            if long_strike < short_strike:
                return "Bull Call Spread", "defined"
            else:
                return "Bear Call Spread", "defined"
    
    if len(puts) == 2 and len(calls) == 0:
        if len(long_legs) == 1 and len(short_legs) == 1:
            long_strike = long_legs[0].get('strike', 0)
            short_strike = short_legs[0].get('strike', 0)
            if long_strike > short_strike:
                return "Bear Put Spread", "defined"
            else:
                return "Bull Put Spread", "defined"
    
    # Default for complex structures
    return f"Combo ({len(legs)} legs)", "complex"


def format_structure_description(structure_type: str, legs: list) -> str:
    """Create human-readable structure description with strikes"""
    if structure_type == "Stock":
        return legs[0]['structure']
    
    opt_legs = sorted([l for l in legs if l['secType'] == 'OPT'], 
                      key=lambda x: x.get('strike', 0))
    
    if not opt_legs:
        return structure_type
    
    if "Spread" in structure_type:
        strikes = [l.get('strike') for l in opt_legs]
        return f"{structure_type} ${min(strikes)}/${max(strikes)}"
    
    if "Risk Reversal" in structure_type:
        put_strike = next((l.get('strike') for l in opt_legs if l.get('right') == 'P'), '?')
        call_strike = next((l.get('strike') for l in opt_legs if l.get('right') == 'C'), '?')
        return f"{structure_type} (P${put_strike}/C${call_strike})"
    
    if structure_type in ("Straddle", "Strangle"):
        strikes = [l.get('strike') for l in opt_legs]
        if len(set(strikes)) == 1:
            return f"{structure_type} ${strikes[0]}"
        return f"{structure_type} ${min(strikes)}/${max(strikes)}"
    
    return structure_type


def collapse_positions(positions: list) -> list:
    """
    Collapse individual legs into multi-leg structures.
    Groups by ticker + expiry, detects structure type.
    """
    from collections import defaultdict
    
    # Group by ticker + expiry
    groups = defaultdict(list)
    for pos in positions:
        # Use N/A expiry for stocks to keep them separate
        key = (pos['symbol'], pos['expiry'])
        groups[key].append(pos)
    
    collapsed = []
    position_id = 1
    
    for (symbol, expiry), legs in groups.items():
        structure_type, risk_profile = detect_structure_type(legs)
        structure_desc = format_structure_description(structure_type, legs)
        
        # Calculate aggregate values
        total_entry_cost = sum(l['entry_cost'] for l in legs)
        total_market_value = None
        if all(l.get('marketValue') is not None for l in legs):
            total_market_value = sum(l['marketValue'] for l in legs)
        
        # Net contracts (for spreads, use the long leg count)
        long_legs = [l for l in legs if l['position'] > 0]
        contracts = int(abs(long_legs[0]['position'])) if long_legs else int(abs(legs[0]['position']))
        
        # Determine net direction
        net_position = sum(l['position'] for l in legs)
        if structure_type == "Stock":
            direction = "LONG" if net_position > 0 else "SHORT"
        elif "Spread" in structure_type:
            direction = "DEBIT" if total_entry_cost > 0 else "CREDIT"
        elif risk_profile == "undefined":
            direction = "COMBO"
        else:
            direction = "LONG" if net_position > 0 else "SHORT"
        
        # Calculate max risk
        if risk_profile == "defined":
            # For defined risk, max loss is net debit paid
            if "Spread" in structure_type:
                strikes = sorted([l.get('strike', 0) for l in legs if l['secType'] == 'OPT'])
                width = (strikes[-1] - strikes[0]) * 100 * contracts if len(strikes) >= 2 else 0
                if direction == "DEBIT":
                    max_risk = total_entry_cost
                else:
                    max_risk = width - abs(total_entry_cost)
            else:
                max_risk = total_entry_cost
        else:
            max_risk = None  # Undefined risk
        
        # Format legs for subtree
        formatted_legs = []
        for leg in sorted(legs, key=lambda x: (x.get('right', 'Z'), x.get('strike', 0))):
            formatted_legs.append({
                "direction": "LONG" if leg['position'] > 0 else "SHORT",
                "contracts": int(abs(leg['position'])),
                "type": "Call" if leg.get('right') == 'C' else ("Put" if leg.get('right') == 'P' else "Stock"),
                "strike": leg.get('strike'),
                "entry_cost": leg['entry_cost'],
                "avg_cost": leg['avgCost'],
                "market_price": leg.get('marketPrice'),
                "market_value": leg.get('marketValue')
            })
        
        collapsed.append({
            "id": position_id,
            "ticker": symbol,
            "structure": structure_desc,
            "structure_type": structure_type,
            "risk_profile": risk_profile,
            "expiry": expiry,
            "contracts": contracts,
            "direction": direction,
            "entry_cost": round(total_entry_cost, 2),
            "max_risk": round(max_risk, 2) if max_risk else None,
            "market_value": round(total_market_value, 2) if total_market_value else None,
            "legs": formatted_legs,
            "kelly_optimal": None,
            "target": None,
            "stop": None
        })
        position_id += 1
    
    # Sort by ticker, then expiry
    collapsed.sort(key=lambda x: (x['ticker'], x['expiry'] or 'Z'))
    
    # Re-assign IDs after sorting
    for i, pos in enumerate(collapsed, 1):
        pos['id'] = i
    
    return collapsed


def parse_expiry(contract) -> str:
    """Parse expiry date from contract"""
    if hasattr(contract, 'lastTradeDateOrContractMonth') and contract.lastTradeDateOrContractMonth:
        expiry_str = contract.lastTradeDateOrContractMonth
        # Format: YYYYMMDD -> YYYY-MM-DD
        if len(expiry_str) == 8:
            return f"{expiry_str[:4]}-{expiry_str[4:6]}-{expiry_str[6:8]}"
        return expiry_str
    return "N/A"


def fetch_positions(ib: IB) -> list:
    """Fetch all positions from IB"""
    positions = ib.positions()
    
    formatted = []
    for pos in positions:
        contract = pos.contract
        
        # Calculate position value
        avg_cost = pos.avgCost
        position_size = pos.position
        
        # For options, avgCost is per share, multiply by 100 for per contract
        if contract.secType == 'OPT':
            entry_cost = abs(avg_cost * position_size)  # Already multiplied by multiplier internally
        else:
            entry_cost = abs(avg_cost * position_size)
        
        formatted.append({
            "symbol": contract.symbol,
            "secType": contract.secType,
            "position": position_size,
            "avgCost": avg_cost,
            "entry_cost": round(entry_cost, 2),
            "expiry": parse_expiry(contract),
            "strike": getattr(contract, 'strike', None),
            "right": getattr(contract, 'right', None),
            "structure": format_option_structure(contract, position_size),
            "contract": contract  # Keep for market data requests
        })
    
    return formatted


def fetch_market_prices(ib: IB, positions: list) -> list:
    """Fetch current market prices for positions"""
    for pos in positions:
        contract = pos['contract']
        ib.qualifyContracts(contract)
        
        # Request market data
        ticker = ib.reqMktData(contract, '', False, False)
        ib.sleep(1)  # Wait for data
        
        if ticker.marketPrice() and not util.isNan(ticker.marketPrice()):
            pos['marketPrice'] = ticker.marketPrice()
            pos['marketValue'] = round(ticker.marketPrice() * abs(pos['position']) * 
                                       (100 if pos['secType'] == 'OPT' else 1), 2)
        else:
            pos['marketPrice'] = None
            pos['marketValue'] = None
        
        ib.cancelMktData(contract)
        del pos['contract']  # Remove non-serializable contract object
    
    return positions


def display_portfolio(account: dict, positions: list, collapsed: list = None):
    """Pretty print portfolio"""
    print("\n" + "="*70)
    print("INTERACTIVE BROKERS PORTFOLIO")
    print("="*70)
    
    print("\n📊 ACCOUNT SUMMARY")
    print("-"*50)
    for key, value in account.items():
        print(f"  {key}: ${value:,.2f}")
    
    print("\n📈 POSITIONS")
    print("-"*50)
    
    # Use collapsed view if available
    display_positions = collapsed if collapsed else positions
    
    if not display_positions:
        print("  No open positions")
    elif collapsed:
        # Display collapsed multi-leg structures
        for pos in collapsed:
            risk_icon = "✓" if pos['risk_profile'] == 'defined' else "⚠"
            print(f"\n  [{pos['id']}] {pos['ticker']} — {pos['structure']}")
            print(f"      {risk_icon} {pos['risk_profile'].upper()} | {pos['direction']} | {pos['contracts']}x")
            print(f"      Entry: ${pos['entry_cost']:,.2f}", end="")
            if pos['max_risk']:
                print(f" | Max Risk: ${pos['max_risk']:,.2f}", end="")
            print()
            if pos.get('market_value'):
                pnl = pos['market_value'] - pos['entry_cost']
                pnl_pct = (pnl / abs(pos['entry_cost']) * 100) if pos['entry_cost'] != 0 else 0
                print(f"      Market Value: ${pos['market_value']:,.2f} ({pnl_pct:+.1f}%)")
            if pos['expiry'] and pos['expiry'] != "N/A":
                print(f"      Expiry: {pos['expiry']}")
            
            # Show legs subtree
            if len(pos['legs']) > 1:
                print("      ├─ Legs:")
                for i, leg in enumerate(pos['legs']):
                    is_last = i == len(pos['legs']) - 1
                    prefix = "└" if is_last else "├"
                    strike_str = f" ${leg['strike']}" if leg['strike'] else ""
                    print(f"      │  {prefix}─ {leg['direction']} {leg['contracts']}x {leg['type']}{strike_str}")
                    print(f"      │     Cost: ${leg['entry_cost']:,.2f}", end="")
                    if leg.get('market_value'):
                        print(f" → ${leg['market_value']:,.2f}", end="")
                    print()
    else:
        # Fallback: group by underlying (old behavior)
        by_symbol = {}
        for pos in positions:
            sym = pos['symbol']
            if sym not in by_symbol:
                by_symbol[sym] = []
            by_symbol[sym].append(pos)
        
        for symbol, symbol_positions in by_symbol.items():
            print(f"\n  {symbol}")
            for pos in symbol_positions:
                direction = "LONG" if pos['position'] > 0 else "SHORT"
                print(f"    {direction} {abs(pos['position'])}x {pos['structure']}")
                print(f"      Entry Cost: ${pos['entry_cost']:,.2f}")
                if pos.get('marketValue'):
                    pnl = pos['marketValue'] - pos['entry_cost']
                    pnl_pct = (pnl / pos['entry_cost'] * 100) if pos['entry_cost'] > 0 else 0
                    print(f"      Market Value: ${pos['marketValue']:,.2f} ({pnl_pct:+.1f}%)")
                if pos['expiry'] != "N/A":
                    print(f"      Expiry: {pos['expiry']}")
    
    print("\n" + "="*70)


def convert_to_portfolio_format(account: dict, collapsed_positions: list) -> dict:
    """Convert IB data to portfolio.json format using collapsed positions"""
    
    bankroll = account.get('NetLiquidation', account.get('TotalCashValue', 0))
    
    # Calculate totals from collapsed positions
    total_deployed = sum(p['entry_cost'] for p in collapsed_positions)
    deployed_pct = (total_deployed / bankroll * 100) if bankroll > 0 else 0
    
    # Add entry_date to positions
    for pos in collapsed_positions:
        pos['entry_date'] = datetime.now().strftime("%Y-%m-%d")  # IB doesn't provide this easily
    
    return {
        "bankroll": round(bankroll, 2),
        "peak_value": round(bankroll, 2),  # Would need historical tracking
        "last_sync": datetime.now().isoformat(),
        "positions": collapsed_positions,
        "total_deployed_pct": round(deployed_pct, 2),
        "total_deployed_dollars": round(total_deployed, 2),
        "remaining_capacity_pct": round(100 - deployed_pct, 2),
        "position_count": len(collapsed_positions),
        "defined_risk_count": len([p for p in collapsed_positions if p['risk_profile'] == 'defined']),
        "undefined_risk_count": len([p for p in collapsed_positions if p['risk_profile'] != 'defined']),
        "avg_kelly_optimal": None  # Needs evaluation
    }


def save_portfolio(portfolio: dict):
    """Save portfolio to JSON file"""
    # Backup existing
    if PORTFOLIO_PATH.exists():
        backup_path = PORTFOLIO_PATH.with_suffix('.json.bak')
        backup_path.write_text(PORTFOLIO_PATH.read_text())
        print(f"✓ Backed up existing portfolio to {backup_path.name}")
    
    with open(PORTFOLIO_PATH, 'w') as f:
        json.dump(portfolio, f, indent=2)
    
    print(f"✓ Saved portfolio to {PORTFOLIO_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Sync portfolio from Interactive Brokers")
    parser.add_argument("--host", default=DEFAULT_HOST, help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, 
                        help="TWS/Gateway port (7497=paper, 7496=live, 4001=gateway)")
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID, help="Client ID")
    parser.add_argument("--sync", action="store_true", help="Sync to portfolio.json")
    parser.add_argument("--no-prices", action="store_true", help="Skip market price fetch")
    
    args = parser.parse_args()
    
    # Connect
    ib = connect_ib(args.host, args.port, args.client_id)
    
    try:
        # Fetch data
        print("Fetching account summary...")
        account = get_account_summary(ib)
        
        print("Fetching positions...")
        positions = fetch_positions(ib)
        
        if not args.no_prices and positions:
            print("Fetching market prices...")
            positions = fetch_market_prices(ib, positions)
        else:
            # Remove contract objects if not fetching prices
            for pos in positions:
                if 'contract' in pos:
                    del pos['contract']
        
        # Collapse multi-leg structures
        print("Analyzing position structures...")
        collapsed = collapse_positions(positions)
        
        # Display with collapsed view
        display_portfolio(account, positions, collapsed)
        
        # Summary stats
        defined = len([p for p in collapsed if p['risk_profile'] == 'defined'])
        undefined = len([p for p in collapsed if p['risk_profile'] != 'defined'])
        print(f"\n📋 SUMMARY: {len(collapsed)} positions ({defined} defined risk, {undefined} undefined)")
        
        # Sync if requested
        if args.sync:
            portfolio = convert_to_portfolio_format(account, collapsed)
            save_portfolio(portfolio)
            print("\n⚠️  Note: kelly_optimal, target, and stop fields need manual evaluation")
        else:
            print("\nRun with --sync to save to portfolio.json")
    
    finally:
        ib.disconnect()
        print("✓ Disconnected from IB")


if __name__ == "__main__":
    main()
