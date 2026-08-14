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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from clients.uw_client import UWClient, UWRateLimitError

try:
    from db.scan_mirror import mirror_scan_snapshot  # type: ignore
except Exception:  # pragma: no cover — DB layer optional
    def mirror_scan_snapshot(*args, **kwargs):  # type: ignore
        return None

# UW /api/stock/{ticker}/oi-change supports limit + page (page from 0).
# Walk until a short page so M3B is not stuck on the first page.
_OI_PAGE_LIMIT = 500
_OI_MAX_PAGES = 40
# Market-wide /api/market/oi-change: max 200, no page cursor.
_MARKET_OI_LIMIT = 200

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_ET = ZoneInfo("America/New_York")
_UW_DAILY_LIMIT_MARKER = "daily request limit"
_UW_DAILY_RESET_HOUR_ET = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _embargo_path() -> Path:
    return _PROJECT_DIR / "data" / "oi_changes_uw_embargo.json"


def _is_uw_daily_quota(exc: BaseException) -> bool:
    return isinstance(exc, UWRateLimitError) and _UW_DAILY_LIMIT_MARKER in str(exc).lower()


def _uw_retry_at(exc: BaseException, now: datetime) -> str:
    """UW daily cap resets at 20:00 ET; other 429s get a 5-minute embargo."""
    if _is_uw_daily_quota(exc):
        local = now.astimezone(_ET)
        reset = local.replace(hour=_UW_DAILY_RESET_HOUR_ET, minute=0, second=0, microsecond=0)
        if local >= reset:
            reset = reset + timedelta(days=1)
        return reset.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return (now + timedelta(minutes=5)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_uw_embargo() -> Optional[datetime]:
    try:
        raw = json.loads(_embargo_path().read_text()).get("next_attempt_at")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _save_uw_embargo(next_attempt_at: str) -> None:
    path = _embargo_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"next_attempt_at": next_attempt_at}))


def _clear_uw_embargo() -> None:
    _embargo_path().unlink(missing_ok=True)


def _uw_embargo_active(now: datetime) -> Optional[str]:
    deadline = _load_uw_embargo()
    if deadline is None or now >= deadline:
        if deadline is not None:
            _clear_uw_embargo()
        return None
    return deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_cycle_error(exc: BaseException, *, now: datetime, next_attempt_at: str) -> None:
    message = str(exc).splitlines()[0].strip()[:300]
    try:
        from db import writer
    except ImportError:
        return
    try:
        writer.ensure_no_replica_for_writers()
        writer.record_service_health(
            "oi-changes",
            "error",
            finished_at=now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            error={"message": message, "next_attempt_at": next_attempt_at},
        )
    except Exception as write_exc:  # noqa: BLE001 — telemetry must not mask the cycle
        print(f"[oi-changes] error heartbeat non-fatal: {write_exc}", file=sys.stderr)


def _handle_market_uw_limit(exc: UWRateLimitError, now: datetime) -> int:
    """Heartbeat + embargo. Daily cap exits 0 so the oneshot does not page."""
    next_attempt = _uw_retry_at(exc, now)
    if _is_uw_daily_quota(exc):
        _save_uw_embargo(next_attempt)
    _record_cycle_error(exc, now=now, next_attempt_at=next_attempt)
    print(
        f"[oi-changes] {str(exc).splitlines()[0].strip()}; "
        f"{'keeping last snapshot until' if _is_uw_daily_quota(exc) else 'retry after'} {next_attempt}",
        file=sys.stderr,
    )
    return 0 if _is_uw_daily_quota(exc) else 1


def _passes_filters(item: dict, min_oi_change: int, min_premium: float) -> bool:
    oi_diff = item.get("oi_diff_plain", 0) or 0
    premium = float(item.get("prev_total_premium", 0) or 0)
    return abs(oi_diff) >= min_oi_change and premium >= min_premium


def _sort_oi_significance(rows: list) -> list:
    """Largest absolute OI move, then premium — not API response order."""
    return sorted(
        rows,
        key=lambda item: (
            abs(item.get("oi_diff_plain", 0) or 0),
            float(item.get("prev_total_premium", 0) or 0),
        ),
        reverse=True,
    )


def _apply_limit(rows: list, limit: Optional[int]) -> list:
    if limit is None or limit <= 0:
        return rows
    return rows[:limit]


def fetch_ticker_oi_changes(
    ticker: str,
    min_oi_change: int = 0,
    min_premium: float = 0,
    limit: Optional[int] = None,
) -> list:
    """Fetch OI changes for a specific ticker (full multi-page walk).

    ``limit`` caps results *after* paging + filter + significance sort.
    Pass ``None`` (default) for the complete filtered set — used by eval M3B.
    """
    with UWClient() as client:
        all_rows: list = []
        for page in range(_OI_MAX_PAGES):
            result = client.get_stock_oi_change(
                ticker, limit=_OI_PAGE_LIMIT, page=page,
            )
            page_rows = result.get("data") or []
            if not isinstance(page_rows, list) or not page_rows:
                break
            all_rows.extend(page_rows)
            if len(page_rows) < _OI_PAGE_LIMIT:
                break

        filtered = [
            item for item in all_rows
            if _passes_filters(item, min_oi_change, min_premium)
        ]
        return _apply_limit(_sort_oi_significance(filtered), limit)


def fetch_market_oi_changes(
    min_oi_change: int = 0,
    min_premium: float = 0,
    limit: Optional[int] = None,
) -> list:
    """Fetch market-wide biggest OI changes.

    UW market endpoint has no page cursor (max 200). Request the max, then
    filter/sort; ``limit`` only truncates the final ranked list.
    """
    with UWClient() as client:
        result = client.get_oi_change(limit=_MARKET_OI_LIMIT)
        data = result.get("data") or []
        if not isinstance(data, list):
            data = []

        filtered = [
            item for item in data
            if _passes_filters(item, min_oi_change, min_premium)
        ]
        ranked = _sort_oi_significance(filtered)
        return _apply_limit(ranked, limit)


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
    parser.add_argument(
        '--limit', type=int, default=50,
        help='Max results after full fetch+rank (default 50; 0 = no cap)',
    )
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    
    args = parser.parse_args()
    
    if not args.ticker and not args.market:
        parser.error('Either provide a ticker or use --market for market-wide scan')

    result_limit = None if args.limit == 0 else args.limit
    
    try:
        if args.market:
            now = _now()
            embargo_until = _uw_embargo_active(now)
            if embargo_until:
                _record_cycle_error(
                    UWRateLimitError(
                        "You have hit your daily request limit of 40000 requests."
                    ),
                    now=now,
                    next_attempt_at=embargo_until,
                )
                print(
                    f"[oi-changes] UW daily quota embargo until {embargo_until}; "
                    "keeping last snapshot",
                    file=sys.stderr,
                )
                return
            data = fetch_market_oi_changes(
                min_oi_change=args.min_oi_change,
                min_premium=args.min_premium,
                limit=result_limit,
            )
            ticker = None
        else:
            data = fetch_ticker_oi_changes(
                args.ticker.upper(),
                min_oi_change=args.min_oi_change,
                min_premium=args.min_premium,
                limit=result_limit,
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
            _clear_uw_embargo()
            mirror_scan_snapshot("oi-changes", output)

        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print_results(data, ticker)
            
    except UWRateLimitError as e:
        if args.market:
            sys.exit(_handle_market_uw_limit(e, _now()))
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
