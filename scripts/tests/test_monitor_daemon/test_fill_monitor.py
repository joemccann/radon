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


@pytest.fixture(autouse=True)
def _disable_orders_mirror(monkeypatch):
    """Persist-to-open_orders is covered in TestFillMonitorOrdersSnapshotMirror.
    Default it off so execute() never hits ib_orders.save_orders in unit tests.
    """
    monkeypatch.setattr(
        FillMonitorHandler,
        "_mirror_ib_orders_snapshot",
        lambda self, client: None,
    )


def make_mock_client(trades=None):
    """Create a mock IBClient for fill monitor tests."""
    mock_client = MagicMock()
    mock_client.get_open_orders.return_value = trades or []
    return mock_client


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
        """Handler connects to IB via IBClient."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler()
            handler.execute()

            mock_client.connect.assert_called_once()

    def test_fetches_open_orders(self):
        """Handler fetches open orders via IBClient.get_open_orders."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler()
            handler.execute()

            mock_client.get_open_orders.assert_called_once()

    def test_detects_new_order(self):
        """Handler detects new orders."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls:
            # Mock a trade
            mock_trade = MagicMock()
            mock_trade.order.orderId = 5
            mock_trade.order.action = "BUY"
            mock_trade.order.totalQuantity = 25
            mock_trade.order.lmtPrice = 1.00
            mock_trade.orderStatus.status = "Submitted"
            mock_trade.orderStatus.filled = 0
            mock_trade.orderStatus.remaining = 25
            mock_trade.orderStatus.avgFillPrice = None
            mock_trade.contract.symbol = "AAOI"
            mock_trade.contract.localSymbol = "AAOI  260306P00090000"

            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler()
            result = handler.execute()

            assert "orders" in result
            assert len(result["orders"]) == 1
            assert result["new_orders"] == 1

    def test_detects_partial_fill(self):
        """Handler detects partial fills."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls:
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

            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler()
            # Pretend we knew about this order with 0 filled
            handler.known_orders = {5: {"filled": 0}}

            result = handler.execute()

            assert result["partial_fills"] == 1
            assert result["fills"][0]["order_id"] == 5
            assert result["fills"][0]["newly_filled"] == 10

    def test_detects_complete_fill(self):
        """Handler detects complete fills (order vanished AND an execution
        exists for it — REL-009: vanishing alone is not proof of a fill)."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls:
            # No open orders now
            mock_client = make_mock_client(trades=[])
            mock_cls.return_value = mock_client
            mock_client.get_managed_accounts.return_value = ["DU123"]
            fill = MagicMock()
            fill.execution.orderId = 5
            mock_client.get_fills.return_value = [fill]

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


class TestFillMonitorOrdersSnapshotMirror:
    """Fills must also replace Turso open_orders/executed_orders.

    fill_monitor already journals the fill, but /orders reads those two
    tables. After RTH the autonomous ib_orders --sync loop is dark, so an
    EXT fill (AVGO SELL 1000 @ 355 at 16:24 ET 2026-09-02) stayed WORKING
    in the UI until a manual SYNC NOW.
    """

    def test_complete_fill_mirrors_orders_snapshot(self):
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
            mock_client = make_mock_client(trades=[])
            mock_cls.return_value = mock_client
            mock_client.get_managed_accounts.return_value = ["DU123"]
            fill = MagicMock()
            fill.execution.orderId = 5
            mock_client.get_fills.return_value = [fill]

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {
                5: {
                    "symbol": "AVGO",
                    "contract": "AVGO",
                    "action": "SELL",
                    "quantity": 1000,
                    "filled": 0,
                    "limit": 355.0,
                }
            }
            with patch.object(
                FillMonitorHandler, "_mirror_ib_orders_snapshot"
            ) as mock_mirror:
                result = handler.execute()

            assert result["complete_fills"] == 1
            mock_mirror.assert_called_once_with(mock_client)

    def test_partial_fill_mirrors_orders_snapshot(self):
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
            mock_trade = MagicMock()
            mock_trade.order.orderId = 5
            mock_trade.order.action = "SELL"
            mock_trade.order.totalQuantity = 1000
            mock_trade.order.lmtPrice = 355.0
            mock_trade.orderStatus.status = "Submitted"
            mock_trade.orderStatus.filled = 400
            mock_trade.orderStatus.remaining = 600
            mock_trade.orderStatus.avgFillPrice = 355.0
            mock_trade.contract.symbol = "AVGO"
            mock_trade.contract.localSymbol = "AVGO"
            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {5: {"filled": 0}}
            with patch.object(
                FillMonitorHandler, "_mirror_ib_orders_snapshot"
            ) as mock_mirror:
                result = handler.execute()

            assert result["partial_fills"] == 1
            mock_mirror.assert_called_once_with(mock_client)

    def test_no_fill_does_not_mirror(self):
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
            mock_client = make_mock_client(trades=[])
            mock_cls.return_value = mock_client
            handler = FillMonitorHandler(send_notifications=False)
            with patch.object(
                FillMonitorHandler, "_mirror_ib_orders_snapshot"
            ) as mock_mirror:
                handler.execute()
            mock_mirror.assert_not_called()

    def test_disconnects_after_execution(self):
        """Handler disconnects from IB after execution."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls:
            mock_client = make_mock_client()
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler()
            handler.execute()

            mock_client.disconnect.assert_called_once()


