"""CTA sync scheduling helpers: the target trading day and the ET slots.

The sync itself (lock, retries, status ledger) lives in
`scripts/cta_sync_service.py`. A second runner here wrote a different status
vocabulary (running/success/error) to the same `cta-sync.json` under a
different lock path; nothing in production called it, so it was retired.
§3.4 of docs/audits/implicit-state-machines-20261103.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from utils.market_calendar import _is_trading_day


ET = ZoneInfo("America/New_York")
MARKET_CLOSE_MINUTE = 16 * 60
CTA_SYNC_ET_SLOTS = [
    (16, 15),
    (17, 0),
]


def latest_closed_trading_day(now: datetime | None = None) -> str:
    """Return the latest trading day whose CTA cache should exist."""

    now_et = _coerce_et(now)
    candidate = now_et.date()
    minutes = now_et.hour * 60 + now_et.minute

    if not (_is_trading_day(_to_naive(candidate)) and minutes >= MARKET_CLOSE_MINUTE):
        candidate -= timedelta(days=1)

    while not _is_trading_day(_to_naive(candidate)):
        candidate -= timedelta(days=1)

    return candidate.isoformat()


def _coerce_et(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def _to_naive(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, 12, 0, 0)
