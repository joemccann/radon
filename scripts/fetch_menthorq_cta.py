#!/usr/bin/env python3
"""Fetch MenthorQ CTA positioning data via MenthorQClient.

Delegates browser automation and Vision extraction to MenthorQClient,
handles caching as daily JSON in data/menthorq_cache/.

Credentials (project root .env, loaded via a local fallback loader):
  MENTHORQ_USER  — MenthorQ email/username
  MENTHORQ_PASS  — MenthorQ password

Vision API key (from web/.env or shell):
  ANTHROPIC_API_KEY / CLAUDE_CODE_API_KEY / CLAUDE_API_KEY

Usage:
    python3 scripts/fetch_menthorq_cta.py              # Fetch + cache + print summary
    python3 scripts/fetch_menthorq_cta.py --json        # JSON to stdout
    python3 scripts/fetch_menthorq_cta.py --date 2026-03-06  # Specific date
    python3 scripts/fetch_menthorq_cta.py --force --save-images  # Re-fetch + save S3 PNGs to tmp/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── path setup ────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
CACHE_DIR = _PROJECT_DIR / "data" / "menthorq_cache"

from utils.env_loader import load_env_file
from utils.atomic_io import atomic_save
from utils.cta_percentiles import reconcile_tables

# Load .env from project root (before any os.environ reads)
load_env_file(_PROJECT_DIR / ".env")
STORAGE_STATE_PATH = _PROJECT_DIR / "data" / "menthorq_cache" / "menthorq_storage_state.json"

# ── MenthorQ CTA table slugs ─────────────────────────────────────
CTA_TABLES = {
    "main": "cta_table",
    "index": "cta_index",
    "commodity": "cta_commodity",
    "currency": "cta_currency",
}

def is_market_open() -> bool:
    """Check if US equity markets are currently open."""
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
    except Exception:
        from datetime import timedelta as _td
        now_utc = datetime.now(timezone.utc)
        et_offset = _td(hours=-5)
        now_et = now_utc + et_offset
        return now_et.weekday() < 5 and 9 * 60 + 30 <= now_et.hour * 60 + now_et.minute <= 16 * 60

    now_et = datetime.now(et)
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def resolve_trading_date() -> str:
    """Return the latest trading session date (YYYY-MM-DD).

    If market is open or it's a weekday after market close, use today.
    On weekends or before market open on Monday, use last Friday.
    """
    import zoneinfo
    try:
        et = zoneinfo.ZoneInfo("America/New_York")
        now = datetime.now(et)
    except Exception:
        from datetime import timedelta as _td
        now = datetime.now(timezone.utc) + _td(hours=-5)

    weekday = now.weekday()  # Mon=0 ... Sun=6

    if weekday == 5:  # Saturday → Friday
        delta = 1
    elif weekday == 6:  # Sunday → Friday
        delta = 2
    elif weekday == 0 and now.hour < 9:  # Monday pre-market → Friday
        delta = 3
    else:
        # Weekday: if before 9:30 AM, use previous trading day
        if now.hour * 60 + now.minute < 9 * 60 + 30:
            delta = 1 if weekday > 0 else 3  # Mon pre-market → Friday
        else:
            delta = 0

    from datetime import timedelta
    target = now - timedelta(days=delta)
    return target.strftime("%Y-%m-%d")


def resolve_menthorq_creds() -> tuple[Optional[str], Optional[str]]:
    """Resolve MenthorQ login credentials from .env or environment."""
    user = os.environ.get("MENTHORQ_USER", "").strip()
    passwd = os.environ.get("MENTHORQ_PASS", "").strip()
    return (user or None, passwd or None)


# ── Vision extraction prompt ─────────────────────────────────────
EXTRACTION_PROMPT = """Extract CTA positioning data from this table image.
Return ONLY a JSON array of objects with these exact fields:
[{"underlying":"E-Mini S&P 500 Index","position_today":0.45,"position_yesterday":0.21,"position_1m_ago":1.06,"percentile_1m":38,"percentile_3m":13,"percentile_1y":38,"z_score_3m":-1.56},...]

Rules:
- "underlying" is the asset name exactly as shown in the table
- Position values are decimal numbers as shown (can be negative)
- Copy each percentile EXACTLY as displayed, digit for digit. Some cards show
  them as 0-100 integers (38) and some as 0-1 decimals (0.38). Never convert
  between the two scales and never round a decimal to an integer — reporting
  0.38 as 0 turns a mid-range reading into a max-short one.
