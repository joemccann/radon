"""Persistent (ticker, date) dark-pool print cache.

Closed-session dark-pool prints are IMMUTABLE — once a trading day closes its
prints never change. But the scheduled data-refresh (``run_data_refresh.sh``,
~28 runs/day) re-fetched ``lookback_days`` of dark-pool data per ticker on every
cycle, paying Unusual Whales ~28x/day for history that was identical each time.
The only existing cache was a 60s in-memory dedup (``utils.uw_cache``) that dies
with each subprocess, so nothing was reused cycle-to-cycle.

This cache persists prior-day prints to disk so only TODAY hits UW intraday:

- Prior days (date < today ET): immutable → cached on disk, served on hit, the
  UW call is skipped entirely.
- Today: never cached here (prints accrue intraday); the caller always fetches
  it live so intraday flow stays fresh.
- Only NON-EMPTY results are cached. An empty list for a closed day is more
  likely a swallowed upstream hiccup than a real zero for a liquid name, so it
  is re-fetched next cycle (cf. feedback_dont_cache_empty_results). Genuine
  failures raise out of ``fetch_darkpool`` and never reach this cache.

This collapses scanner/flow dark-pool load from ~5 calls/ticker/run to ~1
(today only) — an ~80% cut against the UW daily request budget.

Schema:
  - v1 / missing: single-page fetch (UW 500 cap) — treat as miss so liquid
    names re-fetch with multi-page pagination.
  - v2 + complete=true: full-day cursor walk (or the system page cap).
  - v2 + complete=false: a capped scoring walk. Discover may reuse it;
    flow reports must miss and re-fetch.
  - v2 + missing complete: legacy. Treat 500..1000 prints as a discover
    2-page scoring walk (SNDK 2026-09-04 froze at 976 prints this way).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pytz

_ET = pytz.timezone("America/New_York")

# Module-level so tests can monkeypatch it to a tmp dir.
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "darkpool_cache"

# Bump when on-disk payload semantics change (forces re-fetch of stale rows).
CACHE_SCHEMA = 2

# UW /api/darkpool/{ticker} hard-caps each page at 500. Keep in lockstep
# with fetch_flow.DARKPOOL_PAGE_LIMIT. Discover scores with 2 pages
# (discover.DISCOVER_DARKPOOL_MAX_PAGES); that band is a sample, not a day.
PAGE_LIMIT = 500
SCORING_WALK_MAX_PRINTS = PAGE_LIMIT * 2

# Entries older than this are never read again. Flow reports use a 20-trading-day
# window (~28 calendar days); keep a buffer for weekends/holidays.
MAX_AGE_DAYS = 45
_PRUNE_EVERY = 50  # run a prune sweep roughly every N writes
_write_count = 0


def _today_et() -> str:
    """Today's date (YYYY-MM-DD) in US/Eastern — the session-rollover anchor."""
    return datetime.now(_ET).strftime("%Y-%m-%d")


def is_immutable(date: str) -> bool:
    """True when ``date`` is a strictly-prior (closed) session relative to today ET.

    ISO date strings compare lexicographically, so ``date < today`` is correct.
    """
    return bool(date) and date < _today_et()


def _path(ticker: str, date: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}_{date}.json"


def _row_is_complete(payload: dict, trades: list) -> bool:
    """True when the row is a full-day walk, not a capped scoring sample."""
    flag = payload.get("complete")
    if flag is True:
        return True
    if flag is False:
        return False
    n = len(trades)
    return n < PAGE_LIMIT or n > SCORING_WALK_MAX_PRINTS


def get_cached_darkpool(
    ticker: str,
    date: str,
    *,
    require_complete: bool = True,
) -> Optional[List[dict]]:
    """Return cached prints for an immutable (prior) day, else None.

    Today (or any non-immutable date) always returns None so the caller fetches
    it live. Flow reports keep ``require_complete=True`` so a discover
    scoring walk cannot freeze into Daily Dark Pool History. Discover
    passes ``require_complete=False`` to reuse its own sample.
    """
    if not is_immutable(date):
        return None
    path = _path(ticker, date)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    # Reject pre-pagination rows (missing/legacy schema) so liquid names
    # re-walk UW pages instead of serving a 500-print truncated day forever.
    if payload.get("schema") != CACHE_SCHEMA:
        return None
    trades = payload.get("trades")
    if not isinstance(trades, list):
        return None
    if require_complete and not _row_is_complete(payload, trades):
        return None
    return trades


def set_cached_darkpool(
    ticker: str,
    date: str,
    trades,
    *,
    complete: bool = True,
) -> None:
    """Persist an immutable prior day's prints.

    No-op for today (mutable), and for empty/non-list payloads (never cache a
    structurally-empty "success"). Atomic via temp-file + os.replace.
    ``complete=False`` stores a scoring sample that flow consumers miss.
    """
    if not is_immutable(date):
        return
    if not isinstance(trades, list) or not trades:
        return

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    path = _path(ticker, date)
    tmp = path.with_name(path.name + ".tmp")
    payload = {
        "ticker": ticker.upper(),
        "date": date,
        "count": len(trades),
        "schema": CACHE_SCHEMA,
        "complete": bool(complete),
        "cached_at": datetime.now(_ET).isoformat(),
        "trades": trades,
    }
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return

    _maybe_prune()


def _maybe_prune() -> None:
    """Occasionally delete cache entries older than MAX_AGE_DAYS (never re-read)."""
    global _write_count
    _write_count += 1
    if _write_count % _PRUNE_EVERY != 1:
        return
    cutoff = (datetime.now(_ET) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    try:
        entries = list(CACHE_DIR.glob("*.json"))
    except OSError:
        return
    for entry in entries:
        # filename: TICKER_YYYY-MM-DD.json → trailing date segment
        stem = entry.stem  # drops .json
        date_part = stem.rsplit("_", 1)[-1]
        if len(date_part) == 10 and date_part < cutoff:
            try:
                entry.unlink()
            except OSError:
                pass
