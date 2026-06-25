"""Demo trial-expiry math (demo.radon.run).

Pure, unit-testable helpers for the 3-trading-day demo window. Trading days
exclude weekends + US holidays + IBKR ad-hoc closures via the shared market
calendar (``scripts.utils.market_calendar._is_trading_day``).

The trial clock starts at signup (``start_iso_et``). The window spans N
trading days INCLUSIVE of the start day when the start day is itself a
trading day: a Wednesday signup expires at 16:00 ET on Friday (Wed=1, Thu=2,
Fri=3). A weekend/holiday signup rolls forward to the next trading day as
day 1. Expiry is always 16:00 ET (market close) of the Nth trading day.

Phase 2 will call ``trial_expiry_handler`` from a small FastAPI route (the
calendar logic is Python-only, so the Next.js webhook delegates the
computation). This module deliberately registers NO route.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from utils.market_calendar import _is_trading_day

_ET = ZoneInfo("America/New_York")
_MARKET_CLOSE = time(16, 0)
DEFAULT_TRADING_DAYS = 3
# Guard against a misconfigured calendar marking everything a holiday.
_MAX_SCAN_DAYS = 60


def _parse_et(start_iso_et: str) -> datetime:
    """Parse an ISO-8601 timestamp and return it as an ET-aware datetime.

    A naive input is assumed to already be ET wall-clock; an aware input is
    converted to ET.
    """
    parsed = datetime.fromisoformat(start_iso_et)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_ET)
    return parsed.astimezone(_ET)


def compute_trial_expiry(
    start_iso_et: str, trading_days: int = DEFAULT_TRADING_DAYS
) -> str:
    """Return ISO-ET timestamp of 16:00 ET on the Nth trading day from start.

    The start day counts as trading day 1 when it is a trading day; otherwise
    counting begins on the next trading day. Time-of-day on the start day is
    irrelevant — a trial that begins at 09:00 or 15:59 on a trading day still
    expires at 16:00 ET on the Nth trading day.

    Args:
        start_iso_et: ISO-8601 signup timestamp (naive treated as ET).
        trading_days: Number of trading days in the window (default 3).

    Returns:
        ISO-8601 string in ``America/New_York`` (with offset) at 16:00 ET.
    """
    if trading_days < 1:
        raise ValueError("trading_days must be >= 1")

    start = _parse_et(start_iso_et)
    cursor = start.date()
    counted = 0
    scanned = 0
    while True:
        probe = datetime.combine(cursor, time(12, 0), tzinfo=_ET)
        if _is_trading_day(probe):
            counted += 1
            if counted == trading_days:
                break
        cursor += timedelta(days=1)
        scanned += 1
        if scanned > _MAX_SCAN_DAYS:
            raise RuntimeError(
                "compute_trial_expiry: no Nth trading day within "
                f"{_MAX_SCAN_DAYS} days of {start_iso_et} — calendar misconfigured?"
            )

    expiry = datetime.combine(cursor, _MARKET_CLOSE, tzinfo=_ET)
    return expiry.isoformat()


def is_trial_active(expires_at_iso: str, now: datetime | None = None) -> bool:
    """True iff ``now`` (default: current ET instant) is strictly before expiry.

    Boundary: at exactly 16:00 ET on the expiry day the trial is INACTIVE
    (``now < expiry`` is the gate). Accepts a naive ``expires_at_iso`` as ET.
    """
    expires = _parse_et(expires_at_iso)
    if now is None:
        now_et = datetime.now(_ET)
    elif now.tzinfo is None:
        now_et = now.replace(tzinfo=_ET)
    else:
        now_et = now.astimezone(_ET)
    return now_et < expires


def trial_expiry_handler(
    start_iso_et: str, trading_days: int = DEFAULT_TRADING_DAYS
) -> dict:
    """FastAPI-shaped helper (NOT registered as a route — Phase 2).

    Returns the start, computed expiry, and current active state so the
    Phase-2 webhook can persist both timestamps to Clerk metadata + the
    demo_users row in one call.
    """
    expires_at = compute_trial_expiry(start_iso_et, trading_days)
    return {
        "started_at": _parse_et(start_iso_et).isoformat(),
        "expires_at": expires_at,
        "trading_days": trading_days,
        "active": is_trial_active(expires_at),
    }
