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
