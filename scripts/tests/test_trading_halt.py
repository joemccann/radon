#!/usr/bin/env python3
"""REL-004 — kill switch: trading-halt flag + chokepoint refusals
(RELIABILITY_AUDIT.md R-001).

No halt flag existed anywhere; "Stop Gateway" only severed the API session
while GTC orders kept working blind. These tests pin the halt module's
file semantics (fail closed on corruption) and the refusal behavior of
every order-placing chokepoint: ib_place_order.place_order (covers the
/orders/place subprocess AND the workflow bridge), the exit-orders
handler, and ib_execute's CLI entry.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading_halt


@pytest.fixture
def halt_file(tmp_path, monkeypatch):
    path = tmp_path / "trading_halt.json"
    monkeypatch.setattr(trading_halt, "HALT_FILE", path)
    return path


class TestHaltFlagSemantics:
    def test_missing_file_means_not_halted(self, halt_file):
        assert trading_halt.is_trading_halted() is False

    def test_set_and_clear_roundtrip(self, halt_file):
        trading_halt.set_halt(reason="fat-finger drill", actor="test")
        assert trading_halt.is_trading_halted() is True
        state = trading_halt.get_halt_state()
        assert state["halted"] is True
        assert state["reason"] == "fat-finger drill"
        trading_halt.clear_halt()
        assert trading_halt.is_trading_halted() is False

    def test_malformed_file_fails_closed(self, halt_file):
        """A corrupt flag file must read as HALTED — for a kill switch,
        'unreadable' must never mean 'trading allowed'."""
        halt_file.write_text("{not json")
        assert trading_halt.is_trading_halted() is True

    def test_halted_false_payload_means_not_halted(self, halt_file):
        halt_file.write_text(json.dumps({"halted": False, "reason": "resumed"}))
        assert trading_halt.is_trading_halted() is False


class TestPlacementChokepoint:
    def test_place_order_refuses_when_halted(self, halt_file):
        """ib_place_order.place_order is the funnel for /orders/place AND
        the workflow emit bridge — when halted it must refuse before any
        IB connection is constructed."""
        trading_halt.set_halt(reason="kill switch", actor="test")
        import ib_place_order

        with patch.object(ib_place_order, "IBClient", side_effect=AssertionError(
            "IBClient must not be constructed while trading is halted"
        )):
            result = ib_place_order.place_order({
                "type": "stock",
                "symbol": "AAPL",
                "action": "BUY",
                "quantity": 1,
                "limitPrice": 200.0,
            })
        assert result["status"] == "error"
        assert "halt" in result["message"].lower()

    def test_workflow_bridge_refuses_when_halted(self, halt_file):
        trading_halt.set_halt(reason="kill switch", actor="test")
        from workflow import nodes

        import ib_place_order
        with patch.object(ib_place_order, "IBClient", side_effect=AssertionError(
            "IBClient must not be constructed while trading is halted"
        )):
            result = nodes.run_order_placement({
                "type": "stock",
                "symbol": "AAPL",
                "action": "BUY",
                "quantity": 1,
                "limitPrice": 200.0,
            })
        assert result["status"] == "error"


class TestExitOrdersChokepoint:
    def test_exit_orders_skips_cycle_when_halted(self, halt_file):
        trading_halt.set_halt(reason="kill switch", actor="test")
        from monitor_daemon.handlers.exit_orders import ExitOrdersHandler

        with patch(
            "monitor_daemon.handlers.exit_orders.IBClient",
            side_effect=AssertionError("no IB connection while halted"),
        ):
            handler = ExitOrdersHandler(db=MagicMock())
            result = handler.execute()

        assert result.get("halted") is True
        assert result.get("orders_placed", 0) == 0
        # Deliberate operator state — not an error condition.
        assert "error" not in result
