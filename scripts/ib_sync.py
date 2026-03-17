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
from typing import Optional, Tuple

try:
    from ib_insync import util
except ImportError:
    print("ERROR: ib_insync not installed")
    print("Install with: pip install ib_insync")
    sys.exit(1)

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT

# Default connection settings
DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS["ib_sync"]

PORTFOLIO_PATH = Path(__file__).parent.parent / "data" / "portfolio.json"


def connect_ib(host: str, port: int, client_id: int | str = "auto") -> IBClient:
    """Connect to TWS/IB Gateway, return an IBClient."""
    client = IBClient()
    try:
        client.connect(host=host, port=port, client_id=client_id)
        print(f"✓ Connected to IB on {host}:{port}")
        return client
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Ensure TWS or IB Gateway is running")
        print("  2. Enable API connections in TWS: Configure > API > Settings")
        print("  3. Check 'Enable ActiveX and Socket Clients'")
        print("  4. Verify port matches (TWS Paper=7497, TWS Live=7496, Gateway=4001)")
        sys.exit(1)


ACCOUNT_TAGS = [
    'NetLiquidation', 'TotalCashValue',
    'UnrealizedPnL', 'RealizedPnL',
    'AccruedCash', 'NetDividend',
    'MaintMarginReq', 'ExcessLiquidity', 'BuyingPower',
    'AvailableFunds', 'Cushion',
]


def get_account_summary(client: IBClient) -> dict:
    """Fetch account summary from cached account values (instant, no round-trip).

    Uses ib.accountValues() which reads from ib_insync's internal cache
    (populated automatically at connect time) instead of ib.accountSummary()
    which makes a blocking round-trip (~200-700ms).
    """
    account_values = client.ib.accountValues()

    summary = {}
    for av in account_values:
        if av.tag in ACCOUNT_TAGS and av.currency == 'USD':
            summary[av.tag] = float(av.value)

    return summary


def get_pnl(client: IBClient, account: str = "") -> dict:
    """Fetch daily P&L via reqPnL. Polls up to 10s for data to arrive."""
    from ib_insync import util as ib_util

    def _valid(val):
        return val is not None and not ib_util.isNan(val)

    try:
        # IB requires a non-empty account string for reqPnL
        if not account:
            accounts = client.ib.managedAccounts()
            account = accounts[0] if accounts else ""
        pnl = client.get_pnl(account)
        # Poll until dailyPnL is non-NaN (IB streams this asynchronously)
        for _ in range(8):  # 8 x 1s = 8s max on top of the 2s in get_pnl
            if pnl and _valid(getattr(pnl, 'dailyPnL', None)):
                break
            client.sleep(1)

        result = {}
        if pnl and hasattr(pnl, 'dailyPnL'):
            daily = pnl.dailyPnL
            unrealized = pnl.unrealizedPnL
            realized = pnl.realizedPnL
            result['dailyPnL'] = float(daily) if _valid(daily) else None
            result['unrealizedPnL'] = float(unrealized) if _valid(unrealized) else None
            result['realizedPnL'] = float(realized) if _valid(realized) else None
        try:
            client.cancel_pnl(pnl)
        except Exception:
            pass  # ib_insync PnL unhashable in some versions
        return result
    except Exception as e:
        print(f"  Warning: reqPnL failed: {e}")
        return {}


def format_option_structure(contract, position) -> str:
    """Format option contract into readable structure string"""
    if contract.secType == 'OPT':
        right = 'Call' if contract.right == 'C' else 'Put'
        return f"{right} ${contract.strike} ({contract.lastTradeDateOrContractMonth})"
    elif contract.secType == 'STK':
        return f"Stock ({position} shares)"
    else:
        return contract.secType


