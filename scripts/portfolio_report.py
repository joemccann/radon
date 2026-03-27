#!/usr/bin/env python3
"""
Portfolio Report Generator — Full Status Report

Connects directly to IB, fetches live prices + dark pool flow,
computes P&L, thesis checks, risk flags, and generates a comprehensive
HTML report that opens automatically in the browser.

Uses the portfolio template at:
  .pi/skills/html-report/portfolio-template.html

Sections (8 required):
  1. Header — status dot, status text, timestamp
  2. Summary Metrics — 6 cards (net liq, P&L, deployed, margin, positions, kelly)
  3. Quick-Stat Badges — expiring, at-stop, winners counts
  4. Attention Callouts — expiring, at-stop, winners, undefined risk
  5. Thesis Check — entry flow vs current flow with today-highlighted sparklines
  6. All Positions Table — sorted by DTE
  7. Dark Pool Flow — all tickers with today-highlighted sparklines
  8. Footer — data sources, summary

Usage:
  python3 scripts/portfolio_report.py              # Generate and open
  python3 scripts/portfolio_report.py --no-open    # Generate only
  python3 scripts/portfolio_report.py --port 7497  # Custom IB port
"""

import argparse
import json
import os
import subprocess
import sys
import webbrowser
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
PROJECT_DIR = SCRIPT_DIR.parent

sys.path.insert(0, str(SCRIPT_DIR))
from clients.ib_client import DEFAULT_HOST
TEMPLATE_PATH = PROJECT_DIR / ".pi/skills/html-report/portfolio-template.html"
TRADE_LOG_PATH = PROJECT_DIR / "data/trade_log.json"
REPORTS_DIR = PROJECT_DIR / "reports"

TODAY = date.today()
TODAY_STR = TODAY.strftime('%Y-%m-%d')


# ═══════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════

