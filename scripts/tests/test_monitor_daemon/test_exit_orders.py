#!/usr/bin/env python3
"""
Tests for Exit Orders handler.

RED/GREEN TDD
"""

import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler


def make_mock_client():
    """Create a mock IBClient for exit orders tests."""
    mock_client = MagicMock()
    return mock_client


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeJournalDb:
    def __init__(self, trades):
        self.trades = {f"trade-{trade['id']}": trade for trade in trades}
        self.commits = 0

    def execute(self, sql, args=()):
        if sql.strip().upper().startswith("SELECT"):
            if "WHERE trade_id = ?" in sql:
                trade = self.trades.get(args[0])
                rows = [(args[0], json.dumps(trade))] if trade else []
                return _Cursor(rows)
            return _Cursor([
                (trade_id, json.dumps(trade))
                for trade_id, trade in self.trades.items()
            ])
        if sql.strip().upper().startswith("UPDATE"):
            payload, _written_at, trade_id = args
            self.trades[trade_id] = json.loads(payload)
            return _Cursor([])
        raise AssertionError(f"unexpected SQL: {sql}")

    def commit(self):
        self.commits += 1


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

    def test_loads_pending_orders_from_journal(self):
        """Handler loads PENDING orders from the Turso journal."""
        db = FakeJournalDb([
            {
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PENDING",
                        "order_id": None
                    }
                },
            }
        ])

        handler = ExitOrdersHandler(db=db)
        pending = handler._load_pending_orders()

        assert len(pending) == 1
        assert pending[0]["ticker"] == "GOOG"
        assert pending[0]["target_price"] == 15.00
        assert pending[0]["journal_trade_id"] == "trade-8"

    def test_skips_already_placed_orders(self):
        """Handler skips orders that are already placed."""
        db = FakeJournalDb([
            {
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {
                        "price": 15.00,
                        "status": "PLACED",
                        "order_id": 99
                    }
                },
            }
        ])

        handler = ExitOrdersHandler(db=db)
        pending = handler._load_pending_orders()

        assert len(pending) == 0


class TestExitOrdersGapCheck:
    """Test IB gap validation logic."""

    def test_can_place_within_40_pct(self):
        """Can place order within 40% of current price."""
        handler = ExitOrdersHandler()

        # Current price $10, target $13 = 30% gap
        can_place = handler._can_place_order(current_price=10.00, target_price=13.00)

        assert can_place is True

    def test_cannot_place_beyond_40_pct(self):
        """Cannot place order beyond 40% of current price."""
        handler = ExitOrdersHandler()

        # Current price $6, target $15 = 150% gap
        can_place = handler._can_place_order(current_price=6.00, target_price=15.00)

        assert can_place is False

    def test_edge_case_exactly_40_pct(self):
        """Can place at exactly 40% gap."""
        handler = ExitOrdersHandler()

        # Current price $10, target $14 = 40% gap
        can_place = handler._can_place_order(current_price=10.00, target_price=14.00)

        assert can_place is True


class TestExitOrdersExecute:
    """Test execute method."""

    def test_places_order_when_gap_acceptable(self):
        """Places order when within 40% gap."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option') as mock_option, \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder') as mock_limit_order:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            # Mock contract qualification
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_client.qualify_contracts.return_value = [mock_contract]

            # Mock market data showing current price near target
            mock_ticker = MagicMock()
            mock_ticker.bid = 11.90
            mock_ticker.ask = 12.10
            mock_client.get_quote.return_value = mock_ticker

            # Mock order placement
            mock_trade = MagicMock()
            mock_trade.order.orderId = 99
            mock_trade.orderStatus.status = "Submitted"
            mock_client.place_order.return_value = mock_trade

            db = FakeJournalDb([
                {
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
                    },
                }
            ])

            handler = ExitOrdersHandler(db=db)
            result = handler.execute()

            assert result["orders_checked"] >= 1

    def test_skips_order_when_gap_too_large(self):
        """Skips order when gap exceeds 40%."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option') as mock_option:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            # Mock contract qualification
            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_client.qualify_contracts.return_value = [mock_contract]

            # Mock market data showing current price far from target
            mock_ticker = MagicMock()
            mock_ticker.bid = 5.90
            mock_ticker.ask = 6.10
            mock_client.get_quote.return_value = mock_ticker

            db = FakeJournalDb([
                {
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
                    },
                }
            ])

            handler = ExitOrdersHandler(db=db)
            result = handler.execute()

            # Should not place order
            mock_client.place_order.assert_not_called()
            assert result.get("orders_placed", 0) == 0


class TestExitOrdersTradeLogUpdate:
    """Test trade log updates after placing orders."""

    def test_updates_journal_on_placement(self):
        """Updates the Turso journal when order is placed."""
        with patch('monitor_daemon.handlers.exit_orders.IBClient') as mock_cls, \
             patch('monitor_daemon.handlers.exit_orders.Option') as mock_option, \
             patch('monitor_daemon.handlers.exit_orders.LimitOrder') as mock_limit_order:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            mock_contract = MagicMock()
            mock_contract.localSymbol = "GOOG  260417C00315000"
            mock_client.qualify_contracts.return_value = [mock_contract]

            mock_ticker = MagicMock()
            mock_ticker.bid = 11.90
            mock_ticker.ask = 12.10
            mock_client.get_quote.return_value = mock_ticker

            mock_trade = MagicMock()
            mock_trade.order.orderId = 99
            mock_trade.orderStatus.status = "Submitted"
            mock_client.place_order.return_value = mock_trade

            db = FakeJournalDb([
                {
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
                    },
                }
            ])

            handler = ExitOrdersHandler(db=db)
            handler.execute()

            updated = db.trades["trade-8"]
            target_status = updated["exit_orders"]["target"]["status"]
            # When order is within range and placed, status should be PLACED
            assert target_status == "PLACED"
            assert updated["exit_orders"]["target"]["order_id"] == 99
            assert db.commits == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
