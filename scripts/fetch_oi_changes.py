#!/usr/bin/env python3
"""
Fetch Open Interest Changes

Fetches OI change data from UW to identify significant institutional positioning
that may not appear in flow alerts.

Usage:
    python3 scripts/fetch_oi_changes.py MSFT
    python3 scripts/fetch_oi_changes.py MSFT --min-oi-change 10000
    python3 scripts/fetch_oi_changes.py MSFT --min-premium 1000000
    python3 scripts/fetch_oi_changes.py --market  # Market-wide scan
    python3 scripts/fetch_oi_changes.py --json

This is a REQUIRED part of every evaluation. Flow alerts may miss large trades
that don't trigger "unusual" filters. OI changes show ALL significant positioning.

See docs/options-flow-verification.md for methodology.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, List

sys.path.insert(0, str(Path(__file__).parent))

from clients.uw_client import UWClient

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover — DB layer optional
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None


def fetch_ticker_oi_changes(
    ticker: str,
    min_oi_change: int = 0,
    min_premium: float = 0,
    limit: int = 50,
) -> list:
    """Fetch OI changes for a specific ticker."""
    with UWClient() as client:
        result = client.get_stock_oi_change(ticker)
        data = result.get('data', [])
        
        # Filter
        filtered = []
        for item in data:
            oi_diff = item.get('oi_diff_plain', 0) or 0
            premium = float(item.get('prev_total_premium', 0) or 0)
            
            if abs(oi_diff) >= min_oi_change and premium >= min_premium:
                filtered.append(item)
        
        return filtered[:limit]


def fetch_market_oi_changes(
    min_oi_change: int = 0,
    min_premium: float = 0,
    limit: int = 50,
) -> list:
    """Fetch market-wide biggest OI changes."""
    with UWClient() as client:
        result = client.get_oi_change()
        data = result.get('data', [])
        
        # Filter
        filtered = []
        for item in data:
            oi_diff = item.get('oi_diff_plain', 0) or 0
            premium = float(item.get('prev_total_premium', 0) or 0)
            
            if abs(oi_diff) >= min_oi_change and premium >= min_premium:
                filtered.append(item)
        
        return filtered[:limit]


def categorize_signal(item: dict) -> dict:
    """Categorize the OI change signal."""
    oi_diff = item.get('oi_diff_plain', 0) or 0
    premium = float(item.get('prev_total_premium', 0) or 0)
    symbol = item.get('option_symbol', '')
    
    # Determine option type
    is_call = 'C' in symbol[-9:-8] if len(symbol) > 9 else True
    
    # Determine if LEAP (DTE > 180)
    # Symbol format: MSFT270115C00625000
    # Extract expiry from symbol
    is_leap = '27' in symbol[4:10] or '28' in symbol[4:10]  # 2027 or 2028
    
    # Signal strength
    if premium >= 10_000_000:
        strength = 'MASSIVE'
    elif premium >= 5_000_000:
        strength = 'LARGE'
    elif premium >= 1_000_000:
        strength = 'SIGNIFICANT'
    else:
        strength = 'MODERATE'
    
    # Direction
    direction = 'BULLISH' if is_call else 'BEARISH'
    if oi_diff < 0:
        direction = 'CLOSING ' + direction
    
    return {
        'strength': strength,
        'direction': direction,
        'is_leap': is_leap,
        'is_call': is_call,
    }


def print_results(data: list, ticker: Optional[str] = None):
    """Print formatted results."""
    title = f'{ticker} OI CHANGES' if ticker else 'MARKET-WIDE OI CHANGES'
    print('=' * 90)
    print(title)
    print('=' * 90)
    print()
    
    if not data:
        print('No significant OI changes found.')
        return
    
    print(f'{"Symbol":<25} {"Ticker":<6} {"OI Change":>12} {"Curr OI":>10} {"Premium":>14} {"Signal":<10}')
    print('-' * 90)
    
    for item in data:
        symbol = item.get('option_symbol', '')
        underlying = item.get('underlying_symbol', symbol[:4])
        oi_diff = item.get('oi_diff_plain', 0) or 0
        curr_oi = item.get('curr_oi', 0) or 0
        premium = float(item.get('prev_total_premium', 0) or 0)
        
        cat = categorize_signal(item)
        signal = cat['strength']
        
        print(
            f'{symbol:<25} '
            f'{underlying:<6} '
            f'{oi_diff:>+12,} '
            f'{curr_oi:>10,} '
            f'${premium:>12,.0f} '
            f'{signal:<10}'
        )
    
    print()
    
    # Summary
    total_premium = sum(float(item.get('prev_total_premium', 0) or 0) for item in data)
    total_oi = sum(item.get('oi_diff_plain', 0) or 0 for item in data)
    
    print('-' * 90)
    print(f'Total: {len(data)} changes | OI: {total_oi:+,} contracts | Premium: ${total_premium:,.0f}')
    print()
    
    # Highlight massive positions
    massive = [item for item in data if categorize_signal(item)['strength'] == 'MASSIVE']
    if massive:
        print('🚨 MASSIVE POSITIONS (>$10M):')
        for item in massive:
            symbol = item.get('option_symbol', '')
            premium = float(item.get('prev_total_premium', 0) or 0)
            oi_diff = item.get('oi_diff_plain', 0) or 0
            cat = categorize_signal(item)
            leap_tag = ' [LEAP]' if cat['is_leap'] else ''
            print(f'   {symbol}: {oi_diff:+,} contracts, ${premium/1e6:.1f}M {cat["direction"]}{leap_tag}')
        print()


def main():
    parser = argparse.ArgumentParser(
        description='Fetch Open Interest changes to identify institutional positioning',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Ticker-specific OI changes
  python3 scripts/fetch_oi_changes.py MSFT

  # Filter by minimum OI change
  python3 scripts/fetch_oi_changes.py MSFT --min-oi-change 10000

  # Filter by minimum premium
  python3 scripts/fetch_oi_changes.py MSFT --min-premium 1000000

  # Market-wide scan for biggest OI changes
  python3 scripts/fetch_oi_changes.py --market

  # JSON output
  python3 scripts/fetch_oi_changes.py MSFT --json
        """
    )
    
    parser.add_argument('ticker', nargs='?', help='Stock ticker symbol')
    parser.add_argument('--market', '-m', action='store_true', help='Market-wide OI changes')
    parser.add_argument('--min-oi-change', type=int, default=0, help='Minimum OI change (contracts)')
    parser.add_argument('--min-premium', type=float, default=0, help='Minimum premium ($)')
    parser.add_argument('--limit', type=int, default=50, help='Max results')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    
    args = parser.parse_args()
    
    if not args.ticker and not args.market:
        parser.error('Either provide a ticker or use --market for market-wide scan')
    
    try:
        if args.market:
            data = fetch_market_oi_changes(
                min_oi_change=args.min_oi_change,
                min_premium=args.min_premium,
                limit=args.limit,
            )
            ticker = None
        else:
            data = fetch_ticker_oi_changes(
                args.ticker.upper(),
                min_oi_change=args.min_oi_change,
                min_premium=args.min_premium,
                limit=args.limit,
            )
            ticker = args.ticker.upper()
        
        output = {
            'ticker': ticker,
            'market_wide': args.market,
            'data': data,
        }
        if args.market:
            # Only the market-wide scan heartbeats — per-ticker evaluation
            # lookups would mask a dead scheduled scan.
            mirror_scan_snapshot("oi-changes", output)

        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print_results(data, ticker)
            
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
