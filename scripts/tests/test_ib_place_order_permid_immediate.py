"""Confirm-poll reads permId before sleeping.

A trade that already has a non-zero permId must not call client.sleep.
The loop still has to enter: the injected clock stays inside the 6s budget,
so a sleep-first loop fails this test and an already-expired deadline cannot
fake a pass by never reaching the body.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import eventkit

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _make_order(order_id: int = 99, perm_id: int = 12345) -> MagicMock:
    o = MagicMock()
    o.orderId = order_id
    o.permId = perm_id
    return o


def _make_order_status(status: str) -> MagicMock:
    os = MagicMock()
    os.status = status
    os.whyHeld = ""
    return os


def _make_trade(status: str = "Submitted", perm_id: int = 12345) -> MagicMock:
    trade = MagicMock()
    trade.order = _make_order(order_id=99, perm_id=perm_id)
    trade.orderStatus = _make_order_status(status=status)
    trade.log = []
    return trade


def _make_client(trade: MagicMock) -> MagicMock:
    client = MagicMock()
    real_error_event = eventkit.Event("errorEvent")
    ib_mock = MagicMock()
    ib_mock.errorEvent = real_error_event
    client._ib = ib_mock
    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=123456)])
    client.sleep = MagicMock()
    client.disconnect = MagicMock()
    return client


def _make_recording_clock(start: float = 1_000_000.0, step: float = 0.05):
    calls: list[float] = []

    def _clock() -> float:
        value = start + step * len(calls)
        calls.append(value)
        return value

    _clock.calls = calls
    return _clock


def _invoke_place_order(params: dict, client_mock: MagicMock, _clock) -> dict:
    with patch("ib_place_order.IBClient", return_value=client_mock), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        return ib_place_order.place_order(params, _clock=_clock)


_SINGLE_LEG_PARAMS = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "limitPrice": 214.50,
    "tif": "DAY",
}


def test_nonzero_permid_does_not_sleep():
    clock = _make_recording_clock(step=0.05)
    trade = _make_trade(status="Submitted", perm_id=12345)
    client = _make_client(trade)

    result = _invoke_place_order(_SINGLE_LEG_PARAMS, client, _clock=clock)

    assert result["status"] == "ok", result
    assert result["permId"] == 12345
    # deadline = calls[0] + 6.0; the while check must still be inside it.
    assert len(clock.calls) >= 2
    assert clock.calls[-1] < clock.calls[0] + 6.0
    assert client.sleep.call_count == 0
