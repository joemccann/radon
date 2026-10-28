#!/usr/bin/env python3
"""REL-006 — indeterminate-outcome discipline (RELIABILITY_AUDIT.md R-009).

A post-transmit exception used to collapse to a generic
``{"status": "error", "message": str(e)}`` — the order may be LIVE at IB
but the caller sees a plain failure and naturally re-places → duplicate.
The orderRef that could answer "arrived or not" died with the subprocess.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_place_order
import trading_halt
import pytest


def _mock_client(post_transmit_exc=None, qualify_exc=None):
    client = MagicMock()
    contract = MagicMock()
    contract.localSymbol = "AAPL"
    if qualify_exc:
        client.qualify_contracts.side_effect = qualify_exc
    else:
        client.qualify_contracts.return_value = [contract]
    trade = MagicMock()
    trade.order.orderId = 42
    trade.order.permId = 0
    trade.orderStatus.status = "PendingSubmit"
    client.place_order.return_value = trade
    if post_transmit_exc:
        client.sleep.side_effect = post_transmit_exc
    return client


_PARAMS = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": 200.0,
}


class TestPostTransmitIndeterminate:
    def test_post_transmit_exception_returns_indeterminate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        client = _mock_client(post_transmit_exc=RuntimeError("event loop died"))
        with patch.object(ib_place_order, "IBClient", return_value=client):
            result = ib_place_order.place_order(_PARAMS)

        assert result["status"] == "error"
        assert result.get("indeterminate") is True
        assert "check open orders" in result["message"].lower()
        assert result["orderRef"].startswith("radon-")
        assert result["orderId"] == 42

    def test_pre_transmit_exception_stays_plain(self, tmp_path, monkeypatch):
        """An exception BEFORE transmit is a determinate failure — no
        scary indeterminate copy, safe to re-place."""
        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        client = _mock_client(qualify_exc=RuntimeError("no security definition"))
        with patch.object(ib_place_order, "IBClient", return_value=client):
            result = ib_place_order.place_order(_PARAMS)

        assert result["status"] == "error"
        assert result.get("indeterminate") is not True
        assert "check open orders" not in result["message"].lower()

    def test_caller_supplied_order_ref_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        client = _mock_client(post_transmit_exc=RuntimeError("boom"))
        with patch.object(ib_place_order, "IBClient", return_value=client):
            result = ib_place_order.place_order(
                {**_PARAMS, "orderRef": "radon-route-supplied-ref"}
            )
        assert result["orderRef"] == "radon-route-supplied-ref"


@pytest.mark.parametrize("status", ["PendingSubmit", "ApiPending", "Unknown", ""])
@pytest.mark.parametrize("order_type", ["stock", "combo"])
def test_unacknowledged_transmission_preserves_indeterminate_identity(
    tmp_path, monkeypatch, status, order_type
):
    """A deadline cannot establish rejection or make replacement legs safe."""
    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    client = _mock_client()
    client.place_order.return_value.orderStatus.status = status
    params = {**_PARAMS, "type": order_type, "orderRef": "radon-timeout-reconcile"}
    if order_type == "combo":
        params.update(
            limitPrice=1.0,
            legs=[
                {"expiry": "20270115", "strike": 200, "right": "C", "action": "BUY"},
                {"expiry": "20270115", "strike": 210, "right": "C", "action": "SELL"},
            ],
        )
        client.qualify_contracts.side_effect = lambda *contracts: [
            MagicMock(conId=1000 + i) for i in range(len(contracts))
        ]
    # The placement happens, then the acknowledgement deadline expires. No
    # broker calls or real sleep: only the explicit fake client is reachable.
    clock = iter([100.0, 200.0])
    with patch.object(ib_place_order, "IBClient", return_value=client):
        result = ib_place_order.place_order(params, _clock=lambda: next(clock))

    assert result["status"] == "error"
    assert result.get("indeterminate") is True
    assert result.get("orderRef") == "radon-timeout-reconcile"
    assert result["orderId"] == 42
    assert result["permId"] == 0
    assert result["initialStatus"] == (status or "Unknown")
    assert "check open orders" in result["message"].lower()
    assert "legs separately" not in result["message"].lower()
    assert "silently dropped" not in result["message"].lower()
    client.place_order.assert_called_once()
    client.disconnect.assert_called_once()


@pytest.mark.parametrize(
    "broker_status,expected_status",
    [("PendingSubmit", "ok"), ("ApiPending", "ok"), ("Submitted", "ok"), ("Cancelled", "error")],
)
def test_status_callback_permanent_id_is_preserved_without_open_order(
    tmp_path, monkeypatch, broker_status, expected_status
):
    """Exercise ib_insync's real callback: it only updates OrderStatus.permId."""
    from ib_insync import Contract, LimitOrder, OrderStatus, Trade
    from ib_insync.wrapper import Wrapper

    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    client = _mock_client()
    order = LimitOrder("BUY", 1, 1.0, orderId=42, clientId=26)
    trade = Trade(
        Contract(symbol="AAPL", secType="BAG", exchange="SMART"),
        order,
        OrderStatus(orderId=42, status="PendingSubmit"),
    )
    wrapper = Wrapper(client._ib)
    wrapper.trades[(26, 42)] = trade
    client.place_order.return_value = trade
    client.qualify_contracts.side_effect = lambda *contracts: [
        MagicMock(conId=1000 + i) for i in range(len(contracts))
    ]

    def deliver_status(_seconds):
        wrapper.orderStatus(42, broker_status, 0, 1, 0, 987654, 0, 0, 26, "")

    client.sleep.side_effect = deliver_status
    params = {
        **_PARAMS, "type": "combo", "limitPrice": 1.0,
        "legs": [
            {"expiry": "20270115", "strike": 200, "right": "C", "action": "BUY"},
            {"expiry": "20270115", "strike": 210, "right": "C", "action": "SELL"},
        ],
    }
    ticks = iter(range(100, 200))
    with patch.object(ib_place_order, "IBClient", return_value=client):
        result = ib_place_order.place_order(params, _clock=lambda: next(ticks))

    assert trade.order.permId == 0, "No openOrder callback was delivered"
    assert trade.orderStatus.permId == 987654
    assert result["status"] == expected_status
    assert result["permId"] == 987654
    assert result["initialStatus"] == broker_status
    assert result.get("indeterminate") is not True
    client.place_order.assert_called_once()
