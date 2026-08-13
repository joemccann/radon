#!/usr/bin/env python3
"""
Interactive Brokers Portfolio Sync

Connects to TWS/IB Gateway and syncs live positions to Turso.

Requirements:
  pip install ib_insync

Usage:
  python3 scripts/ib_sync.py              # Display portfolio
  python3 scripts/ib_sync.py --sync       # Sync to Turso portfolio_snapshots
  python3 scripts/ib_sync.py --port 7497  # Custom port (7497=TWS paper, 7496=TWS live, 4001=Gateway)
"""

import argparse
import fcntl
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

# Load env files BEFORE importing modules that read env at import time
# (e.g. db.client requires TURSO_DB_URL / TURSO_AUTH_TOKEN). When this script
# runs as a FastAPI subprocess the parent's env is not guaranteed to include
# them, which previously made the Phase-3 dual-write to portfolio_snapshots
# silently fail and left the Turso UI snapshot stale.
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    # Project-level .env first, then the IB-mode overlay, then web/.env which
    # is where TURSO_DB_URL / TURSO_AUTH_TOKEN actually live on Hetzner.
    load_dotenv(_PROJECT_DIR / ".env")
    load_dotenv(_PROJECT_DIR / ".env.ib-mode")
    load_dotenv(_PROJECT_DIR / "web" / ".env")
except Exception:
    pass

try:
    from ib_insync import util
except ImportError:
    print("ERROR: ib_insync not installed")
    print("Install with: pip install ib_insync")
    sys.exit(1)

from clients.ib_client import IBClient, CLIENT_IDS, DEFAULT_HOST, DEFAULT_GATEWAY_PORT
from clients.journal_basis import (
    compute_open_basis_for_ticker,
    prior_net_qty_for_contract,
)
from db.client import get_db
from db.readers import (
    read_journal_entry_date_maps,
    read_latest_portfolio_snapshot,
    read_open_orders,
)

# Default connection settings
DEFAULT_PORT = DEFAULT_GATEWAY_PORT
DEFAULT_CLIENT_ID = CLIENT_IDS["ib_sync"]

DATA_DIR = Path(__file__).parent.parent / "data"


def connect_ib(host: str, port: int, client_id="auto") -> IBClient:
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


def _select_managed_account(accounts: list[str], requested: str = "") -> str:
    """Select one IB account so positions, margin, basis, and PnL share scope."""
    managed = list(dict.fromkeys(account for account in accounts if account))
    if requested:
        if requested not in managed:
            raise ValueError(f"requested account {requested!r} is not managed by this session")
        return requested
    if len(managed) > 1:
        raise ValueError("multiple managed accounts are visible; pass --account explicitly")
    return managed[0] if managed else ""


ACCOUNT_TAGS = [
    'NetLiquidation', 'TotalCashValue', 'SettledCash',
    'UnrealizedPnL', 'RealizedPnL',
    'AccruedCash', 'NetDividend',
    'MaintMarginReq', 'InitMarginReq', 'ExcessLiquidity', 'BuyingPower',
    'AvailableFunds', 'Cushion',
    'EquityWithLoanValue', 'PreviousDayEquityWithLoanValue',
    'RegTEquity', 'SMA', 'GrossPositionValue',
]


def get_account_summary(client: IBClient, account_id: str = "") -> dict:
    """Fetch account summary from cached account values (instant, no round-trip).

    Uses ib.accountValues() which reads from ib_insync's internal cache
    (populated automatically at connect time) instead of ib.accountSummary()
    which makes a blocking round-trip (~200-700ms).
    """
    account_values = client.ib.accountValues()

    summary = {}
    for av in account_values:
        if account_id and getattr(av, "account", "") != account_id:
            continue
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
    long_calls = [l for l in calls if l['position'] > 0]
    short_calls = [l for l in calls if l['position'] < 0]
    long_puts = [l for l in puts if l['position'] > 0]
    short_puts = [l for l in puts if l['position'] < 0]
    
    # Helper: detect if leg contract counts differ (ratio position)
    def _is_ratio(leg_a, leg_b):
        return abs(leg_a['position']) != abs(leg_b['position'])

    def _has_equal_contract_counts(candidate_legs):
        return len({abs(l['position']) for l in candidate_legs}) == 1

    # Three-leg risk reversal spreads:
    # - Bearish: short call + long higher-strike put + short lower-strike put.
    # - Bullish: short put + long lower-strike call + short higher-strike call.
    if len(opt_legs) == 3 and not stk_legs and _has_equal_contract_counts(opt_legs):
        if len(short_calls) == 1 and len(long_puts) == 1 and len(short_puts) == 1:
            if long_puts[0].get('strike', 0) > short_puts[0].get('strike', 0):
                return "Bearish Risk Reversal Bear Spread", "undefined"

        if len(short_puts) == 1 and len(long_calls) == 1 and len(short_calls) == 1:
            if long_calls[0].get('strike', 0) < short_calls[0].get('strike', 0):
                return "Bullish Risk Reversal Call Spread", "undefined"

    # Synthetic or Risk Reversal: Short Put + Long Call (or vice versa)
    # Same strike = Synthetic Long/Short (behaves like stock)
    # Different strikes = Risk Reversal (directional bet with hedge)
    if len(puts) == 1 and len(calls) == 1:
        same_strike = puts[0].get('strike') == calls[0].get('strike')
        ratio_prefix = "Ratio " if _is_ratio(puts[0], calls[0]) else ""

        if puts[0]['position'] < 0 and calls[0]['position'] > 0:
            # Long Call + Short Put
            if same_strike:
                return f"{ratio_prefix}Synthetic Long", "undefined"
            return f"{ratio_prefix}Risk Reversal", "undefined"
        if puts[0]['position'] > 0 and calls[0]['position'] < 0:
            # Long Put + Short Call
            if same_strike:
                return f"{ratio_prefix}Synthetic Short", "undefined"
            return f"{ratio_prefix}Reverse Risk Reversal", "undefined"
        if puts[0]['position'] > 0 and calls[0]['position'] > 0:
            return f"{ratio_prefix}Long {'Straddle' if same_strike else 'Strangle'}", "defined"
        if puts[0]['position'] < 0 and calls[0]['position'] < 0:
            return f"{ratio_prefix}Short {'Straddle' if same_strike else 'Strangle'}", "undefined"

    # Vertical Spreads: Same type, different strikes, opposite directions
    if len(calls) == 2 and len(puts) == 0:
        if len(long_legs) == 1 and len(short_legs) == 1:
            ratio_prefix = "Ratio " if _is_ratio(long_legs[0], short_legs[0]) else ""
            long_strike = long_legs[0].get('strike', 0)
            short_strike = short_legs[0].get('strike', 0)
            if long_strike < short_strike:
                return f"{ratio_prefix}Bull Call Spread", "defined" if not ratio_prefix else "undefined"
            else:
                return f"{ratio_prefix}Bear Call Spread", "defined" if not ratio_prefix else "undefined"

    if len(puts) == 2 and len(calls) == 0:
        if len(long_legs) == 1 and len(short_legs) == 1:
            ratio_prefix = "Ratio " if _is_ratio(long_legs[0], short_legs[0]) else ""
            long_strike = long_legs[0].get('strike', 0)
            short_strike = short_legs[0].get('strike', 0)
            if long_strike > short_strike:
                return f"{ratio_prefix}Bear Put Spread", "defined" if not ratio_prefix else "undefined"
            else:
                return f"{ratio_prefix}Bull Put Spread", "defined" if not ratio_prefix else "undefined"
    
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