- Z-scores are decimal numbers as shown (e.g. -1.56)
- Include ALL rows from the table
- Return ONLY the JSON array, no markdown, no explanation"""


# ══════════════════════════════════════════════════════════════════
# Cache
# ══════════════════════════════════════════════════════════════════

def cache_path(date_str: str) -> Path:
    return CACHE_DIR / f"cta_{date_str}.json"


def read_cache(date_str: str) -> Optional[Dict[str, Any]]:
    """Read cached MenthorQ data for a date. Returns None on miss/expiry."""
    p = cache_path(date_str)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def write_cache(date_str: str, tables: Dict[str, List[Dict]]) -> Path:
    """Write cache file. Returns the path."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": date_str,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "menthorq_s3_vision",
        "tables": tables,
    }
    p = cache_path(date_str)
    atomic_save(str(p), entry)
    return p


# ══════════════════════════════════════════════════════════════════
# Main Fetch Pipeline
# ══════════════════════════════════════════════════════════════════

def fetch_menthorq_cta(
    date_str: Optional[str] = None,
    force: bool = False,
    headless: bool = True,
    save_images: bool = False,
    artifact_dir: str | Path | None = None,
    storage_state_path: str | Path | None = STORAGE_STATE_PATH,
) -> Optional[Dict[str, Any]]:
    """Fetch MenthorQ CTA data: check cache, use client to extract, cache.

    Args:
        date_str: Date to fetch (YYYY-MM-DD). Defaults to the latest trading day.
        force: Bypass cache and re-fetch.
        headless: Run browser in headless mode.
        save_images: Save raw S3 PNGs to tmp/ for visual verification.

    Returns the full cache entry dict, or None on failure.
    """
    if date_str is None:
        date_str = resolve_trading_date()

    # Check cache (unless forced)
    if not force:
        cached = read_cache(date_str)
        if cached:
            print(f"  Cache hit: {cache_path(date_str)}", file=sys.stderr)
            return cached

    # Use MenthorQClient for browser + vision extraction
    try:
        from clients.menthorq_client import MenthorQClient, MenthorQError
        from clients.menthorq_client import CTA_SLUGS
    except Exception as exc:
        print(f"  ERROR: Failed to initialize MenthorQ client: {exc}", file=sys.stderr)
        return None

    try:
        with MenthorQClient(
            headless=headless,
            artifact_dir=artifact_dir,
            storage_state_path=storage_state_path,
        ) as client:
            tables = client.get_cta(date_str)

            # After get_cta(), page is still on CTA — download images for verification
            if save_images:
                images = client._download_card_images(client._page, CTA_SLUGS)
                if images:
                    tmp_dir = _PROJECT_DIR / "tmp"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    for key, png_bytes in images.items():
                        out = tmp_dir / f"menthorq-cta-{key}.png"
                        out.write_bytes(png_bytes)
                        print(f"  Saved: {out} ({len(png_bytes):,} bytes)", file=sys.stderr)

    except MenthorQError as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  ERROR: Unexpected failure: {exc}", file=sys.stderr)
        return None

    # Reject empty AND hollow extractions ({"main": [], ...}) — caching a dict
    # whose tables are all empty poisons data/menthorq_cache for the day
    # (feedback_dont_cache_empty_results).
    if not tables or not any(tables.values()):
        print("  ERROR: No CTA data extracted.", file=sys.stderr)
        return None

    # Percentiles land on two different scales and Vision rounds the fractional
    # one to 0/1 — reconcile before anything persists, so the corruption never
    # reaches the page, the share cards or the history comparisons.
    tables = reconcile_tables(tables)
    dropped = [
        f"{table}/{row.get('underlying')}"
        for table, rows in tables.items()
        for row in rows or []
        if row.get("percentile_3m") is None
    ]
    if dropped:
        print(
            f"  WARN: {len(dropped)} row(s) had percentiles their z-score contradicts: "
            f"{', '.join(dropped[:6])}",
            file=sys.stderr,
        )

    # Cache
    p = write_cache(date_str, tables)
    print(f"  Cached: {p}", file=sys.stderr)

    return read_cache(date_str)


# ══════════════════════════════════════════════════════════════════
# Helper: Find asset in MenthorQ tables
# ══════════════════════════════════════════════════════════════════

def find_by_underlying(
    table: List[Dict[str, Any]],
    search: str,
) -> Optional[Dict[str, Any]]:
    """Find an asset entry by partial underlying name match (case-insensitive)."""
    search_lower = search.lower()
    for entry in table:
        name = entry.get("underlying", "")
        if search_lower in name.lower():
            return entry
    return None


