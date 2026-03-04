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
        results = daemon.run_once()
        
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
        results = daemon.run_once()
        
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
        results = daemon.run_once()
        assert results["failing"]["status"] == "error"


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
