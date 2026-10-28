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


_WARNING_399 = "Order message: review the submitted combination in the broker workstation."
_WARNING_2109 = "Attribute Outside Regular Trading Hours is ignored for this destination."


def _invoke_warning_case(tmp_path, monkeypatch, outcome, events):
    """Deliver real ib_insync warning callbacks while a synthetic BAG awaits ACK."""
    import eventkit
    from ib_insync import Contract, LimitOrder, OrderStatus, Trade
    from ib_insync.wrapper import Wrapper

    monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
    client = _mock_client()
    client._ib.errorEvent = eventkit.Event("errorEvent")
    trade = Trade(
        Contract(symbol="AAPL", secType="BAG", exchange="SMART"),
        LimitOrder("BUY", 1, 1.0, orderId=42, clientId=26),
        OrderStatus(orderId=42, status="PendingSubmit"),
    )
    wrapper = Wrapper(client._ib)
    wrapper.clientId = 26
    wrapper.trades[(26, 42)] = trade
    client.place_order.return_value = trade
    client.qualify_contracts.side_effect = lambda *contracts: [
        MagicMock(conId=1000 + i) for i in range(len(contracts))
    ]
    waits = 0

    def deliver(_seconds):
        nonlocal waits
        waits += 1
        if waits == 1:
            for req_id, code, message in events:
                wrapper.error(req_id, code, message, "")
            if outcome == "exception":
                raise RuntimeError("synthetic event loop failure")
        elif waits == 2 and outcome in ("acknowledged", "cancelled"):
            status = "Submitted" if outcome == "acknowledged" else "Cancelled"
            wrapper.orderStatus(42, status, 0, 1, 0, 987654, 0, 0, 26, "")

    client.sleep.side_effect = deliver
    params = {
        **_PARAMS, "type": "combo", "limitPrice": 1.0,
        "orderRef": "radon-warning-reconcile",
        "legs": [
            {"expiry": "20270115", "strike": 200, "right": "C", "action": "BUY"},
            {"expiry": "20270115", "strike": 210, "right": "C", "action": "SELL"},
        ],
    }
    ticks = iter(range(100, 200))
    with patch.object(ib_place_order, "IBClient", return_value=client):
        result = ib_place_order.place_order(params, _clock=lambda: next(ticks))
    client.place_order.assert_called_once()
    client.disconnect.assert_called_once()
    return result, client, trade


@pytest.mark.parametrize("outcome", ["acknowledged", "timeout", "exception", "cancelled"])
def test_order_scoped_broker_warnings_survive_without_changing_outcome(
    tmp_path, monkeypatch, outcome
):
    events = [
        (42, 399, _WARNING_399),
        (999, 399, "A different order's warning must not be attributed here."),
        (-1, 399, "A session-wide warning must not be attributed here."),
        (42, 2104, "Market data farm connection is OK."),
        (42, 2109, _WARNING_2109),
        (42, 399, _WARNING_399),
    ]
    result, client, trade = _invoke_warning_case(tmp_path, monkeypatch, outcome, events)

    # The library does not put these warning callbacks into Trade.log. The
    # placer must preserve them explicitly before its stderr is discarded.
    assert not any(entry.errorCode in (399, 2109) for entry in trade.log)
    assert result.get("ib_warnings") == [
        {"code": 399, "message": _WARNING_399},
        {"code": 2109, "message": _WARNING_2109},
    ]
    assert result["status"] == ("ok" if outcome == "acknowledged" else "error")
    assert result.get("indeterminate", False) is (outcome in ("timeout", "exception"))
    if outcome == "acknowledged":
        assert client.sleep.call_count == 2, "Warnings must not terminate the ACK wait"
        assert result["permId"] == 987654
    if outcome in ("timeout", "exception"):
        assert result["orderRef"] == "radon-warning-reconcile"
        assert "check open orders" in result["message"].lower()
        assert "legs separately" not in result["message"].lower()


@pytest.mark.parametrize("outcome", ["acknowledged", "timeout"])
def test_unrelated_and_farm_warnings_do_not_create_order_diagnostics(
    tmp_path, monkeypatch, outcome
):
    result, _, _ = _invoke_warning_case(tmp_path, monkeypatch, outcome, [
        (999, 399, "Unrelated order."),
        (-1, 2109, "Session warning."),
        (42, 2104, "Market data farm connection is OK."),
    ])
    assert not result.get("ib_warnings")
    assert result["status"] == ("ok" if outcome == "acknowledged" else "error")
    assert result.get("indeterminate", False) is (outcome == "timeout")


def test_retained_warning_diagnostics_are_bounded_and_credential_scrubbed(tmp_path, monkeypatch):
    events = [(42, 399, "password=synthetic-warning-secret " + "x" * 2000)]
    events.extend((42, 399, f"Broker warning {i}") for i in range(20))
    result, _, _ = _invoke_warning_case(tmp_path, monkeypatch, "timeout", events)

    warnings = result.get("ib_warnings")
    assert warnings and len(warnings) <= 8
    assert all(len(warning["message"]) <= 512 for warning in warnings)
    assert "synthetic-warning-secret" not in str(warnings)
    assert result["indeterminate"] is True
