"""fill_monitor must run during equity EXT (04:00-20:00 ET), not only RTH.

2026-09-02 16:24 ET: AVGO SELL 1000 @ 355 filled in IBKR. fill_monitor's
RTH gate (and 0 post-close grace) had already stopped, so /orders kept the
row WORKING until a manual SYNC NOW. Overnight 20:00-03:50 is not EXT.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler

ET = ZoneInfo("America/New_York")


class ExtHandler(BaseHandler):
    name = "ext_handler"
    interval_seconds = 60
    requires_market_hours = True
    session_window = "equity_ext"
    service_name = None

    def execute(self):
        return {}


def recent_trading_day():
    from utils.market_calendar import load_holidays

    day = datetime.now(ET).date() - timedelta(days=1)
    while day.weekday() >= 5 or day.strftime("%Y-%m-%d") in load_holidays(day.year):
        day -= timedelta(days=1)
    return day


def run_once_at(handler, et_now):
    daemon = MonitorDaemon(respect_market_hours=True)
    daemon.register(handler)
    with patch("monitor_daemon.daemon.datetime") as mock_dt:
        mock_dt.now.return_value = et_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        return daemon.run_once()


@pytest.fixture(autouse=True)
def _static_calendar_only(monkeypatch):
    import utils.market_calendar as mc

    monkeypatch.setattr(mc, "_load_ibkr_calendar", lambda: {})


class TestFillMonitorEquityExtGate:
    def test_runs_at_1630_on_trading_day(self):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 16, 30, tzinfo=ET)
        results = run_once_at(ExtHandler(), at)
        assert "ext_handler" in results

    def test_runs_at_premarket_0800(self):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 8, 0, tzinfo=ET)
        results = run_once_at(ExtHandler(), at)
        assert "ext_handler" in results

    def test_does_not_run_overnight_2100(self):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 21, 0, tzinfo=ET)
        results = run_once_at(ExtHandler(), at)
        assert "ext_handler" not in results

    def test_does_not_run_weekend(self):
        day = datetime.now(ET).date()
        while day.weekday() != 5:
            day -= timedelta(days=1)
        at = datetime(day.year, day.month, day.day, 16, 30, tzinfo=ET)
        results = run_once_at(ExtHandler(), at)
        assert "ext_handler" not in results
