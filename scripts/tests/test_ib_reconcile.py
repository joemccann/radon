"""
Tests for ib_reconcile.py — grouping executions by contract, not just symbol.

Bug: group_executions_by_symbol() merges different option contracts for the
same underlying (e.g., EWY P$130 and EWY C$141) into one group, producing
net_quantity=0 → "CLOSED" when both legs are actually new opens.

Fix: group by (symbol, sec_type, strike, expiry, right) for options.
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock


# Import the functions under test
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ib_reconcile import group_executions_by_symbol, find_new_trades


def _make_execution(symbol, sec_type, side, shares, price, strike=None, expiry=None, right=None, commission=0, realized_pnl=0):
    """Helper to create an execution dict matching ib_reconcile format."""
    return {
        "time": datetime(2026, 3, 10, 10, 0, 0),
        "symbol": symbol,
        "sec_type": sec_type,
        "side": side,
        "shares": shares,
        "price": price,
        "exchange": "SMART",
        "commission": commission,
        "realized_pnl": realized_pnl,
        "strike": strike,
        "expiry": expiry,
        "right": right,
    }


class TestGroupExecutionsByContract:
    """Executions for different contracts on the same symbol must stay separate."""

    def test_same_symbol_different_strikes_grouped_separately(self):
        """EWY P$130 (buy) and EWY C$141 (sell) should NOT merge into net_quantity=0."""
        executions = [
            _make_execution("EWY", "OPT", "BOT", 25, 2.00, strike=130, expiry="20260313", right="P"),
            _make_execution("EWY", "OPT", "SLD", 25, 2.20, strike=141, expiry="20260313", right="C"),
        ]
        grouped = group_executions_by_symbol(executions)

        # Must produce two separate groups, not one with net_quantity=0
        ewy_groups = [g for g in grouped.values() if g["symbol"] == "EWY"]
        assert len(ewy_groups) == 2, (
            f"Expected 2 separate EWY groups (P$130 + C$141), got {len(ewy_groups)}"
        )

        # Verify each group has correct net_quantity
        qtys = sorted([g["net_quantity"] for g in ewy_groups])
        assert qtys == [-25, 25], f"Expected [-25, 25], got {qtys}"

    def test_same_symbol_same_contract_merges(self):
        """Multiple fills for the same contract should still merge."""
        executions = [
            _make_execution("AAOI", "OPT", "SLD", 25, 20.30, strike=105, expiry="20260320", right="C"),
            _make_execution("AAOI", "OPT", "SLD", 25, 22.00, strike=105, expiry="20260320", right="C"),
        ]
        grouped = group_executions_by_symbol(executions)

        aaoi_groups = [g for g in grouped.values() if g["symbol"] == "AAOI"]
        assert len(aaoi_groups) == 1
        assert aaoi_groups[0]["net_quantity"] == -50

    def test_stock_executions_group_by_symbol_only(self):
        """Stock executions don't have strike/expiry, should group by symbol."""
        executions = [
            _make_execution("TSLA", "STK", "BOT", 100, 250.00),
            _make_execution("TSLA", "STK", "BOT", 100, 251.00),
        ]
        grouped = group_executions_by_symbol(executions)

        tsla_groups = [g for g in grouped.values() if g["symbol"] == "TSLA"]
        assert len(tsla_groups) == 1
        assert tsla_groups[0]["net_quantity"] == 200

    def test_collar_legs_not_marked_closed(self):
        """A collar (buy put + sell call) should NOT result in action=CLOSED."""
        executions = [
            _make_execution("EWY", "OPT", "BOT", 25, 2.00, strike=130, expiry="20260313", right="P"),
            _make_execution("EWY", "OPT", "SLD", 25, 2.20, strike=141, expiry="20260313", right="C"),
        ]
        grouped = group_executions_by_symbol(executions)

        for g in grouped.values():
            assert g["action"] != "CLOSED", (
                f"Collar leg {g['symbol']} strike={g.get('strike')} incorrectly marked CLOSED"
            )


class TestFindNewTradesWithContracts:
    """find_new_trades should produce separate entries for each contract."""

    def test_collar_produces_two_new_trades(self):
        """EWY collar should produce two new_trades entries, not one."""
        executions = [
            _make_execution("EWY", "OPT", "BOT", 25, 2.00, strike=130, expiry="20260313", right="P"),
            _make_execution("EWY", "OPT", "SLD", 25, 2.20, strike=141, expiry="20260313", right="C"),
        ]
        trade_log = {"trades": []}

        new_trades = find_new_trades(executions, trade_log)

        ewy_trades = [t for t in new_trades if t["symbol"] == "EWY"]
        assert len(ewy_trades) == 2, (
            f"Expected 2 EWY new_trades for collar legs, got {len(ewy_trades)}"
        )

    def test_new_trade_includes_contract_details(self):
        """Each new_trade entry should include strike, expiry, right for options."""
        executions = [
            _make_execution("EWY", "OPT", "BOT", 25, 2.00, strike=130, expiry="20260313", right="P"),
        ]
        trade_log = {"trades": []}

        new_trades = find_new_trades(executions, trade_log)

        assert len(new_trades) == 1
        t = new_trades[0]
        assert t.get("strike") == 130
        assert t.get("expiry") == "20260313"
        assert t.get("right") == "P"
