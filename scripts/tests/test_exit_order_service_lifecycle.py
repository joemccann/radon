"""Audit §1#6: legacy exit service PENDING_MANUAL -> ACTIVE transition.

Only Cancelled/Inactive counted as failure, so Rejected / ApiCancelled /
PendingSubmit / any unknown status returned the orderId and the journal row
was marked ACTIVE. A failed journal write was dropped on the floor.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import exit_order_service as svc
import trading_halt
from exit_order_service import ExitTargetStatus, PlacementOutcome, classify_placement


LEGS = [
    {"type": "Long Call", "strike": 100.0},
    {"type": "Short Call", "strike": 110.0},
]


class _Client:
    def __init__(self, status, perm_id=0):
        self.status = status
        self.perm_id = perm_id
        self.placed = []
        self.cancelled = []

    def qualify_contracts(self, *_a, **_k):
        return None

    def place_order(self, contract, order):
        self.placed.append(order)
        return SimpleNamespace(
            order=SimpleNamespace(orderId=77, permId=self.perm_id),
            orderStatus=SimpleNamespace(status=self.status, permId=0),
        )

    def cancel_order(self, order):
        self.cancelled.append(order)

    def sleep(self, _s):
        return None


@pytest.fixture(autouse=True)
def _no_halt(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")


@pytest.mark.parametrize("status", ["Rejected", "ApiCancelled", "Cancelled", "Inactive"])
def test_terminal_failure_is_not_placed(status):
    client = _Client(status)
    assert svc.place_target_order(client, "AAPL", LEGS, contracts=1, target_price=5.0) is None


@pytest.mark.parametrize("status", ["PendingSubmit", "ApiPending", "", "SomethingNew"])
def test_unacknowledged_is_not_placed_and_is_cancelled(status):
    client = _Client(status, perm_id=0)
    assert svc.place_target_order(client, "AAPL", LEGS, contracts=1, target_price=5.0) is None
    assert len(client.cancelled) == 1


@pytest.mark.parametrize("status", ["Submitted", "PreSubmitted", "Filled"])
def test_accepted_returns_the_order_id(status):
    client = _Client(status)
    assert svc.place_target_order(client, "AAPL", LEGS, contracts=1, target_price=5.0) == 77
    assert client.cancelled == []


def test_pending_submit_with_a_perm_id_is_acknowledged():
    client = _Client("PendingSubmit", perm_id=5150)
    assert svc.place_target_order(client, "AAPL", LEGS, contracts=1, target_price=5.0) == 77


def test_classify_placement_table():
    c = classify_placement
    assert c("Submitted", 0) is PlacementOutcome.ACCEPTED
    assert c("Filled", 0) is PlacementOutcome.ACCEPTED
    assert c("PendingSubmit", 9) is PlacementOutcome.ACCEPTED
    assert c("Rejected", 9) is PlacementOutcome.REJECTED
    assert c("ApiCancelled", 0) is PlacementOutcome.REJECTED
    assert c("PendingSubmit", 0) is PlacementOutcome.UNACKNOWLEDGED
    assert c("Unknown", 0) is PlacementOutcome.UNACKNOWLEDGED
    assert c(None, 0) is PlacementOutcome.UNACKNOWLEDGED


def _run_cycle(monkeypatch, journaled: bool):
    pending = [{
        "trade_id": 42, "ticker": "AAPL", "target_price": 5.0, "contracts": 1,
        "legs": LEGS,
    }]
    writes = []
    monkeypatch.setattr(svc, "is_market_open", lambda: True)
    monkeypatch.setattr(svc, "load_pending_exits", lambda: pending)
    monkeypatch.setattr(svc, "IBClient", lambda: SimpleNamespace(
        connect=lambda **_k: None, disconnect=lambda: None,
    ))
    monkeypatch.setattr(svc, "get_spread_price", lambda *_a: 4.5)
    monkeypatch.setattr(svc, "place_target_order", lambda *_a, **_k: 77)

    def update(trade_id, order_id, status):
        writes.append((trade_id, order_id, status))
        return journaled

    monkeypatch.setattr(svc, "update_trade_log", update)
    return svc.check_and_place_orders(), writes


def test_failed_journal_write_is_surfaced(monkeypatch):
    result, writes = _run_cycle(monkeypatch, journaled=False)

    assert writes == [(42, 77, ExitTargetStatus.ACTIVE.value)]
    assert result["placed_orders"][0]["journaled"] is False
    assert any("77" in e and "journal" in e.lower() for e in result["errors"])


def test_successful_journal_write_has_no_error(monkeypatch):
    result, _ = _run_cycle(monkeypatch, journaled=True)

    assert result["placed_orders"][0]["journaled"] is True
    assert result["errors"] == []


def test_main_exits_nonzero_when_the_cycle_has_errors(monkeypatch):
    monkeypatch.setattr(svc, "check_and_place_orders", lambda dry_run=False: {
        "placed_orders": [{"ticker": "AAPL", "order_id": 77, "journaled": False}],
        "pending_orders": [], "errors": ["journal write failed for order #77"],
    })
    monkeypatch.setattr(sys, "argv", ["exit_order_service.py"])

    with pytest.raises(SystemExit) as exc:
        svc.main()
    assert exc.value.code == 1
