#!/usr/bin/env python3
"""
Tests for Monitor Daemon core functionality.

RED/GREEN TDD - These tests define the expected behavior.
"""

import pytest
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, time

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler


class TestMonitorDaemonInit:
    """Test daemon initialization."""
    
    def test_daemon_creates_with_empty_handlers(self):
        """Daemon starts with no handlers."""
        daemon = MonitorDaemon()
        assert daemon.handlers == []
    
    def test_daemon_register_handler(self):
        """Can register a handler."""
        daemon = MonitorDaemon()
        handler = Mock(spec=BaseHandler)
        handler.name = "test_handler"
        handler.interval_seconds = 60
        
        daemon.register(handler)
        
        assert len(daemon.handlers) == 1
        assert daemon.handlers[0] == handler
    
    def test_daemon_register_multiple_handlers(self):
        """Can register multiple handlers."""
        daemon = MonitorDaemon()
        h1 = Mock(spec=BaseHandler, name="handler1", interval_seconds=60)
        h1.name = "handler1"
        h2 = Mock(spec=BaseHandler, name="handler2", interval_seconds=120)
        h2.name = "handler2"
        
        daemon.register(h1)
        daemon.register(h2)
        
        assert len(daemon.handlers) == 2


class TestMonitorDaemonMarketHours:
    """Test market hours logic."""
    
    def test_is_market_hours_true_during_trading(self):
        """Returns True during market hours (9:30 AM - 4:00 PM ET, Mon-Fri)."""
        daemon = MonitorDaemon()
        
        # Wednesday at 10:00 AM ET
        with patch('monitor_daemon.daemon.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 4, 10, 0, 0)  # 10 AM
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            # Mock as Wednesday
            with patch('monitor_daemon.daemon.datetime') as mock_dt2:
                mock_now = MagicMock()
                mock_now.weekday.return_value = 2  # Wednesday
                mock_now.hour = 10
                mock_now.minute = 0
                mock_dt2.now.return_value = mock_now
                
                # For simplicity, test the time check method directly
                assert daemon._is_market_hours_time(10, 30, 2) == True
    
    def test_is_market_hours_false_before_open(self):
        """Returns False before market open."""
        daemon = MonitorDaemon()
        assert daemon._is_market_hours_time(9, 0, 2) == False  # 9:00 AM Wed
    
    def test_is_market_hours_false_after_close(self):
        """Returns False after market close."""
        daemon = MonitorDaemon()
        assert daemon._is_market_hours_time(16, 30, 2) == False  # 4:30 PM Wed
    
    def test_is_market_hours_false_on_weekend(self):
        """Returns False on weekends."""
        daemon = MonitorDaemon()
        assert daemon._is_market_hours_time(12, 0, 5) == False  # Noon Saturday
        assert daemon._is_market_hours_time(12, 0, 6) == False  # Noon Sunday


class TestMonitorDaemonDstHandling:
    """Regression for the EDT bug: the daemon previously hardcoded UTC-5
    (EST), which silently shifted the market window one hour each summer.
    Now uses zoneinfo so EST/EDT switch automatically.
    """

    def test_edt_open_uses_correct_offset(self):
        """At 13:30 UTC during EDT (UTC-4), ET = 09:30 = market open."""
        from zoneinfo import ZoneInfo

        daemon = MonitorDaemon()
        # 2026-05-14 13:30 UTC is a Thursday in EDT → 09:30 ET.
        edt_open = datetime(2026, 5, 14, 13, 30, tzinfo=ZoneInfo("UTC"))

        with patch("monitor_daemon.daemon.datetime") as mock_dt:
            mock_dt.now.return_value = edt_open.astimezone(ZoneInfo("America/New_York"))
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert daemon.is_market_hours() is True

    def test_edt_first_hour_no_longer_skipped(self):
        """At 14:30 UTC during EDT, ET = 10:30 = mid-session. The old
        hardcoded UTC-5 made this look like 09:30 ET (technically still
        open) but at 13:30 UTC it would have looked like 08:30 = closed.
        Verify both anchor points now agree the market is open.
        """
        from zoneinfo import ZoneInfo

        daemon = MonitorDaemon()
        for utc_h in (13, 14, 15, 16, 17, 18, 19):  # 09:00-15:00 EDT
            with patch("monitor_daemon.daemon.datetime") as mock_dt:
                anchor = datetime(2026, 5, 14, utc_h, 30, tzinfo=ZoneInfo("UTC"))
                mock_dt.now.return_value = anchor.astimezone(ZoneInfo("America/New_York"))
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                # 09:30-15:30 EDT is within 09:30-16:00 ET window.
                assert daemon.is_market_hours() is True, f"open expected at {utc_h}:30 UTC (EDT)"

    def test_est_window_unchanged(self):
        """During EST (UTC-5), 14:30 UTC = 09:30 ET = market open.
        Confirms the fix didn't regress winter behavior.
        """
        from zoneinfo import ZoneInfo

        daemon = MonitorDaemon()
        # 2026-01-14 14:30 UTC is a Wednesday in EST → 09:30 ET.
        est_open = datetime(2026, 1, 14, 14, 30, tzinfo=ZoneInfo("UTC"))

        with patch("monitor_daemon.daemon.datetime") as mock_dt:
            mock_dt.now.return_value = est_open.astimezone(ZoneInfo("America/New_York"))
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert daemon.is_market_hours() is True

    def test_post_close_returns_false(self):
        """16:30 ET = market closed in either EST or EDT."""
        from zoneinfo import ZoneInfo

        daemon = MonitorDaemon()
        for ymd, expected_offset in [((2026, 5, 14), -4), ((2026, 1, 14), -5)]:
            close = datetime(*ymd, 16, 30, tzinfo=ZoneInfo("America/New_York"))
            with patch("monitor_daemon.daemon.datetime") as mock_dt:
                mock_dt.now.return_value = close
                mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
                assert daemon.is_market_hours() is False


class TestMonitorDaemonRun:
    """Test daemon execution."""
    
    def test_run_once_calls_due_handlers(self):
        """run_once() calls handlers that are due."""
        daemon = MonitorDaemon()
        handler = Mock(spec=BaseHandler)
        handler.name = "test"
        handler.interval_seconds = 60
        handler.is_due.return_value = True
        handler.run.return_value = {"status": "ok"}
        
        daemon.register(handler)
        results = daemon.run_once(market_hours=True)
        
        handler.run.assert_called_once()
        assert "test" in results
    
    def test_run_once_skips_not_due_handlers(self):
        """run_once() skips handlers that aren't due."""
        daemon = MonitorDaemon()
        handler = Mock(spec=BaseHandler)
        handler.name = "test"
        handler.interval_seconds = 300
        handler.is_due.return_value = False
        
        daemon.register(handler)
        results = daemon.run_once(market_hours=True)
        
        handler.run.assert_not_called()
        assert "test" not in results
    
    def test_run_once_handles_handler_exception(self):
        """run_once() catches and logs handler exceptions via handler.run()."""
        daemon = MonitorDaemon()
        
        # Create a handler that returns error status (as BaseHandler.run does on exception)
        handler = Mock(spec=BaseHandler)
        handler.name = "failing"
        handler.interval_seconds = 60
        handler.is_due.return_value = True
        handler.run.return_value = {"status": "error", "error": "Handler failed"}
        
        daemon.register(handler)
        
        # Should not raise
        results = daemon.run_once(market_hours=True)
        assert results["failing"]["status"] == "error"

    def test_run_once_skips_market_hours_handlers_outside_market_hours(self):
        """run_once() skips due market-hours handlers outside market hours."""
        daemon = MonitorDaemon(respect_market_hours=True)
        handler = Mock(spec=BaseHandler)
        handler.name = "market_only"
        handler.interval_seconds = 60
        handler.requires_market_hours = True
        handler.is_due.return_value = True

        daemon.register(handler)
        results = daemon.run_once(market_hours=False)

        handler.run.assert_not_called()
        assert "market_only" not in results

    def test_run_once_allows_off_hours_handlers_outside_market_hours(self):
        """run_once() still runs due off-hours handlers outside market hours."""
        daemon = MonitorDaemon(respect_market_hours=True)
        handler = Mock(spec=BaseHandler)
        handler.name = "off_hours"
        handler.interval_seconds = 86400
        handler.requires_market_hours = False
        handler.is_due.return_value = True
        handler.run.return_value = {"status": "ok"}

        daemon.register(handler)
        results = daemon.run_once(market_hours=False)

        handler.run.assert_called_once()
        assert "off_hours" in results


class TestMonitorDaemonState:
    """Test daemon state persistence."""
    
    def test_saves_state_to_file(self, tmp_path):
        """Daemon saves handler state to JSON file."""
        state_file = tmp_path / "daemon_state.json"
        daemon = MonitorDaemon(state_file=state_file)
        
        handler = Mock(spec=BaseHandler)
        handler.name = "test"
        handler.interval_seconds = 60
        handler.get_state.return_value = {"last_run": "2026-03-04T10:00:00"}
        
        daemon.register(handler)
        daemon.save_state()
        
        assert state_file.exists()
        state = json.loads(state_file.read_text())
        assert "test" in state["handlers"]
    
    def test_loads_state_from_file(self, tmp_path):
        """Daemon loads handler state from JSON file."""
        state_file = tmp_path / "daemon_state.json"
        state_file.write_text(json.dumps({
            "handlers": {
                "test": {"last_run": "2026-03-04T10:00:00"}
            }
        }))
        
        daemon = MonitorDaemon(state_file=state_file)
        handler = Mock(spec=BaseHandler)
        handler.name = "test"
        handler.interval_seconds = 60
        
        daemon.register(handler)
        daemon.load_state()
        
        handler.set_state.assert_called_with({"last_run": "2026-03-04T10:00:00"})

    def test_failed_atomic_replace_preserves_previous_state(self, tmp_path, monkeypatch):
        from utils import atomic_io

        state_file = tmp_path / "daemon_state.json"
        original = '{"sentinel": true}'
        state_file.write_text(original)
        daemon = MonitorDaemon(state_file=state_file)
        handler = Mock(spec=BaseHandler)
        handler.name = "test"
        handler.get_state.return_value = {"last_run": "2026-03-04T10:00:00"}
        daemon.register(handler)
        monkeypatch.setattr(
            atomic_io.os,
            "replace",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("interrupted")),
        )

        with pytest.raises(OSError, match="interrupted"):
            daemon.save_state()

        assert state_file.read_text() == original


def test_create_daemon_uses_auto_client_ids(tmp_path, monkeypatch):
    from monitor_daemon import run

    monkeypatch.setattr(run, "STATE_FILE", tmp_path / "missing-state.json")
    daemon = run.create_daemon()
    handlers = {handler.name: handler for handler in daemon.handlers}

    assert handlers["fill_monitor"].client_id == "auto"
    assert handlers["exit_orders"].client_id == "auto"
    assert handlers["journal_sync"].client_id == "auto"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