def _raw_ratio_label(legs: list) -> str:
    """Compute raw NxM contract counts for two legs, e.g. '75x10'."""
    if len(legs) != 2:
        return ""
    a = int(abs(legs[0].get('position', legs[0].get('contracts', 0))))
    b = int(abs(legs[1].get('position', legs[1].get('contracts', 0))))
    if a == b or a == 0 or b == 0:
        return ""
    return f"{a}x{b}"


def _ratio_label(legs: list) -> str:
    """Compute normalized NxM ratio label from leg contract counts, e.g. '1x2'.

    Returns empty string if legs have equal counts (not a ratio).
    Reduces to smallest integer ratio via GCD.
    """
    if len(legs) != 2:
        return ""
    from math import gcd
    a, b = int(abs(legs[0].get('position', legs[0].get('contracts', 0)))), int(abs(legs[1].get('position', legs[1].get('contracts', 0))))
    if a == b or a == 0 or b == 0:
        return ""
    g = gcd(a, b)
    return f"{a // g}x{b // g}"


def _display_ratio_label(structure_type: str, opt_legs: list, all_legs: list) -> str:
    """Return the display ratio label for a structure.

    Risk reversals/synthetics should show actual contract counts in displayed
    long-short leg order so the label matches the expanded leg rows and top-row
    quantity. Other ratio structures keep the normalized NxM form.
    """
    if "Ratio" not in structure_type:
        return ""

    if "Risk Reversal" in structure_type or "Synthetic" in structure_type:
        long_legs = [l for l in all_legs if l.get('secType') == 'OPT' and l.get('position', 0) > 0]
        short_legs = [l for l in all_legs if l.get('secType') == 'OPT' and l.get('position', 0) < 0]
        if len(long_legs) == 1 and len(short_legs) == 1:
            return _raw_ratio_label([long_legs[0], short_legs[0]])

    return _ratio_label(opt_legs)


