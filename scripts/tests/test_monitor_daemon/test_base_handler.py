#!/usr/bin/env python3
"""
Tests for BaseHandler functionality.

RED/GREEN TDD
"""

import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.base import BaseHandler


class ConcreteHandler(BaseHandler):
    """Concrete implementation for testing."""
    
    name = "concrete_test"
    interval_seconds = 60
    
    def __init__(self):
        super().__init__()
        self.run_count = 0
    
    def execute(self) -> dict:
        self.run_count += 1
        return {"executed": True, "count": self.run_count}


class TestBaseHandlerInit:
    """Test handler initialization."""
    
    def test_handler_has_name(self):
        """Handler must have a name."""
        handler = ConcreteHandler()
        assert handler.name == "concrete_test"
    
    def test_handler_has_interval(self):
        """Handler must have an interval."""
        handler = ConcreteHandler()
        assert handler.interval_seconds == 60
    
    def test_handler_starts_with_no_last_run(self):
        """Handler starts with no last_run timestamp."""
        handler = ConcreteHandler()
        assert handler.last_run is None


class TestBaseHandlerIsDue:
    """Test is_due() logic."""
    
    def test_is_due_true_when_never_run(self):
        """Handler is due if it has never run."""
        handler = ConcreteHandler()
        assert handler.is_due() == True
    
    def test_is_due_false_when_recently_run(self):
        """Handler is not due if recently run."""
        handler = ConcreteHandler()
        handler.last_run = datetime.now()
        assert handler.is_due() == False
    
    def test_is_due_true_when_interval_passed(self):
        """Handler is due when interval has passed."""
        handler = ConcreteHandler()
        handler.last_run = datetime.now() - timedelta(seconds=120)  # 2 mins ago
        assert handler.is_due() == True  # interval is 60s


class TestBaseHandlerRun:
    """Test run() method."""
    
    def test_run_calls_execute(self):
        """run() calls execute() and returns result."""
        handler = ConcreteHandler()
        result = handler.run()
        
        assert result["data"]["executed"] == True
        assert handler.run_count == 1
    
    def test_run_updates_last_run(self):
        """run() updates last_run timestamp."""
        handler = ConcreteHandler()
        assert handler.last_run is None
        
        handler.run()
        
        assert handler.last_run is not None
        assert (datetime.now() - handler.last_run).seconds < 1
    
    def test_run_wraps_result_with_metadata(self):
        """run() wraps execute result with status and timing."""
        handler = ConcreteHandler()
        result = handler.run()
        
        assert "status" in result
        assert result["status"] == "ok"
        assert "timestamp" in result
        assert "data" in result
        assert result["data"]["executed"] == True


class TestBaseHandlerState:
    """Test state get/set."""
    
    def test_get_state_returns_last_run(self):
        """get_state() returns serializable state."""
        handler = ConcreteHandler()
        handler.last_run = datetime(2026, 3, 4, 10, 0, 0)
        
        state = handler.get_state()
        
        assert "last_run" in state
        assert state["last_run"] == "2026-03-04T10:00:00"
    
    def test_set_state_restores_last_run(self):
        """set_state() restores handler state."""
        handler = ConcreteHandler()
        
        handler.set_state({"last_run": "2026-03-04T10:00:00"})
        
        assert handler.last_run == datetime(2026, 3, 4, 10, 0, 0)
    
    def test_set_state_handles_none_last_run(self):
        """set_state() handles None last_run."""
        handler = ConcreteHandler()
        handler.last_run = datetime.now()
        
        handler.set_state({"last_run": None})
        
        assert handler.last_run is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
