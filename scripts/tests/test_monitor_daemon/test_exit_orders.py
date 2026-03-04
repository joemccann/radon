#!/usr/bin/env python3
"""
Tests for Exit Orders handler.

RED/GREEN TDD
"""

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler


class TestExitOrdersInit:
    """Test exit orders handler initialization."""
    
    def test_has_correct_name(self):
        """Handler has correct name."""
        handler = ExitOrdersHandler()
        assert handler.name == "exit_orders"
    
    def test_has_5_minute_interval(self):
        """Handler runs every 5 minutes (300 seconds)."""
        handler = ExitOrdersHandler()
        assert handler.interval_seconds == 300
    
    def test_has_max_gap_threshold(self):
        """Handler has 40% max gap threshold."""
        handler = ExitOrdersHandler()
        assert handler.max_gap_pct == 0.40


class TestExitOrdersLoadPending:
    """Test loading pending orders from trade log."""
    
    def test_loads_pending_orders_from_trade_log(self, tmp_path):
        """Handler loads PENDING orders from trade_log.json."""
        trade_log = tmp_path / "trade_log.json"
        trade_log.write_text(json.dumps({
            "trades": [{
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PENDING",
                        "order_id": None
                    }
                }
            }]
        }))
        
        handler = ExitOrdersHandler(trade_log_path=trade_log)
        pending = handler._load_pending_orders()
        
        assert len(pending) == 1
        assert pending[0]["ticker"] == "GOOG"
        assert pending[0]["target_price"] == 15.00
    
    def test_skips_already_placed_orders(self, tmp_path):
        """Handler skips orders that are already placed."""
        trade_log = tmp_path / "trade_log.json"
        trade_log.write_text(json.dumps({
            "trades": [{
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PLACED",
                        "order_id": 99
                    }
                }
            }]
        }))
        
        handler = ExitOrdersHandler(trade_log_path=trade_log)
        pending = handler._load_pending_orders()
        
        assert len(pending) == 0


class TestExitOrdersGapCheck:
    """Test IB gap validation logic."""
    
    def test_can_place_within_40_pct(self):
        """Can place order within 40% of current price."""
        handler = ExitOrdersHandler()
        
        # Current price $10, target $13 = 30% gap
        can_place = handler._can_place_order(current_price=10.00, target_price=13.00)
        
        assert can_place == True
    
    def test_cannot_place_beyond_40_pct(self):
        """Cannot place order beyond 40% of current price."""
        handler = ExitOrdersHandler()
        
        # Current price $6, target $15 = 150% gap
        can_place = handler._can_place_order(current_price=6.00, target_price=15.00)
        
        assert can_place == False
    
    def test_edge_case_exactly_40_pct(self):
        """Can place at exactly 40% gap."""
        handler = ExitOrdersHandler()
        
        # Current price $10, target $14 = 40% gap
        can_place = handler._can_place_order(current_price=10.00, target_price=14.00)
        
        assert can_place == True