def detect_structure_type(legs: list) -> Tuple[str, str]:
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
    stk_legs = [l for l in legs if l['secType'] == 'STK']
    
    if not opt_legs:
        return "Mixed", "unknown"
    
    # ── Covered Call Detection ──
    # Long stock + short call(s) in same ticker = covered call (defined risk)
    # Requires: stock shares >= short call contracts * 100
    if stk_legs and opt_legs:
        long_stock = [s for s in stk_legs if s['position'] > 0]
        short_calls = [o for o in opt_legs if o.get('right') == 'C' and o['position'] < 0]
        
        if long_stock and short_calls and len(opt_legs) == len(short_calls):
            total_shares = sum(s['position'] for s in long_stock)
            total_short_contracts = sum(abs(o['position']) for o in short_calls)
            shares_needed = total_short_contracts * 100
            
            if total_shares >= shares_needed:
                return "Covered Call", "defined"
            else:
                # Partially covered — still has naked exposure
                return "Partially Covered Call", "undefined"
    
    # Analyze leg composition
    calls = [l for l in opt_legs if l.get('right') == 'C']
    puts = [l for l in opt_legs if l.get('right') == 'P']
    long_legs = [l for l in opt_legs if l['position'] > 0]
    short_legs = [l for l in opt_legs if l['position'] < 0]
    
    # Synthetic or Risk Reversal: Short Put + Long Call (or vice versa)
    # Same strike = Synthetic Long/Short (behaves like stock)
    # Different strikes = Risk Reversal (directional bet with hedge)
    if len(puts) == 1 and len(calls) == 1:
        same_strike = puts[0].get('strike') == calls[0].get('strike')
        
        if puts[0]['position'] < 0 and calls[0]['position'] > 0:
            # Long Call + Short Put
            if same_strike:
                return "Synthetic Long", "undefined"
            return "Risk Reversal", "undefined"
        if puts[0]['position'] > 0 and calls[0]['position'] < 0:
            # Long Put + Short Call
            if same_strike:
                return "Synthetic Short", "undefined"
            return "Reverse Risk Reversal", "undefined"
        if puts[0]['position'] > 0 and calls[0]['position'] > 0:
            return "Strangle" if not same_strike else "Straddle", "defined"
    
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
    
    # ── All-long combos: fully defined risk ──
    # If every option leg is long (position > 0), max loss = total premium paid.
    # Examples: 2 long calls at different strikes, long call + long put (strangle handled above),
    #           3-leg all-long butterflies, etc.
    if opt_legs and all(l['position'] > 0 for l in opt_legs) and not stk_legs:
        if len(calls) > 0 and len(puts) == 0:
            return f"Long Call Combo ({len(opt_legs)} legs)", "defined"
        if len(puts) > 0 and len(calls) == 0:
            return f"Long Put Combo ({len(opt_legs)} legs)", "defined"
        return f"Long Combo ({len(opt_legs)} legs)", "defined"

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
    
    if "Covered Call" in structure_type:
        call_legs = sorted([l for l in opt_legs if l.get('right') == 'C'], key=lambda x: x.get('strike', 0))
        stk_legs = [l for l in legs if l.get('secType') == 'STK' or l.get('type') == 'Stock']
        shares = sum(abs(l.get('position', l.get('contracts', 0))) for l in stk_legs)
        if call_legs:
            strike = call_legs[0].get('strike', '?')
            return f"{structure_type} ${strike} ({int(shares)} shares)"
        return structure_type
    
    if "Synthetic" in structure_type:
        strike = next((l.get('strike') for l in opt_legs if l.get('right') in ('C', 'P')), '?')
        return f"{structure_type} ${strike}"
    
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


