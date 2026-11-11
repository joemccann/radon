"""CTA sync scheduling helpers: the target trading day and the ET slots.

The sync itself (lock, retries, status ledger) lives in
`scripts/cta_sync_service.py`. A second runner here wrote a different status
vocabulary (running/success/error) to the same `cta-sync.json` under a
different lock path; nothing in production called it, so it was retired.
§3.4 of docs/audits/implicit-state-machines-20261103.md.
"""

from __future__ import annotations

from datetime import datetime

from utils.market_calendar import last_completed_session_date


CTA_SYNC_ET_SLOTS = [
    (16, 15),
    (17, 0),
]


def latest_closed_trading_day(now: datetime | None = None) -> str:
    """Return the latest trading day whose CTA cache should exist."""
    return last_completed_session_date(now)