class TestFillMonitorNotifications:
    """Test notification logic."""

    def test_sends_notification_on_fill(self):
        """Handler sends macOS notification on fill."""
        with patch('monitor_daemon.handlers.fill_monitor.IBClient') as mock_cls, \
             patch.object(FillMonitorHandler, '_send_notification') as mock_notify:
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

            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler()
            handler.known_orders = {5: {"filled": 0}}
            handler.execute()

            # Should have called notification method
            mock_notify.assert_called()


class TestFillMonitorJournalPersistence:
    """Detected fills must be mirrored to the Turso journal table inline.

    Today fills land only in self.known_orders (in-memory). A process
    restart between detection and the next journal_sync cycle drops the
    fill from the in-process cache; only Flex rehydrate recovers it.
    Mirror inline via db.writer.upsert_journal_entry.
    """

    def _make_partial_fill_trade(self):
        mock_trade = MagicMock()
        mock_trade.order.orderId = 5
        # Realistic identity fields: permId 0 = not yet assigned by IB (the
        # key then falls back to orderId); bare MagicMocks coerce via
        # __int__ to 1 and shadow the orderId in the T-013 trade_id.
        mock_trade.order.permId = 0
        mock_trade.order.action = "BUY"
        mock_trade.order.totalQuantity = 25
        mock_trade.order.lmtPrice = 1.00
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.filled = 10
        mock_trade.orderStatus.remaining = 15
        mock_trade.orderStatus.avgFillPrice = 0.98
        mock_trade.contract.symbol = "AAOI"
        mock_trade.contract.localSymbol = "AAOI  260306P00090000"
        mock_trade.contract.conId = 265598
        return mock_trade

    def test_partial_fill_writes_to_journal(self):
        """A newly detected partial fill triggers upsert_journal_entry."""
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.fill_monitor.upsert_journal_entry") as mock_upsert:

            mock_trade = self._make_partial_fill_trade()
            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {5: {"filled": 0}}

            result = handler.execute()

            assert result["partial_fills"] == 1
            mock_upsert.assert_called()
            # First positional arg = trade_id; payload follows.
            call_args = mock_upsert.call_args
            trade_id = call_args.args[0] if call_args.args else call_args.kwargs.get("trade_id")
            assert trade_id  # non-empty
            assert "5" in str(trade_id)  # contains order id

    def test_already_known_fill_does_not_rewrite(self):
        """Known fill (same total_filled) does NOT call upsert again."""
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.fill_monitor.upsert_journal_entry") as mock_upsert:

            mock_trade = self._make_partial_fill_trade()
            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            # Pretend we already saw the fill at total_filled=10
            handler.known_orders = {5: {"filled": 10}}

            result = handler.execute()

            assert result["partial_fills"] == 0
            mock_upsert.assert_not_called()

    def test_db_write_failure_does_not_crash_handler(self):
        """A DB upsert exception is logged but never propagates."""
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
             patch(
                 "monitor_daemon.handlers.fill_monitor.upsert_journal_entry",
                 side_effect=RuntimeError("turso write down"),
             ):

            mock_trade = self._make_partial_fill_trade()
            mock_client = make_mock_client(trades=[mock_trade])
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {5: {"filled": 0}}

            # Should NOT raise even though the DB write fails.
            result = handler.execute()

            assert result["partial_fills"] == 1


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


