"""ET date conversion — centralized ZoneInfo handling for performance.

Mirrors `web/lib/parseScanTime.ts:scanTimeToEtDate` / `performanceFreshness.ts`
but in Python with ``zoneinfo``. Naive ISO strings are treated as UTC
(host is UTC on Hetzner), timezone-aware strings are honoured.

Usage:
    from utils.et_date import to_et_date, parse_iso_to_et, normalize_report_date
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET_ZONE = ZoneInfo("America/New_York")
UTC_ZONE = timezone.utc

# Compact reportDate 20260504 and ISO 2026-05-04 both occur in Flex XML.
_COMPACT_RE = re.compile(r"^\d{8}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_iso_to_et(value: str | None) -> datetime | None:
    """Parse an ISO timestamp string to a timezone-aware datetime in ET.

    - Empty / None -> None
    - Naive strings (no tz suffix) are treated as UTC (matches Hetzner host +
      ``web/lib/parseScanTime.ts`` which appends ``Z`` to naive strings).
    - Aware strings are converted as-is.
    - Malformed -> None (caller can fallback to string slicing if desired).
    """
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    # Heuristic: if there's no timezone suffix, treat as UTC
    # Suffix detection: Z or ±HH:MM or ±HHMM
    has_tz = raw.endswith("Z") or bool(re.match(r".*[+-]\d{2}:?\d{2}$", raw))
    try:
        if has_tz:
            # Python's fromisoformat doesn't handle trailing Z
            iso = raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
        else:
            # Naive -> assume UTC
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC_ZONE)
        # Convert to ET
        return dt.astimezone(ET_ZONE)
    except (ValueError, OverflowError):
        return None


def to_et_date(value: str | None) -> str | None:
    """Convert an ISO timestamp to ``YYYY-MM-DD`` in America/New_York.

    Returns None for missing/malformed input.
    """
    dt = parse_iso_to_et(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d")


def to_et_datetime(value: str | None) -> datetime | None:
    """Parse ISO string and return ET-aware datetime, or None."""
    return parse_iso_to_et(value)


def normalize_report_date(raw: str | None) -> str | None:
    """Normalize a Flex ``reportDate`` / date string to ``YYYY-MM-DD``.

    Handles:
    - ``20260504`` (compact)
    - ``2026-05-04`` (ISO)
    - ``2026-05-04T00:00:00`` (ISO datetime)
    - ``2026-05-04T01:23:45.123456`` etc.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if _COMPACT_RE.match(s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    m = _ISO_DATE_RE.match(s)
    if m:
        return m.group(0)
    # Fallback: try ET conversion for timestamp-like strings
    et_date = to_et_date(s)
    if et_date:
        return et_date
    return None


def last_sync_to_et_date(last_sync: str | None) -> str | None:
    """Back-compatible alias for portfolio ``last_sync`` wall-clock timestamps.

    Mirrors ``web/lib/performanceFreshness.ts:portfolioAsOfFromLastSync``.
    Naive timestamps (old ``datetime.now().isoformat()``) are treated as UTC.
    """
    return to_et_date(last_sync)


def et_today_str(now: datetime | None = None) -> str:
    """Return today's date in ET as ``YYYY-MM-DD``."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC_ZONE)
    return now.astimezone(ET_ZONE).strftime("%Y-%m-%d")
