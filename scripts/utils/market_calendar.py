"""Shared market calendar utilities.

Provides holiday data, market-open checks, and trading-day calculations.
Holidays are loaded from scripts/config/market_holidays.json.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config" / "market_holidays.json"
# IBKR-sourced rolling calendar written daily by scripts/fetch_market_calendar.py.
# This is the authoritative layer (ad-hoc closures + half-days the static table
# can't carry); a missing/empty file degrades to the static holiday table.
_IBKR_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "market_calendar.json"

# Module-level cache so we only read the file once per process.
_holidays_cache: dict = {}
# IBKR calendar cache, re-read periodically so a long-running process (FastAPI)
# picks up the daily refresh without a restart.
_ibkr_cache: dict = {}
_ibkr_loaded_at: float = 0.0
_IBKR_RELOAD_SECS = 6 * 3600

_OPEN_MIN = 9 * 60 + 30  # 09:30 ET
_CLOSE_MIN = 16 * 60     # 16:00 ET


def _load_holidays_file() -> dict:
    """Load the holidays JSON file (cached)."""
    if not _holidays_cache:
        try:
            with open(_CONFIG_PATH) as f:
                data = json.load(f)
            _holidays_cache.update(data)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return _holidays_cache


def _load_ibkr_calendar() -> dict:
    """Load the IBKR-sourced calendar ``{date: {status, open_et, close_et}}``.

    Re-reads at most every ``_IBKR_RELOAD_SECS`` whether or not the file exists,
    so a missing file (before the first fetch) is cheap and a long-lived reader
    eventually sees the daily refresh. Never raises.
    """
    global _ibkr_loaded_at
    now = time.time()
    if _ibkr_loaded_at and (now - _ibkr_loaded_at) < _IBKR_RELOAD_SECS:
        return _ibkr_cache
    _ibkr_loaded_at = now
    _ibkr_cache.clear()
    try:
        with open(_IBKR_CACHE_PATH) as f:
            data = json.load(f)
        days = data.get("days") if isinstance(data, dict) else None
        if isinstance(days, dict):
            _ibkr_cache.update(days)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _ibkr_cache


def _hhmm_to_min(hhmm) -> "int | None":
    if not isinstance(hhmm, str):
        return None
    parts = hhmm.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def parse_liquid_hours(liquid_hours: str) -> dict:
    """Parse an IBKR ``liquidHours`` string into ``{date: {status, open_et, close_et}}``.

    Segments are ``;``-delimited, each either ``YYYYMMDD:CLOSED`` or
    ``YYYYMMDD:HHMM-YYYYMMDD:HHMM`` (times in the contract's own tz; ET for SPY).
    A close earlier than 16:00 is ``early_close`` — IBKR has no early-close token,
    a half-day is just a shortened normal segment.
    """
    out: dict = {}
    if not liquid_hours:
        return out
    for seg in liquid_hours.split(";"):
        seg = seg.strip()
        if not seg:
            continue
        date_raw, _, rest = seg.partition(":")
        date_raw = date_raw.strip()
        if len(date_raw) != 8 or not date_raw.isdigit():
            continue
        iso = f"{date_raw[0:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        rest = rest.strip()
        if rest.upper() == "CLOSED":
            out[iso] = {"status": "closed", "open_et": None, "close_et": None}
            continue
        open_part, _, close_part = rest.partition("-")
        open_hhmm = open_part.strip()[-4:]
        close_hhmm = close_part.strip()[-4:]
        if not (len(open_hhmm) == 4 and open_hhmm.isdigit()
                and len(close_hhmm) == 4 and close_hhmm.isdigit()):
            continue
        out[iso] = {
            "status": "open" if close_hhmm == "1600" else "early_close",
            "open_et": f"{open_hhmm[:2]}:{open_hhmm[2:]}",
            "close_et": f"{close_hhmm[:2]}:{close_hhmm[2:]}",
        }
    return out


def _resolve_market_state(minutes: int, weekday: int, ibkr_day, is_holiday: bool) -> dict:
    """Pure market-state decision. ``weekday`` is Python's 0(Mon)..6(Sun).

    Failure-safe order: IBKR cache (authoritative) → weekend → static holiday →
    weekday+time. A missing IBKR row can only fall through to the more
    conservative static/weekday layers — it never widens the open window.
    """
    if ibkr_day:
        if ibkr_day.get("status") == "closed":
            return {"state": "closed", "is_open": False, "reason": "ibkr:closed"}
        open_m = _hhmm_to_min(ibkr_day.get("open_et")) or _OPEN_MIN
        close_m = _hhmm_to_min(ibkr_day.get("close_et")) or _CLOSE_MIN
        if not (open_m <= minutes <= close_m):
            return {"state": "closed", "is_open": False, "reason": "ibkr:outside_hours"}
        state = "early_close" if close_m < _CLOSE_MIN else "open"
        return {"state": state, "is_open": True, "reason": f"ibkr:{state}"}
    if weekday >= 5:
        return {"state": "closed", "is_open": False, "reason": "weekend"}
    if is_holiday:
        return {"state": "closed", "is_open": False, "reason": "static:holiday"}
    is_open = _OPEN_MIN <= minutes <= _CLOSE_MIN
    return {
        "state": "open" if is_open else "closed",
        "is_open": is_open,
        "reason": "weekday_hours" if is_open else "outside_hours",
    }


def market_state(now: datetime = None) -> dict:
    """Single source of truth: ``{state, is_open, reason}`` for an instant.

    ``state`` ∈ {'open','early_close','closed'}; ``is_open`` is the gate boolean.
    Resolves the ET wall-clock, then applies the IBKR cache → static holiday →
    weekday/time fallback chain (see :func:`_resolve_market_state`).
    """
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now_et = (now.astimezone(et) if (now and now.tzinfo) else now) if now else datetime.now(et)
    except Exception:
        now_et = now or datetime.now()
    date_str = now_et.strftime("%Y-%m-%d")
    minutes = now_et.hour * 60 + now_et.minute
    ibkr_day = _load_ibkr_calendar().get(date_str)
    is_holiday = date_str in load_holidays(now_et.year)
    return _resolve_market_state(minutes, now_et.weekday(), ibkr_day, is_holiday)


def load_holidays(year: int = None) -> set:
    """Return a set of holiday date-strings for the given year.

    Args:
        year: 4-digit year.  Defaults to the current year.

    Returns:
        Set of "YYYY-MM-DD" strings.  Empty set if the year is not configured.
    """
    if year is None:
        year = datetime.now().year
    data = _load_holidays_file()
    dates = data.get(str(year), [])
    return set(dates)


def is_market_open(now: datetime = None) -> bool:
    """Check whether the US equity market is open at the given moment.

    Rules:
        - Monday through Friday only
        - 9:30 AM -- 4:00 PM Eastern (naive datetime assumed ET)
        - Not a configured holiday

    Args:
        now: datetime to check.  Defaults to ``datetime.now()``.
    """
    if now is None:
        now = datetime.now()

    # Weekend check
    if now.weekday() >= 5:
        return False

    # Holiday check
    date_str = now.strftime("%Y-%m-%d")
    holidays = load_holidays(now.year)
    if date_str in holidays:
        return False

    # Time window: 9:30 -- 16:00
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if now < market_open or now > market_close:
        return False

    return True


def get_last_n_trading_days(n: int, from_date: datetime = None,
                           include_today: bool = False) -> list:
    """Return the last *n* trading days as ``["YYYY-MM-DD", ...]``.

    A trading day is a weekday that is not a holiday.
    If the market hasn't closed yet today (before 4 PM) or today is not a
    trading day, counting starts from the previous day.

    Args:
        n: Number of trading days to return.
        from_date: Reference datetime.  Defaults to ``datetime.now()``.
        include_today: If True and today is a trading day, always include
            today as the first entry even if the market hasn't closed yet.
            Use this for evaluations that need intraday data.
    """
    if from_date is None:
        from_date = datetime.now()

    trading_days: list = []
    current = from_date

    # If include_today and today is a trading day, add it first
    if include_today and _is_trading_day(current):
        trading_days.append(current.strftime("%Y-%m-%d"))

    # If today isn't eligible yet, step back one day
    if not _is_trading_day(current) or current.hour < 16:
        current -= timedelta(days=1)

    while len(trading_days) < n:
        if _is_trading_day(current):
            day_str = current.strftime("%Y-%m-%d")
            if day_str not in trading_days:  # avoid duplicate if include_today
                trading_days.append(day_str)
        current -= timedelta(days=1)

        # Safety: avoid infinite loops for misconfigured calendars
        if not trading_days and (from_date - current).days > 14:
            break

    return trading_days


def is_market_open_et(now: datetime = None) -> bool:
    """Market-open check that converts to ET first (safe on UTC hosts).

    The legacy ``is_market_open`` treats its datetime's raw fields as ET, which
    is wrong when called bare on a UTC host (e.g. the VPS). This variant resolves
    the ET wall-clock before checking the weekday/holiday/time window.
    """
    # Reimplemented on the single source of truth so every caller inherits the
    # IBKR cache (ad-hoc closures / half-days) without a logic fork. With no
    # cache present this is identical to the legacy weekday+holiday+time check.
    return market_state(now)["is_open"]


def most_recent_session_date(now: datetime = None) -> str:
    """Return the most-recent EXPECTED trading-session date (ET) as YYYY-MM-DD.

    This is the session whose data we should already HAVE: before the cash open
    use the prior trading session; at/after the open use today; weekends and
    holidays resolve back to the prior trading day. Mirrors the frontend
    `lib/marketSession.ts` and CRI's `current_session_date_et` so the off-hours
    cache gate agrees with the route/staleness layer.
    """
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
        now_et = (now.astimezone(et) if (now and now.tzinfo) else now) if now else datetime.now(et)
    except Exception:
        now_et = now or datetime.now()

    def previous_trading_day(dt: datetime) -> datetime:
        candidate = dt - timedelta(days=1)
        while not _is_trading_day(candidate):
            candidate -= timedelta(days=1)
        return candidate

    # Weekend or holiday → prior trading day.
    if not _is_trading_day(now_et):
        return previous_trading_day(now_et).strftime("%Y-%m-%d")

    # Weekday pre-open → prior trading day (the new session has no data yet).
    minutes = now_et.hour * 60 + now_et.minute
    if minutes < 9 * 60 + 30:
        return previous_trading_day(now_et).strftime("%Y-%m-%d")

    return now_et.strftime("%Y-%m-%d")


# ── internal helpers ──────────────────────────────────────────────────

def _is_trading_day(dt: datetime) -> bool:
    """Check if *dt* falls on a trading day (weekday + not holiday)."""
    if dt.weekday() >= 5:
        return False
    date_str = dt.strftime("%Y-%m-%d")
    holidays = load_holidays(dt.year)
    return date_str not in holidays
