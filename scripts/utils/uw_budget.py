"""Process-wide Unusual Whales daily request budget.

Quota day is America/New_York and resets at 20:00 ET (same as fetch_skew).
NDX / preset universe scans refuse after 50% of the 40000 daily cap.
Per-ticker and explicit ticker scans are not blocked.

Every hit is tallied by caller and by endpoint class, and the spent day is
archived on rollover: a bare total answers "how much is left" but never
"who spent it", which is the only question worth asking at 85% used.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DAILY_LIMIT = 40_000
UNIVERSE_BLOCK_AT = 20_000
RESET_HOUR_ET = 20

BUDGET_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "uw_budget.json"

# Overrides the argv-derived caller label for wrappers that shell out.
CALLER_ENV = "RADON_UW_CALLER"
# Tally width. A runaway scan must not grow the budget file without bound.
MAX_TRACKED_KEYS = 200
TOP_N = 15
HISTORY_DAYS = 90
_TICKER_SEGMENT = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


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


def _history_path(target: Path) -> Path:
    return target.with_name(target.stem + "_history.jsonl")


def caller_label(caller: Optional[str] = None) -> str:
    """Who is spending: explicit label, else $RADON_UW_CALLER, else argv[0]."""
    for candidate in (caller, os.environ.get(CALLER_ENV)):
        name = (candidate or "").strip()
        if name:
            return name[:64]
    argv0 = sys.argv[0] if sys.argv else ""
    stem = Path(argv0).stem if argv0 else ""
    return (stem or "unknown")[:64]


def endpoint_class(endpoint: Optional[str]) -> str:
    """Collapse ticker segments so per-ticker paths tally as one endpoint."""
    parts = [part for part in str(endpoint or "").strip("/").split("/") if part]
    if not parts:
        return "unknown"
    collapsed = ("<T>" if _TICKER_SEGMENT.match(part) else part for part in parts)
    return "/".join(collapsed)[:96]


def _tally(state: dict, key: str) -> Dict[str, int]:
    raw = state.get(key)
    if not isinstance(raw, dict):
        return {}
    return {
        str(name): int(hits)
        for name, hits in raw.items()
        if isinstance(hits, int) and not isinstance(hits, bool) and hits > 0
    }


def _bump(tally: Dict[str, int], key: str, hits: int) -> Dict[str, int]:
    tally[key] = tally.get(key, 0) + hits
    if len(tally) <= MAX_TRACKED_KEYS:
        return tally
    ranked = sorted(tally.items(), key=lambda item: item[1], reverse=True)
    return dict(ranked[:MAX_TRACKED_KEYS])


def _ranked(tally: Dict[str, int]) -> list[dict]:
    ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0]))
    return [{"name": name, "hits": hits} for name, hits in ranked[:TOP_N]]


def _archive_unlocked(path: Path, state: dict) -> None:
    """Append the spent quota day so its breakdown outlives the reset."""
    if not state.get("date") or not state.get("count"):
        return
    history = _history_path(path)
    try:
        previous = [
            line for line in history.read_text().splitlines() if line.strip()
        ]
    except OSError:
        previous = []
    previous.append(json.dumps(_payload(state)))
    try:
        history.write_text("\n".join(previous[-HISTORY_DAYS:]) + "\n")
    except OSError:
        pass


def _payload(state: dict) -> dict:
    return {
        "date": state["date"],
        "count": int(state["count"]),
        "by_caller": state.get("by_caller") or {},
        "by_endpoint": state.get("by_endpoint") or {},
    }


# R-172: an unreadable tally is NOT a zero tally. `_read_unlocked` collapsed
# "the file does not exist yet" and "the file is truncated / the disk is
# full" into the same `{}`, so a torn write re-opened the entire 40,000-call
# daily budget to a scanner that had already spent it. A present-but-broken
# file reads as SPENT.
_UNREADABLE = {"_unreadable": True}


def _read_unlocked(path: Path) -> dict:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    except OSError:
        return dict(_UNREADABLE)
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return dict(_UNREADABLE)
    return raw if isinstance(raw, dict) else dict(_UNREADABLE)


def _write_unlocked(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_payload(payload), indent=2))
    tmp.replace(path)


def _with_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.touch(exist_ok=True)
    return open(lock_path, "a+")


def record_hit(
    path: Optional[Path | str] = None,
    now: Optional[datetime] = None,
    *,
    caller: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> int:
    """Increment today's HTTP hit count and persist the attributed totals."""
    return record_hits(1, path=path, now=now, caller=caller, endpoint=endpoint)


def record_hits(
    hits: int,
    path: Optional[Path | str] = None,
    now: Optional[datetime] = None,
    *,
    caller: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> int:
    """Increment today's HTTP hit count by ``hits`` under one flock write.

    The counted path for UW requests made outside ``UWClient`` — the Next.js
    route handlers mirror their hits here via POST /uw/usage/record so the
    gauge and the universe-scan brake see browsing traffic (REL-036 / R-062).
    """
    target = _path(path)
    day = quota_date(now)
    who = caller_label(caller)
    what = endpoint_class(endpoint)
    with _with_lock(target) as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            state = _read_unlocked(target)
            if state.get("date") != day:
                _archive_unlocked(target, state)
                count, by_caller, by_endpoint = hits, {}, {}
            else:
                try:
                    count = int(state.get("count") or 0) + hits
                except (TypeError, ValueError):
                    count = hits
                by_caller = _tally(state, "by_caller")
                by_endpoint = _tally(state, "by_endpoint")
            _write_unlocked(
                target,
                {
                    "date": day,
                    "count": count,
                    "by_caller": _bump(by_caller, who, hits),
                    "by_endpoint": _bump(by_endpoint, what, hits),
                },
            )
            return count
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def used(path: Optional[Path | str] = None, now: Optional[datetime] = None) -> int:
    state = _read_unlocked(_path(path))
    if state.get("_unreadable"):
        # Fail CLOSED: report the whole budget spent until the state file is
        # readable again, rather than handing out calls we cannot account for.
        return DAILY_LIMIT
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
    state = _read_unlocked(_path(path))
    unreadable = bool(state.get("_unreadable"))
    current = (not unreadable) and state.get("date") == quota_date(now)
    hits = used(path=path, now=now)
    return {
        "state_unreadable": unreadable,
        "used": hits,
        "limit": DAILY_LIMIT,
        "remaining": max(0, DAILY_LIMIT - hits),
        "reset_et": f"{RESET_HOUR_ET:02d}:00",
        "quota_day": quota_date(now),
        "universe_scans_blocked": hits >= UNIVERSE_BLOCK_AT,
        "top_callers": _ranked(_tally(state, "by_caller")) if current else [],
        "top_endpoints": _ranked(_tally(state, "by_endpoint")) if current else [],
    }
