#!/usr/bin/env python3
"""
Tests for Fill Monitor handler.

RED/GREEN TDD
"""

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.fill_monitor import FillMonitorHandler


class TestFillMonitorInit:
    """Test fill monitor initialization."""
    
    def test_has_correct_name(self):
        """Handler has correct name."""
        handler = FillMonitorHandler()
        assert handler.name == "fill_monitor"
    
    def test_has_short_interval(self):
        """Handler runs every 60 seconds."""
        handler = FillMonitorHandler()
        assert handler.interval_seconds == 60
    
    def test_tracks_known_orders(self):
        """Handler tracks known order states."""
        handler = FillMonitorHandler()
        assert hasattr(handler, 'known_orders')
        assert isinstance(handler.known_orders, dict)


class TestFillMonitorExecute:
    """Test fill monitor execution."""
    
    def test_connects_to_ib(self):
        """Handler connects to IB."""
        with patch('ib_insync.IB') as mock_ib_class:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            mock_ib.openTrades.return_value = []
            
            handler = FillMonitorHandler()
            handler.execute()
            
            mock_ib.connect.assert_called_once()
    
    def test_fetches_open_orders(self):
        """Handler fetches open orders."""
        with patch('ib_insync.IB') as mock_ib_class:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            mock_ib.openTrades.return_value = []
            
            handler = FillMonitorHandler()
            handler.execute()
            
            mock_ib.reqAllOpenOrders.assert_called_once()
    
    def test_detects_new_order(self):
        """Handler detects new orders."""
        with patch('ib_insync.IB') as mock_ib_class:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            # Mock a trade
            mock_trade = MagicMock()
            mock_trade.order.orderId = 5
            mock_trade.order.action = "BUY"
            mock_trade.order.totalQuantity = 25
            mock_trade.order.lmtPrice = 1.00
            mock_trade.orderStatus.status = "Submitted"
            mock_trade.orderStatus.filled = 0
            mock_trade.orderStatus.remaining = 25
            mock_trade.contract.symbol = "AAOI"
            mock_trade.contract.localSymbol = "AAOI  260306P00090000"
            
            mock_ib.openTrades.return_value = [mock_trade]
            
            handler = FillMonitorHandler()
            result = handler.execute()
            
            assert "orders" in result
            assert len(result["orders"]) == 1
            assert result["new_orders"] == 1
    
    def test_detects_partial_fill(self):
        """Handler detects partial fills."""
        with patch('ib_insync.IB') as mock_ib_class:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            mock_trade = MagicMock()
            mock_trade.order.orderId = 5
            mock_trade.order.action = "BUY"
            mock_trade.order.totalQuantity = 25
            mock_trade.order.lmtPrice = 1.00
            mock_trade.orderStatus.status = "Submitted"
            mock_trade.orderStatus.filled = 10
            mock_trade.orderStatus.remaining = 15
            mock_trade.orderStatus.avgFillPrice = 0.98
            mock_trade.contract.symbol = "AAOI"
            mock_trade.contract.localSymbol = "AAOI  260306P00090000"
            
            mock_ib.openTrades.return_value = [mock_trade]
            
            handler = FillMonitorHandler()
            # Pretend we knew about this order with 0 filled
            handler.known_orders = {5: {"filled": 0}}
            
            result = handler.execute()
            
            assert result["partial_fills"] == 1
            assert result["fills"][0]["order_id"] == 5
            assert result["fills"][0]["newly_filled"] == 10
    
    def test_detects_complete_fill(self):
        """Handler detects complete fills (order no longer in open orders)."""
        with patch('ib_insync.IB') as mock_ib_class:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            # No open orders now
            mock_ib.openTrades.return_value = []
            
            # But we had an order before
            handler = FillMonitorHandler()
            handler.known_orders = {
                5: {
                    "symbol": "AAOI",
                    "contract": "AAOI  260306P00090000",
                    "action": "BUY",
                    "quantity": 25,
                    "filled": 20,
                    "limit": 1.00
                }
            }
            
            result = handler.execute()
            
            assert result["complete_fills"] == 1
            assert result["completed"][0]["order_id"] == 5
    
    def test_disconnects_after_execution(self):
        """Handler disconnects from IB after execution."""
        with patch('ib_insync.IB') as mock_ib_class:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            mock_ib.openTrades.return_value = []
            
            handler = FillMonitorHandler()
            handler.execute()
            
            mock_ib.disconnect.assert_called_once()


class TestFillMonitorNotifications:
    """Test notification logic."""
    
    def test_sends_notification_on_fill(self):
        """Handler sends macOS notification on fill."""
        with patch('ib_insync.IB') as mock_ib_class, \
             patch.object(FillMonitorHandler, '_send_notification') as mock_notify:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            mock_trade = MagicMock()
            mock_trade.order.orderId = 5
            mock_trade.order.action = "BUY"
            mock_trade.order.totalQuantity = 25
            mock_trade.order.lmtPrice = 1.00
            mock_trade.orderStatus.status = "Submitted"
            mock_trade.orderStatus.filled = 25
            mock_trade.orderStatus.remaining = 0
            mock_trade.orderStatus.avgFillPrice = 0.98
            mock_trade.contract.symbol = "AAOI"
            mock_trade.contract.localSymbol = "AAOI  260306P00090000"
            
            mock_ib.openTrades.return_value = [mock_trade]
            
            handler = FillMonitorHandler()
            handler.known_orders = {5: {"filled": 0}}
            handler.execute()
            
            # Should have called notification method
            mock_notify.assert_called()


class TestFillMonitorState:
    """Test state persistence for fill monitor."""
    
    def test_get_state_includes_known_orders(self):
        """get_state includes known_orders."""
        handler = FillMonitorHandler()
        handler.known_orders = {5: {"filled": 10, "symbol": "AAOI"}}
        
        state = handler.get_state()
        
        assert "known_orders" in state
        assert "5" in state["known_orders"] or 5 in state["known_orders"]
    
    def test_set_state_restores_known_orders(self):
        """set_state restores known_orders."""
        handler = FillMonitorHandler()
        
        handler.set_state({
            "last_run": "2026-03-04T10:00:00",
            "known_orders": {"5": {"filled": 10, "symbol": "AAOI"}}
        })
        
        assert 5 in handler.known_orders or "5" in handler.known_orders


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