class TestFillMonitorTradeIdUniqueness:
    """T-013: the synthetic journal trade_id collided across DIFFERENT
    orders (IB orderIds restart per session/clientId) — and JOURNAL_UPSERT_SQL
    is ON CONFLICT(trade_id) DO UPDATE, so the second fill destructively
    overwrote the first order's journal row."""

    @staticmethod
    def _trade(symbol, con_id, perm_id, strike=90.0):
        t = MagicMock()
        t.order.orderId = 5          # same orderId on purpose — new session
        t.order.permId = perm_id
        t.order.action = "BUY"
        t.order.totalQuantity = 25
        t.order.lmtPrice = 1.00
        t.orderStatus.status = "Submitted"
        t.orderStatus.filled = 10
        t.orderStatus.remaining = 15
        t.orderStatus.avgFillPrice = 0.98
        t.contract.symbol = symbol
        t.contract.localSymbol = f"{symbol}  260306P000{int(strike)}000"
        t.contract.conId = con_id
        t.contract.secType = "OPT"
        t.contract.strike = strike
        t.contract.right = "P"
        t.contract.lastTradeDateOrContractMonth = "20260306"
        return t

    def _run_cycle(self, trade, captured_trade_ids):
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.fill_monitor.upsert_journal_entry") as mock_upsert:
            mock_cls.return_value = make_mock_client(trades=[trade])
            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {5: {"filled": 0}}
            handler.execute()
            for call in mock_upsert.call_args_list:
                tid = call.args[0] if call.args else call.kwargs.get("trade_id")
                captured_trade_ids.append(str(tid))

    def test_same_order_id_on_different_contracts_gets_distinct_trade_ids(self):
        trade_ids: list = []
        self._run_cycle(self._trade("AAOI", con_id=111, perm_id=9001), trade_ids)
        self._run_cycle(self._trade("MU", con_id=222, perm_id=9002, strike=105.0), trade_ids)

        assert len(trade_ids) == 2
        assert trade_ids[0] != trade_ids[1], (
            "two different orders sharing orderId 5 produced the SAME journal "
            f"trade_id ({trade_ids[0]}) — the second upsert overwrites the first row"
        )

    def test_same_fill_state_re_detection_stays_idempotent(self):
        trade_ids: list = []
        self._run_cycle(self._trade("AAOI", con_id=111, perm_id=9001), trade_ids)
        self._run_cycle(self._trade("AAOI", con_id=111, perm_id=9001), trade_ids)

        assert len(trade_ids) == 2
        assert trade_ids[0] == trade_ids[1], (
            "re-detecting the SAME fill state must upsert the same row, not append"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


_REAL_MIRROR = FillMonitorHandler._mirror_ib_orders_snapshot


class TestMirrorGuardsAgainstDegradedSnapshot:
    """REL-212 (R-579): an empty mirror read while working orders are tracked
    must NOT whole-replace Turso open_orders."""

    @pytest.fixture(autouse=True)
    def _restore_real_mirror(self, monkeypatch):
        # The file-level autouse fixture stubs the mirror to a no-op; this
        # class tests the mirror itself.
        monkeypatch.setattr(
            FillMonitorHandler, "_mirror_ib_orders_snapshot", _REAL_MIRROR
        )

    def _handler_with_tracked_order(self):
        handler = FillMonitorHandler(send_notifications=False)
        handler.known_orders = {
            7: {"symbol": "NVDA", "action": "BUY", "quantity": 10, "filled": 0},
        }
        return handler

    def test_empty_open_orders_with_tracked_orders_skips_the_replace(self):
        import monitor_daemon.handlers.fill_monitor as fm

        saved = []
        with patch.object(fm, "fetch_open_orders_for_mirror", lambda c: []), \
             patch.object(fm, "fetch_executed_orders_for_mirror", lambda c: []), \
             patch.object(fm, "build_orders_data_for_mirror", lambda o, e: {"open_orders": o}), \
             patch.object(fm, "save_orders_snapshot", lambda data: saved.append(data)):
            self._handler_with_tracked_order()._mirror_ib_orders_snapshot(MagicMock())
        assert saved == [], (
            "an empty open-orders read whole-replaced Turso while a working "
            "order was still tracked (degraded-snapshot race, R-579)"
        )

    def test_empty_open_orders_with_nothing_tracked_still_mirrors(self):
        import monitor_daemon.handlers.fill_monitor as fm

        saved = []
        handler = FillMonitorHandler(send_notifications=False)
        handler.known_orders = {}
        with patch.object(fm, "fetch_open_orders_for_mirror", lambda c: []), \
             patch.object(fm, "fetch_executed_orders_for_mirror", lambda c: []), \
             patch.object(fm, "build_orders_data_for_mirror", lambda o, e: {"open_orders": o}), \
             patch.object(fm, "save_orders_snapshot", lambda data: saved.append(data)):
            handler._mirror_ib_orders_snapshot(MagicMock())
        assert len(saved) == 1

    def test_populated_open_orders_still_mirror(self):
        import monitor_daemon.handlers.fill_monitor as fm

        saved = []
        with patch.object(fm, "fetch_open_orders_for_mirror", lambda c: [{"orderId": 7}]), \
             patch.object(fm, "fetch_executed_orders_for_mirror", lambda c: []), \
             patch.object(fm, "build_orders_data_for_mirror", lambda o, e: {"open_orders": o}), \
             patch.object(fm, "save_orders_snapshot", lambda data: saved.append(data)):
            self._handler_with_tracked_order()._mirror_ib_orders_snapshot(MagicMock())
        assert len(saved) == 1
