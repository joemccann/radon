"""Canonical IB order-status vocabulary.

One place for every order-status string IB / ib_insync emits, plus the local
placeholders this repo already uses. Mirrored in
``web/lib/orders/ibOrderStatus.ts``; ``tests/test_ib_order_status_parity.py``
fails if the two drift. Sets agree with ``ib_insync.order.OrderStatus``:
``ActiveStates`` is a subset of WORKING and ``DoneStates`` of TERMINAL.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

PENDING_SUBMIT = "PendingSubmit"
PENDING_CANCEL = "PendingCancel"
PRE_SUBMITTED = "PreSubmitted"
SUBMITTED = "Submitted"
API_PENDING = "ApiPending"
API_CANCELLED = "ApiCancelled"
CANCELLED = "Cancelled"
FILLED_STATUS = "Filled"
INACTIVE = "Inactive"
# Not an ib_insync OrderStatus; local callers treat it as a terminal failure.
REJECTED = "Rejected"
# Local placeholder ib_place_order uses before IB reports any status.
UNKNOWN = "Unknown"

# Still live at the broker (a PendingCancel order can still fill).
WORKING = frozenset({PENDING_SUBMIT, API_PENDING, PRE_SUBMITTED, SUBMITTED, PENDING_CANCEL})
TERMINAL_FAILED = frozenset({CANCELLED, API_CANCELLED, INACTIVE, REJECTED})
FILLED = frozenset({FILLED_STATUS})
TERMINAL = TERMINAL_FAILED | FILLED
# No broker acknowledgement yet.
UNACKNOWLEDGED = frozenset({PENDING_SUBMIT, API_PENDING, UNKNOWN})
ALL_STATUSES = WORKING | TERMINAL | {UNKNOWN}

_BY_LOWER = {status.lower(): status for status in ALL_STATUSES}


class CancelOutcome(str, Enum):
    CANCELLED = "cancelled"
    FILLED = "filled"
    PARTIALLY_FILLED_THEN_CANCELLED = "partially_filled_then_cancelled"
    STILL_WORKING = "still_working"
    UNKNOWN = "unknown"


def normalize(status: object) -> Optional[str]:
    """Case-insensitive match to a canonical status, else None."""
    if not isinstance(status, str):
        return None
    return _BY_LOWER.get(status.strip().lower())


def is_working(status: object) -> bool:
    """True only for an exact canonical working status."""
    return status in WORKING


def is_terminal(status: object) -> bool:
    """True only for an exact canonical terminal status (filled or failed)."""
    return status in TERMINAL
