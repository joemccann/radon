#!/usr/bin/env python3
"""
LEAP IV Mispricing Scanner (Unusual Whales primary)

Uses Unusual Whales for OHLC price data (HV calculation) and
LEAP option IV data. Yahoo Finance as ABSOLUTE LAST RESORT
for price data when UW fails.

No IB connection required.

API Reference: docs/unusual_whales_api.md
Full Spec: docs/unusual_whales_api_spec.yaml

Key endpoints used:
  - GET /api/stock/{ticker}/option-contracts - Options chain with IV
  - GET /api/stock/{ticker}/volatility/realized - IV vs realized volatility
  - GET /api/stock/{ticker}/info - Ticker metadata

Usage:
  python3 scripts/leap_scanner_uw.py XLK XLE XLF
  python3 scripts/leap_scanner_uw.py --preset sectors
  python3 scripts/leap_scanner_uw.py --preset mag7 --min-gap 20
  python3 scripts/leap_scanner_uw.py --preset row          # All country ETFs
  python3 scripts/leap_scanner_uw.py --preset row-europe   # European country ETFs
  python3 scripts/leap_scanner_uw.py --preset row-asia     # Asian country ETFs

Presets:
  sectors      - S&P 500 sector ETFs (XLK, XLE, XLF, etc.)
  mag7         - Magnificent 7 (AAPL, MSFT, NVDA, etc.)
  semis        - Semiconductors (NVDA, AMD, TSM, etc.)
  emerging     - Emerging market ETFs (EEM, EWZ, FXI, etc.)
  china        - China stocks and ETFs (BABA, FXI, KWEB, etc.)
  row          - Rest of World: 45 country-specific ETFs
  row-americas - Americas only (EWC, EWW, EWZ, etc.)
  row-europe   - Europe only (EWU, EWG, EWQ, etc.)
  row-asia     - Asia-Pacific only (EWJ, EWY, INDA, etc.)
  row-mena     - Middle East & Africa (EIS, EZA, KSA, etc.)
  metals       - Metals & Mining: Gold, Silver, Copper, Uranium, Miners
  energy       - Energy: Oil, Natural Gas, Refiners, MLPs, Clean Energy
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from dataclasses import dataclass, field, asdict

from clients.uw_client import UWClient, UWAPIError

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover — DB layer optional
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

DASHBOARD_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "leap.json"

# Preset ticker groups
PRESETS = {
    "sectors": ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"],
    "mag7": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
    "semis": ["NVDA", "AMD", "INTC", "AVGO", "QCOM", "MU", "AMAT", "LRCX", "TSM"],
    "emerging": ["EEM", "EWZ", "EWY", "EWT", "INDA", "FXI", "EWW", "ILF"],
    "china": ["BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI", "FXI", "KWEB"],
    # Rest of World - Country-specific ETFs
    "row": [
        # Americas
        "EWC",   # Canada
        "EWW",   # Mexico
        "EWZ",   # Brazil
        "ECH",   # Chile
        "ARGT",  # Argentina
        # Europe - Western
        "EWU",   # United Kingdom
        "EWG",   # Germany
        "EWQ",   # France
        "EWI",   # Italy
        "EWP",   # Spain
        "EWL",   # Switzerland
        "EWN",   # Netherlands
        "EWK",   # Belgium
        "EWO",   # Austria
        "EIRL",  # Ireland
        # Europe - Nordic
        "EWD",   # Sweden
        "NORW",  # Norway
        "EDEN",  # Denmark
        "EFNL",  # Finland
        # Europe - Eastern/Other
        "EPOL",  # Poland
        "GREK",  # Greece
        "TUR",   # Turkey
        # Asia Pacific - Developed
        "EWJ",   # Japan
        "EWY",   # South Korea
        "EWT",   # Taiwan
        "EWA",   # Australia
        "EWH",   # Hong Kong
        "EWS",   # Singapore
        # Asia Pacific - Emerging
        "FXI",   # China Large Cap
        "KWEB",  # China Internet
        "MCHI",  # China (broader)
        "INDA",  # India
        "EPI",   # India (WisdomTree)
        "EWM",   # Malaysia
        "THD",   # Thailand
        "VNM",   # Vietnam
        "EIDO",  # Indonesia
        # Middle East / Africa
        "EIS",   # Israel
        "EZA",   # South Africa
        "KSA",   # Saudi Arabia
        "UAE",   # United Arab Emirates
        "QAT",   # Qatar
    ],
    # Subsets of RoW for targeted scans
    "row-americas": ["EWC", "EWW", "EWZ", "ECH", "ARGT"],
    "row-europe": ["EWU", "EWG", "EWQ", "EWI", "EWP", "EWL", "EWN", "EWK", "EWO", "EIRL", "EWD", "NORW", "EDEN", "EFNL", "EPOL", "GREK", "TUR"],
    "row-asia": ["EWJ", "EWY", "EWT", "EWA", "EWH", "EWS", "FXI", "KWEB", "MCHI", "INDA", "EPI", "EWM", "THD", "VNM", "EIDO"],
    "row-mena": ["EIS", "EZA", "KSA", "UAE", "QAT"],
    # Metals - Commodities and Miners
    "metals": [
        # Physical / Spot ETFs
        "GLD",   # Gold (SPDR)
        "IAU",   # Gold (iShares)
        "GLDM",  # Gold Mini (SPDR)
        "SLV",   # Silver (iShares)
        "SIVR",  # Silver (abrdn)
        "PPLT",  # Platinum (abrdn)
        "PALL",  # Palladium (abrdn)
        "CPER",  # Copper (United States Copper)
        "DBB",   # Base Metals (DB)
        "DBA",   # Agriculture (DB) - included for commodity correlation
        # Gold Miners
        "GDX",   # Gold Miners (VanEck)
        "GDXJ",  # Junior Gold Miners (VanEck)
        "RING",  # Gold Miners (iShares)
        "GOAU",  # Gold Miners (US Global)
        # Silver Miners
        "SIL",   # Silver Miners (Global X)
        "SILJ",  # Junior Silver Miners (ETFMG)
        # Broad Mining / Materials
        "XME",   # Metals & Mining (SPDR)
        "PICK",  # Global Metals & Mining (iShares)
        "COPX",  # Copper Miners (Global X)
        "LIT",   # Lithium & Battery Tech (Global X)
        "REMX",  # Rare Earth / Strategic Metals (VanEck)
        "URA",   # Uranium (Global X)
        "URNM",  # Uranium Miners (Sprott)
    ],
    # Energy - Oil, Gas, Refiners, Infrastructure
    "energy": [
        # Broad Energy
        "XLE",   # Energy Select Sector (SPDR)
        "VDE",   # Energy (Vanguard)
        "IYE",   # US Energy (iShares)
        "XOP",   # Oil & Gas E&P (SPDR)
        "IEO",   # US Oil & Gas E&P (iShares)
        # Oil
        "USO",   # Oil Fund (United States)
        "BNO",   # Brent Oil (United States)
        "DBO",   # Oil Fund (DB)
        "OIH",   # Oil Services (VanEck)
        "IEZ",   # US Oil Equipment & Services (iShares)
        # Natural Gas
        "UNG",   # Natural Gas Fund (United States)
        "BOIL",  # 2x Natural Gas (ProShares)
        "FCG",   # Natural Gas (First Trust)
        # Refiners / Downstream
        "CRAK",  # Oil Refiners (VanEck)
        "PXE",   # Energy E&P (Invesco)
        # MLPs / Midstream / Infrastructure
        "AMLP",  # MLP (Alerian)
        "MLPA",  # MLP (Global X)
        "EMLP",  # Energy Infrastructure (First Trust)
        "TPYP",  # Midstream Energy (Tortoise)
        # Clean Energy (for comparison)
        "ICLN",  # Global Clean Energy (iShares)
        "TAN",   # Solar (Invesco)
        "QCLN",  # Clean Energy (First Trust)
        "PBW",   # Clean Energy (Invesco)
    ],
}

MIN_IV_GAP = 15


@dataclass
class VolData:
    ticker: str
    price: float
    hv_20: float
    hv_60: float
    hv_252: float
    avg_hv: float


@dataclass
class LeapOption:
    symbol: str
    expiry: str
    strike: float
    right: str
    iv: float
    volume: int
    oi: int
    delta_approx: float  # Approximate based on moneyness


@dataclass
class ScanResult:
    ticker: str
    vol_data: VolData
    current_iv: float
    iv_rank: float
    leaps: List[LeapOption]
    best_gap: float
    is_mispriced: bool


def get_uw_history(ticker: str, uw_client: Optional[UWClient] = None) -> List[float]:
    """Fetch historical daily closes from Unusual Whales OHLC endpoint."""
    try:
        if uw_client:
            data = uw_client.get_stock_ohlc(ticker, candle_size="1d")
        else:
            with UWClient() as client:
                data = client.get_stock_ohlc(ticker, candle_size="1d")
        bars = data.get("data", [])
        if bars:
            return [float(b["close"]) for b in bars if b.get("close") is not None]
    except Exception:
        pass
    return []


def get_yahoo_history(ticker: str, days: int = 400) -> List[float]:
    """ABSOLUTE LAST RESORT: Fetch historical daily closes from Yahoo Finance."""
    end = int(datetime.now().timestamp())
    start = int((datetime.now() - timedelta(days=days)).timestamp())
    
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start}&period2={end}&interval=1d"
    
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.load(resp)
        
        result = data['chart']['result'][0]
        closes = result['indicators']['quote'][0]['close']
        return [c for c in closes if c is not None]
    except Exception as e:
        print(f"  ⚠ Yahoo Finance error for {ticker}: {e}")
        return []


def get_price_history(ticker: str, uw_client: Optional[UWClient] = None) -> List[float]:
    """Fetch daily closes: UW primary, Yahoo ABSOLUTE LAST RESORT."""
    prices = get_uw_history(ticker, uw_client)
    if len(prices) >= 60:
        return prices
    # LAST RESORT
    return get_yahoo_history(ticker)


def calculate_hv(prices: List[float], period: int) -> Optional[float]:
    """Calculate annualized historical volatility."""
    if len(prices) < period + 1:
        return None
    
    recent = prices[-(period + 1):]
    returns = []
    for i in range(1, len(recent)):
        if recent[i-1] > 0:
            returns.append(math.log(recent[i] / recent[i-1]))
    
    if len(returns) < 2:
        return None
    
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(variance)
    
    return daily_vol * math.sqrt(252) * 100


def get_vol_data(ticker: str, uw_client: Optional[UWClient] = None) -> Optional[VolData]:
    """Get historical volatility data for a ticker."""
    prices = get_price_history(ticker, uw_client)
    
    if len(prices) < 60:
        print(f"  ⚠ Insufficient price data for {ticker}")
        return None
    
    current_price = prices[-1]
    hv_20 = calculate_hv(prices, 20) or 0
    hv_60 = calculate_hv(prices, 60) or 0
    hv_252 = calculate_hv(prices, 252) or hv_60
    
    avg_hv = (hv_20 + hv_60 + hv_252) / 3
    
    return VolData(
        ticker=ticker,
        price=current_price,
        hv_20=round(hv_20, 2),
        hv_60=round(hv_60, 2),
        hv_252=round(hv_252, 2),
        avg_hv=round(avg_hv, 2)
    )


def get_current_iv(ticker: str, _client: UWClient = None) -> tuple:
    """Get current IV and IV rank from UW."""
    def _fetch(client):
        try:
            data = client.get_iv_rank(ticker)
        except UWAPIError:
            return 0, 0
        if 'data' in data and data['data']:
            latest = data['data'][0]
            iv = float(latest.get('volatility', 0)) * 100
            rank = float(latest.get('iv_rank_1y', 0))
            return iv, rank
        return 0, 0

    if _client is not None:
        return _fetch(_client)
    with UWClient() as client:
        return _fetch(client)


def get_leap_options(ticker: str, min_year: int = 2027, _client: UWClient = None) -> List[LeapOption]:
    """Get LEAP call options from UW."""
    def _fetch(client):
        try:
            return client.get_option_contracts(ticker)
        except UWAPIError:
            return {}

    if _client is not None:
        data = _fetch(_client)
    else:
        with UWClient() as client:
            data = _fetch(client)

    if 'data' not in data:
        return []
    
    leaps = []
    for c in data['data']:
        symbol = c['option_symbol']
        
        # Parse option symbol: XLK270115C00150000
        # Format: TICKER + YYMMDD + C/P + STRIKE*1000
        try:
            # Find where the date starts (after ticker)
            for i in range(3, len(symbol)):
                if symbol[i:i+2].isdigit():
                    date_start = i
                    break
            else:
                continue
            
            date_str = symbol[date_start:date_start+6]
            year = int("20" + date_str[:2])
            
            if year < min_year:
                continue
            
            right_idx = date_start + 6
            right = symbol[right_idx]
            
            if right != 'C':  # Only calls for now
                continue
            
            strike = int(symbol[right_idx+1:]) / 1000
            expiry = f"20{date_str[:2]}-{date_str[2:4]}-{date_str[4:6]}"
            
            iv = float(c.get('implied_volatility', 0)) * 100
            volume = int(c.get('volume', 0))
            oi = int(c.get('open_interest', 0))
            
            if iv == 0:
                continue
            
            leaps.append(LeapOption(
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                right='C',
                iv=round(iv, 2),
                volume=volume,
                oi=oi,
                delta_approx=0  # Will calculate below
            ))
        except (ValueError, IndexError):
            continue
    
    return leaps


def approximate_delta(strike: float, price: float, iv: float, dte: int) -> float:
    """Rough delta approximation based on moneyness."""
    if price == 0 or dte == 0:
        return 0.5
    
    moneyness = price / strike
    
    # Very rough approximation
    if moneyness > 1.2:
        return 0.8
    elif moneyness > 1.05:
        return 0.6
    elif moneyness > 0.95:
        return 0.5
    elif moneyness > 0.85:
        return 0.35
    elif moneyness > 0.75:
        return 0.2
    else:
        return 0.1


def scan_ticker(ticker: str, min_gap: float) -> Optional[ScanResult]:
    """Scan a single ticker for LEAP IV mispricing."""
    print(f"\n{'='*50}")
    print(f"Scanning {ticker}")
    print(f"{'='*50}")

    # Get current IV and LEAP options using shared client
    with UWClient() as client:
        # Get historical volatility (UW primary, Yahoo LAST RESORT)
        vol_data = get_vol_data(ticker, uw_client=client)
        if not vol_data:
            return None

        print(f"  Price: ${vol_data.price:.2f}")
        print(f"  HV20: {vol_data.hv_20:.1f}% | HV60: {vol_data.hv_60:.1f}% | HV252: {vol_data.hv_252:.1f}%")
        print(f"  Avg HV: {vol_data.avg_hv:.1f}%")

        current_iv, iv_rank = get_current_iv(ticker, _client=client)
        print(f"  Current IV: {current_iv:.1f}% | IV Rank: {iv_rank:.1f}")

        leaps = get_leap_options(ticker, _client=client)
    if not leaps:
        print(f"  ⚠ No LEAP options found")
        return None
    
    print(f"  Found {len(leaps)} LEAP calls")
    
    # Calculate deltas and find interesting strikes
    for leap in leaps:
        dte = (datetime.strptime(leap.expiry, "%Y-%m-%d") - datetime.now()).days
        leap.delta_approx = approximate_delta(leap.strike, vol_data.price, leap.iv, dte)
    
    # Group by approximate delta
    delta_groups = {
        "50Δ (ATM)": [l for l in leaps if 0.45 <= l.delta_approx <= 0.55],
        "30Δ": [l for l in leaps if 0.25 <= l.delta_approx < 0.45],
        "20Δ": [l for l in leaps if 0.15 <= l.delta_approx < 0.25],
        "10Δ": [l for l in leaps if 0.05 <= l.delta_approx < 0.15],
    }
    
    # Find best mispricing
    best_gap = 0
    is_mispriced = False
    
    print(f"\n  LEAP IV Analysis:")
    for group_name, group_leaps in delta_groups.items():
        if not group_leaps:
            continue
        
        # Average IV for this delta group
        avg_iv = sum(l.iv for l in group_leaps) / len(group_leaps)
        
        # Gap vs HV
        gap_20 = vol_data.hv_20 - avg_iv
        gap_60 = vol_data.hv_60 - avg_iv
        
        status = ""
        if gap_20 >= min_gap or gap_60 >= min_gap:
            status = "🔥 MISPRICED"
            is_mispriced = True
            if gap_20 > best_gap:
                best_gap = gap_20
        
        print(f"    {group_name}: IV={avg_iv:.1f}% | Gap vs HV20: {gap_20:+.1f}% | vs HV60: {gap_60:+.1f}% {status}")
    
    return ScanResult(
        ticker=ticker,
        vol_data=vol_data,
        current_iv=current_iv,
        iv_rank=iv_rank,
        leaps=leaps,
        best_gap=best_gap,
        is_mispriced=is_mispriced
    )


def generate_report(results: List[ScanResult], min_gap: float) -> str:
    """Generate HTML report."""
    
    mispriced = [r for r in results if r.is_mispriced]
    
    html = f'''<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LEAP IV Mispricing Scan | {datetime.now().strftime("%Y-%m-%d")}</title>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    :root {{ --bg: #050505; --panel: #0A0A0A; --border: #1C1C1C; --text: #F0F0F0; --muted: #666; --green: #22C55E; --red: #EF4444; }}
    [data-theme="light"] {{ --bg: #F5F5F5; --panel: #FFF; --border: #D9D9D9; --text: #0A0A0A; --muted: #6B6B6B; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'JetBrains Mono', monospace; font-size: 12px; padding: 20px; }}
    h1 {{ font-size: 14px; margin-bottom: 20px; text-transform: uppercase; letter-spacing: 0.1em; }}
    .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--border); margin-bottom: 20px; }}
    .summary-card {{ background: var(--panel); padding: 16px; }}
    .summary-label {{ font-size: 10px; color: var(--muted); text-transform: uppercase; margin-bottom: 8px; }}
    .summary-value {{ font-size: 24px; font-weight: 700; }}
    .section {{ background: var(--panel); border: 1px solid var(--border); margin-bottom: 20px; }}
    .section-header {{ padding: 12px 16px; border-bottom: 1px solid var(--border); font-size: 11px; text-transform: uppercase; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 10px 16px; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{ font-size: 10px; color: var(--muted); text-transform: uppercase; }}
    .right {{ text-align: right; }}
    .green {{ color: var(--green); }}
    .red {{ color: var(--red); }}
    .pill {{ display: inline-block; padding: 2px 6px; border: 1px solid; font-size: 10px; }}
    .pill.green {{ border-color: var(--green); color: var(--green); }}
    .pill.red {{ border-color: var(--red); color: var(--red); }}
    .toggle {{ position: fixed; top: 20px; right: 20px; background: var(--panel); border: 1px solid var(--border); color: var(--text); padding: 8px 12px; cursor: pointer; }}
  </style>
</head>
<body>
  <button class="toggle" onclick="document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'">◐</button>
  
  <h1>LEAP IV Mispricing Scanner</h1>
  
  <div class="summary">
    <div class="summary-card">
      <div class="summary-label">Tickers Scanned</div>
      <div class="summary-value">{len(results)}</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">LEAP Contracts</div>
      <div class="summary-value">{sum(len(r.leaps) for r in results)}</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">Mispriced (Gap ≥{min_gap}%)</div>
      <div class="summary-value green">{len(mispriced)}</div>
    </div>
    <div class="summary-card">
      <div class="summary-label">Best Gap</div>
      <div class="summary-value">{max((r.best_gap for r in results), default=0):.1f}%</div>
    </div>
  </div>
'''
    
    if mispriced:
        html += '''
  <div class="section">
    <div class="section-header">🔥 Mispriced Opportunities</div>
    <table>
      <tr>
        <th>Ticker</th>
        <th class="right">Price</th>
        <th class="right">HV20</th>
        <th class="right">HV60</th>
        <th class="right">Avg LEAP IV</th>
        <th class="right">Gap</th>
        <th class="right">IV Rank</th>
      </tr>
'''
        for r in sorted(mispriced, key=lambda x: x.best_gap, reverse=True):
            avg_leap_iv = sum(l.iv for l in r.leaps) / len(r.leaps) if r.leaps else 0
            html += f'''
      <tr>
        <td><strong>{r.ticker}</strong></td>
        <td class="right">${r.vol_data.price:.2f}</td>
        <td class="right">{r.vol_data.hv_20:.1f}%</td>
        <td class="right">{r.vol_data.hv_60:.1f}%</td>
        <td class="right">{avg_leap_iv:.1f}%</td>
        <td class="right green">+{r.best_gap:.1f}%</td>
        <td class="right">{r.iv_rank:.1f}</td>
      </tr>
'''
        html += '''
    </table>
  </div>
'''
    
    # All tickers summary
    html += '''
  <div class="section">
    <div class="section-header">All Tickers</div>
    <table>
      <tr>
        <th>Ticker</th>
        <th class="right">Price</th>
        <th class="right">HV20</th>
        <th class="right">HV60</th>
        <th class="right">HV252</th>
        <th class="right">Current IV</th>
        <th class="right">IV Rank</th>
        <th class="right">LEAPs</th>
        <th class="right">Status</th>
      </tr>
'''
    for r in results:
        status = '<span class="pill green">MISPRICED</span>' if r.is_mispriced else '<span class="pill">OK</span>'
        html += f'''
      <tr>
        <td><strong>{r.ticker}</strong></td>
        <td class="right">${r.vol_data.price:.2f}</td>
        <td class="right">{r.vol_data.hv_20:.1f}%</td>
        <td class="right">{r.vol_data.hv_60:.1f}%</td>
        <td class="right">{r.vol_data.hv_252:.1f}%</td>
        <td class="right">{r.current_iv:.1f}%</td>
        <td class="right">{r.iv_rank:.1f}</td>
        <td class="right">{len(r.leaps)}</td>
        <td class="right">{status}</td>
      </tr>
'''
    
    html += f'''
    </table>
  </div>
  
  <div class="section">
    <div class="section-header" style="color: var(--muted)">
      Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Data: Unusual Whales (HV, IV) · Yahoo Finance (LAST RESORT fallback)
    </div>
  </div>
</body>
</html>
'''
    return html


def main():
    parser = argparse.ArgumentParser(
        description="LEAP IV Mispricing Scanner (UW primary, Yahoo LAST RESORT)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument("tickers", nargs="*", help="Tickers to scan")
    parser.add_argument("--preset", help="Use preset ticker group (built-in or from data/presets/)")
    parser.add_argument("--min-gap", type=float, default=MIN_IV_GAP, help=f"Min HV-IV gap (default: {MIN_IV_GAP})")
    parser.add_argument("--output", default="reports/leap-scan-uw.html", help="Output file")
    parser.add_argument("--json", action="store_true", help="Also output JSON")
    parser.add_argument("--list-presets", action="store_true", help="List all available presets")
    
    args = parser.parse_args()

    # List presets mode
    if args.list_presets:
        print("\n=== BUILT-IN PRESETS ===\n")
        for name in sorted(PRESETS.keys()):
            print(f"  {name:20s}  {len(PRESETS[name]):>4d} tickers")
        print("\n=== FILE PRESETS (data/presets/) ===\n")
        try:
            from utils.presets import list_presets
            for name, desc, count in list_presets():
                print(f"  {name:50s}  {count:>4d} tickers  {desc[:60]}")
        except ImportError:
            print("  (preset loader not available)")
        sys.exit(0)
    
    # Determine tickers
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    elif args.preset:
        # Check built-in presets first, then file presets
        if args.preset in PRESETS:
            tickers = PRESETS[args.preset]
        else:
            try:
                from utils.presets import load_preset
                p = load_preset(args.preset)
                tickers = p.tickers
                print(f"📂 Loaded preset: {p.name} ({p.ticker_count} tickers)")
            except (FileNotFoundError, ImportError) as e:
                print(f"\n❌ Preset '{args.preset}' not found.")
                print(f"   Built-in: {', '.join(sorted(PRESETS.keys()))}")
                print(f"   Use --list-presets to see all available")
                sys.exit(1)
    else:
        parser.print_help()
        print("\n⚠ Specify tickers or use --preset")
        sys.exit(1)
    
    print(f"\n{'='*60}")
    print("LEAP IV MISPRICING SCANNER")
    print(f"{'='*60}")
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Min Gap: {args.min_gap}%")
    print(f"Data: Unusual Whales (HV, IV) · Yahoo Finance (LAST RESORT fallback)")
    
    results = []
    for ticker in tickers:
        try:
            result = scan_ticker(ticker, args.min_gap)
            if result:
                results.append(result)
        except Exception as e:
            print(f"  ✗ Error scanning {ticker}: {e}")
    
    # Summary
    print(f"\n{'='*60}")
    print("SCAN COMPLETE")
    print(f"{'='*60}")
    
    mispriced = [r for r in results if r.is_mispriced]
    print(f"Scanned: {len(results)}")
    print(f"Mispriced: {len(mispriced)}")
    
    if mispriced:
        print("\n🔥 TOP OPPORTUNITIES:")
        for r in sorted(mispriced, key=lambda x: x.best_gap, reverse=True)[:5]:
            print(f"   {r.ticker}: HV20={r.vol_data.hv_20:.1f}% vs LEAP IV gap +{r.best_gap:.1f}%")
    
    # Generate report
    if results:
        report = generate_report(results, args.min_gap)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)
        print(f"\n✓ Report saved to {output_path}")
        
        if args.json:
            json_data = {
                "scan_time": datetime.now().isoformat(),
                "min_gap": args.min_gap,
                "results": [
                    {
                        "ticker": r.ticker,
                        "price": r.vol_data.price,
                        "hv_20": r.vol_data.hv_20,
                        "hv_60": r.vol_data.hv_60,
                        "hv_252": r.vol_data.hv_252,
                        "current_iv": r.current_iv,
                        "iv_rank": r.iv_rank,
                        "leap_count": len(r.leaps),
                        "best_gap": r.best_gap,
                        "is_mispriced": r.is_mispriced,
                    }
                    for r in results
                ]
            }
            json_path = output_path.with_suffix(".json")
            json_path.write_text(json.dumps(json_data, indent=2))
            print(f"✓ JSON saved to {json_path}")

            # Also mirror to data/leap.json so the dashboard
            # Opportunities → LEAP tab can read the latest scan via the
            # /api/leap route. Other scans (scanner, discover) follow the
            # same data/<scan>.json convention.
            cache_path = DASHBOARD_CACHE_PATH
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(json_data, indent=2))
            print(f"✓ Dashboard cache saved to {cache_path}")
            # No leap table in Turso — the file cache is canonical; the
            # service_health row lets the banner spot a stale scheduled scan.
            mirror_scan_snapshot("leap-scan", json_data)


if __name__ == "__main__":
    main()
