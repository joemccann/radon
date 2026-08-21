"""Flex error codes must be classified the way IBKR defines them.

Verified against IBKR's published error table
(https://www.ibkrguides.com/clientportal/performanceandstatements/flex3error.htm):

    1001  "Statement could not be generated at this time. Please try again shortly."
    1009  "The server is under heavy load. Statement could not be generated at
           this time. Please try again shortly."
    1018  "Too many requests have been made from this token. Please try again
           shortly."  Limited to one request per second, 10 requests per minute
           (per token).
    1019  "Statement generation in progress. Please try again shortly."

Only **1018** is a rate limit, and IBKR's published limit is 10 requests per
MINUTE, which clears in a minute. IBKR documents no daily, 24h or multi-day
cooldown anywhere; the 24h/48h/72h/168h ladder is Radon-local policy.

The bug this pins: `_FLEX_THROTTLE_CODES` contained 1001, 1018 AND 1019, and
every one of them raised FlexThrottleError and escalated that ladder. So:

  * **1019 means "still generating, poll again"** -- the ordinary not-ready
    response -- and it triggered a 24-HOUR backoff.
  * **1001 is a transient generation failure** -- "try again shortly" -- and it
    escalated a ladder toward a one-week embargo.

That is a system punishing itself for being told to wait a moment, and it is the
most plausible root cause of the 10-day cash-flow outage that began 2026-08-06,
ahead of both the 180s subprocess wall and any real IBKR throttle.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cash_flow_sync as cfs  # noqa: E402


def _err(code: str, message: str = "msg") -> ET.Element:
    return ET.fromstring(
        f"<FlexStatementResponse><Status>Fail</Status>"
        f"<ErrorCode>{code}</ErrorCode><ErrorMessage>{message}</ErrorMessage>"
        f"</FlexStatementResponse>"
    )


def _throttle_cls():
    from monitor_daemon.handlers._throttle_backoff import FlexThrottleError

    return FlexThrottleError


# ---------------------------------------------------------------------------
# 1018 — the ONLY documented rate limit
# ---------------------------------------------------------------------------


def test_1018_is_the_only_rate_limit_code():
    assert cfs._FLEX_THROTTLE_CODES == {"1018"}


def test_1018_raises_the_throttle_error():
    exc = cfs._flex_error_from(_err("1018", "Too many requests"), "SendRequest")
    assert isinstance(exc, _throttle_cls())


# ---------------------------------------------------------------------------
# 1019 — "generation in progress" is NOT-READY, not a throttle
# ---------------------------------------------------------------------------


def test_1019_is_not_a_throttle():
    """It is the ordinary not-ready response during polling."""
    exc = cfs._flex_error_from(_err("1019", "Statement generation in progress."), "GetStatement")
    assert not isinstance(exc, _throttle_cls())


def test_1019_is_not_an_error_at_all_on_a_poll():
    """Returning an exception at all would abort a poll loop that should keep
    going. 1019 must read as "keep waiting"."""
    assert cfs._flex_error_from(_err("1019"), "GetStatement") is None


# ---------------------------------------------------------------------------
# 1001 / 1009 — transient generation failures, the soft lane
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["1001", "1009"])
def test_transient_generation_failures_are_not_throttles(code):
    """IBKR says "try again shortly". Escalating a ladder toward a one-week
    embargo on these is what produced the 10-day outage."""
    exc = cfs._flex_error_from(_err(code), "SendRequest")
    assert exc is not None
    assert not isinstance(exc, _throttle_cls())


@pytest.mark.parametrize("code", ["1001", "1009"])
def test_transient_generation_failures_are_retryable(code):
    assert code in cfs._FLEX_TRANSIENT_CODES


def test_a_transient_failure_is_distinguishable_from_a_permanent_one():
    """A bad token or an unknown query id must NOT be retried; a generation
    failure must be. They cannot share an exception type."""
    transient = cfs._flex_error_from(_err("1001"), "SendRequest")
    permanent = cfs._flex_error_from(_err("1003", "Statement is not available."), "SendRequest")
    assert type(transient) is not type(permanent)


# ---------------------------------------------------------------------------
# the ladder is ours, not IBKR's
# ---------------------------------------------------------------------------


def test_the_docstring_no_longer_claims_1001_and_1019_are_throttles():
    src = Path(cfs.__file__).read_text()
    assert "throttle codes (1001 / 1018 / 1019)" not in src
    assert "1001 / 1018 / 1019" not in src


# ---------------------------------------------------------------------------
# 1025 — undocumented lockout. Not in IBKR's published v3 table (ends 1021).
# Observed message: "Too many failed attempts. Please review your configuration."
# Earned by retrying 1001/failed generation. Retrying it extends the lockout.
# ---------------------------------------------------------------------------


FAIL_1025_MSG = "Too many failed attempts. Please review your configuration."


def test_1025_is_a_lockout_not_an_app_error():
    """1012/1014 are config. 1025 is a failed-attempts lockout on a valid query."""
    exc = cfs._flex_error_from(_err("1025", FAIL_1025_MSG), "SendRequest")
    assert isinstance(exc, cfs._FlexLockoutError)
    assert not isinstance(exc, cfs._FlexAppError)
    assert not isinstance(exc, _throttle_cls())


def test_1025_is_not_retryable_on_either_leg():
    send = cfs._flex_error_from(_err("1025", FAIL_1025_MSG), "SendRequest")
    poll = cfs._flex_error_from(_err("1025", FAIL_1025_MSG), "GetStatement")
    assert type(send) is type(poll) is cfs._FlexLockoutError