def _merge_covered_call_groups(groups: dict) -> dict:
    """
    Merge standalone short-call groups into same-ticker stock groups to form covered calls.
    
    IB returns stock and options as separate positions with different expiries,
    so they end up in separate (symbol, expiry) groups. This pass detects when
    a short-call-only group can be merged with a long-stock group for the same ticker,
    creating a covered call structure.
    
    Only merges if:
    1. The short call group contains ONLY short calls (no other option types)
    2. A stock group exists for the same ticker with long shares
    3. Stock shares >= short call contracts * 100 (fully covered)
    """
    from collections import defaultdict
    
    # Find all stock groups and short-call-only groups per ticker
    stock_groups = {}  # ticker -> (key, legs)
    short_call_groups = {}  # ticker -> [(key, legs), ...]
    
    for key, legs in groups.items():
        symbol = key[0]
        
        # Is this a stock-only group?
        if all(l['secType'] == 'STK' for l in legs):
            long_shares = sum(l['position'] for l in legs if l['position'] > 0)
            if long_shares > 0:
                stock_groups[symbol] = key
        
        # Is this a short-call-only group?
        elif all(l['secType'] == 'OPT' for l in legs):
            opt_legs = legs
            if all(l.get('right') == 'C' and l['position'] < 0 for l in opt_legs):
                if symbol not in short_call_groups:
                    short_call_groups[symbol] = []
                short_call_groups[symbol].append(key)
    
    # Merge matching pairs
    merged = dict(groups)  # copy
    for symbol, sc_keys in short_call_groups.items():
        if symbol not in stock_groups:
            continue
        
        stk_key = stock_groups[symbol]
        stk_legs = merged[stk_key]
        total_shares = sum(l['position'] for l in stk_legs if l['position'] > 0)
        
        for sc_key in sc_keys:
            sc_legs = merged.get(sc_key, [])
            total_short_contracts = sum(abs(l['position']) for l in sc_legs)
            shares_needed = total_short_contracts * 100
            
            if total_shares >= shares_needed:
                # Merge: combine legs into a single group keyed by the option expiry
                combined = list(stk_legs) + list(sc_legs)
                # Use the option expiry as the group key (more informative than N/A)
                merged[sc_key] = combined
                # Remove the stock group (now absorbed)
                del merged[stk_key]
                # Reduce available shares for any additional short call groups
                total_shares -= shares_needed
                break  # Only merge one short call group per stock group
    
    return merged


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
    
    # ── Second pass: merge covered calls ──
    # A standalone short call group + same-ticker stock group = covered call.
    # Merge them into a single group so detect_structure_type can identify it.
    groups = _merge_covered_call_groups(groups)
    
    collapsed = []
    position_id = 1
    
    for (symbol, expiry), legs in groups.items():
        structure_type, risk_profile = detect_structure_type(legs)
        structure_desc = format_structure_description(structure_type, legs)
        
        # Calculate aggregate values — sign-aware (short legs are credits)
        total_entry_cost = 0
        for leg in legs:
            if leg['position'] > 0:
                total_entry_cost += leg['entry_cost']
            else:
                total_entry_cost -= leg['entry_cost']

        known_mv = []
        is_market_price_calculated = False
        for leg in legs:
            mv = leg.get('marketValue')
            if mv is not None:
                sign = 1 if leg['position'] > 0 else -1
                known_mv.append(sign * mv)
                if leg.get('marketPriceIsCalculated'):
                    is_market_price_calculated = True
        total_market_value = sum(known_mv) if known_mv else None
        
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
        
        # Aggregate per-position daily P&L from IB's reqPnLSingle.
        # This is more accurate than WS close-based calculation because IB
        # correctly handles intraday additions (only overnight contracts use
        # yesterday's close; intraday adds use fill price as reference).
        ib_daily_pnl_parts = [leg.get('ibDailyPnl') for leg in legs]
        if all(p is not None for p in ib_daily_pnl_parts):
            ib_daily_pnl = round(sum(ib_daily_pnl_parts), 2)
        else:
            ib_daily_pnl = None

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
                "market_value": leg.get('marketValue'),
                "market_price_is_calculated": bool(leg.get('marketPriceIsCalculated'))
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
            "max_risk": round(max_risk, 2) if max_risk is not None else None,
            "market_value": round(total_market_value, 2) if total_market_value is not None else None,
            "market_price_is_calculated": bool(is_market_price_calculated) if total_market_value is not None else False,
            "ib_daily_pnl": ib_daily_pnl,
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


def _normalize_market_price(raw_price) -> Optional[float]:
    """Return a valid market price or None when IB provides unusable values."""
    if raw_price is None:
        return None
    if util.isNan(raw_price):
        return None
    if raw_price < 0:
        return None
    return float(raw_price)


def _resolve_market_price(market_price: Optional[float], bid: Optional[float], ask: Optional[float], close: Optional[float] = None) -> Tuple[Optional[float], bool]:
    """Return a usable price and whether it was calculated.

    Fallback chain: marketPrice → midpoint(bid, ask) → close.
    The close fallback handles degraded gateway states where live/delayed
    data is unavailable but the previous session's close is still cached.
    """
    if market_price is not None:
        return market_price, False
    if bid is not None and ask is not None:
        return round((bid + ask) / 2, 4), True
    if close is not None:
        return close, True
    return None, False


def fetch_positions(client: IBClient) -> list:
    """Fetch all positions from IB"""
    positions = client.get_positions()
    
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
            "conId": contract.conId,  # Needed for reqPnLSingle
            "contract": contract  # Keep for market data requests
        })
    
    return formatted


