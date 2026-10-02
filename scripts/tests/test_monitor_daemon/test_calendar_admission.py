"""REL-021b / R-030: RTH uses the calendar without losing R-625 monitoring."""
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from monitor_daemon import daemon as mod
from utils import market_calendar as calendar


@pytest.mark.parametrize('day,hour,minute,entry,expected', [
    ('2026-10-02', 10, 0, {'status': 'closed'}, False),
    ('2026-10-02', 13, 1, {'status': 'early_close', 'open_et': '09:30', 'close_et': '13:00'}, False),
    ('2026-10-02', 12, 59, {'status': 'early_close', 'open_et': '09:30', 'close_et': '13:00'}, True),
    ('2026-07-03', 10, 0, None, False),
])
def test_rth_admission_observes_calendar(day, hour, minute, entry, expected, monkeypatch):
    now = datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=ZoneInfo('America/New_York'))
    monkeypatch.setattr(mod, 'datetime', Mock(now=Mock(return_value=now)))
    monkeypatch.setattr(calendar, '_load_ibkr_calendar', lambda: {day: entry} if entry else {})
    daemon = mod.MonitorDaemon()
    assert daemon.is_market_hours() is expected
    handler = Mock(session_window='rth', requires_market_hours=True, post_close_grace_minutes=0)
    handler.is_due.return_value = True
    assert daemon._handler_can_run_now(handler) is expected


def test_ext_monitoring_keeps_clock_fallback_despite_calendar_closed(monkeypatch, caplog):
    now = datetime(2026, 10, 2, 10, tzinfo=ZoneInfo('America/New_York'))
    monkeypatch.setattr(mod, 'datetime', Mock(now=Mock(return_value=now)))
    monkeypatch.setattr(calendar, '_load_ibkr_calendar', lambda: {'2026-10-02': {'status': 'closed'}})
    daemon = mod.MonitorDaemon()
    handler = Mock(session_window='equity_ext', requires_market_hours=True, post_close_grace_minutes=0)
    handler.is_due.return_value = True
    assert daemon._handler_can_run_now(handler, market_hours=False) is True
    assert 'disagree' in caplog.text


def test_calendar_failure_keeps_valid_rth_clock_fallback(monkeypatch, caplog):
    now = datetime(2026, 10, 2, 10, tzinfo=ZoneInfo('America/New_York'))
    monkeypatch.setattr(mod, 'datetime', Mock(now=Mock(return_value=now)))
    def failure(_now):
        raise OSError('fixture unreadable calendar')
    monkeypatch.setattr(calendar, 'market_state', failure)
    assert mod.MonitorDaemon().is_market_hours() is True
    assert 'calendar' in caplog.text.lower()
