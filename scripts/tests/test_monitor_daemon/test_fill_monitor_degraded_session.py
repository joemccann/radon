#!/usr/bin/env python3
"""REL-009 — fill_monitor session sanity (RELIABILITY_AUDIT.md R-016).

"Disappeared from open orders" used to mean "completely filled": a
degraded session returning an empty snapshot mass-completed every
tracked order (destroying fill baselines), and a cancelled order was
announced as "Order Complete: filled Nx" with a stale count.

Pinned here:
  1. Empty snapshot + empty managedAccounts = degraded session → skip
     the cycle with an error heartbeat; tracked orders retained.
  2. A vanished order IS completed when an execution exists for it.
  3. A vanished order with NO execution is reported cancelled/gone —
     never a complete-fill notification.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.fill_monitor import FillMonitorHandler


def _tracked(order_id, filled=2, quantity=5):
    return {
        "order_id": order_id,
        "symbol": "GOOG",
        "contract": "GOOG  260417C00315000",
        "action": "SELL",
        "quantity": quantity,
        "filled": filled,
        "remaining": quantity - filled,
        "status": "Submitted",
    }


def _fill_for(order_id):
    fill = MagicMock()
    fill.execution.orderId = order_id
    fill.execution.permId = 700000 + order_id
    return fill


def _run(known, open_trades, accounts, fills):
    with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        client.get_open_orders.return_value = open_trades
        client.get_managed_accounts.return_value = accounts
        client.get_fills.return_value = fills

        handler = FillMonitorHandler(send_notifications=False)
        handler.known_orders = dict(known)
        result = handler.execute()
        return result, handler


class TestDegradedSessionGuard:
    def test_empty_snapshot_no_accounts_skips_cycle(self):
        known = {1: _tracked(1), 2: _tracked(2)}
        result, handler = _run(known, open_trades=[], accounts=[], fills=[])

        assert result["complete_fills"] == 0
        assert result["completed"] == []
        assert "error" in result
        # Baselines must survive so partials during the blip are still
        # detected after recovery.
        assert set(handler.known_orders.keys()) == {1, 2}

    def test_vanished_with_execution_is_completed(self):
        known = {1: _tracked(1)}
        result, handler = _run(
            known, open_trades=[], accounts=["DU123"], fills=[_fill_for(1)]
        )
        assert result["complete_fills"] == 1
        assert result["completed"][0]["status"] == "COMPLETED"
        assert 1 not in handler.known_orders

    def test_vanished_without_execution_is_cancelled_not_filled(self):
        known = {1: _tracked(1)}
        result, handler = _run(known, open_trades=[], accounts=["DU123"], fills=[])

        assert result["complete_fills"] == 0
        assert result.get("cancelled_or_gone", 0) == 1
        assert result["completed"][0]["status"] == "CANCELLED_OR_GONE"
        assert 1 not in handler.known_orders

    def test_fills_lookup_failure_defers_completion(self):
        """If the execution cross-check itself fails, defer judgment —
        keep tracking rather than announcing fills we can't verify."""
        known = {1: _tracked(1)}
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            client.get_open_orders.return_value = []
            client.get_managed_accounts.return_value = ["DU123"]
            client.get_fills.side_effect = RuntimeError("reqExecutions timeout")

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = dict(known)
            result = handler.execute()

        assert result["complete_fills"] == 0
        assert 1 in handler.known_orders
