"""REL-209 (R-578, R-583): a calendar error in the equity_ext gate must fail
to RTH, not to 'never', and the gated-off due-handler path is observable.

R-578: `is_equity_ext_session_et` raising left `_handler_can_run_now` on the
grace path with fill_monitor's 0-minute grace — no fill detection all day,
including regular hours. server.py falls back to RTH on the same error; the
daemon must match.
"""
from __future__ import annotations

import logging
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


def can_run_at(et_now, monkeypatch):
    import utils.market_calendar as mc

    def _boom(*a, **kw):
        raise RuntimeError("tzdata / calendar regression")

    monkeypatch.setattr(mc, "is_equity_ext_session_et", _boom)

    daemon = MonitorDaemon(respect_market_hours=True)
    handler = ExtHandler()
    daemon.register(handler)
    with patch("monitor_daemon.daemon.datetime") as mock_dt:
        mock_dt.now.return_value = et_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        return daemon._handler_can_run_now(handler)


@pytest.fixture(autouse=True)
def _static_calendar_only(monkeypatch):
    import utils.market_calendar as mc

    monkeypatch.setattr(mc, "_load_ibkr_calendar", lambda: {})


class TestExtGateFailsToRth:
    def test_calendar_error_still_runs_at_1000_et(self, monkeypatch):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=ET)
        assert can_run_at(at, monkeypatch) is True

    def test_calendar_error_does_not_run_at_2100_et(self, monkeypatch):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 21, 0, tzinfo=ET)
        assert can_run_at(at, monkeypatch) is False

    def test_calendar_error_logs_a_warning(self, monkeypatch, caplog):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=ET)
        with caplog.at_level(logging.WARNING, logger="monitor_daemon.daemon"):
            can_run_at(at, monkeypatch)
        assert any("equity_ext" in r.message for r in caplog.records)


class TestGatedOffDueHandlerIsObservable:
    def test_due_handler_outside_window_logs_at_info(self, caplog):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 21, 0, tzinfo=ET)
        daemon = MonitorDaemon(respect_market_hours=True)
        daemon.register(ExtHandler())
        with caplog.at_level(logging.INFO, logger="monitor_daemon.daemon"):
            with patch("monitor_daemon.daemon.datetime") as mock_dt:
                mock_dt.now.return_value = at
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                daemon.run_once()
        assert any(
            "ext_handler" in r.message and r.levelno >= logging.INFO
            for r in caplog.records
            if "Skipping" in r.message or "outside" in r.message
        )


def can_run_at_with_calendar_saying_closed(et_now, monkeypatch):
    """The realistic regression: `_load_ibkr_calendar` swallows
    FileNotFoundError / JSONDecodeError / OSError, so a stale or corrupt
    cache makes `is_equity_ext_session_et` return FALSE, never raise."""
    import utils.market_calendar as mc

    monkeypatch.setattr(mc, "is_equity_ext_session_et", lambda _now: False)

    daemon = MonitorDaemon(respect_market_hours=True)
    handler = ExtHandler()
    daemon.register(handler)
    with patch("monitor_daemon.daemon.datetime") as mock_dt:
        mock_dt.now.return_value = et_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        return daemon._handler_can_run_now(handler)


class TestCalendarDisagreementIsNotSilentlyAWeekend:
    """R-625: REL-209's fallback covers only the RAISING branch, but the
    module it guards is documented 'Never raises'. A corrupt calendar cache
    marking a real trading day closed disabled fill monitoring for the whole
    day, indistinguishable in the log from a normal weekend skip."""

    def test_a_calendar_saying_closed_at_1000_et_still_runs(self, monkeypatch):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=ET)
        assert can_run_at_with_calendar_saying_closed(at, monkeypatch) is True

    def test_the_disagreement_is_logged_distinctly(self, monkeypatch, caplog):
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 10, 0, tzinfo=ET)
        with caplog.at_level(logging.WARNING, logger="monitor_daemon.daemon"):
            can_run_at_with_calendar_saying_closed(at, monkeypatch)
        assert any("disagree" in r.message for r in caplog.records), [
            r.message for r in caplog.records
        ]

    def test_a_genuine_overnight_hour_is_still_gated_off(self, monkeypatch):
        """No disagreement at 21:00 ET — both gates say closed, so the
        widened branch must not turn the ext gate into 'always run'."""
        day = recent_trading_day()
        at = datetime(day.year, day.month, day.day, 21, 0, tzinfo=ET)
        assert can_run_at_with_calendar_saying_closed(at, monkeypatch) is False