def load_menthorq_cache(date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load the latest MenthorQ cache. Tries trading date first, then yesterday."""
    if date_str:
        return read_cache(date_str)

    trading_date = resolve_trading_date()
    cached = read_cache(trading_date)
    if cached:
        return cached

    # Try previous trading day fallback
    from datetime import timedelta
    yesterday = (datetime.strptime(trading_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return read_cache(yesterday)


# ══════════════════════════════════════════════════════════════════
# Console Summary
# ══════════════════════════════════════════════════════════════════

def print_summary(data: Dict[str, Any]) -> None:
    """Print human-readable summary of MenthorQ CTA data."""
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"MENTHORQ CTA POSITIONING — {data['date']}", file=sys.stderr)
    print(f"Source: {data['source']} | Fetched: {data['fetched_at']}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    for table_key in ["main", "index", "commodity", "currency"]:
        table = data.get("tables", {}).get(table_key, [])
        if not table:
            continue

        label = table_key.upper()
        print(f"\n  {label} ({len(table)} assets):", file=sys.stderr)
        print(f"  {'Underlying':<35} {'Pos Today':>10} {'Pos Yest':>10} {'Pctl 3M':>8} {'Z-Score':>8}", file=sys.stderr)
        print(f"  {'-'*35} {'-'*10} {'-'*10} {'-'*8} {'-'*8}", file=sys.stderr)

        for entry in table:
            name = entry.get("underlying", "?")[:35]
            pos_t = entry.get("position_today", "---")
            pos_y = entry.get("position_yesterday", "---")
            pctl = entry.get("percentile_3m", "---")
            zscore = entry.get("z_score_3m", "---")

            pos_t_str = f"{pos_t:>10.2f}" if isinstance(pos_t, (int, float)) else f"{pos_t:>10}"
            pos_y_str = f"{pos_y:>10.2f}" if isinstance(pos_y, (int, float)) else f"{pos_y:>10}"
            pctl_str = f"{pctl:>8}" if isinstance(pctl, (int, float)) else f"{pctl:>8}"
            zscore_str = f"{zscore:>8.2f}" if isinstance(zscore, (int, float)) else f"{zscore:>8}"

            print(f"  {name:<35} {pos_t_str} {pos_y_str} {pctl_str} {zscore_str}", file=sys.stderr)

    # Highlight SPX
    main_table = data.get("tables", {}).get("main", [])
    spx = find_by_underlying(main_table, "S&P 500")
    if spx:
        print(f"\n  KEY: E-Mini S&P 500", file=sys.stderr)
        print(f"    Position Today     : {spx.get('position_today', '---')}", file=sys.stderr)
        print(f"    Position Yesterday : {spx.get('position_yesterday', '---')}", file=sys.stderr)
        print(f"    3M Percentile      : {spx.get('percentile_3m', '---')}", file=sys.stderr)
        print(f"    3M Z-Score         : {spx.get('z_score_3m', '---')}", file=sys.stderr)

    print(f"\n{'='*60}\n", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Fetch MenthorQ CTA positioning data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Fetches CTA positioning data from MenthorQ via headless browser + Vision.
Requires MENTHORQ_USER, MENTHORQ_PASS, and an Anthropic API key.

Examples:
  python3 scripts/fetch_menthorq_cta.py              # Fetch + summary
  python3 scripts/fetch_menthorq_cta.py --json        # JSON to stdout
  python3 scripts/fetch_menthorq_cta.py --date 2026-03-06
  python3 scripts/fetch_menthorq_cta.py --force        # Bypass cache
""",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout")
    parser.add_argument("--date", help="Date to fetch (YYYY-MM-DD, default: latest trading day)")
    parser.add_argument("--force", action="store_true", help="Bypass cache, force re-fetch")
    parser.add_argument("--no-headless", action="store_true", help="Show browser (debug)")
    parser.add_argument("--save-images", action="store_true", help="Save raw S3 PNGs to tmp/ for verification")

    args = parser.parse_args()

    date_str = args.date or resolve_trading_date()

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"MENTHORQ CTA FETCH — {date_str}", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    artifact_dir = os.environ.get("MENTHORQ_ARTIFACT_DIR")

    t_start = time.time()
    result = fetch_menthorq_cta(
        date_str=date_str,
        force=args.force,
        headless=not args.no_headless,
        save_images=args.save_images,
        artifact_dir=artifact_dir,
    )
    elapsed = time.time() - t_start

    if not result:
        print("  FAILED: No MenthorQ data retrieved.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_summary(result)

    print(f"  Completed in {elapsed:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