class TestExitOrdersExecute:
    """Test execute method."""
    
    def test_places_order_when_gap_acceptable(self, tmp_path):
        """Places order when within 40% gap."""
        with patch('ib_insync.IB') as mock_ib_class, \
             patch('ib_insync.Option') as mock_option, \
             patch('ib_insync.LimitOrder') as mock_limit_order:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            # Mock contract qualification
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_ib.qualifyContracts.return_value = [mock_contract]
            
            # Mock market data showing current price near target
            mock_ticker = MagicMock()
            mock_ticker.bid = 11.90
            mock_ticker.ask = 12.10
            mock_ib.reqMktData.return_value = mock_ticker
            
            # Mock order placement
            mock_trade = MagicMock()
            mock_trade.order.orderId = 99
            mock_trade.orderStatus.status = "Submitted"
            mock_ib.placeOrder.return_value = mock_trade
            
            trade_log = tmp_path / "trade_log.json"
            trade_log.write_text(json.dumps({
                "trades": [{
                    "id": 8,
                    "ticker": "GOOG",
                    "contract": "GOOG  260417C00315000",
                    "structure": "Bull Call Spread",
                    "exit_orders": {
                        "target": {
                            "price": 15.00,
                            "status": "PENDING",
                            "order_id": None,
                            "contracts": 44,
                            "contract_spec": {
                                "symbol": "GOOG",
                                "expiry": "20260417",
                                "strike": 315,
                                "right": "C"
                            }
                        }
                    }
                }]
            }))
            
            handler = ExitOrdersHandler(trade_log_path=trade_log)
            result = handler.execute()
            
            assert result["orders_checked"] >= 1
    
    def test_skips_order_when_gap_too_large(self, tmp_path):
        """Skips order when gap exceeds 40%."""
        with patch('ib_insync.IB') as mock_ib_class, \
             patch('ib_insync.Option') as mock_option:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            # Mock contract qualification
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_ib.qualifyContracts.return_value = [mock_contract]
            
            # Mock market data showing current price far from target
            mock_ticker = MagicMock()
            mock_ticker.bid = 5.90
            mock_ticker.ask = 6.10
            mock_ib.reqMktData.return_value = mock_ticker
            
            trade_log = tmp_path / "trade_log.json"
            trade_log.write_text(json.dumps({
                "trades": [{
                    "id": 8,
                    "ticker": "GOOG",
                    "structure": "Bull Call Spread",
                    "exit_orders": {
                        "target": {
                            "price": 15.00,
                            "status": "PENDING",
                            "order_id": None,
                            "contracts": 44,
                            "contract_spec": {
                                "symbol": "GOOG",
                                "expiry": "20260417",
                                "strike": 315,
                                "right": "C"
                            }
                        }
                    }
                }]
            }))
            
            handler = ExitOrdersHandler(trade_log_path=trade_log)
            result = handler.execute()
            
            # Should not place order
            mock_ib.placeOrder.assert_not_called()
            assert result.get("orders_placed", 0) == 0


class TestExitOrdersTradeLogUpdate:
    """Test trade log updates after placing orders."""
    
    def test_updates_trade_log_on_placement(self, tmp_path):
        """Updates trade_log.json when order is placed."""
        with patch('ib_insync.IB') as mock_ib_class, \
             patch('ib_insync.Option') as mock_option, \
             patch('ib_insync.LimitOrder') as mock_limit_order:
            mock_ib = MagicMock()
            mock_ib_class.return_value = mock_ib
            
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_ib.qualifyContracts.return_value = [mock_contract]
            
            mock_ticker = MagicMock()
            mock_ticker.bid = 11.90
            mock_ticker.ask = 12.10
            mock_ib.reqMktData.return_value = mock_ticker
            
            mock_trade = MagicMock()
            mock_trade.order.orderId = 99
            mock_trade.orderStatus.status = "Submitted"
            mock_ib.placeOrder.return_value = mock_trade
            
            trade_log = tmp_path / "trade_log.json"
            trade_log.write_text(json.dumps({
                "trades": [{
                    "id": 8,
                    "ticker": "GOOG",
                    "structure": "Bull Call Spread",
                    "exit_orders": {
                        "target": {
                            "price": 15.00,
                            "status": "PENDING",
                            "order_id": None,
                            "contracts": 44,
                            "contract_spec": {
                                "symbol": "GOOG",
                                "expiry": "20260417",
                                "strike": 315,
                                "right": "C"
                            }
                        }
                    }
                }]
            }))
            
            handler = ExitOrdersHandler(trade_log_path=trade_log)
            handler.execute()
            
            # Reload and check - order should be placed so status updated
            updated = json.loads(trade_log.read_text())
            target_status = updated["trades"][0]["exit_orders"]["target"]["status"]
            # When order is within range and placed, status should be PLACED
            assert target_status == "PLACED"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
