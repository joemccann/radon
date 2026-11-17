"""Shared MarketDesk PDF admission rate.

The account clock is one persisted interval and one batch size. A cycle may
admit that batch once, then waits out the interval. Missed timer fires do not
stack. Provider quota signals multiply the interval; a run of transport or
5xx errors multiplies it more slowly; successes subtract a fixed step.

ea66c3842 (PR 1322) metered PDF bytes against the signed-in account and
replaced a 300s floor with a fixed 1800s floor after preview_quota. That
commit does not publish a numeric allowance (no requests-per-day cap). The
1800s value was the pacing choice, and it admits 48 PDFs/day against
100-190/day of intake. This controller's fastest pace is the drain rate.
preview_quota, download_quota, http_429 and Retry-After are what slow it.

Safety reset (2026-11-14): the live row starts at 900s and batch 1, then
ramps. It does not open at 300s and batch 6. A clean cycle subtracts 30s,
down to the floor (300s, or MARKETDESK_PDF_MIN_INTERVAL_SECONDS when higher).
The batch grows by one only after 8 clean cycles, up to 6. A rolling 24h cap
defaults to 400 admissions (MARKETDESK_PDF_DAILY_CAP, 0 disables it). At that
cap, 1,275 backlog rows plus 190 new PDFs a day clear inside 7 days. Quota
signals, including HTTP 401/402/403 and an HTTP-date Retry-After, double the
interval and reset the batch to 1.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os

FLOOR_SECONDS = 300
CEILING_SECONDS = 24 * 60 * 60
# Safety reset, 2026-11-14. The live row starts here and ramps. It does not
# open at the 300s / batch-6 ceiling. Joe raises pace by leaving this ramp
# alone, or by setting MARKETDESK_PDF_DAILY_CAP (0 disables the cap).
START_INTERVAL_SECONDS = 900
START_BATCH = 1
MAX_BATCH = 6
MIN_BATCH = 1
ADDEND_SECONDS = 30
QUOTA_FACTOR = 2
SOFT_RUN = 3
SOFT_NUMERATOR = 3
SOFT_DENOMINATOR = 2
BATCH_RECOVER_EVERY = 8
DEFAULT_DAILY_CAP = 400
DAILY_CAP_ENV = "MARKETDESK_PDF_DAILY_CAP"
FLOOR_ENV = "MARKETDESK_PDF_MIN_INTERVAL_SECONDS"
JITTER_MAX_BPS = 1000  # 10.00% added delay. Never negative, so jitter cannot admit early.


@dataclass(frozen=True)
class RateState:
    interval_seconds: int
    batch_size: int
    soft_streak: int
    success_credits: int


def start_state() -> RateState:
    return RateState(START_INTERVAL_SECONDS, START_BATCH, 0, 0)


def interval_floor(env=None) -> int:
    """Code floor, raised by MARKETDESK_PDF_MIN_INTERVAL_SECONDS when that is higher."""
    source = os.environ if env is None else env
    raw = source.get(FLOOR_ENV) if hasattr(source, "get") else None
    floor = FLOOR_SECONDS
    if raw not in (None, ""):
        try:
            floor = max(FLOOR_SECONDS, min(int(raw), CEILING_SECONDS))
        except (TypeError, ValueError):
            floor = FLOOR_SECONDS
    return floor


def daily_cap(env=None) -> int:
    """Rolling 24h admission cap. 0 disables it. Unset means 400."""
    source = os.environ if env is None else env
    raw = source.get(DAILY_CAP_ENV) if hasattr(source, "get") else None
    if raw in (None, ""):
        return DEFAULT_DAILY_CAP
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP
    return 0 if value <= 0 else min(value, 100000)


def clamp_interval(seconds: int, floor: int | None = None) -> int:
    low = FLOOR_SECONDS if floor is None else int(floor)
    return max(low, min(CEILING_SECONDS, int(seconds)))


def clamp_batch(size: int) -> int:
    return max(MIN_BATCH, min(MAX_BATCH, int(size)))


def load_state(interval_seconds, batch_size, soft_streak, success_credits, floor: int | None = None) -> RateState:
    low = interval_floor() if floor is None else int(floor)
    try:
        interval = clamp_interval(int(interval_seconds), low)
    except (TypeError, ValueError):
        interval = max(START_INTERVAL_SECONDS, low)
    try:
        batch = clamp_batch(int(batch_size))
    except (TypeError, ValueError):
        batch = START_BATCH
    try:
        soft = max(0, min(SOFT_RUN, int(soft_streak)))
    except (TypeError, ValueError):
        soft = 0
    try:
        credits = max(0, min(BATCH_RECOVER_EVERY, int(success_credits)))
    except (TypeError, ValueError):
        credits = 0
    return RateState(interval, batch, soft, credits)


def on_quota(state: RateState, *, retry_after: int | None = None) -> tuple[RateState, int]:
    """Double the persisted interval. Retry-After lengthens this wait only.

    A one-hour Retry-After must not become the steady pace, or the next
    hour of successes would still be stuck at a few files an hour.
    """
    interval = clamp_interval(state.interval_seconds * QUOTA_FACTOR, interval_floor())
    delay = interval
    if retry_after is not None:
        try:
            delay = max(delay, int(retry_after))
        except (TypeError, ValueError):
            pass
    delay = min(rate_ceiling(delay), CEILING_SECONDS)
    # A quota or suspension signal drops the batch back to one file.
    return RateState(interval, MIN_BATCH, 0, 0), delay


def rate_ceiling(seconds: int) -> int:
    return min(CEILING_SECONDS, max(0, int(seconds)))


def on_soft(state: RateState) -> tuple[RateState, int | None]:
    """Count a 5xx or transport error. The third in a row multiplies by 1.5."""
    streak = state.soft_streak + 1
    if streak < SOFT_RUN:
        return RateState(state.interval_seconds, state.batch_size, streak, state.success_credits), None
    delay = clamp_interval((state.interval_seconds * SOFT_NUMERATOR) // SOFT_DENOMINATOR, interval_floor())
    return RateState(delay, state.batch_size, 0, state.success_credits), delay


def on_success(state: RateState, floor: int | None = None) -> RateState:
    """Subtract one step. The batch grows by one only after BATCH_RECOVER_EVERY clean cycles."""
    low = interval_floor() if floor is None else int(floor)
    interval = max(low, state.interval_seconds - ADDEND_SECONDS)
    credits = state.success_credits + 1
    batch = state.batch_size
    if credits >= BATCH_RECOVER_EVERY:
        credits = 0
        batch = min(MAX_BATCH, batch + 1)
    return RateState(interval, batch, 0, credits)


def parse_retry_after(raw, *, now=None) -> tuple[int | None, bool]:
    """Return (seconds, is_http_date). An HTTP-date is a suspension signal."""
    if raw is None:
        return None, False
    text = str(raw).strip()
    if not text:
        return None, False
    if text.lstrip("-").isdigit():
        try:
            return max(0, min(CEILING_SECONDS, int(text))), False
        except (TypeError, ValueError):
            return None, False
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None, False
    if when is None:
        return None, False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0, min(CEILING_SECONDS, int((when - current).total_seconds()))), True


def jittered_delay(interval_seconds: int, jitter_bps: int) -> int:
    """Extra delay in whole seconds. jitter_bps is 0..1000 (0..10%)."""
    span = max(0, min(JITTER_MAX_BPS, int(jitter_bps)))
    return int(interval_seconds) + (int(interval_seconds) * span) // 10000
