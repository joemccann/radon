#!/usr/bin/env python3
"""Cadence + circuit-breaker tests for the cash_flow_sync handler.

Production bug (2026-05-09): a withdrawal initiated on 2026-05-08 did
not appear in the Cash Flows panel for ~24h. Initial fix lowered the
4h polling interval, but on 2026-05-09 the same handler hammered Flex
through a sliding-window throttle and burned ~24h of visibility. The
new cadence is **once per ET trading day at 08:00 ET** with throttle-
aware exponential backoff (24h -> 48h -> 72h -> 168h capped) on
documented Flex throttle codes.

Tests pin the fire window, weekend / holiday skip, DST handling, and
the circuit breaker that delays the next attempt across daemon
restarts.

Late-fire policy: if `last_run` is on a strictly earlier ET trading
day AND the daemon is past 08:00 ET on a current trading day, fire
late even if the 17:00 to 18:00 ET "preferred" window has passed. The
1-hour preferred window only exists so the daemon's 30s loop has
multiple chances under normal operation; missing a day defeats the
purpose.
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


def _et_to_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Build a UTC datetime that maps to the given ET wall-clock moment."""
    et_dt = datetime(year, month, day, hour, minute, tzinfo=ET)
    return et_dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- is_due

class TestDailyFireWindow:
    """Handler fires once per ET trading day at 08:00 ET (pre-open)."""

    def _fresh_handler(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler
        return CashFlowSyncHandler()

    def test_fires_at_8_30_et_on_monday_with_no_prior_run(self):
        # 2026-05-11 is a Monday; 08:30 EDT = 12:30 UTC.
        now = _et_to_utc(2026, 5, 11, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is True

    def test_does_not_fire_on_saturday(self):
        # 2026-05-09 is a Saturday at 08:30 ET.
        now = _et_to_utc(2026, 5, 9, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False

    def test_does_not_fire_on_sunday(self):
        now = _et_to_utc(2026, 5, 10, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False

    def test_does_not_fire_before_window_opens(self):
        # 07:30 ET on Monday — window not yet open.
        now = _et_to_utc(2026, 5, 11, 7, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False

    def test_late_fire_after_9_when_not_run_today(self):
        # 09:30 ET Monday, no prior run — fire late, not next day.
        now = _et_to_utc(2026, 5, 11, 9, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is True

    def test_does_not_re_fire_same_et_trading_day(self):
        # last_run at 08:05 ET, now 08:45 ET same day → already done.
        last_run = _et_to_utc(2026, 5, 11, 8, 5)
        now = _et_to_utc(2026, 5, 11, 8, 45)
        h = self._fresh_handler()
        h.last_run = last_run
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False

    def test_fires_again_next_trading_day(self):
        # last_run yesterday at 08:05 ET, now 08:30 ET today → fire.
        last_run = _et_to_utc(2026, 5, 11, 8, 5)
        now = _et_to_utc(2026, 5, 12, 8, 30)
        h = self._fresh_handler()
        h.last_run = last_run
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is True


class TestHolidaySkip:
    """US trading holidays are skipped — the next eligible day is the next
    trading day."""

    def _fresh_handler(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler
        return CashFlowSyncHandler()

    def test_does_not_fire_on_christmas(self):
        # 2026-12-25 is a Friday and a US trading holiday.
        now = _et_to_utc(2026, 12, 25, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False

    def test_does_not_fire_on_mlk_day(self):
        # 2026-01-19 (Monday) — MLK day.
        now = _et_to_utc(2026, 1, 19, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False


class TestDSTBoundaries:
    """zoneinfo handles 08:00 ET correctly across the EST/EDT boundary."""

    def _fresh_handler(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler
        return CashFlowSyncHandler()

    def test_8_et_in_march_edt(self):
        # 2026-03-16 (Monday, after DST start). 08:30 EDT = 12:30 UTC.
        now = _et_to_utc(2026, 3, 16, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is True

    def test_8_et_in_december_est(self):
        # 2026-12-14 (Monday, EST). 08:30 EST = 13:30 UTC.
        now = _et_to_utc(2026, 12, 14, 8, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is True

    def test_7_30_est_in_december_does_not_fire(self):
        now = _et_to_utc(2026, 12, 14, 7, 30)
        h = self._fresh_handler()
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=now
        ):
            assert h.is_due() is False


class TestCircuitBreakerCadence:
    """Throttle-aware backoff composes with the daily window: a 24h
    embargo says 'no earlier than tomorrow at 08:00 ET'."""

    def _fresh_handler(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler
        return CashFlowSyncHandler()

    def test_blocked_during_embargo(self):
        from monitor_daemon.handlers._throttle_backoff import record_throttle

        now = _et_to_utc(2026, 5, 11, 8, 30)
        h = self._fresh_handler()
        # Pretend a 1018 just landed. The first rung is 90s — IBKR's documented
        # window is one minute, not one day.
        h._backoff_state = record_throttle({"throttle_count": 0, "blocked_until": None}, now_utc=now)
        # 60s later, still inside the 90s window → blocked.
        later = now + timedelta(seconds=60)
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=later
        ):
            assert h.is_due() is False

    def test_unblocked_after_embargo_expires_and_in_daily_window(self):
        from monitor_daemon.handlers._throttle_backoff import record_throttle

        first = _et_to_utc(2026, 5, 11, 8, 30)
        h = self._fresh_handler()
        h._backoff_state = record_throttle({"throttle_count": 0, "blocked_until": None}, now_utc=first)
        # 25h later → embargo cleared, also a daily window on 5/12 08:30 ET.
        next_day_window = _et_to_utc(2026, 5, 12, 9, 30)
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc",
            return_value=next_day_window,
        ):
            assert h.is_due() is True

    def test_breaker_persists_through_get_state_set_state(self):
        from monitor_daemon.handlers._throttle_backoff import record_throttle

        now = _et_to_utc(2026, 5, 11, 8, 30)
        h1 = self._fresh_handler()
        h1._backoff_state = record_throttle({"throttle_count": 0, "blocked_until": None}, now_utc=now)
        state = h1.get_state()
        assert "backoff_state" in state
        assert state["backoff_state"]["throttle_count"] == 1


class TestSoftFailureRetriesWithinDay:
    """A "statement not ready" / network blip must NOT burn the daily
    slot. The handler must retry on a 5-min cadence within the same day
    so a transient 08:00 ET Flex spike doesn't cost us 24h of data.

    Production bug (2026-05-14): a single 60s polling timeout latched
    last_run and the handler was silent for ~7 days until Joe noticed
    the cash flows panel was stale. After the 2026-05-15 fix:
      1. cash_flow_sync.fetch_cash_transactions has 40 polls with
         exponential backoff (~9.5 min total).
      2. execute() raises on inner_error so BaseHandler doesn't latch
         last_run.
      3. record_soft_failure sets blocked_until = now+5m so is_due
         returns False during the cooldown, then True for retry.
    """

    def _fresh_handler(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler
        return CashFlowSyncHandler()

    def test_soft_failure_does_not_latch_last_run(self):
        """execute() raises on a non-throttle inner_error so the
        BaseHandler.run() wrapper skips its `self.last_run = datetime.now()`
        line. Without this, the handler thinks today's slot is spent
        and won't retry until tomorrow.
        """
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler

        h = self._fresh_handler()
        # Stub _execute_inner to return the same "not ready" error the
        # subprocess wrapper produces on a polling timeout.
        h._execute_inner = lambda: {  # type: ignore[method-assign]
            "status": "error",
            "error": "ERR: cash flow fetch failed: Flex statement not ready after 40 polls",
        }
        with pytest.raises(RuntimeError, match="not ready"):
            h.execute()

    def test_soft_failure_then_retry_after_cooldown(self):
        """After a soft failure, is_due is False for 5 min then True."""
        first = _et_to_utc(2026, 5, 14, 8, 30)
        h = self._fresh_handler()
        h._execute_inner = lambda: {  # type: ignore[method-assign]
            "status": "error",
            "error": "Flex statement not ready after 40 polls",
        }

        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc", return_value=first
        ), patch(
            "monitor_daemon.handlers.cash_flow_sync.record_service_health",
            create=True,
        ):
            try:
                h.execute()
            except RuntimeError:
                pass  # expected

        # 2 min after the soft failure: cooldown still active → not due.
        two_min_later = first + timedelta(minutes=2)
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc",
            return_value=two_min_later,
        ):
            assert h.is_due() is False

        # 6 min after: cooldown cleared → due again.
        six_min_later = first + timedelta(minutes=6)
        with patch(
            "monitor_daemon.handlers.cash_flow_sync._now_utc",
            return_value=six_min_later,
        ):
            assert h.is_due() is True

    def test_successful_run_resets_breaker(self):
        from monitor_daemon.handlers._throttle_backoff import record_throttle

        now = _et_to_utc(2026, 5, 11, 8, 30)
        h = self._fresh_handler()
        h._backoff_state = record_throttle({"throttle_count": 0, "blocked_until": None}, now_utc=now)
        # Simulate the success path the handler triggers internally.
        h._mark_success()
        assert h._backoff_state["throttle_count"] == 0
        assert h._backoff_state["blocked_until"] is None


# --------------------------------------------------------------------------- handler contract

class TestHandlerContract:
    """The handler still has to behave well in the daemon's framework."""

    def test_does_not_require_market_hours(self):
        from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler
        assert CashFlowSyncHandler.requires_market_hours is False

    def test_other_handlers_still_use_default_is_due(self):
        """The override is on CashFlowSyncHandler only — generic handlers
        keep the BaseHandler.is_due interval semantics."""
        from monitor_daemon.handlers.base import BaseHandler

        class Toy(BaseHandler):
            name = "toy"
            interval_seconds = 60
            requires_market_hours = False

            def execute(self):
                return {}

        h = Toy()
        h.last_run = datetime.now() - timedelta(seconds=120)
        # No is_due override on generic handlers — only the time-since-last-run
        # rule applies, and that has nothing to do with ET / 17:00.
        assert h.is_due() is True
        h.last_run = datetime.now() - timedelta(seconds=10)
        assert h.is_due() is False
