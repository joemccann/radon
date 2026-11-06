"""fill_monitor order identity + completion outcome (audit §3.1, C2).

- A mirror keyed by orderId (permId 0) is never superseded: journal_sync
  only covers (conId, permId, date). No such mirror may be written.
- known_orders keyed by orderId collides across clients (manual TWS orders
  all carry orderId 0). Orders are keyed by permId.
- An order that completes between cycles has its final increment
  journaled from the executions.
- The stored status is read: a vanished order with no executions whose
  last status was Cancelled is CANCELLED; a partial then gone is
  PARTIALLY_FILLED_THEN_CANCELLED.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from monitor_daemon.handlers import fill_monitor
from monitor_daemon.handlers.fill_monitor import FillMonitorHandler


def _contract(con_id=11, sec_type="OPT"):
    return SimpleNamespace(
        symbol="AAOI", localSymbol="AAOI C40", conId=con_id, secType=sec_type,
        strike=40.0, right="C", lastTradeDateOrContractMonth="20261218",
    )


def _trade(*, order_id, perm_id, filled, quantity=10, client_id=0, status="Submitted", avg=2.0):
    return SimpleNamespace(
        order=SimpleNamespace(orderId=order_id, permId=perm_id, clientId=client_id,
                              action="BUY", totalQuantity=quantity, lmtPrice=2.0),
        orderStatus=SimpleNamespace(filled=filled, remaining=quantity - filled,
                                    status=status, avgFillPrice=avg),
        contract=_contract(),
    )


def _exec_fill(*, perm_id, order_id, shares, price=2.0, con_id=11):
    return SimpleNamespace(
        execution=SimpleNamespace(permId=perm_id, orderId=order_id, shares=shares, price=price),
        contract=_contract(con_id),
    )


def _cycle(handler, trades, fills=(), accounts=("DU1",)):
    with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
         patch.object(FillMonitorHandler, "_lookup_prior_qty", return_value=0.0), \
         patch.object(FillMonitorHandler, "_mirror_ib_orders_snapshot"):
        client = MagicMock()
        mock_cls.return_value = client
        client.get_open_orders.return_value = list(trades)
        client.get_managed_accounts.return_value = list(accounts)
        client.get_fills.return_value = list(fills)
        return handler.execute()


def test_mirror_is_not_written_without_a_perm_id(monkeypatch):
    upsert = MagicMock()
    monkeypatch.setattr(fill_monitor, "upsert_journal_entry", upsert)
    handler = FillMonitorHandler(send_notifications=False)
    _cycle(handler, [_trade(order_id=5, perm_id=0, filled=0)])
    _cycle(handler, [_trade(order_id=5, perm_id=0, filled=4)])
    upsert.assert_not_called()


def test_manual_tws_orders_sharing_order_id_zero_do_not_collide(monkeypatch):
    monkeypatch.setattr(fill_monitor, "upsert_journal_entry", MagicMock())
    handler = FillMonitorHandler(send_notifications=False)
    book = [_trade(order_id=0, perm_id=111, filled=3), _trade(order_id=0, perm_id=222, filled=0)]
    _cycle(handler, book)
    result = _cycle(handler, book)
    assert result["fills"] == []
    assert set(handler.known_orders) == {111, 222}


def test_final_increment_journaled_when_order_completes_between_cycles(monkeypatch):
    upsert = MagicMock()
    monkeypatch.setattr(fill_monitor, "upsert_journal_entry", upsert)
    handler = FillMonitorHandler(send_notifications=False)
    _cycle(handler, [_trade(order_id=5, perm_id=77, filled=2)])
    result = _cycle(handler, [], fills=[_exec_fill(perm_id=77, order_id=5, shares=2),
                                        _exec_fill(perm_id=77, order_id=5, shares=8, price=2.5)])
    assert result["completed"][0]["status"] == "COMPLETED"
    trade_id, payload = upsert.call_args[0][0], upsert.call_args[0][1]
    assert trade_id.endswith(":filled-10")
    assert ":order-77:" in trade_id
    assert payload["newly_filled"] == 8
    assert payload["fill_price"] == 2.5


def test_stored_cancelled_status_is_read():
    handler = FillMonitorHandler(send_notifications=False)
    _cycle(handler, [_trade(order_id=5, perm_id=77, filled=0, status="Cancelled")])
    result = _cycle(handler, [])
    assert result["completed"][0]["status"] == "CANCELLED"


def test_partial_then_gone_is_partially_filled_then_cancelled(monkeypatch):
    monkeypatch.setattr(fill_monitor, "upsert_journal_entry", MagicMock())
    handler = FillMonitorHandler(send_notifications=False)
    _cycle(handler, [_trade(order_id=5, perm_id=77, filled=2)])
    result = _cycle(handler, [], fills=[_exec_fill(perm_id=77, order_id=5, shares=2)])
    assert result["completed"][0]["status"] == "PARTIALLY_FILLED_THEN_CANCELLED"
