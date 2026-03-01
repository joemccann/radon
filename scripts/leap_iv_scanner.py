#!/usr/bin/env python3
"""
LEAP IV Mispricing Scanner

Identifies long-dated options where implied volatility diverges from
realized volatility, creating vega alpha opportunities.

Core thesis: When HV20/HV60 > LEAP IV by 15-20+ points AND structural
reasons exist for elevated vol to persist, the market is mispricing
forward volatility.

Usage:
  # Scan specific tickers
  python3 scripts/leap_iv_scanner.py AAPL MSFT NVDA
  
  # Scan preset groups
  python3 scripts/leap_iv_scanner.py --preset sectors
  python3 scripts/leap_iv_scanner.py --preset mag7
  
  # Scan portfolio holdings  
  python3 scripts/leap_iv_scanner.py --portfolio
  
  # Custom parameters
  python3 scripts/leap_iv_scanner.py TSLA --min-gap 20 --years 2027 2028
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field, asdict

try:
    from ib_insync import IB, Stock, Option, util
except ImportError as e:
    print(f"ERROR: Missing dependency: {e}")
    print("Install with: pip install ib_insync")
    sys.exit(1)


# Preset ticker groups
PRESETS = {
    "sectors": {
        "XLB": "Materials",
        "XLC": "Communication Services",
        "XLE": "Energy",
        "XLF": "Financials",
        "XLI": "Industrials",
        "XLK": "Technology",
        "XLP": "Consumer Staples",
        "XLRE": "Real Estate",
        "XLU": "Utilities",
        "XLV": "Health Care",
        "XLY": "Consumer Discretionary",
    },
    "mag7": {
        "AAPL": "Technology",
        "MSFT": "Technology",
        "GOOGL": "Technology",
        "AMZN": "Consumer Discretionary",
        "NVDA": "Technology",
        "META": "Technology",
        "TSLA": "Consumer Discretionary",
    },
    "semis": {
        "NVDA": "Semiconductors",
        "AMD": "Semiconductors",
        "INTC": "Semiconductors",
        "AVGO": "Semiconductors",
        "QCOM": "Semiconductors",
        "MU": "Semiconductors",
        "AMAT": "Semiconductors",
        "LRCX": "Semiconductors",
        "KLAC": "Semiconductors",
        "TSM": "Semiconductors",
    },
    "financials": {
        "JPM": "Banking",
        "BAC": "Banking",
        "WFC": "Banking",
        "GS": "Banking",
        "MS": "Banking",
        "C": "Banking",
        "BLK": "Asset Management",
        "SCHW": "Brokerage",
    },
    "energy": {
        "XOM": "Oil & Gas",
        "CVX": "Oil & Gas",
        "COP": "Oil & Gas",
        "SLB": "Oil Services",
        "EOG": "Oil & Gas",
        "PXD": "Oil & Gas",
        "OXY": "Oil & Gas",
    },
    "china": {
        "BABA": "E-Commerce",
        "JD": "E-Commerce",
        "PDD": "E-Commerce",
        "BIDU": "Technology",
        "NIO": "EV",
        "XPEV": "EV",
        "LI": "EV",
        "FXI": "China ETF",
        "KWEB": "China Internet ETF",
    },
    "emerging": {
        "EEM": "EM Broad",
        "EWZ": "Brazil",
        "EWY": "South Korea",
        "EWT": "Taiwan",
        "INDA": "India",
        "FXI": "China",
        "EWW": "Mexico",
        "ILF": "Latin America",
    },
}

# Connection defaults
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4001
DEFAULT_CLIENT_ID = 10  # Different from main sync script

# Analysis parameters
DEFAULT_YEARS = [2027, 2028]
TARGET_DELTAS = [0.50, 0.30, 0.20, 0.10]  # ATM, 30Δ, 20Δ, 10Δ
MIN_IV_GAP = 15  # Minimum HV-IV spread to flag as mispriced
HV_PERIODS = [20, 60, 252]  # Trading days for HV calculation


@dataclass
class VolatilityData:
    """Historical volatility metrics for an underlying"""
    ticker: str
    sector: str
    current_price: float
    hv_20: float
    hv_60: float
    hv_252: float  # 1 year
    hv_756: Optional[float] = None  # 3 year (if available)
    avg_hv: float = 0.0
    
    def __post_init__(self):
        # Calculate average HV across timeframes
        hvs = [self.hv_20, self.hv_60, self.hv_252]
        if self.hv_756:
            hvs.append(self.hv_756)
        self.avg_hv = sum(hvs) / len(hvs)


@dataclass
class OptionData:
    """LEAP option with IV analysis"""
    ticker: str
    expiry: str
    strike: float
    right: str  # C or P
    delta: float
    iv: float
    bid: float
    ask: float
    mid: float
    vega: float
    theta: float
    oi: int
    volume: int
    
    # Mispricing analysis
    hv_20_gap: float = 0.0
    hv_60_gap: float = 0.0
    hv_avg_gap: float = 0.0
    is_mispriced: bool = False
    mispricing_score: float = 0.0


@dataclass 
class ScanResult:
    """Complete scan result for one ETF"""
    volatility: VolatilityData
    options: list = field(default_factory=list)
    mispriced_count: int = 0
    best_opportunity: Optional[OptionData] = None


def connect_ib(host: str, port: int, client_id: int) -> IB:
    """Connect to TWS/IB Gateway"""
    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id)
        print(f"✓ Connected to IB on {host}:{port}")
        return ib
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        sys.exit(1)


def calculate_historical_volatility(prices: list, period: int) -> float:
    """
    Calculate annualized historical volatility from price series.
    Uses log returns and standard deviation, annualized by sqrt(252).
    """
    if len(prices) < period + 1:
        return 0.0
    
    # Use most recent 'period' prices
    recent_prices = prices[-(period + 1):]
    
    # Calculate log returns
    returns = []
    for i in range(1, len(recent_prices)):
        if recent_prices[i-1] > 0:
            log_return = math.log(recent_prices[i] / recent_prices[i-1])
            returns.append(log_return)
    
    if len(returns) < 2:
        return 0.0
    
    # Standard deviation of returns
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(variance)
    
    # Annualize (252 trading days)
    annual_vol = daily_vol * math.sqrt(252) * 100
    
    return round(annual_vol, 2)


def fetch_historical_data(ib: IB, ticker: str, days: int = 800) -> list:
    """Fetch historical daily closes for HV calculation"""
    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)
    
    # IB requires durations > 365 days to use year format
    if days > 365:
        years = max(1, days // 252)  # Trading days per year
        duration_str = f"{years} Y"
    else:
        duration_str = f"{days} D"
    
    # Request historical data
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration_str,
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1
    )
    
    if not bars:
        print(f"  ⚠ No historical data for {ticker}")
        return []
    
    return [bar.close for bar in bars]


def fetch_volatility_data(ib: IB, ticker: str, sector: str) -> Optional[VolatilityData]:
    """Calculate multi-timeframe HV for an ETF"""
    print(f"  Fetching historical data for {ticker}...")
    
    prices = fetch_historical_data(ib, ticker, days=800)
    
    if len(prices) < 60:
        print(f"  ⚠ Insufficient data for {ticker} ({len(prices)} days)")
        return None
    
    current_price = prices[-1]
    
    hv_20 = calculate_historical_volatility(prices, 20)
    hv_60 = calculate_historical_volatility(prices, 60)
    hv_252 = calculate_historical_volatility(prices, 252) if len(prices) >= 253 else hv_60
    hv_756 = calculate_historical_volatility(prices, 756) if len(prices) >= 757 else None
    
    return VolatilityData(
        ticker=ticker,
        sector=sector,
        current_price=current_price,
        hv_20=hv_20,
        hv_60=hv_60,
        hv_252=hv_252,
        hv_756=hv_756
    )


def get_leap_expirations(ib: IB, ticker: str, target_years: list) -> list:
    """Get available LEAP expiration dates for target years"""
    contract = Stock(ticker, "SMART", "USD")
    ib.qualifyContracts(contract)
    
    # Request option chain parameters
    chains = ib.reqSecDefOptParams(ticker, "", "STK", contract.conId)
    
    if not chains:
        return []
    
    # Find expirations in target years
    leap_expirations = []
    for chain in chains:
        if chain.exchange == "SMART":
            for exp in chain.expirations:
                year = int(exp[:4])
                if year in target_years:
                    leap_expirations.append(exp)
    
    # Sort and dedupe
    leap_expirations = sorted(set(leap_expirations))
    
    return leap_expirations


def find_strikes_by_delta(options: list, target_deltas: list, current_price: float) -> dict:
    """
    Find strikes closest to target deltas.
    Returns dict mapping delta -> option data
    """
    result = {}
    
    for target_delta in target_deltas:
        best_match = None
        best_diff = float('inf')
        
        for opt in options:
            if opt.get('delta') is None:
                continue
            
            delta = abs(opt['delta'])
            diff = abs(delta - target_delta)
            
            if diff < best_diff:
                best_diff = diff
                best_match = opt
        
        if best_match and best_diff < 0.15:  # Within 15 delta points
            result[target_delta] = best_match
    
    return result


def fetch_option_chain(ib: IB, ticker: str, expiry: str, current_price: float) -> list:
    """Fetch call options for a specific expiration with greeks"""
    
    # Define strike range (roughly 70% to 150% of current price for LEAPs)
    min_strike = current_price * 0.70
    max_strike = current_price * 1.50
    
    # Round to reasonable strike intervals
    if current_price > 100:
        strike_interval = 5
    elif current_price > 50:
        strike_interval = 2.5
    else:
        strike_interval = 1
    
    # Generate strike list
    strikes = []
    strike = math.floor(min_strike / strike_interval) * strike_interval
    while strike <= max_strike:
        strikes.append(strike)
        strike += strike_interval
    
    # Create option contracts for calls
    contracts = []
    for strike in strikes:
        opt = Option(ticker, expiry, strike, "C", "SMART")
        contracts.append(opt)
    
    # Qualify contracts
    qualified = []
    for contract in contracts:
        try:
            ib.qualifyContracts(contract)
            if contract.conId:
                qualified.append(contract)
        except:
            pass
    
    if not qualified:
        return []
    
    # Request market data for all options
    tickers = []
    for contract in qualified:
        ticker_data = ib.reqMktData(contract, "106", False, False)  # 106 = greeks
        tickers.append((contract, ticker_data))
    
    # Wait for data
    ib.sleep(3)
    
    options = []
    for contract, ticker_data in tickers:
        # Extract data
        bid = ticker_data.bid if ticker_data.bid and not util.isNan(ticker_data.bid) else 0
        ask = ticker_data.ask if ticker_data.ask and not util.isNan(ticker_data.ask) else 0
        
        # Get greeks from model
        greeks = ticker_data.modelGreeks
        
        if greeks:
            iv = greeks.impliedVol * 100 if greeks.impliedVol else 0
            delta = greeks.delta if greeks.delta else 0
            vega = greeks.vega if greeks.vega else 0
            theta = greeks.theta if greeks.theta else 0
        else:
            iv = delta = vega = theta = 0
        
        # Skip if no meaningful data
        if iv == 0 or delta == 0:
            ib.cancelMktData(contract)
            continue
        
        options.append({
            'strike': contract.strike,
            'expiry': contract.lastTradeDateOrContractMonth,
            'bid': bid,
            'ask': ask,
            'mid': (bid + ask) / 2 if bid and ask else 0,
            'iv': round(iv, 2),
            'delta': round(delta, 4),
            'vega': round(vega, 4),
            'theta': round(theta, 4),
            'oi': 0,  # Would need separate request
            'volume': 0
        })
        
        ib.cancelMktData(contract)
    
    return options


def analyze_mispricing(option: dict, vol_data: VolatilityData, min_gap: float) -> OptionData:
    """Analyze IV vs HV gap for mispricing"""
    
    iv = option['iv']
    
    # Calculate gaps (positive = HV > IV = potentially underpriced vol)
    hv_20_gap = vol_data.hv_20 - iv
    hv_60_gap = vol_data.hv_60 - iv
    hv_avg_gap = vol_data.avg_hv - iv
    
    # Determine if mispriced
    # Primary signal: recent HV significantly above LEAP IV
    is_mispriced = (hv_20_gap >= min_gap or hv_60_gap >= min_gap) and hv_avg_gap > 0
    
    # Score the opportunity (higher = better)
    # Weight recent vol more heavily
    mispricing_score = (hv_20_gap * 0.4 + hv_60_gap * 0.35 + hv_avg_gap * 0.25)
    
    # Boost score for high vega (more leverage on IV expansion)
    vega_boost = min(option['vega'] / 0.30, 1.5)  # Cap at 1.5x
    mispricing_score *= vega_boost
    
    return OptionData(
        ticker=vol_data.ticker,
        expiry=option['expiry'],
        strike=option['strike'],
        right="C",
        delta=option['delta'],
        iv=iv,
        bid=option['bid'],
        ask=option['ask'],
        mid=option['mid'],
        vega=option['vega'],
        theta=option['theta'],
        oi=option.get('oi', 0),
        volume=option.get('volume', 0),
        hv_20_gap=round(hv_20_gap, 2),
        hv_60_gap=round(hv_60_gap, 2),
        hv_avg_gap=round(hv_avg_gap, 2),
        is_mispriced=is_mispriced,
        mispricing_score=round(mispricing_score, 2)
    )


def scan_etf(ib: IB, ticker: str, sector: str, target_years: list, 
             target_deltas: list, min_gap: float) -> Optional[ScanResult]:
    """Complete scan of one ETF for LEAP IV mispricing"""
    
    print(f"\n{'='*50}")
    print(f"Scanning {ticker} ({sector})")
    print(f"{'='*50}")
    
    # 1. Get volatility data
    vol_data = fetch_volatility_data(ib, ticker, sector)
    if not vol_data:
        return None
    
    print(f"  HV20: {vol_data.hv_20}% | HV60: {vol_data.hv_60}% | HV252: {vol_data.hv_252}%")
    if vol_data.hv_756:
        print(f"  HV756 (3Y): {vol_data.hv_756}%")
    print(f"  Average HV: {vol_data.avg_hv:.1f}%")
    print(f"  Current Price: ${vol_data.current_price:.2f}")
    
    # 2. Get LEAP expirations
    expirations = get_leap_expirations(ib, ticker, target_years)
    if not expirations:
        print(f"  ⚠ No LEAP expirations found for {target_years}")
        return ScanResult(volatility=vol_data)
    
    print(f"  LEAP expirations: {', '.join(expirations)}")
    
    # 3. Scan each expiration
    all_options = []
    
    for expiry in expirations:
        print(f"\n  Fetching {expiry} chain...")
        
        chain = fetch_option_chain(ib, ticker, expiry, vol_data.current_price)
        if not chain:
            print(f"    ⚠ No options data for {expiry}")
            continue
        
        # Find options at target deltas
        delta_matches = find_strikes_by_delta(chain, target_deltas, vol_data.current_price)
        
        for target_delta, opt in delta_matches.items():
            analyzed = analyze_mispricing(opt, vol_data, min_gap)
            all_options.append(analyzed)
            
            delta_label = f"{int(target_delta*100)}Δ"
            status = "🔥 MISPRICED" if analyzed.is_mispriced else "  "
            print(f"    {delta_label} ${opt['strike']}: IV={opt['iv']:.1f}% | "
                  f"Gap: HV20={analyzed.hv_20_gap:+.1f}, HV60={analyzed.hv_60_gap:+.1f} {status}")
    
    # 4. Compile results
    mispriced = [o for o in all_options if o.is_mispriced]
    best = max(all_options, key=lambda x: x.mispricing_score) if all_options else None
    
    return ScanResult(
        volatility=vol_data,
        options=all_options,
        mispriced_count=len(mispriced),
        best_opportunity=best
    )


def generate_report(results: list, min_gap: float, target_years: list = None) -> str:
    """Generate HTML report of scan results"""
    
    # Sort by best mispricing score
    results_sorted = sorted(
        [r for r in results if r.best_opportunity],
        key=lambda x: x.best_opportunity.mispricing_score,
        reverse=True
    )
    
    # Count total mispriced
    total_mispriced = sum(r.mispriced_count for r in results)
    
    html = f'''<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LEAP IV Mispricing Scanner | {datetime.now().strftime("%Y-%m-%d")}</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    @import url('https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700&display=swap');
    
    :root {{
      --font-sans: 'Satoshi', -apple-system, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }}
    
    [data-theme="dark"] {{
      --bg-base: #050505;
      --bg-panel: #0A0A0A;
      --bg-hover: #141414;
      --border-dim: #1C1C1C;
      --border-focus: #333333;
      --text-primary: #F0F0F0;
      --text-muted: #666666;
      --accent-bg: #FFFFFF;
      --accent-text: #000000;
      --positive: #22C55E;
      --negative: #EF4444;
      --warning: #F59E0B;
    }}
    
    [data-theme="light"] {{
      --bg-base: #F5F5F5;
      --bg-panel: #FFFFFF;
      --bg-hover: #F0F0F0;
      --border-dim: #D9D9D9;
      --border-focus: #A3A3A3;
      --text-primary: #0A0A0A;
      --text-muted: #6B6B6B;
      --accent-bg: #000000;
      --accent-text: #FFFFFF;
      --positive: #16A34A;
      --negative: #DC2626;
      --warning: #D97706;
    }}
    
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    
    body {{
      background: var(--bg-base);
      color: var(--text-primary);
      font-family: var(--font-sans);
      font-size: 13px;
      line-height: 1.5;
      padding: 24px;
    }}
    
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border-dim);
    }}
    
    h1 {{
      font-family: var(--font-mono);
      font-size: 16px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.1em;
    }}
    
    .meta {{
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-muted);
    }}
    
    .theme-toggle {{
      background: transparent;
      border: 1px solid var(--border-dim);
      color: var(--text-primary);
      padding: 6px 12px;
      cursor: pointer;
      font-family: var(--font-mono);
      font-size: 12px;
    }}
    
    .theme-toggle:hover {{
      background: var(--bg-hover);
    }}
    
    .summary {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0;
      margin-bottom: 24px;
    }}
    
    .summary-card {{
      background: var(--bg-panel);
      border: 1px solid var(--border-dim);
      padding: 16px;
      margin-right: -1px;
    }}
    
    .summary-label {{
      font-family: var(--font-mono);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      margin-bottom: 8px;
    }}
    
    .summary-value {{
      font-family: var(--font-mono);
      font-size: 24px;
      font-weight: 500;
    }}
    
    .summary-value.positive {{ color: var(--positive); }}
    .summary-value.negative {{ color: var(--negative); }}
    
    .section {{
      background: var(--bg-panel);
      border: 1px solid var(--border-dim);
      margin-bottom: 24px;
    }}
    
    .section-header {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-dim);
      font-family: var(--font-mono);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    
    th {{
      font-family: var(--font-mono);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      text-align: left;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-dim);
      font-weight: 500;
    }}
    
    th.right {{ text-align: right; }}
    th.center {{ text-align: center; }}
    
    td {{
      font-family: var(--font-mono);
      font-size: 12px;
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-dim);
    }}
    
    td.right {{ text-align: right; }}
    td.center {{ text-align: center; }}
    
    tr:hover {{ background: var(--bg-hover); }}
    tr:last-child td {{ border-bottom: none; }}
    
    .pill {{
      display: inline-block;
      font-family: var(--font-mono);
      font-size: 10px;
      text-transform: uppercase;
      padding: 2px 6px;
      border: 1px solid;
    }}
    
    .pill.mispriced {{
      border-color: var(--positive);
      color: var(--positive);
    }}
    
    .pill.neutral {{
      border-color: var(--text-muted);
      color: var(--text-muted);
    }}
    
    .gap-positive {{ color: var(--positive); }}
    .gap-negative {{ color: var(--negative); }}
    
    .bar {{
      width: 80px;
      height: 6px;
      background: var(--border-dim);
      display: inline-block;
      vertical-align: middle;
      margin-right: 8px;
    }}
    
    .bar-fill {{
      height: 100%;
      background: var(--positive);
    }}
    
    .bar-fill.negative {{
      background: var(--negative);
    }}
    
    .etf-row {{
      background: var(--bg-hover);
    }}
    
    .etf-row td {{
      font-weight: 500;
    }}
    
    .option-row td {{
      padding-left: 32px;
      font-size: 11px;
    }}
    
    .score {{
      font-weight: 700;
    }}
    
    .score.high {{ color: var(--positive); }}
    .score.medium {{ color: var(--warning); }}
    .score.low {{ color: var(--text-muted); }}
    
    .methodology {{
      padding: 16px;
      font-size: 12px;
      color: var(--text-muted);
      border-top: 1px solid var(--border-dim);
    }}
    
    .methodology h3 {{
      font-family: var(--font-mono);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-primary);
      margin-bottom: 8px;
    }}
    
    .methodology p {{
      margin-bottom: 8px;
    }}
    
    .methodology code {{
      background: var(--bg-base);
      padding: 2px 4px;
      font-family: var(--font-mono);
      font-size: 11px;
    }}
  </style>
</head>
<body>
  <div class="header">
    <div>
      <h1>LEAP IV Mispricing Scanner</h1>
      <div class="meta">State Street Sector ETFs • 2027/2028 Expirations • Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
    </div>
    <button class="theme-toggle" onclick="toggleTheme()">◐ Theme</button>
  </div>
  
  <div class="summary">
    <div class="summary-card">
      <div class="summary-label">ETFs Scanned</div>
      <div class="summary-value">{len(results)}</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">Options Analyzed</div>
      <div class="summary-value">{sum(len(r.options) for r in results)}</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">Mispriced (HV-IV ≥ {min_gap})</div>
      <div class="summary-value positive">{total_mispriced}</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">Top Score</div>
      <div class="summary-value">{results_sorted[0].best_opportunity.mispricing_score if results_sorted else 0}</div>
    </div>
  </div>
'''

    # Mispriced Opportunities Table
    html += '''
  <div class="section">
    <div class="section-header">
      <span>🔥 Mispriced Opportunities (HV > IV)</span>
      <span class="pill mispriced">{} FOUND</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>ETF</th>
          <th>Sector</th>
          <th>Expiry</th>
          <th>Strike</th>
          <th class="center">Delta</th>
          <th class="right">IV</th>
          <th class="right">HV20</th>
          <th class="right">HV60</th>
          <th class="right">Gap (HV20-IV)</th>
          <th class="right">Vega</th>
          <th class="right">Score</th>
        </tr>
      </thead>
      <tbody>
'''.format(total_mispriced)
    
    for result in results_sorted:
        mispriced_opts = [o for o in result.options if o.is_mispriced]
        mispriced_opts.sort(key=lambda x: x.mispricing_score, reverse=True)
        
        for opt in mispriced_opts:
            score_class = "high" if opt.mispricing_score > 20 else ("medium" if opt.mispricing_score > 10 else "low")
            gap_class = "gap-positive" if opt.hv_20_gap > 0 else "gap-negative"
            
            html += f'''
        <tr>
          <td><strong>{opt.ticker}</strong></td>
          <td>{result.volatility.sector}</td>
          <td>{opt.expiry}</td>
          <td>${opt.strike}</td>
          <td class="center">{int(opt.delta*100)}Δ</td>
          <td class="right">{opt.iv:.1f}%</td>
          <td class="right">{result.volatility.hv_20:.1f}%</td>
          <td class="right">{result.volatility.hv_60:.1f}%</td>
          <td class="right {gap_class}">{opt.hv_20_gap:+.1f}%</td>
          <td class="right">{opt.vega:.3f}</td>
          <td class="right"><span class="score {score_class}">{opt.mispricing_score:.1f}</span></td>
        </tr>
'''
    
    html += '''
      </tbody>
    </table>
  </div>
'''
    
    # All ETFs Volatility Summary
    html += '''
  <div class="section">
    <div class="section-header">
      <span>📊 Volatility Summary — All ETFs</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>ETF</th>
          <th>Sector</th>
          <th class="right">Price</th>
          <th class="right">HV20</th>
          <th class="right">HV60</th>
          <th class="right">HV252</th>
          <th class="right">HV756 (3Y)</th>
          <th class="right">Avg HV</th>
          <th class="right">Best LEAP IV</th>
          <th class="right">Max Gap</th>
        </tr>
      </thead>
      <tbody>
'''
    
    for result in results:
        vol = result.volatility
        best_iv = min(o.iv for o in result.options) if result.options else 0
        max_gap = max(o.hv_20_gap for o in result.options) if result.options else 0
        gap_class = "gap-positive" if max_gap > 10 else ("gap-negative" if max_gap < -10 else "")
        
        html += f'''
        <tr>
          <td><strong>{vol.ticker}</strong></td>
          <td>{vol.sector}</td>
          <td class="right">${vol.current_price:.2f}</td>
          <td class="right">{vol.hv_20:.1f}%</td>
          <td class="right">{vol.hv_60:.1f}%</td>
          <td class="right">{vol.hv_252:.1f}%</td>
          <td class="right">{f"{vol.hv_756:.1f}%" if vol.hv_756 else "—"}</td>
          <td class="right">{vol.avg_hv:.1f}%</td>
          <td class="right">{best_iv:.1f}%</td>
          <td class="right {gap_class}">{max_gap:+.1f}%</td>
        </tr>
'''
    
    html += '''
      </tbody>
    </table>
  </div>
'''
    
    # Methodology
    html += f'''
  <div class="section">
    <div class="methodology">
      <h3>Methodology</h3>
      <p><strong>Mispricing Detection:</strong> Flag when <code>HV20 - LEAP IV ≥ {min_gap}%</code> or <code>HV60 - LEAP IV ≥ {min_gap}%</code></p>
      <p><strong>Score Calculation:</strong> <code>(HV20_gap × 0.4) + (HV60_gap × 0.35) + (Avg_gap × 0.25) × vega_boost</code></p>
      <p><strong>Target Deltas:</strong> 50Δ (ATM), 30Δ, 20Δ, 10Δ calls</p>
      <p><strong>Target Expirations:</strong> {", ".join(str(y) for y in (target_years or DEFAULT_YEARS))} LEAPs</p>
      <p><strong>Trade Thesis:</strong> Buy long-dated calls when forward vol is underpriced vs. realized. Profit from IV expansion (vega) even if underlying is flat.</p>
    </div>
  </div>
  
  <script>
    function toggleTheme() {{
      const html = document.documentElement;
      const current = html.getAttribute('data-theme');
      html.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
    }}
  </script>
</body>
</html>
'''
    
    return html


def load_portfolio_tickers() -> Dict[str, str]:
    """Load tickers from portfolio.json"""
    portfolio_path = Path(__file__).parent.parent / "data" / "portfolio.json"
    if not portfolio_path.exists():
        print("⚠ No portfolio.json found")
        return {}
    
    with open(portfolio_path) as f:
        portfolio = json.load(f)
    
    tickers = {}
    for pos in portfolio.get("positions", []):
        ticker = pos.get("ticker")
        if ticker and ticker not in tickers:
            tickers[ticker] = pos.get("structure_type", "Portfolio")
    
    return tickers


def load_watchlist_tickers(path: str) -> Dict[str, str]:
    """Load tickers from watchlist file"""
    watchlist_path = Path(path)
    if not watchlist_path.exists():
        print(f"⚠ Watchlist not found: {path}")
        return {}
    
    with open(watchlist_path) as f:
        watchlist = json.load(f)
    
    tickers = {}
    for item in watchlist:
        if isinstance(item, dict):
            ticker = item.get("ticker")
            sector = item.get("sector", "Watchlist")
        else:
            ticker = item
            sector = "Watchlist"
        if ticker:
            tickers[ticker] = sector
    
    return tickers


def main():
    parser = argparse.ArgumentParser(
        description="LEAP IV Mispricing Scanner — Find underpriced volatility in long-dated options",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s AAPL MSFT NVDA              Scan specific tickers
  %(prog)s --preset sectors            Scan State Street sector ETFs
  %(prog)s --preset mag7               Scan Magnificent 7
  %(prog)s --portfolio                 Scan current portfolio holdings
  %(prog)s --watchlist data/watch.json Scan from watchlist file
  %(prog)s TSLA --min-gap 20           Custom mispricing threshold
  %(prog)s --years 2026 2027           Custom expiration years

Available presets: sectors, mag7, semis, financials, energy, china, emerging
        """
    )
    
    # Ticker selection (mutually exclusive methods)
    parser.add_argument("tickers", nargs="*", help="Tickers to scan")
    parser.add_argument("--preset", type=str, choices=list(PRESETS.keys()),
                        help="Use a preset ticker group")
    parser.add_argument("--portfolio", action="store_true",
                        help="Scan tickers from portfolio.json")
    parser.add_argument("--watchlist", type=str,
                        help="Path to watchlist JSON file")
    
    # Scan parameters
    parser.add_argument("--years", type=int, nargs="+", default=DEFAULT_YEARS,
                        help=f"Target expiration years (default: {DEFAULT_YEARS})")
    parser.add_argument("--min-gap", type=float, default=MIN_IV_GAP,
                        help=f"Minimum HV-IV gap to flag (default: {MIN_IV_GAP}%%)")
    parser.add_argument("--deltas", type=float, nargs="+", default=TARGET_DELTAS,
                        help=f"Target deltas (default: {TARGET_DELTAS})")
    
    # Connection
    parser.add_argument("--host", default=DEFAULT_HOST, help="IB Gateway host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="IB Gateway port")
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID, help="Client ID")
    
    # Output
    parser.add_argument("--output", type=str, default="reports/leap-iv-scan.html",
                        help="Output HTML report path")
    parser.add_argument("--json", action="store_true",
                        help="Also output JSON results")
    
    args = parser.parse_args()
    
    # Determine tickers to scan (priority: positional > preset > portfolio > watchlist > default)
    if args.tickers:
        tickers_to_scan = {t.upper(): "Custom" for t in args.tickers}
        source = "command line"
    elif args.preset:
        tickers_to_scan = PRESETS[args.preset]
        source = f"preset '{args.preset}'"
    elif args.portfolio:
        tickers_to_scan = load_portfolio_tickers()
        source = "portfolio"
    elif args.watchlist:
        tickers_to_scan = load_watchlist_tickers(args.watchlist)
        source = f"watchlist '{args.watchlist}'"
    else:
        # Default: show help
        parser.print_help()
        print("\n⚠ No tickers specified. Use positional args, --preset, --portfolio, or --watchlist")
        sys.exit(1)
    
    if not tickers_to_scan:
        print("✗ No tickers to scan")
        sys.exit(1)
    
    # Connect to IB
    ib = connect_ib(args.host, args.port, args.client_id)
    
    target_years = args.years
    target_deltas = args.deltas
    
    print(f"\n{'='*60}")
    print("LEAP IV MISPRICING SCANNER")
    print(f"{'='*60}")
    print(f"Source: {source}")
    print(f"Tickers: {', '.join(tickers_to_scan.keys())}")
    print(f"Target Years: {target_years}")
    print(f"Target Deltas: {target_deltas}")
    print(f"Min Gap for Mispricing: {args.min_gap}%")
    print(f"{'='*60}")
    
    results = []
    
    try:
        for ticker, sector in tickers_to_scan.items():
            try:
                result = scan_etf(
                    ib, ticker, sector,
                    target_years, target_deltas, args.min_gap
                )
                if result:
                    results.append(result)
            except Exception as e:
                print(f"  ✗ Error scanning {ticker}: {e}")
                continue
        
        # Summary
        print(f"\n{'='*60}")
        print("SCAN COMPLETE")
        print(f"{'='*60}")
        
        total_mispriced = sum(r.mispriced_count for r in results)
        print(f"ETFs Scanned: {len(results)}")
        print(f"Total Options Analyzed: {sum(len(r.options) for r in results)}")
        print(f"Mispriced Opportunities: {total_mispriced}")
        
        if total_mispriced > 0:
            print(f"\n🔥 TOP MISPRICED:")
            
            all_mispriced = []
            for r in results:
                for o in r.options:
                    if o.is_mispriced:
                        all_mispriced.append((r.volatility, o))
            
            all_mispriced.sort(key=lambda x: x[1].mispricing_score, reverse=True)
            
            for vol, opt in all_mispriced[:5]:
                print(f"   {opt.ticker} {opt.expiry} ${opt.strike} {int(opt.delta*100)}Δ: "
                      f"IV={opt.iv:.1f}% vs HV20={vol.hv_20:.1f}% (gap: {opt.hv_20_gap:+.1f}%) "
                      f"Score: {opt.mispricing_score:.1f}")
        
        # Generate report
        if results:
            report_html = generate_report(results, args.min_gap, target_years)
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report_html)
            print(f"\n✓ Report saved to {output_path}")
            
            # JSON output if requested
            if args.json:
                json_output = {
                    "scan_time": datetime.now().isoformat(),
                    "parameters": {
                        "target_years": target_years,
                        "target_deltas": target_deltas,
                        "min_gap": args.min_gap,
                    },
                    "results": []
                }
                for r in results:
                    json_output["results"].append({
                        "ticker": r.volatility.ticker,
                        "sector": r.volatility.sector,
                        "price": r.volatility.current_price,
                        "hv_20": r.volatility.hv_20,
                        "hv_60": r.volatility.hv_60,
                        "hv_252": r.volatility.hv_252,
                        "hv_756": r.volatility.hv_756,
                        "avg_hv": r.volatility.avg_hv,
                        "mispriced_count": r.mispriced_count,
                        "options": [asdict(o) for o in r.options]
                    })
                json_path = output_path.with_suffix(".json")
                json_path.write_text(json.dumps(json_output, indent=2))
                print(f"✓ JSON saved to {json_path}")
        
    finally:
        ib.disconnect()
        print("✓ Disconnected from IB")


if __name__ == "__main__":
    main()
