#!/usr/bin/env python3
"""Post-close grace window — journal_sync end-of-session blind spot.

A fill landing between journal_sync's last in-hours cycle and the 16:00 ET
close (15:59:52 / 15:58:04 production incidents) was never journaled: the
market-hours gate stopped further cycles that day, and next-day cycles use a
fresh IB session that no longer sees prior-day executions.

Fix under test: a per-handler ``post_close_grace_minutes`` opt-in consumed by
the daemon gate. journal_sync sets 15 (two more 300s cycles sweep the
same-day IB session); every other market-hours handler keeps the hard close.
The "was the market open within the last N minutes" question routes through
``utils.market_calendar.market_state`` (the SoT), so weekends/holidays never
gain a window — grace only extends a session that actually happened.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler

ET = ZoneInfo("America/New_York")


class GraceHandler(BaseHandler):
    """Market-hours handler that opts into the 15-minute post-close grace."""

    name = "grace_handler"
    interval_seconds = 300
    requires_market_hours = True
    post_close_grace_minutes = 15
    service_name = None

    def execute(self):
        return {}


class HardCloseHandler(BaseHandler):
    """Market-hours handler on default (zero) grace — the hard close."""

    name = "hard_close_handler"
    interval_seconds = 60
    requires_market_hours = True
    service_name = None

    def execute(self):
        return {}


# ── window-relative dates (never hardcoded — they rot in CI) ─────────────


def recent_trading_day():
    from utils.market_calendar import load_holidays

    day = datetime.now(ET).date() - timedelta(days=1)
    while day.weekday() >= 5 or day.strftime("%Y-%m-%d") in load_holidays(day.year):
        day -= timedelta(days=1)
    return day


def recent_saturday():
    day = datetime.now(ET).date()
    while day.weekday() != 5:
        day -= timedelta(days=1)
    return day


def recent_weekday_holiday():
    from utils.market_calendar import load_holidays

    today = datetime.now(ET).date()
    candidates = [
        datetime.strptime(iso, "%Y-%m-%d").date()
        for year in (today.year, today.year - 1)
        for iso in load_holidays(year)
    ]
    candidates = [d for d in candidates if d <= today and d.weekday() < 5]
    if not candidates:
        pytest.skip("no past weekday holiday configured")
    return max(candidates)


@pytest.fixture(autouse=True)
def _static_calendar_only(monkeypatch):
    """Pin market_state to the deterministic weekday+holiday layers. The
    IBKR rolling cache on the host could mark the probed day early_close /
    closed and flake the assertions."""
    import utils.market_calendar as mc

    monkeypatch.setattr(mc, "_load_ibkr_calendar", lambda: {})


def run_once_at(handler, et_now):
    """Drive a real daemon cycle with the daemon's clock pinned to et_now."""
    daemon = MonitorDaemon(respect_market_hours=True)
    daemon.register(handler)
    with patch("monitor_daemon.daemon.datetime") as mock_dt:
        mock_dt.now.return_value = et_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        return daemon.run_once()


def at_close_plus(day, minutes):
    close = datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)
    return close + timedelta(minutes=minutes)


class TestPostCloseGraceGate:
    def test_grace_handler_runs_at_close_plus_10_on_trading_day(self):
        """(a) journal_sync-style handler still cycles 10 min after the close."""
        results = run_once_at(GraceHandler(), at_close_plus(recent_trading_day(), 10))
        assert "grace_handler" in results
        assert results["grace_handler"]["status"] == "ok"

    def test_grace_handler_not_runnable_at_close_plus_20(self):
        """(b) the window is bounded — 20 min after the close is dark."""
        results = run_once_at(GraceHandler(), at_close_plus(recent_trading_day(), 20))
        assert "grace_handler" not in results

    def test_grace_never_opens_a_weekend(self):
        """(c) grace only extends a session that actually happened."""
        results = run_once_at(GraceHandler(), at_close_plus(recent_saturday(), 10))
        assert "grace_handler" not in results

    def test_grace_never_opens_a_holiday(self):
        """(c) static-holiday weekdays stay closed regardless of grace."""
        results = run_once_at(GraceHandler(), at_close_plus(recent_weekday_holiday(), 10))
        assert "grace_handler" not in results

    def test_zero_grace_handler_keeps_hard_close(self):
        """(d) default-grace handlers are untouched — closed at close+1min."""
        results = run_once_at(HardCloseHandler(), at_close_plus(recent_trading_day(), 1))
        assert "hard_close_handler" not in results

    def test_grace_handler_still_runs_in_hours(self):
        """Grace never affects the normal in-hours path."""
        day = recent_trading_day()
        noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=ET)
        results = run_once_at(GraceHandler(), noon)
        assert "grace_handler" in results


class TestHandlerOptIn:
    def test_journal_sync_opts_into_15_minute_grace(self):
        from monitor_daemon.handlers.journal_sync import JournalSyncHandler

        assert JournalSyncHandler.post_close_grace_minutes == 15

    def test_other_market_hours_handlers_default_to_zero(self):
        from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
        from monitor_daemon.handlers.fill_monitor import FillMonitorHandler

        assert BaseHandler.post_close_grace_minutes == 0
        assert FillMonitorHandler.post_close_grace_minutes == 0
        assert ExitOrdersHandler.post_close_grace_minutes == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
