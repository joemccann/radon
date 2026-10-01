"""REL-021b / R-046: unknown Eastern time cannot admit a market-hours action."""
from datetime import datetime, timezone
from unittest.mock import Mock
import logging
import zoneinfo

import pytest
from monitor_daemon import daemon as mod


@pytest.mark.parametrize('utc_time', ['2026-03-09T20:30:00', '2026-11-02T15:30:00'])
def test_missing_timezone_refuses_instead_of_inventing_est(utc_time, monkeypatch, caplog):
    now = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(mod, 'datetime', Mock(now=Mock(return_value=now)))
    def missing(_name):
        raise zoneinfo.ZoneInfoNotFoundError('fixture missing timezone')
    monkeypatch.setattr(zoneinfo, 'ZoneInfo', missing)
    daemon = mod.MonitorDaemon()
    with caplog.at_level(logging.ERROR):
        assert daemon.is_market_hours() is False
    assert any('timezone' in row.message.lower() for row in caplog.records)
    gated = Mock(session_window='rth', requires_market_hours=True, post_close_grace_minutes=0)
    gated.is_due.return_value = True
    assert daemon._handler_can_run_now(gated) is False
    always = Mock(session_window='rth', requires_market_hours=False)
    always.is_due.return_value = True
    assert daemon._handler_can_run_now(always) is True


@pytest.mark.parametrize('utc_time,expected', [
    ('2026-03-09T13:30:00', True), ('2026-03-09T20:00:00', False),
    ('2026-11-02T14:30:00', True), ('2026-11-02T21:00:00', False),
])
def test_real_timezone_retains_dst_boundaries(utc_time, expected, monkeypatch):
    now = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(mod, 'datetime', Mock(now=lambda tz: now.astimezone(tz)))
    assert mod.MonitorDaemon().is_market_hours() is expected