def fetch_market_prices(client: IBClient, positions: list) -> list:
    """Fetch current market prices for positions (batched for speed)"""
    # Request Delayed-Frozen data so closed-market queries return last known prices
    # Type 4 cascades: Live → Delayed → Frozen → Delayed-Frozen
    client.set_market_data_type(4)

    # Qualify all contracts at once
    contracts = [pos['contract'] for pos in positions]
    client.qualify_contracts(*contracts)

    # Request all market data simultaneously
    tickers = []
    for pos in positions:
        ticker = client.get_quote(pos['contract'])
        tickers.append(ticker)

    # Single sleep for all data to arrive
    client.sleep(3)

    # Read results and cancel
    for pos, ticker in zip(positions, tickers):
        market_price = _normalize_market_price(ticker.marketPrice())
        bid = _normalize_market_price(ticker.bid)
        ask = _normalize_market_price(ticker.ask)
        close = _normalize_market_price(ticker.close)
        price, is_calculated = _resolve_market_price(market_price, bid, ask, close)

        if price is not None:
            multiplier = 100 if pos['secType'] == 'OPT' else 1
            pos['marketPrice'] = price
            pos['marketValue'] = round(price * abs(pos['position']) * multiplier, 2)
            pos['marketPriceIsCalculated'] = is_calculated
        else:
            pos['marketPrice'] = None
            pos['marketValue'] = None
            pos['marketPriceIsCalculated'] = False
        client.cancel_market_data(pos['contract'])
        del pos['contract']  # Remove non-serializable contract object

    return positions


def fetch_position_daily_pnl(client: IBClient, positions: list, account: str = "") -> list:
    """Fetch IB's per-position daily P&L via reqPnLSingle (batched).
    
    IB correctly handles intraday additions — if you held 25 contracts
    overnight and bought 25 more today, IB's dailyPnL reflects:
      overnight_contracts × (current - yesterday_close) + 
      intraday_contracts × (current - fill_price)
    
    This is more accurate than our WS close-based calculation which
    assumes all contracts were held overnight.
    
    Performance: All PnL subscriptions are requested at once (no per-request
    sleep), then a single combined sleep waits for data to arrive.
    """
    from ib_insync import util as ib_util

    def _valid(val):
        return val is not None and not ib_util.isNan(val) and val != 1.7976931348623157e+308

    if not account:
        accounts = client.ib.managedAccounts()
        account = accounts[0] if accounts else ""

    if not account:
        return positions

    # Request PnL for all positions simultaneously — bypass IBClient's
    # get_pnl_single() which sleeps 0.5s per call, and call IB API directly.
    pnl_requests = []
    for pos in positions:
        con_id = pos.get('conId')
        if con_id:
            try:
                pnl_single = client.ib.reqPnLSingle(account, "", con_id)
                pnl_requests.append((pos, pnl_single, con_id))
            except Exception as e:
                print(f"  Warning: reqPnLSingle failed for {pos['symbol']} conId={con_id}: {e}")
                pnl_requests.append((pos, None, con_id))
        else:
            pnl_requests.append((pos, None, None))

    # Single combined sleep — all subscriptions are concurrent
    client.sleep(3)

    # Read results and cancel subscriptions
    for pos, pnl_single, con_id in pnl_requests:
        if pnl_single is not None:
            daily = getattr(pnl_single, 'dailyPnL', None)
            if _valid(daily):
                pos['ibDailyPnl'] = round(float(daily), 2)
            else:
                pos['ibDailyPnl'] = None
            # Cancel subscription
            if con_id:
                client.cancel_pnl_single(account, con_id)
        else:
            pos['ibDailyPnl'] = None

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
            if pos['max_risk'] is not None:
                print(f" | Max Risk: ${pos['max_risk']:,.2f}", end="")
            print()
            if pos.get('market_value') is not None:
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
                    if leg.get('market_value') is not None:
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
                if pos.get('marketValue') is not None:
                    pnl = pos['marketValue'] - pos['entry_cost']
                    pnl_pct = (pnl / pos['entry_cost'] * 100) if pos['entry_cost'] > 0 else 0
                    print(f"      Market Value: ${pos['marketValue']:,.2f} ({pnl_pct:+.1f}%)")
                if pos['expiry'] != "N/A":
                    print(f"      Expiry: {pos['expiry']}")
    
    print("\n" + "="*70)


