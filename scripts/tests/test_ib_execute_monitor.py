"""Audit §3.1 / §1#14: ib_execute.monitor_order outcome machine.

Inactive / Rejected used to loop as "Working" until timeout, and an order that
left the open list was matched to fills by SYMBOL, so every same-day fill in
the name was summed and journaled as this order's quantity and price.
"""
from types import SimpleNamespace

import pytest

import ib_execute
from ib_execute import MonitorOutcome, classify_monitor


def make_trade(status="Submitted", filled=0, avg=0.0, order_id=7, perm_id=7007,
               qty=100, symbol="NFLX"):
    return SimpleNamespace(
        order=SimpleNamespace(orderId=order_id, permId=perm_id, clientId=25,
                              totalQuantity=qty, action="SELL"),
        contract=SimpleNamespace(symbol=symbol),
        orderStatus=SimpleNamespace(status=status, filled=filled, avgFillPrice=avg),
        fills=[],
    )


def make_fill(shares, price, perm_id=7007, order_id=7, client_id=25, exec_id=None,
              symbol="NFLX", commission=1.0):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=symbol),
        execution=SimpleNamespace(
            permId=perm_id, orderId=order_id, clientId=client_id, shares=shares,
            avgPrice=price, execId=exec_id or f"e-{perm_id}-{shares}-{price}",
            time="2026-11-03 10:00:00",
        ),
        commissionReport=SimpleNamespace(commission=commission),
    )


class FakeClient:
    def __init__(self, open_trades=(), fills=()):
        self.open_trades = list(open_trades)
        self.fills = list(fills)
        self.sleeps = 0

    def sleep(self, _seconds):
        self.sleeps += 1

    def get_open_orders(self):
        return list(self.open_trades)

    def get_open_trades(self):
        return list(self.open_trades)

    def get_fills(self):
        return list(self.fills)


def run_monitor(trade, client, timeout=30, interval=3):
    executor = ib_execute.OrderExecutor.__new__(ib_execute.OrderExecutor)
    executor.client = client
    return executor.monitor_order(trade, timeout=timeout, interval=interval)


@pytest.mark.parametrize("status", ["Inactive", "Rejected"])
def test_inactive_and_rejected_end_the_monitor(status):
    trade = make_trade(status=status)
    client = FakeClient(open_trades=[trade])

    result = run_monitor(trade, client)

    assert result["status"] == "rejected"
    assert result["broker_status"] == status
    assert client.sleeps <= 2  # first check, not the 30s timeout


def test_gone_order_ignores_other_orders_fills_in_the_same_symbol():
    trade = make_trade()
    other = make_fill(100, 98.0, perm_id=1234, order_id=8)
    client = FakeClient(open_trades=[], fills=[other])

    result = run_monitor(trade, client)

    assert result["status"] == "unknown"


def test_gone_order_sums_only_its_own_fills():
    trade = make_trade()
    mine = [make_fill(60, 98.0), make_fill(40, 99.0)]
    other = make_fill(500, 50.0, perm_id=1234, order_id=8)
    client = FakeClient(open_trades=[], fills=[*mine, other])

    result = run_monitor(trade, client)

    assert result["status"] == "filled"
    assert result["quantity"] == 100
    assert result["avg_price"] == pytest.approx(98.4)
    assert len(result["fills"]) == 2


def test_duplicate_exec_ids_count_once():
    trade = make_trade()
    fill = make_fill(100, 98.0, exec_id="x1")
    trade.fills = [make_fill(100, 98.0, exec_id="x1")]
    client = FakeClient(open_trades=[], fills=[fill])

    result = run_monitor(trade, client)

    assert result["status"] == "filled"
    assert result["quantity"] == 100


def test_gone_order_with_partial_fills_is_not_filled():
    trade = make_trade()
    client = FakeClient(open_trades=[], fills=[make_fill(30, 98.0)])

    result = run_monitor(trade, client)

    assert result["status"] == "partially_filled"
    assert result["quantity"] == 30


def test_cancelled_after_partial_fill_reports_the_fill():
    trade = make_trade(status="Cancelled", filled=30, avg=98.0)
    client = FakeClient(open_trades=[], fills=[make_fill(30, 98.0)])

    result = run_monitor(trade, client)

    assert result["status"] == "partially_filled"
    assert result["broker_status"] == "Cancelled"
    assert result["quantity"] == 30


def test_cancelled_without_fills_is_cancelled():
    trade = make_trade(status="ApiCancelled")
    client = FakeClient(open_trades=[])

    assert run_monitor(trade, client)["status"] == "cancelled"


def test_working_order_times_out():
    trade = make_trade(status="PendingCancel")
    client = FakeClient(open_trades=[trade])

    result = run_monitor(trade, client, timeout=6, interval=3)

    assert result["status"] == "timeout"


def test_partially_filled_does_not_journal(monkeypatch, capsys):
    """main() journals only a full fill; a partial exits non-zero."""
    import sys

    class Partial:
        def __init__(self, *a):
            pass

        def connect(self):
            return True

        def disconnect(self):
            pass

        def get_stock_contract(self, symbol):
            return SimpleNamespace(symbol=symbol)

        def get_market_data(self, contract):
            return {"bid": 98.0, "ask": 99.0, "mid": 98.5, "last": 98.5, "spread": 1.0}

        def place_order(self, *a):
            return object()

        def monitor_order(self, *a, **k):
            return {"status": "partially_filled", "order_id": 7, "symbol": "NFLX", "quantity": 30}

        def log_trade(self, *a, **k):
            raise AssertionError("partial fill must not be journaled as a fill")

    monkeypatch.setattr(ib_execute, "OrderExecutor", Partial)
    monkeypatch.setattr(sys, "argv", [
        "ib_execute.py", "--type", "stock", "--symbol", "NFLX", "--qty", "100",
        "--side", "SELL", "--limit", "98.70", "--yes",
    ])
    with pytest.raises(SystemExit) as exc:
        ib_execute.main()
    assert exc.value.code == 1


class TestClassifyMonitor:
    def test_table(self):
        c = classify_monitor
        assert c("Submitted", is_open=True, filled=0, target=100) is None
        assert c("PendingCancel", is_open=True, filled=10, target=100) is None
        assert c("Filled", is_open=False, filled=100, target=100) is MonitorOutcome.FILLED
        assert c("Cancelled", is_open=False, filled=0, target=100) is MonitorOutcome.CANCELLED
        assert c("ApiCancelled", is_open=True, filled=0, target=100) is MonitorOutcome.CANCELLED
        assert c("Inactive", is_open=True, filled=0, target=100) is MonitorOutcome.REJECTED
        assert c("Rejected", is_open=True, filled=0, target=100) is MonitorOutcome.REJECTED
        assert c("Cancelled", is_open=False, filled=5, target=100) is MonitorOutcome.PARTIALLY_FILLED
        assert c("Submitted", is_open=False, filled=0, target=100) is MonitorOutcome.UNKNOWN
        assert c("Submitted", is_open=False, filled=100, target=100) is MonitorOutcome.FILLED
        assert c("Submitted", is_open=False, filled=40, target=100) is MonitorOutcome.PARTIALLY_FILLED