def format_structure_description(structure_type: str, legs: list) -> str:
    """Create human-readable structure description with strikes"""
    if structure_type == "Stock":
        return legs[0]['structure']

    opt_legs = sorted([l for l in legs if l['secType'] == 'OPT'],
                      key=lambda x: x.get('strike', 0))

    if not opt_legs:
        return structure_type

    ratio = _display_ratio_label(structure_type, opt_legs, legs)
    ratio_suffix = f" {ratio}" if ratio else ""

    def _strike_text(value) -> str:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:g}"
        return str(value)

    if "Risk Reversal Bear Spread" in structure_type:
        put_legs = sorted([l for l in opt_legs if l.get('right') == 'P'], key=lambda x: x.get('strike', 0))
        call_leg = next((l for l in opt_legs if l.get('right') == 'C'), None)
        if len(put_legs) == 2 and call_leg:
            low_put, high_put = put_legs
            return (
                f"{structure_type}{ratio_suffix} "
                f"(P${_strike_text(low_put.get('strike', '?'))}/${_strike_text(high_put.get('strike', '?'))}"
                f"/C${_strike_text(call_leg.get('strike', '?'))})"
            )

    if "Risk Reversal Call Spread" in structure_type:
        call_legs = sorted([l for l in opt_legs if l.get('right') == 'C'], key=lambda x: x.get('strike', 0))
        put_leg = next((l for l in opt_legs if l.get('right') == 'P'), None)
        if len(call_legs) == 2 and put_leg:
            low_call, high_call = call_legs
            return (
                f"{structure_type}{ratio_suffix} "
                f"(P${_strike_text(put_leg.get('strike', '?'))}"
                f"/C${_strike_text(low_call.get('strike', '?'))}/${_strike_text(high_call.get('strike', '?'))})"
            )

    if "Spread" in structure_type:
        strikes = [l.get('strike') for l in opt_legs]
        return f"{structure_type}{ratio_suffix} ${min(strikes)}/${max(strikes)}"

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
        return f"{structure_type}{ratio_suffix} ${strike}"

    if "Risk Reversal" in structure_type:
        put_strike = next((l.get('strike') for l in opt_legs if l.get('right') == 'P'), '?')
        call_strike = next((l.get('strike') for l in opt_legs if l.get('right') == 'C'), '?')
        return f"{structure_type}{ratio_suffix} (P${put_strike}/C${call_strike})"

    if structure_type.endswith("Straddle") or structure_type.endswith("Strangle"):
        strikes = [l.get('strike') for l in opt_legs]
        if len(set(strikes)) == 1:
            return f"{structure_type}{ratio_suffix} ${strikes[0]}"
        return f"{structure_type}{ratio_suffix} ${min(strikes)}/${max(strikes)}"

    # Single-leg options: Short Put, Short Call, Long Put, Long Call
    if len(opt_legs) == 1:
        strike = opt_legs[0].get('strike', '?')
        return f"{structure_type} ${strike}"

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
        account_id, symbol, _expiry = key
        
        # Is this a stock-only group?
        if all(l['secType'] == 'STK' for l in legs):
            long_shares = sum(l['position'] for l in legs if l['position'] > 0)
            if long_shares > 0:
                stock_groups[(account_id, symbol)] = key
        
        # Is this a short-call-only group?
        elif all(l['secType'] == 'OPT' for l in legs):
            opt_legs = legs
            if all(l.get('right') == 'C' and l['position'] < 0 for l in opt_legs):
                short_call_groups.setdefault((account_id, symbol), []).append(key)
    
    # Merge matching pairs
    merged = dict(groups)  # copy
    for account_symbol, sc_keys in short_call_groups.items():
        if account_symbol not in stock_groups:
            continue

        stk_key = stock_groups[account_symbol]
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
        key = (pos.get('account_id') or '', pos['symbol'], pos['expiry'])
        groups[key].append(pos)
    
    # ── Second pass: merge covered calls ──
    # A standalone short call group + same-ticker stock group = covered call.
    # Merge them into a single group so detect_structure_type can identify it.
    groups = _merge_covered_call_groups(groups)
    
    collapsed = []
    position_id = 1
    
    for (account_id, symbol, expiry), legs in groups.items():
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
        num_legs = len(legs)
        if structure_type == "Stock":
            direction = "LONG" if net_position > 0 else "SHORT"
        elif "Spread" in structure_type and "Risk Reversal" not in structure_type:
            direction = "DEBIT" if total_entry_cost > 0 else "CREDIT"
        elif num_legs > 1 and risk_profile == "undefined":
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
                "con_id": leg.get('conId'),
                "currency": leg.get('currency'),
                "multiplier": leg.get('multiplier'),
                "direction": "LONG" if leg['position'] > 0 else "SHORT",
                "contracts": int(abs(leg['position'])),
                "type": "Call" if leg.get('right') == 'C' else ("Put" if leg.get('right') == 'P' else "Stock"),
                "strike": leg.get('strike'),
                "entry_cost": leg['entry_cost'],
                "avg_cost": leg['avgCost'],
                "ib_avg_cost": leg.get('ibAvgCost'),
                "market_price": leg.get('marketPrice'),
                "market_value": leg.get('marketValue'),
                "market_price_is_calculated": bool(leg.get('marketPriceIsCalculated'))
            })
        
        collapsed.append({
            "id": position_id,
            "account_id": account_id,
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
    try:
        price = float(raw_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0 or price >= 1e307:
        return None
    return price


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


def _journal_basis_key(symbol: str, expiry, right, strike) -> Optional[str]:
    digits = "".join(ch for ch in str(expiry or "") if ch.isdigit())
    if len(digits) == 6:
        digits = f"20{digits}"
    if len(digits) != 8:
        return None

    right_value = str(right or "").strip().upper()[:1]
    if right_value not in {"C", "P"}:
        return None

    try:
        strike_value = float(strike)
    except (TypeError, ValueError):
        return None

    symbol_value = str(symbol or "").strip().upper()
    if not symbol_value:
        return None

    return f"{symbol_value}|{digits}|{right_value}|{strike_value}"


def build_journal_basis_lookup(client: IBClient, db=None) -> dict[str, float]:
    """Build a per-contract open-basis lookup from raw journal rows.

    Also records the journal net qty for each option contract on the returned
    dict's ``net_qty`` sidecar so ``fetch_positions`` can reject an override
    whose journal basis reflects fewer fills than the live position holds
    (the MU 1050 C incomplete-journal class). ``db`` is injectable for tests.
    """
    lookup: dict[str, float] = {}
    net_qty_lookup: dict[str, float] = {}

    try:
        raw_positions = client.get_positions()
    except Exception as exc:
        print(f"  Warning: journal basis prefetch skipped, positions unavailable: {exc}")
        return _with_net_qty(lookup, net_qty_lookup)

    option_positions = [
        pos
        for pos in raw_positions
        if getattr(getattr(pos, "contract", None), "secType", None) == "OPT"
        and getattr(getattr(pos, "contract", None), "symbol", None)
    ]
    if not option_positions:
        return _with_net_qty(lookup, net_qty_lookup)

    if db is None:
        try:
            db = get_db()
        except Exception as exc:
            print(f"  Warning: journal basis unavailable, falling back to IB avgCost: {exc}")
            return _with_net_qty(lookup, net_qty_lookup)

    tickers = sorted({str(pos.contract.symbol).strip().upper() for pos in option_positions})
    for ticker in tickers:
        try:
            lookup.update(compute_open_basis_for_ticker(db, ticker))
        except Exception as exc:
            print(f"  Warning: journal basis lookup failed for {ticker}: {exc}")

    for pos in option_positions:
        contract = pos.contract
        journal_key = _journal_basis_key(
            contract.symbol,
            getattr(contract, "lastTradeDateOrContractMonth", None),
            getattr(contract, "right", None),
            getattr(contract, "strike", None),
        )
        if not journal_key or journal_key in net_qty_lookup:
            continue
        try:
            net_qty_lookup[journal_key] = prior_net_qty_for_contract(
                db,
                ticker=contract.symbol,
                sec_type=contract.secType,
                strike=getattr(contract, "strike", None),
                right=getattr(contract, "right", None),
                expiry=getattr(contract, "lastTradeDateOrContractMonth", None),
            )
        except Exception as exc:
            print(f"  Warning: journal net-qty lookup failed for {journal_key}: {exc}")

    return _with_net_qty(lookup, net_qty_lookup)


class _BasisLookup(dict):
    """Open-basis dict that also carries a per-key journal net-qty sidecar."""

    net_qty: dict[str, float]


def _with_net_qty(lookup: dict, net_qty_lookup: dict) -> "_BasisLookup":
    enriched = _BasisLookup(lookup)
    enriched.net_qty = net_qty_lookup
    return enriched


def fetch_positions(client: IBClient, journal_basis_lookup: Optional[dict[str, float]] = None) -> list:
    """Fetch all positions from IB"""
    positions = client.get_positions()
    journal_basis_lookup = journal_basis_lookup or {}
    journal_net_qty_lookup = getattr(journal_basis_lookup, "net_qty", {}) or {}

    formatted = []
    for pos in positions:
        contract = pos.contract
        
        # Calculate position value
        avg_cost = pos.avgCost
        ib_avg_cost = pos.avgCost
        position_size = pos.position
        
        # For options, avgCost is per share, multiply by 100 for per contract
        if contract.secType == 'OPT':
            entry_cost = abs(avg_cost * position_size)  # Already multiplied by multiplier internally
        else:
            entry_cost = abs(avg_cost * position_size)

        journal_key = _journal_basis_key(
            contract.symbol,
            getattr(contract, 'lastTradeDateOrContractMonth', None),
            getattr(contract, 'right', None),
            getattr(contract, 'strike', None),
        )
        if journal_key and journal_key in journal_basis_lookup and position_size != 0:
            journal_net = journal_net_qty_lookup.get(journal_key)
            # SIGNED equality: |+10| == |-10| let a sign-flipped journal
            # apply a long lot's basis to a short position (T-022). A
            # journal that disagrees on DIRECTION is as incomplete as one
            # that disagrees on size. None = legacy lookup without net-qty.
            basis_is_complete = (
                journal_net is None
                or journal_net == position_size
            )
            if basis_is_complete:
                entry_cost = abs(float(journal_basis_lookup[journal_key]))
                avg_cost = entry_cost / abs(position_size)
            else:
                print(
                    f"  Warning: journal basis SKIPPED for {journal_key}: "
                    f"journal_net_qty={journal_net} != position_qty={position_size} "
                    f"— keeping IB avgCost (incomplete journal)"
                )
        
        formatted.append({
            "account_id": getattr(pos, 'account', '') or '',
            "symbol": contract.symbol,
            "secType": contract.secType,
            "position": position_size,
            "avgCost": avg_cost,
            "ibAvgCost": ib_avg_cost,
            "entry_cost": round(entry_cost, 2),
            "expiry": parse_expiry(contract),
            "strike": getattr(contract, 'strike', None),
            "right": getattr(contract, 'right', None),
            "structure": format_option_structure(contract, position_size),
            "conId": contract.conId,  # Needed for reqPnLSingle
            "currency": getattr(contract, 'currency', '') or 'USD',
            "multiplier": float(getattr(contract, 'multiplier', '') or (100 if contract.secType == 'OPT' else 1)),
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

    Tag mapping (IB accountValues tags → Radon fields):
      net_liquidation  → NetLiquidation
      cash             → TotalCashValue (unsettled + settled)
      settled_cash     → SettledCash (falls back to TotalCashValue if IB omits)
      dividends        → NetDividend (not AccruedCash — that's margin interest)
      daily_pnl        → reqPnL().dailyPnL (only source for daily P&L)
      equity_with_loan → EquityWithLoanValue
      previous_day_ewl → PreviousDayEquityWithLoanValue
      reg_t_equity     → RegTEquity
      sma              → SMA (Special Memorandum Account)
      gross_position_value → GrossPositionValue
      available_funds  → AvailableFunds
      initial_margin   → InitMarginReq
      maintenance_margin → MaintMarginReq
      excess_liquidity → ExcessLiquidity
      buying_power     → BuyingPower
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

    # SettledCash may not always be present; fall back to TotalCashValue
    settled = account.get('SettledCash')
    if settled is None:
        settled = account.get('TotalCashValue')

    return {
        "net_liquidation": safe_float(account.get('NetLiquidation')),
        "daily_pnl": pnl_data.get('dailyPnL'),  # None when unavailable, not 0
        "unrealized_pnl": safe_float(unrealized),
        "realized_pnl": safe_float(realized),
        "cash": safe_float(account.get('TotalCashValue')),
        "settled_cash": safe_float(settled),
        "maintenance_margin": safe_float(account.get('MaintMarginReq')),
        "initial_margin": safe_float(account.get('InitMarginReq')),
        "excess_liquidity": safe_float(account.get('ExcessLiquidity')),
        "buying_power": safe_float(account.get('BuyingPower')),
        "available_funds": safe_float(account.get('AvailableFunds')),
        "dividends": safe_float(account.get('NetDividend')),
        "equity_with_loan": safe_float(account.get('EquityWithLoanValue')),
        "previous_day_ewl": safe_float(account.get('PreviousDayEquityWithLoanValue')),
        "reg_t_equity": safe_float(account.get('RegTEquity')),
        "sma": safe_float(account.get('SMA')),
        "gross_position_value": safe_float(account.get('GrossPositionValue')),
    }


def build_fill_dates(client) -> dict:
    """Extract earliest execution date per contract from IB session fills.

    Returns dict keyed by "TICKER|EXPIRY|RIGHT|STRIKE" → "YYYY-MM-DD".
    Covers same-session trades that haven't appeared in Flex Query (blotter) yet.
    """
    fill_dates: dict[str, str] = {}
    try:
        fills = client.get_fills()
    except Exception:
        return fill_dates

    for fill in fills:
        contract = fill.contract
        if contract.secType != "OPT":
            continue
        ticker = contract.symbol
        expiry_raw = getattr(contract, "lastTradeDateOrContractMonth", "")
        if not expiry_raw or len(expiry_raw) < 8:
            continue
        expiry = f"{expiry_raw[:4]}-{expiry_raw[4:6]}-{expiry_raw[6:8]}"
        right = getattr(contract, "right", "")
        strike = getattr(contract, "strike", None)
        if not right or strike is None:
            continue
        key = f"{ticker}|{expiry}|{right}|{float(strike)}"

        exec_time = getattr(fill.execution, "time", None)
        if exec_time is None:
            continue
        if hasattr(exec_time, "strftime"):
            date_str = exec_time.strftime("%Y-%m-%d")
        else:
            date_str = str(exec_time)[:10]

        if key not in fill_dates or date_str < fill_dates[key]:
            fill_dates[key] = date_str

    return fill_dates


def _basis_carry_key(ticker, structure, expiry) -> str:
    """Size-independent key for same-side basis carry-forward. A stock's
    `structure` embeds the share count ("Stock (-1000.0 shares)"), which changes
    on a partial close, so collapse it to "Stock" — otherwise the pre/post-cover
    snapshots never share a key. Option structures are strike-based and already
    count-independent."""
    s = structure or ""
    if s.startswith("Stock"):
        s = "Stock"
    return f"{ticker}|{s}|{expiry}"


def convert_to_portfolio_format(account: dict, collapsed_positions: list, pnl_data: Optional[dict] = None, fill_dates: Optional[dict] = None) -> dict:
    """Convert IB data to the portfolio snapshot payload."""

    bankroll = account.get('NetLiquidation', account.get('TotalCashValue', 0))

    # Calculate totals from collapsed positions
    total_deployed = sum(p['entry_cost'] for p in collapsed_positions)
    deployed_pct = (total_deployed / bankroll * 100) if bankroll > 0 else 0

    # Derive entry_date from journal rows and the previous portfolio snapshot.
    # Priority: journal per-contract date → journal ticker+structure date →
    # IB fills → previous portfolio → today (truly new position).
    #
    # `today` MUST be the ET trading day, not the host's local day. On Hetzner
    # (UTC host) `datetime.now()` after 20:00 ET is already the next calendar
    # day in UTC, so a brand-new position opened at 21:00 ET would get stamped
    # with tomorrow's ET date and the same-day P&L branch on the frontend
    # (web/lib/positionUtils.ts:isSameDay) would miss it. Always use ET.
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    try:
        trade_log_dates, blotter_dates = read_journal_entry_date_maps()
    except Exception:
        trade_log_dates, blotter_dates = {}, {}

    # Previous portfolio dates + per-unit basis (fallback)
    prev_dates: dict[str, str] = {}
    prev_basis: dict[str, dict] = {}
    try:
        prev = read_latest_portfolio_snapshot() or {}
        for p in prev.get("positions", []):
            key = f"{p.get('ticker')}|{p.get('structure')}|{p.get('expiry')}"
            ed = p.get("entry_date", "")
            # Only carry forward dates that aren't today (avoids inheriting
            # the old bug where every sync set entry_date = today)
            if ed and ed != today:
                prev_dates[key] = ed
            # Per-unit basis for same-side reduce carry-forward (a partial close
            # must NOT change per-unit basis; IB drifts pos.avgCost on a reduce
            # and assignment-originated stock has no journal opener).
            try:
                pc = abs(float(p.get("contracts") or 0))
                pec = abs(float(p.get("entry_cost") or 0))
                pdir = (p.get("direction") or "").upper()
                if pc > 0 and pec > 0 and pdir in ("LONG", "SHORT"):
                    prev_basis[_basis_carry_key(p.get("ticker"), p.get("structure"), p.get("expiry"))] = {
                        "direction": pdir, "contracts": pc, "per_unit": pec / pc
                    }
            except (TypeError, ValueError):
                pass
    except Exception:
        pass

    for pos in collapsed_positions:
        key = f"{pos.get('ticker')}|{pos.get('structure')}|{pos.get('expiry')}"
        ticker = pos.get("ticker", "")
        structure = pos.get("structure", "")
        expiry = pos.get("expiry", "")

        # Build per-contract blotter keys from collapsed option legs.
        # Collapsed portfolio legs use the UI-facing `type` field ("Call"/"Put"),
        # not the raw IB `secType`/`right` shape, so derive the contract keys
        # from those normalized fields.
        blotter_contract_date = None
        legs = pos.get("legs", [])
        contract_dates = []
        for leg in legs:
            leg_type = leg.get("type")
            strike = leg.get("strike")
            if leg_type not in ("Call", "Put") or strike in (None, 0):
                continue
            right = "C" if leg_type == "Call" else "P"
            contract_key = f"{ticker}|{expiry}|{right}|{float(strike)}"
            contract_date = blotter_dates.get(contract_key)
            if contract_date:
                contract_dates.append(contract_date)
        if contract_dates and len(contract_dates) == len([
            leg for leg in legs
            if leg.get("type") in ("Call", "Put") and leg.get("strike") not in (None, 0)
        ]):
            blotter_contract_date = min(contract_dates)

        # IB fill dates (same-session trades not yet in journal)
        fill_contract_date = None
        if fill_dates:
            for leg in legs:
                leg_type = leg.get("type")
                strike = leg.get("strike")
                if leg_type not in ("Call", "Put") or strike in (None, 0):
                    continue
                right = "C" if leg_type == "Call" else "P"
                fill_key = f"{ticker}|{expiry}|{right}|{float(strike)}"
                fd = fill_dates.get(fill_key)
                if fd:
                    fill_contract_date = min(fill_contract_date, fd) if fill_contract_date else fd

        # Fallback chain — ORDERED FROM MOST → LEAST SPECIFIC. Anything weaker
        # than per-contract risks attributing a brand-new contract to an
        # unrelated older trade on the same ticker (regression: AMD Risk
        # Reversal P$320/C$330 was assigned 2026-04-22 because an unrelated
        # AMD 295P existed in the blotter — see test_combo_entry_date.py).
        #
        #   1. journal (per-contract: ticker|expiry|right|strike)
        #   2. journal (ticker|structure)
        #   3. IB fills (per-contract, same-session)
        #   4. prev portfolio (ticker|structure|expiry, excluding today)
        #   5. today  ← brand-new positions default here so the frontend's
        #              same-day P&L branch fires correctly. We deliberately
        #              do NOT use a per-ticker blotter fallback or "unknown".
        pos['entry_date'] = (
            blotter_contract_date
            or trade_log_dates.get(f"{ticker}|{structure}")
            or fill_contract_date
            or prev_dates.get(key)
            or today
        )

        # Same-side basis carry-forward. A partial close must NOT change the
        # remaining per-unit basis, but IB drifts pos.avgCost on a reduce and
        # assignment-originated stock (MU short from an assigned call) has no
        # journal opener. When this single-leg position is NOT larger than the
        # prior snapshot on the SAME side AND the prior per-unit basis still
        # differs from IB's avgCost, carry the prior basis forward. Sticky
        # (size-not-increased + basis-differs): corrects the reduce and holds the
        # pin across unchanged syncs, never freezing an add/grow, a direction
        # flip, or a position IB and the snapshot already agree on. A
        # journal-corrected leg (avg_cost != ib_avg_cost) wins and is left alone.
        pb = prev_basis.get(_basis_carry_key(ticker, structure, expiry))
        if pb is not None and len(legs) == 1:
            leg0 = legs[0]
            cur_contracts = abs(float(pos.get("contracts") or 0))
            cur_dir = (pos.get("direction") or "").upper()
            ac = leg0.get("avg_cost")
            ibc = leg0.get("ib_avg_cost")
            journal_corrected = (
                ac is not None and ibc is not None and abs(float(ac) - float(ibc)) > 1e-6
            )
            drifted = ibc is not None and abs(pb["per_unit"] - float(ibc)) > 1e-6
            if (
                not journal_corrected
                and drifted
                and cur_dir == pb["direction"]
                and 0 < cur_contracts <= pb["contracts"]
            ):
                per_unit = pb["per_unit"]
                sign = -1 if cur_dir == "SHORT" else 1
                pos["entry_cost"] = round(sign * per_unit * cur_contracts, 2)
                leg0["avg_cost"] = round(per_unit, 6)
                leg0["entry_cost"] = round(per_unit * cur_contracts, 2)

    # Hydrate only execution-instance-linked v2 capital. Structure similarity
    # and modeled margin are intentionally never accepted here.
    try:
        from db.readers import read_active_position_return_capital
        from position_return_capital import hydrate_return_capital

        _active_bases = read_active_position_return_capital()
        if _active_bases:
            n_att = hydrate_return_capital(collapsed_positions, _active_bases)
            if n_att:
                print(f"  return_capital attached on {n_att} position(s)")
    except Exception as exc:  # noqa: BLE001 — table may not exist yet
        print(f"  return_capital hydrate skipped: {exc}")

    result = {
        "bankroll": round(bankroll, 2),
        "peak_value": round(bankroll, 2),  # Would need historical tracking
        # Wall-clock timestamp consumed by web/lib/performanceFreshness.ts.
        # Must include a timezone offset so the JS side does not parse it as
        # the user's local time and shift the derived ET session date.
        "last_sync": datetime.now(timezone.utc).isoformat(),
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

    attach_correlation_risk_report(result)
    return result


def attach_correlation_risk_report(portfolio: dict) -> dict:
    """Attach the existing Gate-3 report to the canonical live snapshot."""
    try:
        import portfolio_risk

        series = portfolio_risk.load_price_series_for_portfolio(portfolio)
        portfolio["risk_budget"] = portfolio_risk.build_risk_budget_report(portfolio, series)
    except Exception as exc:  # noqa: BLE001 - measurement failure stays explicit in payload
        portfolio["risk_budget"] = None
        print(f"  correlation risk measurement unavailable: {type(exc).__name__}", file=sys.stderr)
    return portfolio


NAV_HISTORY_PATH = DATA_DIR / "nav_history.jsonl"


def _append_nav_snapshot(net_liq: float, daily_pnl=None) -> None:
    """Append today's NAV to the daily history file (JSONL, one entry per day)."""
    import pytz

    et = pytz.timezone("America/New_York")
    today = datetime.now(et).strftime("%Y-%m-%d")
    entry = {"date": today, "nav": round(net_liq, 2)}
    if daily_pnl is not None:
        entry["daily_pnl"] = round(float(daily_pnl), 2)

    NAV_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = NAV_HISTORY_PATH.with_suffix(NAV_HISTORY_PATH.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        existing = []
        if NAV_HISTORY_PATH.exists():
            for line in NAV_HISTORY_PATH.read_text().strip().splitlines():
                try:
                    existing.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue

        found = False
        for e in existing:
            if e.get("date") == today:
                e["nav"] = entry["nav"]
                if "daily_pnl" in entry:
                    e["daily_pnl"] = entry["daily_pnl"]
                found = True
                break
        if not found:
            existing.append(entry)

        fd, temp_name = tempfile.mkstemp(
            dir=NAV_HISTORY_PATH.parent,
            prefix=f".{NAV_HISTORY_PATH.name}.",
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                for e in sorted(existing, key=lambda x: x.get("date", "")):
                    output.write(json.dumps(e) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, NAV_HISTORY_PATH)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    print(f"✓ NAV snapshot: {today} → ${net_liq:,.2f}")


def build_account_position_vector(raw_positions: list, account_id: str) -> dict[str, float]:
    """Canonical signed conId quantities for one IB account margin sample."""
    return {
        str(pos["conId"]): float(pos["position"])
        for pos in raw_positions
        if pos.get("conId")
        and (not pos.get("account_id") or pos.get("account_id") == account_id)
        and abs(float(pos.get("position") or 0)) > 1e-9
    }


class EmptySnapshotError(RuntimeError):
    """Zero-position snapshot refused: the prior snapshot was non-empty.

    A degraded IB session (reqPositions failure → empty ib_insync cache)
    must not overwrite a non-empty book with a flat one (REL-016 / R-018).
    """


def _refuse_empty_snapshot(portfolio: dict, allow_empty: bool) -> None:
    if portfolio.get("positions"):
        return
    if allow_empty or os.environ.get("RADON_ALLOW_EMPTY_SNAPSHOT") == "1":
        return
    try:
        prior = read_latest_portfolio_snapshot() or {}
    except Exception:
        return  # no readable prior — a genuinely-flat book stays writable
    prior_positions = prior.get("positions") or []
    if prior_positions:
        raise EmptySnapshotError(
            f"refusing zero-position snapshot: prior snapshot holds "
            f"{len(prior_positions)} position(s) — likely a degraded IB session. "
            f"Override with RADON_ALLOW_EMPTY_SNAPSHOT=1 or --allow-empty."
        )


def save_portfolio(
    portfolio: dict,
    *,
    account_id: str = "",
    raw_positions: Optional[list] = None,
    margin_observed_from: str = "",
    margin_observed_through: str = "",
    allow_empty: bool = False,
):
    """Save portfolio to the canonical Turso portfolio_snapshots table."""
    from db.service_cycle import service_cycle
    from db.writer import insert_account_margin_sample, upsert_portfolio_snapshot

    taken_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with service_cycle("portfolio-sync", market_hours_class="intraday") as cycle:
        _refuse_empty_snapshot(portfolio, allow_empty)
        cycle.finished_at = taken_at
        account_summary = portfolio.get("account_summary") or {}
        initial_margin = account_summary.get("initial_margin")
        if account_id and initial_margin is not None and margin_observed_from and margin_observed_through:
            position_vector = build_account_position_vector(raw_positions or [], account_id)
            insert_account_margin_sample(
                account_id,
                observed_from=margin_observed_from,
                observed_through=margin_observed_through,
                initial_margin=float(initial_margin),
                maintenance_margin=account_summary.get("maintenance_margin"),
                currency="USD",
                positions=position_vector,
            )
        upsert_portfolio_snapshot(taken_at, portfolio)
        try:
            from capture_position_return_capital import reconcile_position_return_capital
            from db.client import get_db

            reconcile_position_return_capital(get_db(), apply=True)
        except Exception as exc:  # noqa: BLE001 - migration/evidence may be unavailable during rollout
            print(f"  return_capital reconcile skipped: {exc}")
    print(f"✓ Saved portfolio snapshot to Turso at {taken_at}")

    # Track daily NAV for performance history
    acct = portfolio.get("account_summary", {})
    net_liq = acct.get("net_liquidation") or portfolio.get("bankroll")
    if net_liq:
        try:
            _append_nav_snapshot(float(net_liq), acct.get("daily_pnl"))
        except Exception as exc:
            print(f"  Warning: NAV snapshot failed: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Sync portfolio from Interactive Brokers")
    parser.add_argument("--host", default=DEFAULT_HOST, help="TWS/Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, 
                        help="TWS/Gateway port (7497=paper, 7496=live, 4001=gateway)")
    parser.add_argument("--client-id", type=int, default=None, help="Client ID (omit for auto-allocation)")
    parser.add_argument("--account", default="", help="Explicit managed IB account ID")
    parser.add_argument("--sync", action="store_true", help="Sync to Turso portfolio_snapshots")
    parser.add_argument("--json-output", action="store_true", help="Print final portfolio JSON to stdout")
    parser.add_argument(
        "--db-optional",
        action="store_true",
        help="Keep returning live portfolio JSON if the Turso snapshot write fails",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow a zero-position snapshot even when the prior snapshot was non-empty",
    )
    parser.add_argument("--no-prices", action="store_true", help="Skip market price fetch")
    parser.add_argument("--skip-audit", action="store_true", help="Skip naked short audit after sync")

    args = parser.parse_args()

    # Connect
    client = connect_ib(args.host, args.port, args.client_id or "auto")

    try:
        # ── Phase 1: Account summary (fast, no sleep needed) ──
        print("Fetching account summary...")
        accounts = client.ib.managedAccounts()
        try:
            ib_account = _select_managed_account(accounts, args.account)
        except ValueError as exc:
            parser.error(str(exc))
        margin_observed_from = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        account = get_account_summary(client, ib_account)

        # ── Phase 2: Request account PnL + positions concurrently ──
        # reqPnL is a subscription — request it, then do other work while it streams
        print("Fetching P&L + positions...")
        from ib_insync import util as ib_util

        def _valid_pnl(val):
            return val is not None and not ib_util.isNan(val)

        pnl_obj = client.ib.reqPnL(ib_account) if ib_account else None

        # Fetch positions while PnL streams
        journal_basis_lookup = build_journal_basis_lookup(client)
        positions = [
            position
            for position in fetch_positions(client, journal_basis_lookup=journal_basis_lookup)
            if not position.get("account_id") or position.get("account_id") == ib_account
        ]
        margin_observed_through = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

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

        portfolio = None
        db_save_error = None

        # Sync or emit JSON if requested
        if args.sync or args.json_output:
            fill_dates = build_fill_dates(client)
            portfolio = convert_to_portfolio_format(account, collapsed, pnl_data, fill_dates=fill_dates)

        if args.sync and portfolio is not None:
            try:
                save_portfolio(
                    portfolio,
                    account_id=ib_account,
                    raw_positions=positions,
                    margin_observed_from=margin_observed_from,
                    margin_observed_through=margin_observed_through,
                    allow_empty=args.allow_empty,
                )
            except Exception as exc:
                db_save_error = exc
                if args.db_optional:
                    print(f"  Warning: portfolio snapshot save failed: {exc}", file=sys.stderr)
                else:
                    raise

            # ── Naked Short Audit (post-sync) ──
            if not args.skip_audit:
                try:
                    from naked_short_audit import find_naked_short_violations

                    import logging
                    log = logging.getLogger("ib_sync.audit")

                    orders = read_open_orders()
                    if orders:
                        violations = find_naked_short_violations(orders, portfolio["positions"])
                        if violations:
                            log.warning("NAKED SHORT AUDIT: %d violation(s) detected", len(violations))
                            for v in violations:
                                log.warning("  → %s: %s (order %s)", v["symbol"], v["reason"], v["order_id"])

                            # Auto-cancel only if client is still connected
                            if client.is_connected():
                                from naked_short_audit import cancel_violations
                                cancelled = cancel_violations(client, violations)
                                log.warning("  Cancelled %d violating order(s)", cancelled)
                            else:
                                log.warning("  Client disconnected — skipping auto-cancel")
                        else:
                            print("✓ Naked short audit: no violations")
                    else:
                        print("  Naked short audit: no open orders in Turso, skipping")
                except ImportError:
                    import logging
                    logging.getLogger("ib_sync.audit").warning(
                        "naked_short_audit module not available — skipping audit"
                    )
                except Exception:
                    import logging
                    logging.getLogger("ib_sync.audit").warning(
                        "Naked short audit failed — sync completed successfully",
                        exc_info=True,
                    )

            print("\n⚠️  Note: kelly_optimal, target, and stop fields need manual evaluation")
        else:
            print("\nRun with --sync to save to Turso")

        if args.json_output and portfolio is not None:
            if db_save_error is not None:
                portfolio["_sync_warning"] = "portfolio snapshot save failed"
            print(json.dumps(portfolio, separators=(",", ":"), default=str))

    finally:
        client.disconnect()
        print("✓ Disconnected from IB")


if __name__ == "__main__":
    main()
