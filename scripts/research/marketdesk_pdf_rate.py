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

Fastest pace: batch 6 every 300s. The timer fires 288 times/day, so the
ceiling is 1,728 PDFs/day (72/hour). A 48h window holds 3,456 admissions.
Clearing 1,300 backlog rows while 200/day arrive is 1,700 PDFs, about 20h
at that pace (1,300 / (1,728 - 200)). The hard floor is 300s and the hard
ceiling is 24h, matching the old embargo cap. Nothing here is a claimed
provider quota.
"""
from __future__ import annotations

from dataclasses import dataclass

FLOOR_SECONDS = 300
CEILING_SECONDS = 24 * 60 * 60
START_INTERVAL_SECONDS = FLOOR_SECONDS
START_BATCH = 6
MAX_BATCH = START_BATCH
MIN_BATCH = 1
ADDEND_SECONDS = 30
QUOTA_FACTOR = 2
SOFT_RUN = 3
SOFT_NUMERATOR = 3
SOFT_DENOMINATOR = 2
BATCH_RECOVER_EVERY = 8
JITTER_MAX_BPS = 1000  # 10.00% added delay. Never negative, so jitter cannot admit early.


@dataclass(frozen=True)
class RateState:
    interval_seconds: int
    batch_size: int
    soft_streak: int
    success_credits: int


def start_state() -> RateState:
    return RateState(START_INTERVAL_SECONDS, START_BATCH, 0, 0)


def clamp_interval(seconds: int) -> int:
    return max(FLOOR_SECONDS, min(CEILING_SECONDS, int(seconds)))


def clamp_batch(size: int) -> int:
    return max(MIN_BATCH, min(MAX_BATCH, int(size)))


def load_state(interval_seconds, batch_size, soft_streak, success_credits) -> RateState:
    try:
        interval = clamp_interval(int(interval_seconds))
    except (TypeError, ValueError):
        interval = START_INTERVAL_SECONDS
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
    interval = clamp_interval(state.interval_seconds * QUOTA_FACTOR)
    delay = interval
    if retry_after is not None:
        try:
            delay = max(delay, int(retry_after))
        except (TypeError, ValueError):
            pass
    delay = min(rate_ceiling(delay), CEILING_SECONDS)
    return RateState(interval, state.batch_size, 0, 0), delay


def rate_ceiling(seconds: int) -> int:
    return min(CEILING_SECONDS, max(0, int(seconds)))


def on_soft(state: RateState) -> tuple[RateState, int | None]:
    """Count a 5xx or transport error. The third in a row multiplies by 1.5."""
    streak = state.soft_streak + 1
    if streak < SOFT_RUN:
        return RateState(state.interval_seconds, state.batch_size, streak, state.success_credits), None
    delay = clamp_interval((state.interval_seconds * SOFT_NUMERATOR) // SOFT_DENOMINATOR)
    return RateState(delay, state.batch_size, 0, state.success_credits), delay


def on_success(state: RateState) -> RateState:
    """Subtract one step. A batch grows by one only after several clean cycles."""
    interval = max(FLOOR_SECONDS, state.interval_seconds - ADDEND_SECONDS)
    credits = state.success_credits + 1
    batch = state.batch_size
    if credits >= BATCH_RECOVER_EVERY:
        credits = 0
        batch = min(MAX_BATCH, batch + 1)
    return RateState(interval, batch, 0, credits)


def jittered_delay(interval_seconds: int, jitter_bps: int) -> int:
    """Extra delay in whole seconds. jitter_bps is 0..1000 (0..10%)."""
    span = max(0, min(JITTER_MAX_BPS, int(jitter_bps)))
    return int(interval_seconds) + (int(interval_seconds) * span) // 10000
