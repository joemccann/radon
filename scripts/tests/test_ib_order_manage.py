"""Tests for ib_order_manage.py — mocks IBClient connection."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ib_order_manage import find_trade, cancel_order, modify_order, output


# ─── Helpers ────────────────────────────────────────────

def make_trade(order_id=10, perm_id=12345, status="Submitted", order_type="LMT", lmt_price=22.50):
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.order.orderType = order_type
    trade.order.lmtPrice = lmt_price
    trade.order.totalQuantity = 50
    trade.order.outsideRth = False
    trade.order.clientId = 0
    trade.orderStatus.status = status
    trade.contract = MagicMock()
    return trade


def make_client(trades=None):
    client = MagicMock()
    client.get_open_orders.return_value = trades or []
    client.sleep = MagicMock()
    # Expose ib property for error event handling and clientId access
    client.ib = MagicMock()
    client.ib.client.clientId = 0
    return client


# ─── find_trade ─────────────────────────────────────────

class TestFindTrade:
    def test_find_by_perm_id(self):
        t = make_trade(order_id=10, perm_id=999)
        client = make_client([t])
        assert find_trade(client, 0, 999) is t

    def test_find_by_order_id(self):
        t = make_trade(order_id=42, perm_id=0)
        client = make_client([t])
        assert find_trade(client, 42, 0) is t

    def test_perm_id_preferred_over_order_id(self):
        t1 = make_trade(order_id=10, perm_id=100)
        t2 = make_trade(order_id=10, perm_id=200)
        client = make_client([t1, t2])
        assert find_trade(client, 10, 200) is t2

    def test_not_found(self):
        client = make_client([make_trade(order_id=10, perm_id=100)])
        assert find_trade(client, 99, 88) is None


# ─── cancel_order ───────────────────────────────────────

class TestCancelOrder:
    def test_cancel_success(self):
        t = make_trade(status="Submitted")
        t.orderStatus.status = "Submitted"

        def side_effect(order):
            t.orderStatus.status = "Cancelled"

        client = make_client([t])
        client.cancel_order = MagicMock(side_effect=side_effect)

        with pytest.raises(SystemExit) as exc:
            cancel_order(client, 10, 12345, "127.0.0.1", 4001)
        assert exc.value.code == 0
        client.cancel_order.assert_called_once_with(t.order)

    def test_cancel_already_filled(self):
        t = make_trade(status="Filled")
        client = make_client([t])

        with pytest.raises(SystemExit) as exc:
            cancel_order(client, 10, 12345, "127.0.0.1", 4001)
        assert exc.value.code == 1

    def test_cancel_not_found(self):
        client = make_client([])

        with pytest.raises(SystemExit) as exc:
            cancel_order(client, 99, 88, "127.0.0.1", 4001)
        assert exc.value.code == 1

    def test_cancel_reconnects_as_original_client_id(self):
        """When cancel script connects as clientId 0 but order was placed by clientId 9,
        it should disconnect + reconnect as clientId 9 before cancelling."""
        t = make_trade(status="Submitted")
        t.order.clientId = 9  # Order placed by different client

        def side_effect(order):
            t.orderStatus.status = "Cancelled"

        client = make_client([t])
        client.ib.client.clientId = 0  # Connected as clientId 0
        client.cancel_order = MagicMock(side_effect=side_effect)
        # After reconnect, find_trade should still find the trade
        client.get_open_orders.return_value = [t]

        with pytest.raises(SystemExit) as exc:
            cancel_order(client, 10, 12345, "127.0.0.1", 4001)
        assert exc.value.code == 0
        # Should have disconnected and reconnected as clientId 9
        client.disconnect.assert_called_once()
        client.connect.assert_called_once_with(host="127.0.0.1", port=4001, client_id=9)
        client.cancel_order.assert_called_once_with(t.order)

    def test_cancel_same_client_id_no_reconnect(self):
        """When cancel script is already connected as the right clientId, no reconnect needed."""
        t = make_trade(status="Submitted")
        t.order.clientId = 0

        def side_effect(order):
            t.orderStatus.status = "Cancelled"

        client = make_client([t])
        client.ib.client.clientId = 0  # Same clientId
        client.cancel_order = MagicMock(side_effect=side_effect)

        with pytest.raises(SystemExit) as exc:
            cancel_order(client, 10, 12345, "127.0.0.1", 4001)
        assert exc.value.code == 0
        client.disconnect.assert_not_called()

    def test_cancel_reports_error_on_pending_cancel_timeout(self, capsys):
        """When cancel request times out in PendingCancel state, report as error."""
        t = make_trade(status="Submitted")
        t.order.clientId = 0

        client = make_client([t])
        client.ib.client.clientId = 0
        # cancel_order is called but status stays PendingCancel
        def side_effect(order):
            t.orderStatus.status = "PendingCancel"
        client.cancel_order = MagicMock(side_effect=side_effect)

        with pytest.raises(SystemExit) as exc:
            cancel_order(client, 10, 12345, "127.0.0.1", 4001)
        assert exc.value.code == 1  # Error, not success
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "error"
        assert "PendingCancel" in data["message"]


# ─── modify_order ───────────────────────────────────────

class TestModifyOrder:
    def test_modify_success(self):
        t = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        confirmed = make_trade(status="Submitted", order_type="LMT", lmt_price=22.50)
        client = make_client()
        client.get_open_orders.side_effect = [[t], [confirmed]]
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001)
        assert exc.value.code == 0
        assert t.order.lmtPrice == 22.50
        client.place_order.assert_called_once_with(t.contract, t.order)

    def test_modify_non_lmt_fails(self):
        t = make_trade(status="Submitted", order_type="MKT")
        client = make_client([t])

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001)
        assert exc.value.code == 1

    def test_modify_already_filled(self):
        t = make_trade(status="Filled", order_type="LMT")
        client = make_client([t])

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001)
        assert exc.value.code == 1

    def test_modify_zero_price_fails(self):
        t = make_trade(status="Submitted", order_type="LMT")
        client = make_client([t])

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 0, "127.0.0.1", 4001)
        assert exc.value.code == 1

    def test_modify_not_found(self):
        client = make_client([])

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 99, 88, 22.50, "127.0.0.1", 4001)
        assert exc.value.code == 1

    def test_modify_stp_lmt_allowed(self):
        t = make_trade(status="Submitted", order_type="STP LMT", lmt_price=18.00)
        confirmed = make_trade(status="Submitted", order_type="STP LMT", lmt_price=19.00)
        client = make_client()
        client.get_open_orders.side_effect = [[t], [confirmed]]
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 19.00, "127.0.0.1", 4001)
        assert exc.value.code == 0
        assert t.order.lmtPrice == 19.00

    def test_modify_quantity_success(self):
        t = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        t.order.totalQuantity = 50
        confirmed = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        confirmed.order.totalQuantity = 75
        client = make_client()
        client.get_open_orders.side_effect = [[t], [confirmed]]
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 20.00, "127.0.0.1", 4001, new_quantity=75)
        assert exc.value.code == 0
        assert t.order.totalQuantity == 75
        client.place_order.assert_called_once_with(t.contract, t.order)

    def test_modify_errors_when_refreshed_order_keeps_old_price(self, capsys):
        t = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        stale = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        client = make_client()
        client.get_open_orders.side_effect = [[t]] + [[stale]] * 10
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "error"
        assert "not confirmed" in data["message"].lower()
        assert data["currentPrice"] == 20.00

    def test_modify_with_outside_rth_sets_flag(self):
        """When outside_rth=True, the order's outsideRth attribute should be set to True."""
        t = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        t.order.outsideRth = False  # Default
        confirmed = make_trade(status="Submitted", order_type="LMT", lmt_price=22.50)
        confirmed.order.outsideRth = True
        client = make_client()
        client.get_open_orders.side_effect = [[t], [confirmed]]
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001, outside_rth=True)
        assert exc.value.code == 0
        assert t.order.outsideRth is True
        assert t.order.lmtPrice == 22.50
        client.place_order.assert_called_once_with(t.contract, t.order)

    def test_modify_without_outside_rth_preserves_flag(self):
        """When outside_rth is not set, the order's existing outsideRth should be preserved."""
        t = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        t.order.outsideRth = False
        confirmed = make_trade(status="Submitted", order_type="LMT", lmt_price=22.50)
        confirmed.order.outsideRth = False
        client = make_client()
        client.get_open_orders.side_effect = [[t], [confirmed]]
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001)
        assert exc.value.code == 0
        assert t.order.outsideRth is False  # Preserved, not changed

    def test_modify_outside_rth_false_clears_flag(self):
        """When outside_rth=False explicitly, clear the outsideRth flag even if it was True."""
        t = make_trade(status="Submitted", order_type="LMT", lmt_price=20.00)
        t.order.outsideRth = True  # Was previously set
        confirmed = make_trade(status="Submitted", order_type="LMT", lmt_price=22.50)
        confirmed.order.outsideRth = False
        client = make_client()
        client.get_open_orders.side_effect = [[t], [confirmed]]
        client.place_order = MagicMock()

        with pytest.raises(SystemExit) as exc:
            modify_order(client, 10, 12345, 22.50, "127.0.0.1", 4001, outside_rth=False)
        assert exc.value.code == 0
        assert t.order.outsideRth is False


# ─── output ─────────────────────────────────────────────

class TestOutput:
    def test_output_ok(self, capsys):
        with pytest.raises(SystemExit) as exc:
            output("ok", "done")
        assert exc.value.code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "ok"
        assert data["message"] == "done"

    def test_output_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            output("error", "fail")
        assert exc.value.code == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "error"

    def test_output_extra_fields(self, capsys):
        with pytest.raises(SystemExit) as exc:
            output("ok", "done", orderId=42, newPrice=22.5)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["orderId"] == 42
        assert data["newPrice"] == 22.5
