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


class TestTheFlagFailsClosedOnEveryUnprovenShape:
    """§3.4: `{}` or an object without a boolean `halted` read as ALLOWED."""

    @pytest.mark.parametrize(
        "payload",
        [{}, {"reason": "drill"}, {"halted": "false"}, {"halted": None}, {"halted": 0}],
        ids=["empty", "no-halted", "string", "null", "int"],
    )
    def test_a_payload_without_a_boolean_halted_is_halted(self, halt_file, payload):
        halt_file.write_text(json.dumps(payload))
        assert trading_halt.is_trading_halted() is True, (
            f"{payload!r} read as trading ALLOWED; the kill switch fails open"
        )
        state = trading_halt.get_halt_state()
        assert state["halted"] is True
        assert "malformed" in state["reason"]

    def test_the_states_are_explicit(self, halt_file):
        assert trading_halt.read_halt_status() is trading_halt.HaltStatus.ABSENT
        trading_halt.set_halt("drill")
        assert trading_halt.read_halt_status() is trading_halt.HaltStatus.HALTED
        trading_halt.clear_halt()
        assert trading_halt.read_halt_status() is trading_halt.HaltStatus.RESUMED
        halt_file.write_text("{}")
        assert trading_halt.read_halt_status() is trading_halt.HaltStatus.MALFORMED


class TestResumeIsBoundToTheHaltItSaw:
    def test_each_halt_carries_its_own_id(self, halt_file):
        first = trading_halt.set_halt("one")
        second = trading_halt.set_halt("two")
        assert first["halt_id"] and second["halt_id"]
        assert first["halt_id"] != second["halt_id"]

    def test_a_resume_for_an_older_halt_does_not_clear_a_newer_one(self, halt_file):
        seen = trading_halt.set_halt("first halt")
        trading_halt.clear_halt(halt_id=seen["halt_id"])
        newer = trading_halt.set_halt("kill switch from another session")
        with pytest.raises(trading_halt.HaltConflict):
            trading_halt.clear_halt(halt_id=seen["halt_id"])
        state = trading_halt.get_halt_state()
        assert state["halted"] is True
        assert state["halt_id"] == newer["halt_id"]

    def test_a_resume_naming_the_live_halt_clears_it(self, halt_file):
        seen = trading_halt.set_halt("drill")
        resumed = trading_halt.clear_halt(halt_id=seen["halt_id"])
        assert resumed["halted"] is False
        assert resumed["halt_id"] == seen["halt_id"]
        assert trading_halt.is_trading_halted() is False

    def test_a_resume_without_an_id_still_clears(self, halt_file):
        trading_halt.set_halt("drill")
        trading_halt.clear_halt(actor="api")
        assert trading_halt.is_trading_halted() is False


class TestTheFallbackWriteIsAtomic:
    def test_no_atomic_io_still_writes_through_a_rename(self, halt_file, monkeypatch):
        monkeypatch.setitem(sys.modules, "utils.atomic_io", None)
        replaced: list = []
        real_replace = trading_halt.os.replace
        monkeypatch.setattr(
            trading_halt.os, "replace", lambda a, b: (replaced.append(str(b)), real_replace(a, b))[1]
        )
        trading_halt.set_halt("drill")
        assert replaced == [str(halt_file)], "the ImportError fallback is a plain write_text"
        assert trading_halt.is_trading_halted() is True


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
