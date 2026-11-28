"""Off-hours scan cache gate (defense-in-depth against UW/IB waste).

The regime scan scripts (cri_scan, vcg_scan, gex_scan, gamma_rotation_gap) are
also driven by autonomous server timers. If a timer fires while the market is
closed, the script would re-fetch UW/IB for a session whose data is already
final — burning quota for nothing. This gate lets a script short-circuit: when
the market is closed AND a usable cache exists for the most-recent expected
session, serve the cache and skip the fetch entirely.

The frontend (`lib/marketSession.ts`) and FastAPI staleness layer apply the same
rule; this is the script-side backstop so even a stray off-hours invocation
costs zero UW/IB quota. An explicit `force` (wired to a `--force` CLI flag)
bypasses the gate for the rare manual off-hours refresh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from utils.json_dict import read_json_dict
from utils.market_calendar import is_market_open_et, most_recent_session_date


def _session_date_from_payload(data: dict) -> Optional[str]:
    """Best-effort ET session date (YYYY-MM-DD) for a cached scan payload.

    CRI stamps a top-level `date`; vcg/gex/gamma stamp a UTC `scan_time`. For
    scan_time we convert to ET. Returns None when neither is present/parseable.
    """
    raw_date = data.get("date")
    if isinstance(raw_date, str) and len(raw_date) >= 10:
        return raw_date[:10]

    scan_time = data.get("scan_time")
    if not isinstance(scan_time, str) or not scan_time:
        return None
    iso = scan_time.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        # Fall back to the bare date prefix if the timestamp is malformed.
        return scan_time[:10] if len(scan_time) >= 10 else None
    # Naive timestamps from these producers are UTC (datetime.now(timezone.utc)).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        import zoneinfo
        return dt.astimezone(zoneinfo.ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def cached_scan_if_fresh(
    cache_path,
    *,
    force: bool = False,
    now: datetime = None,
) -> Optional[dict]:
    """Return the cached scan payload to serve in place of a fresh fetch, else None.

    Serves the cache ONLY when all hold:
      - not `force`
      - the market is currently closed
      - the cache file exists and parses to a dict with a session date
      - that session date is current (>= the most-recent expected session)

    The `>=` comparison is intentional: it serves a cache that is current OR
    newer (e.g. a scan_time that rolled to the next UTC day) and only re-scans
    when the cache is genuinely behind the expected session (a missed run). On
    the safe side — at worst it serves slightly-fresher data; it never silently
    serves stale data into a new session.
    """
    if force:
        return None
    if is_market_open_et(now):
        return None

    data = read_json_dict(Path(cache_path))
    if data is None:
        return None

    session_date = _session_date_from_payload(data)
    if not session_date:
        return None

    expected = most_recent_session_date(now)
    if session_date >= expected:
        return data
    return None