def build_account_summary(account: dict, pnl_data: dict) -> dict:
    """Build account_summary dict from account values and PnL data.

    Tag mapping:
      settled_cash → TotalCashValue (not SettledCash — that tag doesn't exist in accountSummary)
      dividends    → NetDividend (not AccruedCash — that's margin interest)
      daily_pnl    → reqPnL().dailyPnL (only source for daily P&L)
    """
    def safe_float(val, default=0.0):
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # Prefer reqPnL for unrealized/realized (real-time), fall back to accountSummary
    unrealized = pnl_data.get('unrealizedPnL')
    if unrealized is None:
        unrealized = account.get('UnrealizedPnL')
    realized = pnl_data.get('realizedPnL')
    if realized is None:
        realized = account.get('RealizedPnL')

    return {
        "net_liquidation": safe_float(account.get('NetLiquidation')),
        "daily_pnl": pnl_data.get('dailyPnL'),  # None when unavailable, not 0
        "unrealized_pnl": safe_float(unrealized),
        "realized_pnl": safe_float(realized),
        "settled_cash": safe_float(account.get('TotalCashValue')),
        "maintenance_margin": safe_float(account.get('MaintMarginReq')),
        "excess_liquidity": safe_float(account.get('ExcessLiquidity')),
        "buying_power": safe_float(account.get('BuyingPower')),
        "dividends": safe_float(account.get('NetDividend')),
    }


def convert_to_portfolio_format(account: dict, collapsed_positions: list, pnl_data: Optional[dict] = None) -> dict:
    """Convert IB data to portfolio.json format using collapsed positions"""

    bankroll = account.get('NetLiquidation', account.get('TotalCashValue', 0))

    # Calculate totals from collapsed positions
    total_deployed = sum(p['entry_cost'] for p in collapsed_positions)
    deployed_pct = (total_deployed / bankroll * 100) if bankroll > 0 else 0

    # Add entry_date to positions
    for pos in collapsed_positions:
        pos['entry_date'] = datetime.now().strftime("%Y-%m-%d")  # IB doesn't provide this easily

    result = {
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
        "avg_kelly_optimal": None,  # Needs evaluation
        "account_summary": build_account_summary(account, pnl_data or {}),
    }

    return result


def save_portfolio(portfolio: dict):
    """Save portfolio to JSON file (atomic write with SHA-256 checksum)."""
    from utils.atomic_io import atomic_save

    # Backup existing
    if PORTFOLIO_PATH.exists():
        backup_path = PORTFOLIO_PATH.with_suffix('.json.bak')
        backup_path.write_text(PORTFOLIO_PATH.read_text())
        print(f"✓ Backed up existing portfolio to {backup_path.name}")

    checksum = atomic_save(str(PORTFOLIO_PATH), portfolio)
    print(f"✓ Saved portfolio to {PORTFOLIO_PATH} (checksum: {checksum[:12]}…)")