def connect_ib(port: int = 4001, client_id: int = 55):
    """Connect to IB Gateway/TWS."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from ib_insync import IB
    ib = IB()
    ib.connect(DEFAULT_HOST, port, clientId=client_id)
    ib.reqMarketDataType(4)  # frozen data fallback
    return ib


def fetch_ib_data(ib) -> Tuple[List[Dict], Dict[str, str]]:
    """Fetch all positions with live prices and account values."""
    acct_vals = ib.accountValues()
    account = {}
    for av in acct_vals:
        if av.currency == 'USD' and av.tag in [
            'NetLiquidation', 'TotalCashValue', 'GrossPositionValue',
            'UnrealizedPnL', 'RealizedPnL', 'MaintMarginReq', 'InitMarginReq'
        ]:
            account[av.tag] = av.value

    raw_positions = ib.positions()
    for pos in raw_positions:
        ib.qualifyContracts(pos.contract)
        ib.reqMktData(pos.contract, '', False, False)
    ib.sleep(3)

    tickers_map = {t.contract.conId: t for t in ib.tickers()}
    positions = []

    for pos in raw_positions:
        c = pos.contract
        qty = float(pos.position)
        avg_cost = float(pos.avgCost)
        t = tickers_map.get(c.conId)

        bid = float(t.bid) if t and t.bid and t.bid > 0 else -1
        ask = float(t.ask) if t and t.ask and t.ask > 0 else -1
        last = float(t.last) if t and t.last and t.last > 0 else (
            float(t.close) if t and t.close and t.close > 0 else -1)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else last

        is_opt = c.secType == 'OPT'
        multiplier = 100 if is_opt else 1
        entry_per = avg_cost / 100 if is_opt else avg_cost
        entry_cost = entry_per * multiplier * abs(qty)
        mkt_val = mid * multiplier * abs(qty) if mid > 0 else 0
        pnl = ((entry_per - mid) * multiplier * abs(qty) if qty < 0
               else (mid - entry_per) * multiplier * qty) if mid > 0 else 0
        pnl_pct = (pnl / entry_cost) * 100 if entry_cost > 0 else 0

        expiry_raw = c.lastTradeDateOrContractMonth if is_opt else ''
        if expiry_raw:
            try:
                exp_date = datetime.strptime(expiry_raw, '%Y%m%d').date()
                dte = (exp_date - TODAY).days
                expiry_fmt = exp_date.strftime('%Y-%m-%d')
            except ValueError:
                dte = None
                expiry_fmt = expiry_raw
        else:
            dte = None
            expiry_fmt = None

        positions.append({
            'symbol': c.symbol, 'sec_type': c.secType,
            'strike': float(c.strike) if is_opt else None,
            'right': c.right if is_opt else None,
            'expiry': expiry_fmt, 'dte': dte, 'qty': qty,
            'entry_per': entry_per, 'mid': mid,
            'entry_cost': entry_cost, 'mkt_val': mkt_val,
            'pnl': pnl, 'pnl_pct': pnl_pct,
        })

    return positions, account


def fetch_dark_pool_flow(tickers: List[str], days: int = 5) -> Dict[str, Dict]:
    """Fetch dark pool flow for multiple tickers in parallel.

    Returns per-ticker aggregate + daily breakdown. The daily data is ordered
    oldest-first so sparkline builders can iterate left→right, with the
    *last* element being today.
    """
    import requests as _req

    token = os.environ.get('UW_TOKEN', '')
    if not token:
        return {t: _no_flow('NO_TOKEN') for t in tickers}

    # Last N business days (most recent first → we reverse later)
    date_list = []
    d = TODAY
    while len(date_list) < days:
        if d.weekday() < 5:
            date_list.append(d.strftime('%Y-%m-%d'))
        d -= timedelta(days=1)

    def _fetch_one(ticker: str) -> Tuple[str, Dict]:
        try:
            daily = defaultdict(lambda: {'buy_vol': 0, 'sell_vol': 0})
            headers = {'Authorization': f'Bearer {token}'}

            for dt in date_list:
                try:
                    url = f"https://api.unusualwhales.com/api/darkpool/{ticker}?date={dt}"
                    resp = _req.get(url, headers=headers, timeout=10)
                    if resp.status_code != 200:
                        continue
                    for trade in resp.json().get('data', []):
                        price = float(trade.get('price', 0) or 0)
                        bid = float(trade.get('nbbo_bid', 0) or 0)
                        ask = float(trade.get('nbbo_ask', 0) or 0)
                        size = int(trade.get('size', 0) or 0)
                        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else price
                        if price >= mid:
                            daily[dt]['buy_vol'] += size
                        else:
                            daily[dt]['sell_vol'] += size
                except Exception:
                    continue

            if not daily:
                return ticker, _no_flow('NO_DATA')

            # Build day_data oldest → newest (so sparkline reads left→right)
            days_sorted = sorted(daily.keys())
            day_data = []
            total_buy = total_sell = 0
            for dt in days_sorted:
                bv = daily[dt]['buy_vol']
                sv = daily[dt]['sell_vol']
                total = bv + sv
                ratio = bv / total if total > 0 else 0.5
                total_buy += bv
                total_sell += sv
                direction = ('ACCUMULATION' if ratio >= 0.7
                             else 'DISTRIBUTION' if ratio <= 0.3
                             else 'NEUTRAL')
                strength = _flow_strength(ratio)
                day_data.append({
                    'date': dt, 'buy_ratio': ratio,
                    'direction': direction, 'strength': strength,
                    'is_today': dt == TODAY_STR,
                })

            agg = total_buy / (total_buy + total_sell) if (total_buy + total_sell) > 0 else 0.5
            agg_dir = ('ACCUMULATION' if agg >= 0.7
                       else 'DISTRIBUTION' if agg <= 0.3
                       else 'NEUTRAL')

            # Today's specific data
            today_data = next((d for d in day_data if d['is_today']), None)

            return ticker, {
                'direction': agg_dir, 'buy_ratio': agg,
                'strength': _flow_strength(agg), 'days': day_data,
                'today': today_data,
            }
        except Exception as e:
            return ticker, _no_flow('ERROR', str(e))

    results = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_one, t): t for t in tickers}
        for f in as_completed(futures):
            tk, data = f.result()
            results[tk] = data
    return results


def _no_flow(reason: str, detail: str = '') -> Dict:
    return {'direction': reason, 'buy_ratio': 0, 'strength': 0, 'days': [], 'today': None, 'error': detail}


def _flow_strength(ratio: float) -> float:
    return max(0, abs(ratio - 0.5) * 200)


# ═══════════════════════════════════════════════════════════════════════════
# POSITION GROUPING
# ═══════════════════════════════════════════════════════════════════════════

def group_positions(positions: List[Dict]) -> List[Dict]:
    """Group raw position legs into logical structures."""
    by_sym_exp = defaultdict(list)
    for p in positions:
        key = (p['symbol'], p['expiry'] or 'stock')
        by_sym_exp[key].append(p)

    # ── Covered call merge pass ──
    # Merge standalone short-call group with same-ticker stock group
    by_sym_exp = _merge_covered_call_groups_report(by_sym_exp)

    grouped = []
    for (sym, exp), legs in by_sym_exp.items():
        if len(legs) == 1:
            grouped.append(_single_leg(sym, exp, legs[0]))
        else:
            grouped.append(_multi_leg(sym, exp, legs))

    grouped.sort(key=lambda g: (0, g['dte']) if g['dte'] is not None else (1, 9999))
    return grouped


def _merge_covered_call_groups_report(groups: dict) -> dict:
    """
    Merge standalone short-call groups into same-ticker stock groups for covered calls.
    Portfolio report variant — uses 'sec_type', 'right', 'qty' field names.
    """
    stock_groups = {}   # symbol -> key
    short_call_groups = {}  # symbol -> [key, ...]

    for key, legs in groups.items():
        symbol = key[0]

        if all(l.get('sec_type') == 'STK' for l in legs):
            long_shares = sum(l['qty'] for l in legs if l['qty'] > 0)
            if long_shares > 0:
                stock_groups[symbol] = key

        elif all(l.get('sec_type') == 'OPT' for l in legs):
            if all(l.get('right') == 'C' and l['qty'] < 0 for l in legs):
                if symbol not in short_call_groups:
                    short_call_groups[symbol] = []
                short_call_groups[symbol].append(key)

    merged = dict(groups)
    for symbol, sc_keys in short_call_groups.items():
        if symbol not in stock_groups:
            continue

        stk_key = stock_groups[symbol]
        stk_legs = merged[stk_key]
        total_shares = sum(l['qty'] for l in stk_legs if l['qty'] > 0)

        for sc_key in sc_keys:
            sc_legs = merged.get(sc_key, [])
            total_short = sum(abs(l['qty']) for l in sc_legs)
            shares_needed = total_short * 100

            if total_shares >= shares_needed:
                merged[sc_key] = list(stk_legs) + list(sc_legs)
                del merged[stk_key]
                total_shares -= shares_needed
                break

    return merged


def _single_leg(sym, exp, leg):
    risk = 'equity' if leg['sec_type'] == 'STK' else ('undefined' if leg['qty'] < 0 else 'defined')
    if leg['sec_type'] == 'STK':
        structure = f"{sym} Stock ({abs(int(leg['qty'])):,} shares)"
    elif leg['qty'] < 0:
        structure = f"Short {sym} {leg['right']}{leg['strike']:.0f} {exp}"
    else:
        structure = f"{sym} {leg['right']}{leg['strike']:.0f} {exp}"
    return {
        'symbol': sym, 'structure': structure, 'legs': [leg],
        'entry_cost': leg['entry_cost'], 'mkt_val': leg['mkt_val'],
        'pnl': leg['pnl'], 'pnl_pct': leg['pnl_pct'],
        'dte': leg['dte'], 'expiry': leg['expiry'], 'risk': risk,
    }


def _multi_leg(sym, exp, legs):
    stk_legs = [l for l in legs if l.get('sec_type') == 'STK']
    opt_legs = [l for l in legs if l.get('sec_type') != 'STK']
    calls = [l for l in opt_legs if l.get('right') == 'C']
    puts = [l for l in opt_legs if l.get('right') == 'P']
    long_calls = [c for c in calls if c['qty'] > 0]
    short_calls = [c for c in calls if c['qty'] < 0]
    long_puts = [p for p in puts if p['qty'] > 0]
    short_puts = [p for p in puts if p['qty'] < 0]

    total_pnl = sum(l['pnl'] for l in legs)
    total_entry = sum(l['entry_cost'] for l in legs if l['qty'] > 0)
    pnl_pct = (total_pnl / total_entry * 100) if total_entry > 0 else 0
    # Use the option DTE if present, otherwise stock DTE (None)
    dte = next((l['dte'] for l in opt_legs if l.get('dte') is not None), legs[0].get('dte'))
    risk = 'defined'
    structure = f"{sym} Multi-leg {exp}"

    # ── Covered Call: long stock + short calls ──
    if stk_legs and short_calls and not long_calls and not puts:
        long_shares = sum(l['qty'] for l in stk_legs if l['qty'] > 0)
        short_contracts = sum(abs(c['qty']) for c in short_calls)
        if long_shares >= short_contracts * 100:
            sc = short_calls[0]
            structure = f"{sym} Covered Call ${sc['strike']:.0f} ({int(long_shares):,} shares)"
            risk = 'defined'
            # Expiry from the option leg
            opt_expiry = sc.get('expiry')
            return {
                'symbol': sym, 'structure': structure, 'legs': legs,
                'entry_cost': total_entry, 'mkt_val': sum(l['mkt_val'] for l in legs),
                'pnl': total_pnl, 'pnl_pct': pnl_pct,
                'dte': sc.get('dte', dte), 'expiry': opt_expiry if opt_expiry and opt_expiry != 'stock' else exp,
                'risk': risk,
            }

    if long_calls and short_calls and not puts:
        lc = min(long_calls, key=lambda x: x['strike'])
        sc = max(short_calls, key=lambda x: x['strike'])
        structure = f"{sym} Bull Call ${lc['strike']:.0f}/${sc['strike']:.0f}"
        pnl_pct = (total_pnl / lc['entry_cost'] * 100) if lc['entry_cost'] > 0 else 0
    elif long_puts and short_puts and not calls:
        lp = max(long_puts, key=lambda x: x['strike'])
        sp = min(short_puts, key=lambda x: x['strike'])
        structure = f"{sym} Bear Put ${lp['strike']:.0f}/${sp['strike']:.0f}"
        pnl_pct = (total_pnl / lp['entry_cost'] * 100) if lp['entry_cost'] > 0 else 0
    elif long_calls and short_puts and not long_puts and not short_calls:
        lc, sp = long_calls[0], short_puts[0]
        if lc['strike'] == sp['strike']:
            structure = f"{sym} Synthetic Long ${lc['strike']:.0f}"
        else:
            structure = f"{sym} Risk Rev P${sp['strike']:.0f}/C${lc['strike']:.0f}"
        risk = 'undefined'
    elif long_calls and short_calls:
        lc = min(long_calls, key=lambda x: x['strike'])
        sc = max(short_calls, key=lambda x: x['strike'])
        structure = f"{sym} Bull Call ${lc['strike']:.0f}/${sc['strike']:.0f}"
        pnl_pct = (total_pnl / total_entry * 100) if total_entry > 0 else 0

    return {
        'symbol': sym, 'structure': structure, 'legs': legs,
        'entry_cost': total_entry, 'mkt_val': sum(l['mkt_val'] for l in legs),
        'pnl': total_pnl, 'pnl_pct': pnl_pct,
        'dte': dte, 'expiry': exp if exp != 'stock' else None, 'risk': risk,
    }


# ═══════════════════════════════════════════════════════════════════════════
# TRADE LOG
# ═══════════════════════════════════════════════════════════════════════════

def load_trade_log() -> Dict[str, Dict]:
    if not TRADE_LOG_PATH.exists():
        return {}
    with open(TRADE_LOG_PATH) as f:
        data = json.load(f)
    return {
        t['ticker']: t for t in data.get('trades', [])
        if t.get('decision') == 'EXECUTED' and 'close_date' not in t
    }


# ═══════════════════════════════════════════════════════════════════════════
# HTML HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def fc(val, sign=False):
    """Format currency."""
    if val is None: return '—'
    return f"${val:+,.0f}" if sign else f"${val:,.0f}"


def fp(val):
    """Format percentage."""
    if val is None: return '—'
    return f"{val:+.1f}%"


def pclass(val):
    return 'text-positive' if val >= 0 else 'text-negative'


def risk_pill(r):
    if r == 'defined':   return '<span class="pill pill-positive">DEFINED</span>'
    if r == 'undefined': return '<span class="pill pill-negative">UNDEFINED</span>'
    return '<span class="pill">EQUITY</span>'


def status_pill(pnl_pct, dte):
    if dte is not None and dte <= 0:  return '<span class="pill pill-negative">EXPIRING TODAY</span>'
    if dte is not None and dte <= 7:  return '<span class="pill pill-negative">EXPIRING</span>'
    if pnl_pct >= 100:               return '<span class="pill pill-positive">WINNER</span>'
    if pnl_pct <= -50:               return '<span class="pill pill-negative">AT STOP</span>'
    if pnl_pct < -25:               return '<span class="pill pill-warning">UNDERWATER</span>'
    return '<span class="pill">ACTIVE</span>'


def thesis_pill(s):
    m = {'INTACT': 'positive', 'WEAKENING': 'warning', 'BROKEN': 'negative'}
    cls = m.get(s, '')
    return f'<span class="pill pill-{cls}">{s}</span>' if cls else f'<span class="pill">{s}</span>'


def build_sparkline(days: List[Dict]) -> str:
    """Build a sparkline of flow bars. Today's bar gets the `today` CSS class
    which adds a white outline ring, making it instantly visible."""
    if not days:
        return '<span class="text-muted">No data</span>'
    html = '<div class="spark">'
    for d in days:                                                # oldest → newest
        ratio = d['buy_ratio']
        height = max(4, int(ratio * 28))
        if ratio >= 0.7:   bar_cls = 'accumulation'
        elif ratio <= 0.3: bar_cls = 'distribution'
        else:              bar_cls = 'neutral'
        today_cls = ' today' if d.get('is_today') else ''
        tooltip = f"{d['date']}: {ratio:.0%}"
        if d.get('is_today'):
            tooltip += ' (TODAY)'
        html += (f'<div class="spark-bar {bar_cls}{today_cls}" '
                 f'title="{tooltip}" '
                 f'style="height:{height}px"></div>')
    html += '</div>'
    # "TODAY →" label under the rightmost bar
    if days and days[-1].get('is_today'):
        html += '<div class="spark-today-label">today →</div>'
    return html


def build_today_cell(today_data: Optional[Dict]) -> str:
    """Build the dedicated 'Today' column cell for the flow table."""
    if not today_data:
        return '<span class="text-muted">—</span>'
    ratio = today_data['buy_ratio']
    direction = today_data['direction']
    cls = ('accumulation' if direction == 'ACCUMULATION'
           else 'distribution' if direction == 'DISTRIBUTION'
           else 'neutral')
    return (f'<span class="flow-dir {cls}">{ratio:.0%}</span>'
            f'<span class="today-tag">LIVE</span>')


def check_thesis(symbol: str, trade: Dict, flow: Dict) -> Tuple[str, str]:
    """Compare entry flow to current flow."""
    edge = trade.get('edge_analysis', {})
    entry_type = edge.get('edge_type', '')
    entry_dp_dir = edge.get('dp_flow', '')
    current_dir = flow.get('direction', 'NO_DATA')
    current_ratio = flow.get('buy_ratio', 0)
    today = flow.get('today')
    today_dir = today['direction'] if today else 'NO_DATA'
    today_ratio = today['buy_ratio'] if today else 0

    # IV plays — flow is context, not the edge
    if 'IV' in entry_type and 'FLOW' not in entry_type:
        return 'INTACT', f"IV play — flow not required (currently {current_dir} {current_ratio:.0%})"

    if entry_dp_dir == 'ACCUMULATION':
        if today_dir == 'DISTRIBUTION':
            return 'BROKEN', f"Today DISTRIBUTION ({today_ratio:.0%}) — was ACCUMULATION at entry"
        if today_dir == 'NEUTRAL' and current_dir != 'ACCUMULATION':
            return 'WEAKENING', f"Today NEUTRAL ({today_ratio:.0%}) — accumulation fading"
        if current_dir == 'ACCUMULATION':
            return 'INTACT', f"Still ACCUMULATION ({current_ratio:.0%}), today {today_ratio:.0%}"
    if entry_dp_dir == 'DISTRIBUTION':
        if today_dir == 'ACCUMULATION':
            return 'BROKEN', f"Today ACCUMULATION ({today_ratio:.0%}) — was DISTRIBUTION at entry"
        if current_dir == 'DISTRIBUTION':
            return 'INTACT', f"Still DISTRIBUTION ({current_ratio:.0%})"

    return 'INTACT', f"Current: {current_dir} ({current_ratio:.0%})"


# ═══════════════════════════════════════════════════════════════════════════
# HTML SECTION BUILDERS
# ═══════════════════════════════════════════════════════════════════════════

def build_metrics_html(account: Dict, grouped: List[Dict], trade_log: Dict) -> str:
    net_liq = float(account.get('NetLiquidation', 0))
    cash = float(account.get('TotalCashValue', 0))
    gross = float(account.get('GrossPositionValue', 0))
    unrealized = float(account.get('UnrealizedPnL', 0))
    realized = float(account.get('RealizedPnL', 0))
    margin = float(account.get('MaintMarginReq', 0))
    deployed_pct = (gross / net_liq * 100) if net_liq > 0 else 0
    margin_pct = (margin / net_liq * 100) if net_liq > 0 else 0

    n = len(grouped)
    defined = sum(1 for g in grouped if g['risk'] == 'defined')
    undefined = sum(1 for g in grouped if g['risk'] == 'undefined')
    equity = sum(1 for g in grouped if g['risk'] == 'equity')

    kelly_sizes = [t.get('kelly_calculation', {}).get('actual_size_pct', 0)
                   for t in trade_log.values() if t.get('kelly_calculation', {}).get('actual_size_pct')]
    avg_kelly = sum(kelly_sizes) / len(kelly_sizes) if kelly_sizes else 0

    return f"""
      <div class="metric">
        <div class="metric-label">Net Liquidation</div>
        <div class="metric-value">{fc(net_liq)}</div>
        <div class="metric-change">Cash: {fc(cash)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Unrealized P&L</div>
        <div class="metric-value {pclass(unrealized)}">{fc(unrealized, True)}</div>
        <div class="metric-change">Realized today: {fc(realized, True)}</div>
      </div>
      <div class="metric">
        <div class="metric-label">Deployed</div>
        <div class="metric-value">{deployed_pct:.0f}%</div>
        <div class="metric-change">{fc(gross)} gross</div>
      </div>
      <div class="metric">
        <div class="metric-label">Margin Required</div>
        <div class="metric-value">{fc(margin)}</div>
        <div class="metric-change">{margin_pct:.0f}% of net liq</div>
      </div>
      <div class="metric">
        <div class="metric-label">Positions</div>
        <div class="metric-value">{n}</div>
        <div class="metric-change">{defined} defined · {undefined} undefined · {equity} equity</div>
      </div>
      <div class="metric">
        <div class="metric-label">Evaluated / Kelly</div>
        <div class="metric-value">{len(kelly_sizes)}</div>
        <div class="metric-change">Avg size: {avg_kelly:.1f}% · Cap: 2.5%</div>
      </div>
    """


def build_quick_stats(grouped: List[Dict]) -> Tuple[str, int, int, int]:
    exp = [g for g in grouped if g['dte'] is not None and g['dte'] <= 7]
    stops = [g for g in grouped if g['pnl_pct'] <= -50]
    winners = [g for g in grouped if g['pnl_pct'] >= 100]
    html = f"""
      <div class="panel"><div class="panel-header">Expiring ≤7 DTE
        <span class="count-badge {'alert' if exp else ''}">{len(exp)}</span></div></div>
      <div class="panel"><div class="panel-header">At Stop (≤-50%)
        <span class="count-badge {'alert' if stops else ''}">{len(stops)}</span></div></div>
      <div class="panel"><div class="panel-header">Big Winners (≥+100%)
        <span class="count-badge {'success' if winners else ''}">{len(winners)}</span></div></div>
    """
    return html, len(exp), len(stops), len(winners)


def build_attention_html(grouped: List[Dict]) -> str:
    parts = []
    expiring = [g for g in grouped if g['dte'] is not None and g['dte'] <= 7]
    at_stop = [g for g in grouped if g['pnl_pct'] <= -50 and g not in expiring]
    winners = [g for g in grouped if g['pnl_pct'] >= 100]
    undefined = [g for g in grouped if g['risk'] == 'undefined']

    if expiring:
        li = ''.join(f"<li><strong>{g['symbol']}</strong> — {g['structure']} | {g['dte']} DTE | {fp(g['pnl_pct'])} ({fc(g['pnl'], True)}) | {g['risk'].upper()}</li>" for g in expiring)
        parts.append(f'<div class="callout negative"><div class="callout-title">🔴 Expiring This Week</div><ul>{li}</ul></div>')
    if at_stop:
        li = ''.join(f"<li><strong>{g['symbol']}</strong> — {g['structure']} | {fp(g['pnl_pct'])} ({fc(g['pnl'], True)})</li>" for g in at_stop)
        parts.append(f'<div class="callout warning"><div class="callout-title">🟡 At or Below Stop (≤-50%)</div><ul>{li}</ul></div>')
    if winners:
        li = ''.join(f"<li><strong>{g['symbol']}</strong> — {g['structure']} | {fp(g['pnl_pct'])} ({fc(g['pnl'], True)}) | {g['dte'] or 'No'} DTE</li>" for g in winners)
        parts.append(f'<div class="callout positive"><div class="callout-title">🟢 Big Winners — Consider Profit Taking</div><ul>{li}</ul></div>')
    if undefined:
        li = ''.join(f"<li><strong>{g['symbol']}</strong> — {g['structure']} | {g['dte'] or 'N/A'} DTE</li>" for g in undefined)
        parts.append(f'<div class="callout negative"><div class="callout-title">⛔ Undefined Risk Positions (Rule Violation)</div><ul>{li}</ul></div>')
    if not parts:
        parts.append('<div class="callout positive"><div class="callout-title">✓ No Immediate Actions Required</div><p>All positions within normal parameters.</p></div>')
    return '\n'.join(parts)


def build_thesis_html(grouped: List[Dict], trade_log: Dict, flow_data: Dict) -> str:
    rows = []
    for g in grouped:
        sym = g['symbol']
        trade = trade_log.get(sym)
        flow = flow_data.get(sym)
        if not trade or not flow or flow.get('direction') in ('ERROR', 'NO_DATA', 'NO_TOKEN'):
            continue
        status, detail = check_thesis(sym, trade, flow)
        edge_type = trade.get('edge_analysis', {}).get('edge_type', 'N/A')[:32]
        spark = build_sparkline(flow.get('days', []))
        today = flow.get('today')
        today_cell = build_today_cell(today)
        rows.append(f"""
        <tr>
          <td><strong>{sym}</strong></td>
          <td>{edge_type}</td>
          <td>{spark}</td>
          <td>{today_cell}</td>
          <td class="text-center">{thesis_pill(status)}</td>
          <td class="text-small">{detail}</td>
        </tr>""")

    if not rows:
        return ''  # No logged positions with flow data — omit section

    return f"""
    <div class="section-header">🔬 Thesis Check — Entry Flow vs Current Flow</div>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Edge Type</th>
            <th>5-Day Flow (→ Today)</th>
            <th>Today</th>
            <th class="text-center">Thesis</th>
            <th>Detail</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def build_positions_html(grouped: List[Dict]) -> str:
    rows = []
    for g in grouped:
        dte_str = str(g['dte']) if g['dte'] is not None else '—'
        row_cls = 'highlight' if (g['dte'] is not None and g['dte'] <= 7) or g['pnl_pct'] <= -50 else ''
        rows.append(f"""
        <tr class="{row_cls}">
          <td><strong>{g['symbol']}</strong></td>
          <td>{g['structure'][:42]}</td>
          <td class="text-right">{fc(g['entry_cost'])}</td>
          <td class="text-right">{fc(g['mkt_val'])}</td>
          <td class="text-right {pclass(g['pnl'])}">{fc(g['pnl'], True)}</td>
          <td class="text-right {pclass(g['pnl_pct'])}">{fp(g['pnl_pct'])}</td>
          <td class="text-center">{dte_str}</td>
          <td class="text-center">{risk_pill(g['risk'])}</td>
          <td class="text-center">{status_pill(g['pnl_pct'], g['dte'])}</td>
        </tr>""")
    return '\n'.join(rows)


def build_flow_html(grouped: List[Dict], flow_data: Dict) -> str:
    rows = []
    for sym in sorted(set(g['symbol'] for g in grouped)):
        flow = flow_data.get(sym)
        if not flow or flow.get('direction') in ('ERROR', 'NO_DATA', 'NO_TOKEN'):
            continue
        spark = build_sparkline(flow.get('days', []))
        today = flow.get('today')
        today_cell = build_today_cell(today)
        dir_cls = ('accumulation' if flow['direction'] == 'ACCUMULATION'
                   else 'distribution' if flow['direction'] == 'DISTRIBUTION'
                   else 'neutral')
        rows.append(f"""
        <tr>
          <td><strong>{sym}</strong></td>
          <td><span class="flow-dir {dir_cls}">{flow['direction']}</span></td>
          <td>{flow['buy_ratio']:.0%}</td>
          <td>{flow['strength']:.0f}</td>
          <td>{today_cell}</td>
          <td>{spark}</td>
        </tr>""")
    return '\n'.join(rows)


# ═══════════════════════════════════════════════════════════════════════════
# REPORT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════════

def generate_report(grouped, account, flow_data, trade_log, timestamp, market_open) -> str:
    """Assemble the full report by filling in the template."""
    template = TEMPLATE_PATH.read_text()

    net_liq = float(account.get('NetLiquidation', 0))
    gross = float(account.get('GrossPositionValue', 0))
    deployed_pct = (gross / net_liq * 100) if net_liq > 0 else 0
    undefined_count = sum(1 for g in grouped if g['risk'] == 'undefined')

    # Freshness
    if market_open:
        freshness_cls = ''
        freshness_txt = f"📊 {timestamp} · Market OPEN · All prices and flow data include <strong>today ({TODAY_STR})</strong>"
    else:
        freshness_cls = 'stale'
        freshness_txt = f"📊 {timestamp} · Market CLOSED · Using closing prices from last session"

    # Quick stats
    quick_html, n_exp, n_stop, n_win = build_quick_stats(grouped)
    actions = n_exp + n_stop + undefined_count
    if actions > 0:
        status_cls, status_txt = 'negative', f"{actions} ACTION{'S' if actions != 1 else ''} NEEDED"
    elif n_win > 0:
        status_cls, status_txt = 'positive', f"{n_win} WINNER{'S' if n_win != 1 else ''} TO REVIEW"
    else:
        status_cls, status_txt = 'positive', 'ALL POSITIONS ACTIVE'

    # Fill template
    replacements = {
        '{{DATE}}': TODAY_STR,
        '{{TIMESTAMP}}': timestamp,
        '{{STATUS_CLASS}}': status_cls,
        '{{STATUS_TEXT}}': status_txt,
        '{{FRESHNESS_CLASS}}': freshness_cls,
        '{{FRESHNESS_TEXT}}': freshness_txt,
        '{{METRICS_HTML}}': build_metrics_html(account, grouped, trade_log),
        '{{QUICK_STATS_HTML}}': quick_html,
        '{{ATTENTION_HTML}}': build_attention_html(grouped),
        '{{THESIS_SECTION_HTML}}': build_thesis_html(grouped, trade_log, flow_data),
        '{{POSITION_ROWS_HTML}}': build_positions_html(grouped),
        '{{FLOW_ROWS_HTML}}': build_flow_html(grouped, flow_data),
        '{{FOOTER_SUMMARY}}': f"{len(grouped)} positions · {fc(net_liq)} net liq · {deployed_pct:.0f}% deployed",
    }

    html = template
    for key, val in replacements.items():
        html = html.replace(key, val)
    return html


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate portfolio HTML report with live IB data")
    parser.add_argument("--no-open", action="store_true", help="Don't open in browser")
    parser.add_argument("--sync", action="store_true", help="Also sync portfolio.json from IB")
    parser.add_argument("--port", type=int, default=4001, help="IB port (default: 4001)")
    parser.add_argument("--output", type=str, help="Custom output path")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M %Z") or datetime.now().strftime("%Y-%m-%d %H:%M PST")
    print("📊 Portfolio Report Generator")
    print(f"   {timestamp}")
    print()

    # Detect market status
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from utils.market_hours import is_market_open
        market_open = is_market_open()
    except Exception:
        market_open = True  # assume open if can't check

    # 1. IB data
    print("[1/4] Connecting to IB — fetching positions + live prices...")
    try:
        ib = connect_ib(port=args.port)
        positions, account = fetch_ib_data(ib)
        ib.disconnect()
        print(f"  ✓ {len(positions)} legs, net liq {fc(float(account.get('NetLiquidation', 0)))}")
    except Exception as e:
        print(f"  ✗ IB failed: {e}")
        positions, account = [], {}

    # 2. Group
    print("[2/4] Grouping into structures...")
    grouped = group_positions(positions)
    print(f"  ✓ {len(grouped)} positions")

    # 3. Dark pool flow
    all_syms = sorted(set(g['symbol'] for g in grouped))
    print(f"[3/4] Fetching dark pool flow for {len(all_syms)} tickers (5 days incl. today)...")
    flow_data = fetch_dark_pool_flow(all_syms)
    flow_ok = sum(1 for v in flow_data.values() if v.get('direction') not in ('ERROR', 'NO_DATA', 'NO_TOKEN'))
    has_today = sum(1 for v in flow_data.values() if v.get('today'))
    print(f"  ✓ {flow_ok}/{len(all_syms)} tickers | {has_today} include today's data")

    # 4. Trade log
    trade_log = load_trade_log()
    print(f"  ✓ {len(trade_log)} open logged trades")

    # Generate
    print("[4/4] Generating HTML report...")
    html = generate_report(grouped, account, flow_data, trade_log, timestamp, market_open)

    REPORTS_DIR.mkdir(exist_ok=True)
    out = Path(args.output) if args.output else REPORTS_DIR / f"portfolio-{TODAY_STR}.html"
    out.write_text(html)
    print(f"  ✓ {out}")

    if args.sync:
        print("\n  Syncing portfolio.json...")
        subprocess.run(["python3", str(SCRIPT_DIR / "ib_sync.py"), "--sync"],
                       capture_output=True, text=True)

    if not args.no_open:
        print("\n🌐 Opening in browser...")
        webbrowser.open(f"file://{out.absolute()}")

    print(f"\n✅ Done: {out.name}")
    return str(out)


if __name__ == "__main__":
    main()
