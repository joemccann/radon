#!/usr/bin/env python3
"""Throttle-aware exponential backoff for Flex Web Service polling.

Scoped to ONE error code. IBKR's 1018 row is the only documented rate limit:

    "Too many requests have been made from this token. Please try again
     shortly.  Limited to one request per second, 10 requests per minute
     (per token)."
    https://www.ibkrguides.com/clientportal/performanceandstatements/flex3error.htm

That is a ONE MINUTE window, and IBKR documents no daily, multi-day or weekly
cooldown anywhere. 1001 and 1019 are NOT rate limits and must never reach this
module; see `cash_flow_sync._FLEX_THROTTLE_CODES`.

State machine:

    success      → counter reset to 0, no embargo.
    throttle hit → counter++; embargo = THROTTLE_EMBARGO[counter-1].
    transient    → no escalation; embargo = SOFT_EMBARGO_SECS (one cycle).

Embargo schedule (capped at 1h):

    1st 1018:   90s   — just past the documented window
    2nd 1018:    5m
    3rd 1018:   15m
    4th+ 1018:   1h   — cap

**Why this changed.** The ladder was 24h → 48h → 72h → 168h, modelling a
constraint roughly three orders of magnitude harsher than the one IBKR actually
imposes; a full walk cost 312 hours. Paired with the classifier bug that treated
1001 and 1019 as throttles, a statement seconds from ready could put the sync to
sleep for a day and a few transient failures could walk it to a week. That is
the 10-day outage from 2026-08-06.

It still escalates, deliberately. A second 1018 ninety seconds after the first
means something is making sustained requests rather than the once-a-day call
this handler makes, and there is a known uncontrolled consumer in
`portfolio_performance.py` (up to two on-demand requests per page render, no
breaker). Escalating in minutes surfaces that without hiding the data for a week.

Stored as a plain dict so the calling handler can persist it via
``BaseHandler.get_state`` / ``set_state`` and survive daemon restarts.
"""
from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


# Embargo durations in seconds, indexed by (counter - 1). The last entry
# is the cap that all further attempts use.
THROTTLE_EMBARGO_SECS = (
    90,              # 90s after 1st — just past IBKR's documented 1-minute window
    5 * 60,          # 5m after 2nd
    15 * 60,         # 15m after 3rd
    60 * 60,         # 1h cap
)

# A non-throttle transient (network blip, parse error) does not escalate.
# Daily handlers re-fire the next 17:00 ET window — short embargo only.
SOFT_EMBARGO_SECS = 0


class FlexThrottleError(RuntimeError):
    """Raised when IBKR returns a documented throttle code (1001/1018/1019).

    The daemon handler intercepts this specifically to advance the
    circuit breaker without retrying — every retry burns the
    sliding-window budget and resets the throttle clock.
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"Flex throttle (code {code}): {message}")


def initial_state() -> Dict[str, Any]:
    """Return a fresh, empty backoff state."""
    return {"throttle_count": 0, "blocked_until": None}


def _embargo_seconds_for(count: int) -> int:
    if count <= 0:
        return 0
    idx = min(count - 1, len(THROTTLE_EMBARGO_SECS) - 1)
    return THROTTLE_EMBARGO_SECS[idx]


def record_throttle(state: Dict[str, Any], *, now_utc: datetime) -> Dict[str, Any]:
    """Advance the throttle counter and compute the next eligible time."""
    count = int(state.get("throttle_count") or 0) + 1
    embargo = _embargo_seconds_for(count)
    return {
        "throttle_count": count,
        "blocked_until": (now_utc + timedelta(seconds=embargo)).isoformat(),
    }


def record_success(state: Dict[str, Any]) -> Dict[str, Any]:
    """Reset on a successful sync."""
    return initial_state()


SOFT_RETRY_COOLDOWN_SECS = 5 * 60  # 5-min spacing between within-day retries


def record_soft_failure(
    state: Dict[str, Any],
    *,
    now_utc: datetime,
    cooldown_seconds: int = SOFT_RETRY_COOLDOWN_SECS,
) -> Dict[str, Any]:
    """Network blip / parse error / "statement not ready" — do not
    escalate the throttle counter, but DO set a short embargo so the
    handler retries on a measured cadence rather than every 30s.

    The cooldown is intentionally bounded (5 min default) so a transient
    EOD-spike "not ready" failure at 17:00 ET still recovers within the
    same trading day. Throttle (1001/1018/1019) errors take a different
    path with the exponential `record_throttle` ladder (24h+).
    """
    return {
        "throttle_count": int(state.get("throttle_count") or 0),
        "blocked_until": (now_utc + timedelta(seconds=cooldown_seconds)).isoformat(),
    }


# R-169: a deadline that will not parse is a breaker in an UNKNOWN state.
# `blocked_until` returns this instead of None so a caller can tell "no
# lockout was ever recorded" from "a lockout was recorded and the record is
# corrupt" — the two used to be the same answer.
log = logging.getLogger(__name__)

CORRUPT_DEADLINE_SENTINEL = object()


def is_blocked(state: Dict[str, Any], *, now_utc: datetime) -> bool:
    """True iff `now_utc` is before the recorded `blocked_until`.

    Fails CLOSED on an unparseable deadline. Returning False there silently
    disarmed the 1018 rate-limit breaker: unlike the 1025 lockout, which has
    the token-wide embargo standing the caller down behind it, nothing else
    bounds this path, so a corrupt persisted string put the handler straight
    back onto the endpoint that throttled it.
    """
    raw = state.get("blocked_until")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        log.warning("throttle blocked_until is unparseable (%r) — staying blocked", raw)
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return now_utc < until


def blocked_until(state: Dict[str, Any]) -> Optional[datetime]:
    """Return the parsed `blocked_until` datetime, None, or the corrupt
    sentinel (R-169) when a deadline was recorded but will not parse."""
    raw = state.get("blocked_until")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return CORRUPT_DEADLINE_SENTINEL  # type: ignore[return-value]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