def main():
    parser = argparse.ArgumentParser(description="Sync portfolio from Interactive Brokers")
    parser.add_argument("--host", default=DEFAULT_HOST, help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, 
                        help="TWS/Gateway port (7497=paper, 7496=live, 4001=gateway)")
    parser.add_argument("--client-id", type=int, default=None, help="Client ID (omit for auto-allocation)")
    parser.add_argument("--sync", action="store_true", help="Sync to portfolio.json")
    parser.add_argument("--no-prices", action="store_true", help="Skip market price fetch")

    args = parser.parse_args()

    # Connect
    client = connect_ib(args.host, args.port, args.client_id or "auto")

    try:
        # ── Phase 1: Account summary (fast, no sleep needed) ──
        print("Fetching account summary...")
        account = get_account_summary(client)

        # ── Phase 2: Request account PnL + positions concurrently ──
        # reqPnL is a subscription — request it, then do other work while it streams
        print("Fetching P&L + positions...")
        from ib_insync import util as ib_util

        def _valid_pnl(val):
            return val is not None and not ib_util.isNan(val)

        accounts = client.ib.managedAccounts()
        ib_account = accounts[0] if accounts else ""
        pnl_obj = client.ib.reqPnL(ib_account) if ib_account else None

        # Fetch positions while PnL streams
        positions = fetch_positions(client)

        if not args.no_prices and positions:
            # ── Phase 3: Set exchange + request ALL data at once ──
            # Contracts from get_positions() already have conId but lack exchange.
            # Setting exchange='SMART' avoids the 1s qualifyContracts() round-trip.
            print("Requesting market data + per-position PnL...")
            client.set_market_data_type(4)
            for pos in positions:
                # Force SMART for all — stocks from get_positions() may have
                # exchange-specific values (AMEX, BATS) that fail with reqMktData type 4
                pos['contract'].exchange = 'SMART'

            # Request PnL Single FIRST (takes slightly longer to arrive)
            pnl_requests = []
            if ib_account:
                for pos in positions:
                    con_id = pos.get('conId')
                    if con_id:
                        try:
                            pnl_single = client.ib.reqPnLSingle(ib_account, "", con_id)
                            pnl_requests.append((pos, pnl_single, con_id))
                        except Exception as e:
                            print(f"  Warning: reqPnLSingle failed for {pos['symbol']} conId={con_id}: {e}")
                            pnl_requests.append((pos, None, con_id))
                    else:
                        pnl_requests.append((pos, None, None))

            # Then request market data — use ib.reqMktData directly
            # (bypasses subscription tracking in IBClient.get_quote)
            tickers = []
            for pos in positions:
                ticker = client.ib.reqMktData(pos['contract'], "", False, False)
                tickers.append(ticker)

            # ── Phase 4: ONE combined sleep for all streaming data ──
            # Market data + PnL Single + account PnL all stream concurrently.
            # 2.7 seconds — accounts for the faster Phase 1 (accountValues is instant
            # vs accountSummary's ~200ms round-trip that used to provide implicit delay).
            client.sleep(2.5)

            # ── Phase 5: Read all results ──
            # Market prices
            for pos, ticker in zip(positions, tickers):
                market_price = _normalize_market_price(ticker.marketPrice())
                bid = _normalize_market_price(ticker.bid)
                ask = _normalize_market_price(ticker.ask)
                close = _normalize_market_price(ticker.close)
                price, is_calculated = _resolve_market_price(market_price, bid, ask, close)

                if price is not None:
                    multiplier = 100 if pos['secType'] == 'OPT' else 1
                    pos['marketPrice'] = price
                    pos['marketValue'] = round(price * abs(pos['position']) * multiplier, 2)
                    pos['marketPriceIsCalculated'] = is_calculated
                else:
                    pos['marketPrice'] = None
                    pos['marketValue'] = None
                    pos['marketPriceIsCalculated'] = False
                client.ib.cancelMktData(pos['contract'])
                del pos['contract']

            # Per-position PnL
            def _valid_daily(val):
                return val is not None and not ib_util.isNan(val) and val != 1.7976931348623157e+308

            for pos, pnl_single, con_id in pnl_requests:
                if pnl_single is not None:
                    daily = getattr(pnl_single, 'dailyPnL', None)
                    if _valid_daily(daily):
                        pos['ibDailyPnl'] = round(float(daily), 2)
                    else:
                        pos['ibDailyPnl'] = None
                    if con_id:
                        client.cancel_pnl_single(ib_account, con_id)
                else:
                    pos['ibDailyPnl'] = None
        else:
            # No prices requested
            for pos in positions:
                if 'contract' in pos:
                    del pos['contract']
                pos['ibDailyPnl'] = None

        # ── Phase 6: Read account PnL (should have arrived during the combined sleep) ──
        pnl_data = {}
        if pnl_obj:
            # reqPnL subscription started in Phase 2 — has had 2.7s+ to arrive.
            # No fallback sleep: if data isn't here by now, accept None for
            # account-level daily_pnl (per-position PnL is independent).
            daily = pnl_obj.dailyPnL
            unrealized = pnl_obj.unrealizedPnL
            realized = pnl_obj.realizedPnL
            pnl_data['dailyPnL'] = float(daily) if _valid_pnl(daily) else None
            pnl_data['unrealizedPnL'] = float(unrealized) if _valid_pnl(unrealized) else None
            pnl_data['realizedPnL'] = float(realized) if _valid_pnl(realized) else None
            try:
                client.cancel_pnl(pnl_obj)
            except Exception:
                pass

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
            portfolio = convert_to_portfolio_format(account, collapsed, pnl_data)
            save_portfolio(portfolio)
            print("\n⚠️  Note: kelly_optimal, target, and stop fields need manual evaluation")
        else:
            print("\nRun with --sync to save to portfolio.json")

    finally:
        client.disconnect()
        print("✓ Disconnected from IB")


if __name__ == "__main__":
    main()
