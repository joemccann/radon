"""NF-1 follow-up: operator preference extends Gate 3 to ib_execute + exit service.

RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS (app_preferences, Turso-backed). Default
Off (bypass): these paths behave as before. On (enforce): the same 2.5% NLV /
15-minute freshness check as place_order; close-outs stay allowed.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import app_preferences
import bankroll_guard
import exit_order_service
import ib_execute
import trading_halt

PREF = "RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS"
REAL_CHECK = bankroll_guard.check_bankroll_admission


def _snapshot(*, age_min=1.0, nlv=100_000.0, positions=None):
    return {
        "last_sync": (datetime.now(timezone.utc) - timedelta(minutes=age_min)).isoformat(),
        "account_summary": {"net_liquidation": nlv},
        "positions": positions or [],
    }


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "trading_halt.json")
    monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", REAL_CHECK)
    monkeypatch.delenv(PREF, raising=False)
    app_preferences.clear_snapshot_for_tests()
    state = {"snapshot": _snapshot(age_min=600)}
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: state["snapshot"])
    yield state
    app_preferences.clear_snapshot_for_tests()


def _enforce():
    app_preferences.seed_snapshot_for_tests({PREF: "true"})


def test_preference_is_registered_default_off():
    pref = app_preferences.get_preference(PREF)
    assert pref is not None and pref.value_type == "bool" and pref.default is False
    assert pref.group == "Order Limits"


# ── ib_execute ────────────────────────────────────────────────────────


def _run_execute(argv):
    placed: list = []
    executor = MagicMock()
    executor.connect.return_value = True
    executor.get_stock_contract.return_value = MagicMock(localSymbol="AAPL")
    executor.get_option_contract.return_value = MagicMock(localSymbol="AAPL C")
    executor.get_market_data.return_value = {"bid": 1.0, "ask": 1.1, "mid": 1.05, "spread": 0.1}
    executor.place_order.side_effect = lambda *a, **k: placed.append(a)
    with patch.object(ib_execute, "OrderExecutor", return_value=executor):
        with patch.object(sys, "argv", ["ib_execute.py", *argv]):
            try:
                ib_execute.main()
            except SystemExit:
                pass
    return placed


BUY_CALL = ["--type", "option", "--symbol", "AAPL", "--expiry", "20261016", "--strike", "200",
            "--right", "C", "--qty", "5", "--side", "BUY", "--limit", "1.00", "--yes", "--no-log"]


class TestIbExecute:
    def test_default_bypass_passes_stale(self, guard):
        assert len(_run_execute(BUY_CALL)) == 1

    def test_enforce_refuses_stale(self, guard, capsys):
        _enforce()
        assert _run_execute(BUY_CALL) == []
        assert "15 min" in capsys.readouterr().out

    def test_enforce_refuses_over_cap(self, guard):
        _enforce()
        guard["snapshot"] = _snapshot(nlv=10_000.0)  # cap $250; order $500
        assert _run_execute(BUY_CALL) == []

    def test_enforce_admits_fresh_under_cap(self, guard):
        _enforce()
        guard["snapshot"] = _snapshot()
        assert len(_run_execute(BUY_CALL)) == 1

    def test_enforce_allows_close_out(self, guard):
        _enforce()
        guard["snapshot"] = _snapshot(age_min=600, positions=[{
            "ticker": "AAPL", "expiry": "2026-10-16",
            "legs": [{"type": "Call", "strike": 200.0, "direction": "LONG", "contracts": 5}],
        }])
        sell = [a if a != "BUY" else "SELL" for a in BUY_CALL]
        assert len(_run_execute(sell)) == 1


# ── exit_order_service ────────────────────────────────────────────────

EXIT_LEGS = [
    {"type": "Long Call", "strike": 200.0},
    {"type": "Short Call", "strike": 210.0},
]
HELD_SPREAD = {
    "ticker": "AAPL", "expiry": "2026-10-16",
    "legs": [
        {"type": "Call", "strike": 200.0, "direction": "LONG", "contracts": 2},
        {"type": "Call", "strike": 210.0, "direction": "SHORT", "contracts": 2},
    ],
}


def _place_exit(contracts=2):
    client = MagicMock()
    client.place_order.return_value = MagicMock(
        orderStatus=MagicMock(status="Submitted"), order=MagicMock(orderId=7)
    )
    result = exit_order_service.place_target_order(
        client, "AAPL", EXIT_LEGS, contracts, 8.0, order_data={"legs": [{"expiry": "20261016"}]}
    )
    return result, client.place_order.called


class TestExitOrderService:
    def test_default_bypass_passes_stale(self, guard):
        assert _place_exit()[1] is True

    def test_enforce_refuses_non_close_with_stale(self, guard):
        _enforce()  # nothing held -> not a close-out -> stale refuses
        assert _place_exit() == (None, False)

    def test_enforce_allows_close_out_with_stale(self, guard):
        _enforce()
        guard["snapshot"] = _snapshot(age_min=600, positions=[HELD_SPREAD])
        assert _place_exit()[1] is True
