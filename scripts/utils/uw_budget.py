"""Process-wide Unusual Whales daily request budget.

Quota day is America/New_York and resets at 20:00 ET (same as fetch_skew).
NDX / preset universe scans refuse after 50% of the 40000 daily cap.
Per-ticker and explicit ticker scans are not blocked.
"""

from __future__ import annotations

import fcntl
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DAILY_LIMIT = 40_000
UNIVERSE_BLOCK_AT = 20_000
RESET_HOUR_ET = 20

BUDGET_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "uw_budget.json"


def quota_date(now: Optional[datetime] = None) -> str:
    """Return the UW quota-day key (the ET calendar date of the 20:00 window start)."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ET)
    if local.hour < RESET_HOUR_ET:
        local = local - timedelta(days=1)
    return local.date().isoformat()


def _path(path: Optional[Path | str] = None) -> Path:
    return Path(path) if path is not None else BUDGET_PATH


def _read_unlocked(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_unlocked(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"date": payload["date"], "count": int(payload["count"])}, indent=2))
    tmp.replace(path)


def _with_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.touch(exist_ok=True)
    return open(lock_path, "a+")


def record_hit(path: Optional[Path | str] = None, now: Optional[datetime] = None) -> int:
    """Increment today's HTTP hit count and persist ``{date, count}``."""
    return record_hits(1, path=path, now=now)


def record_hits(
    hits: int, path: Optional[Path | str] = None, now: Optional[datetime] = None
) -> int:
    """Increment today's HTTP hit count by ``hits`` under one flock write.

    The counted path for UW requests made outside ``UWClient`` — the Next.js
    route handlers mirror their hits here via POST /uw/usage/record so the
    gauge and the universe-scan brake see browsing traffic (REL-036 / R-062).
    """
    target = _path(path)
    day = quota_date(now)
    with _with_lock(target) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_unlocked(target)
            if state.get("date") != day:
                count = hits
            else:
                try:
                    count = int(state.get("count") or 0) + hits
                except (TypeError, ValueError):
                    count = hits
            _write_unlocked(target, {"date": day, "count": count})
            return count
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def used(path: Optional[Path | str] = None, now: Optional[datetime] = None) -> int:
    state = _read_unlocked(_path(path))
    if state.get("date") != quota_date(now):
        return 0
    try:
        return max(0, int(state.get("count") or 0))
    except (TypeError, ValueError):
        return 0


def remaining(path: Optional[Path | str] = None, now: Optional[datetime] = None) -> int:
    return max(0, DAILY_LIMIT - used(path=path, now=now))


def should_block_universe_scan(
    path: Optional[Path | str] = None, now: Optional[datetime] = None
) -> bool:
    return used(path=path, now=now) >= UNIVERSE_BLOCK_AT


def usage_snapshot(
    path: Optional[Path | str] = None, now: Optional[datetime] = None
) -> dict:
    """Operator-facing GET /uw/usage payload."""
    hits = used(path=path, now=now)
    return {
        "used": hits,
        "limit": DAILY_LIMIT,
        "remaining": max(0, DAILY_LIMIT - hits),
        "reset_et": f"{RESET_HOUR_ET:02d}:00",
        "quota_day": quota_date(now),
        "universe_scans_blocked": hits >= UNIVERSE_BLOCK_AT,
    }
