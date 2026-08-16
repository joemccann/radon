#!/usr/bin/env python3
"""Regression test — cash_flow_sync SendRequest budget under repeated timeouts.

Production incident (2026-08-03, VPS journald UTC):

    21:00:44  handler fired (17:00 ET daily window)
    21:03:44  sync subprocess timed out after 180s (not a Flex code)
              -> soft-failure lane, record_soft_failure ~5-min embargo
    21:08:47  handler re-fired; second Flex SendRequest hit throttle
              code 1001 -> record_throttle -> 24h circuit breaker,
              next_attempt_at 2026-08-04T21:08:47Z

That timeline is the BEST case: the second attempt happened to surface a
documented throttle code (1001), which engaged the 24h breaker after only
2 SendRequests. The unguarded case is a Flex service that keeps TIMING
OUT and never returns 1001/1018/1019: every timeout routes through
``record_soft_failure``, which never escalates ``throttle_count`` and
always sets a flat ~5-min embargo. ``is_due()`` keeps answering True for
the rest of the ET day (past 17:00, last_run never latched on failure),
so the daemon retries roughly every 5 minutes until midnight ET —
dozens of Flex SendRequests in one evening.

The handler's own docstring (cash_flow_sync.py lines 8-15) states the
budget: IBKR Flex is a sliding-window rate limit where every request
during throttle — INCLUDING FAILURES — pushes the reset further out
(May 9 2026: ~24h of visibility burned by exactly this pattern at 4h
cadence). Design intent is ONE well-timed daily call. A timeout during
throttle is indistinguishable, request-budget-wise, from a 1001: it
still burns a SendRequest. The soft-retry lane must therefore be
bounded per ET day (a small handful of attempts), not unbounded.

Dates are window-relative (most recent past trading day resolved via the
handler's own ``_is_trading_day_et``) — hardcoded dates rot in CI.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 fallback
    from backports.zoneinfo import ZoneInfo  # type: ignore

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

ET = ZoneInfo("America/New_York")

# Budget derived from the module docstring ("one well-timed daily call
# is sufficient and stays inside the rate budget"). Allow a small
# soft-retry allowance for a genuinely transient blip, but repeated
# timeouts must trip a bound — not loop until midnight.
MAX_FLEX_ATTEMPTS_PER_ET_DAY = 3

DAEMON_LOOP_SECONDS = 30


def _subprocess_timeout_seconds() -> float:
    from monitor_daemon.handlers.cash_flow_sync import subprocess_timeout_seconds

    return subprocess_timeout_seconds()


def _recent_past_trading_day_17et() -> datetime:
    """17:00 ET (as UTC) on the most recent trading day at least 2 days ago."""
    from monitor_daemon.handlers.cash_flow_sync import _is_trading_day_et

    candidate = datetime.now(ET) - timedelta(days=2)
    for _ in range(10):
        probe = candidate.replace(hour=17, minute=0, second=0, microsecond=0)
        probe_utc = probe.astimezone(timezone.utc)
        if _is_trading_day_et(probe_utc):
            return probe_utc
        candidate -= timedelta(days=1)
    pytest.fail("no ET trading day found in the last 10 days")


def _fresh_handler():
    from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler

    return CashFlowSyncHandler()


class _TimeoutCountingInner:
    """Stub for ``_execute_inner`` — every call is one Flex SendRequest
    that ends in the production 180s subprocess timeout."""

    def __init__(self) -> None:
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        raise RuntimeError(
            f"cash_flow_sync timed out after {_subprocess_timeout_seconds():.0f}s"
        )


def _drive_daemon_evening(handler, inner, start_utc: datetime) -> int:
    """Simulate the daemon's 30s loop from 17:00 ET until midnight ET.

    Each due cycle runs ``handler.run()`` (the real BaseHandler wrapper —
    soft-failure lane, no last_run latch) and then advances the clock by
    the 180s subprocess timeout the attempt actually consumed. Non-due
    cycles advance 30s. Returns the number of Flex attempts made.
    """
    end_of_day_et = start_utc.astimezone(ET).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    end_utc = end_of_day_et.astimezone(timezone.utc)

    now = start_utc
    while now <= end_utc:
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            if handler.is_due():
                handler.run()
                now += timedelta(seconds=_subprocess_timeout_seconds())
                continue
        now += timedelta(seconds=DAEMON_LOOP_SECONDS)
    return inner.attempts


class TestTimeoutRetryBudget:
    """Repeated 180s timeouts must NOT produce an unbounded ~5-min
    SendRequest loop within one ET day."""

    def test_repeated_timeouts_stay_within_daily_flex_budget(self):
        start = _recent_past_trading_day_17et()
        handler = _fresh_handler()
        inner = _TimeoutCountingInner()
        handler._execute_inner = inner  # type: ignore[method-assign]

        attempts = _drive_daemon_evening(handler, inner, start)

        assert attempts <= MAX_FLEX_ATTEMPTS_PER_ET_DAY, (
            f"{attempts} Flex SendRequests fired in a single ET evening of "
            f"repeated 180s timeouts (budget: {MAX_FLEX_ATTEMPTS_PER_ET_DAY}). "
            "record_soft_failure never escalates or caps, so a Flex outage "
            "that times out (never returning code 1001/1018/1019) retries "
            "every ~5 min until midnight ET — each retry burns the "
            "sliding-window budget the module docstring says to protect."
        )


class TestPollBudgetOrdering:
    """C1 — one number, one owner.

    Production numbers before this test: the script polls on a capped
    exponential ladder with a ~569s wall budget, the handler kills the
    subprocess at 180s, and the daemon's own deadline (REL-008, landed
    2026-08-09) preempts BOTH at 120s. Only 14 of the script's 40 polls
    ever ran; its own "statement not ready after N polls" error has never
    fired in production. Fourteen of the 16 recorded `cash_flow_sync`
    failure transitions between 2026-06-16 and 2026-08-07 are
    `timed out after 180s` — the dominant failure mode is us killing our
    own process mid-poll, then spending up to two more SendRequests that
    evening on the soft-retry lane.

    The three deadlines must nest, outermost first. Moving one without
    the others just relabels which layer does the killing.
    """

    def test_script_budget_is_inside_the_subprocess_timeout(self):
        import cash_flow_sync

        assert cash_flow_sync.FLEX_POLL_BUDGET_SECONDS < _subprocess_timeout_seconds()

    def test_subprocess_timeout_is_inside_the_handler_deadline(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler

        assert _subprocess_timeout_seconds() < CashFlowSyncHandler.max_runtime_seconds

    def test_handler_deadline_overrides_the_daemon_default(self):
        from monitor_daemon.daemon import DEFAULT_HANDLER_DEADLINE_SECONDS
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler

        assert (
            CashFlowSyncHandler.max_runtime_seconds > DEFAULT_HANDLER_DEADLINE_SECONDS
        ), (
            "without an explicit override the 120s daemon deadline kills the "
            "subprocess first, bypassing _record_failure entirely"
        )

    def test_the_subprocess_actually_uses_the_reconciled_timeout(self):
        """The number must reach `subprocess.run`, not just a constant."""
        from unittest.mock import patch as _patch
        from types import SimpleNamespace

        from monitor_daemon.handlers import cash_flow_sync as handler_mod

        handler = handler_mod.CashFlowSyncHandler()
        with _patch.dict(
            "os.environ",
            {"IB_FLEX_TOKEN": "t", "IB_FLEX_NAV_QUERY_ID": "1442520"},
        ), _patch.object(
            handler_mod.subprocess, "run",
            return_value=SimpleNamespace(returncode=0, stdout='{"status":"ok"}', stderr=""),
        ) as run_mock:
            handler._execute_inner()

        assert run_mock.call_args.kwargs["timeout"] == _subprocess_timeout_seconds()


class TestIncidentTimelineIsBestCase:
    """The 2026-08-03 production sequence — timeout -> 5-min soft embargo
    -> retry -> 1001 -> 24h breaker — stops after exactly 2 SendRequests.
    This PASSES today and pins the interaction; it exists to document
    that the bounded outcome currently depends on IBKR volunteering a
    documented throttle code on the retry, which is luck, not a guard."""

    def test_timeout_then_1001_engages_24h_breaker_after_two_attempts(self):
        from monitor_daemon.handlers import _throttle_backoff

        start = _recent_past_trading_day_17et()
        handler = _fresh_handler()

        outcomes = iter(
            [
                RuntimeError("cash_flow_sync timed out after 180s"),
                _throttle_backoff.FlexThrottleError(
                    "1001", "Statement could not be generated at this time"
                ),
            ]
        )
        attempts = {"n": 0}

        def inner():
            attempts["n"] += 1
            raise next(outcomes)

        handler._execute_inner = inner  # type: ignore[method-assign]

        # Attempt 1 at 17:00 ET: timeout -> soft 5-min embargo.
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=start
        ):
            assert handler.is_due() is True
            handler.run()
        assert attempts["n"] == 1

        # ~2 min later: soft embargo holds.
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc",
            return_value=start + timedelta(minutes=2),
        ):
            assert handler.is_due() is False

        # ~8 min later (production re-fire ~21:08 UTC): retry hits 1001.
        refire = start + timedelta(minutes=8)
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=refire
        ):
            assert handler.is_due() is True
            handler.run()
        assert attempts["n"] == 2
        assert handler._backoff_state["throttle_count"] == 1

        # 24h breaker: not due for the rest of the evening nor before
        # the embargo expires the next day.
        for probe in (
            refire + timedelta(hours=2),
            refire + timedelta(hours=23),
        ):
            with patch(
                "monitor_daemon.handlers.cash_flow_sync._now_utc",
                return_value=probe,
            ):
                assert handler.is_due() is False
        assert attempts["n"] == 2
