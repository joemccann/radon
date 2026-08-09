#!/usr/bin/env python3
"""REL-002 — exit-order ack discipline (RELIABILITY_AUDIT.md R-003).

The handler used to ``place_order`` → ``sleep(1)`` → mark the journal
PLACED. IB silently discards orders still unacknowledged (permId==0,
PendingSubmit) when the placing client disconnects — the documented
2026-05-27 MU failure mode ``ib_place_order.py`` defends against. These
fault injections pin the required behavior:

  - never-acked placement (permId stays 0, PendingSubmit) → journal stays
    PENDING, the order is cancelled best-effort, cycle reports error
  - rejected placement → journal stays PENDING, cycle reports error
  - confirmed placement (status Submitted / nonzero permId) → PLACED as
    before, including a late ack that lands within the poll deadline
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
from test_exit_orders import FakeJournalDb


def _pending_goog_db():
    return FakeJournalDb([
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
                        "right": "C",
                    },
                }
            },
        }
    ])


def _wire_market(mock_client):
    contract = MagicMock()
    contract.localSymbol = "GOOG  260417C00315000"
    mock_client.qualify_contracts.return_value = [contract]
    ticker = MagicMock()
    ticker.bid = 14.90
    ticker.ask = 15.10
    ticker.halted = 0
    mock_client.get_quote.return_value = ticker
    return contract


def _trade(perm_id, status, order_id=99):
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.orderStatus.status = status
    return trade


def _journal_status(db):
    return db.trades["trade-8"]["exit_orders"]["target"]["status"]


class TestExitOrderAckDiscipline:
    def _run(self, db, trade, sleep_side_effect=None):
        with patch("monitor_daemon.handlers.exit_orders.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.exit_orders.Option"), \
             patch("monitor_daemon.handlers.exit_orders.LimitOrder"):
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            _wire_market(mock_client)
            mock_client.place_order.return_value = trade
            if sleep_side_effect is not None:
                mock_client.sleep.side_effect = sleep_side_effect
            handler = ExitOrdersHandler(db=db)
            result = handler.execute()
            return result, mock_client

    def test_never_acked_placement_keeps_journal_pending(self):
        """permId==0 + PendingSubmit past the deadline: journal must NOT
        read PLACED, the cycle must surface an error, and the unconfirmed
        order must be cancelled so it cannot go live after we move on."""
        db = _pending_goog_db()
        trade = _trade(perm_id=0, status="PendingSubmit")
        result, mock_client = self._run(db, trade)

        assert _journal_status(db) == "PENDING"
        assert result.get("orders_placed", 0) == 0
        assert "error" in result
        mock_client.cancel_order.assert_called_once()

    def test_rejected_placement_keeps_journal_pending(self):
        db = _pending_goog_db()
        trade = _trade(perm_id=0, status="Rejected")
        result, _ = self._run(db, trade)

        assert _journal_status(db) == "PENDING"
        assert result.get("orders_placed", 0) == 0
        assert "error" in result

    def test_confirmed_placement_marks_placed(self):
        db = _pending_goog_db()
        trade = _trade(perm_id=1234567, status="Submitted")
        result, mock_client = self._run(db, trade)

        assert _journal_status(db) == "PLACED"
        assert result["orders_placed"] == 1
        assert "error" not in result
        mock_client.cancel_order.assert_not_called()

    def test_late_ack_within_deadline_marks_placed(self):
        """permId arrives after a few poll steps — must still confirm."""
        db = _pending_goog_db()
        trade = _trade(perm_id=0, status="PendingSubmit")

        calls = {"n": 0}

        def late_ack(_seconds=1):
            calls["n"] += 1
            if calls["n"] >= 3:
                trade.order.permId = 424242
                trade.orderStatus.status = "Submitted"

        result, _ = self._run(db, trade, sleep_side_effect=late_ack)

        assert _journal_status(db) == "PLACED"
        assert result["orders_placed"] == 1
        assert "error" not in result

    def test_mock_junk_permid_does_not_confirm(self):
        """A non-int permId (mock junk / None) must not count as an ack —
        the T-023 lesson: identity-dependent int(MagicMock()) passes by
        luck. Confirmation requires a real nonzero int or an accepted
        status."""
        db = _pending_goog_db()
        trade = _trade(perm_id=MagicMock(), status="PendingSubmit")
        result, _ = self._run(db, trade)

        assert _journal_status(db) == "PENDING"
        assert result.get("orders_placed", 0) == 0
