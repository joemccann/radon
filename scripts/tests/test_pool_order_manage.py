"""Legacy helpers operate only on the original placing session (R-039).

Production HTTP routes continue to use the original-client subprocess path.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def _not_halted(monkeypatch):
    import trading_halt
    monkeypatch.setattr(trading_halt, "is_trading_halted", lambda: False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trade(order_id=10, perm_id=12345, status="Submitted",
                client_id=26, lmt_price=5.0, total_qty=1, order_type="LMT"):
    """Create a mock IB Trade object."""
    trade = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.order.clientId = client_id
    trade.order.lmtPrice = lmt_price
    trade.order.totalQuantity = total_qty
    trade.order.orderType = order_type
    trade.order.outsideRth = False
    trade.orderStatus.status = status
    trade.contract = MagicMock()
    return trade


# ---------------------------------------------------------------------------
# Cancel via pool
# ---------------------------------------------------------------------------


class TestPoolCancelOrder:
    """Cancel orders through pool instead of subprocess."""

    @pytest.mark.asyncio
    async def test_cancel_returns_ok_when_order_disappears(self):
        """Cancel succeeds when order disappears from open orders."""
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade()
        client = MagicMock()
        # First call: finds order. Second call (after cancel): order gone
        client.get_open_orders.side_effect = [[trade], []]
        client.ib.client.clientId = 26  # original placing session

        result = await pool_cancel_order(client, order_id=10, perm_id=12345,
                                        poll_interval=0.001)
        assert result["status"] == "ok"
        client.cancel_order.assert_called_once_with(trade.order)

    @pytest.mark.asyncio
    async def test_cancel_returns_ok_when_status_cancelled(self):
        """Cancel succeeds when order status becomes Cancelled."""
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade()
        cancelled_trade = _make_trade(status="Cancelled")
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade], [cancelled_trade]]
        client.ib.client.clientId = 26

        result = await pool_cancel_order(client, order_id=10, perm_id=12345,
                                        poll_interval=0.001)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_cancel_returns_error_when_trade_not_found(self):
        """Cancel fails when trade doesn't exist."""
        from api.pool_order_manage import pool_cancel_order

        client = MagicMock()
        client.get_open_orders.return_value = []
        client.ib.client.clientId = 26

        result = await pool_cancel_order(client, order_id=10, perm_id=0,
                                        poll_interval=0.001)
        assert result["status"] == "error"
        assert "no longer working" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_cancel_returns_error_when_already_filled(self):
        """Cancel fails when order is already filled."""
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade(status="Filled")
        client = MagicMock()
        client.get_open_orders.return_value = [trade]
        client.ib.client.clientId = 26

        result = await pool_cancel_order(client, order_id=10, perm_id=12345,
                                        poll_interval=0.001)
        assert result["status"] == "error"
        assert "Filled" in result["message"]

    @pytest.mark.asyncio
    async def test_cancel_uses_perm_id_over_order_id(self):
        """permId is preferred for finding orders (globally unique)."""
        from api.pool_order_manage import pool_cancel_order

        trade_a = _make_trade(order_id=10, perm_id=111)
        trade_b = _make_trade(order_id=20, perm_id=222)
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade_a, trade_b], [trade_a]]
        client.ib.client.clientId = 26

        result = await pool_cancel_order(client, order_id=0, perm_id=222,
                                        poll_interval=0.001)
        assert result["status"] == "ok"
        client.cancel_order.assert_called_once_with(trade_b.order)

    @pytest.mark.asyncio
    async def test_cancel_refuses_a_different_client_id(self):
        """A master sees another session’s order but cannot cancel it."""
        from api.pool_order_manage import pool_cancel_order

        # Order placed by clientId=26 (subprocess), but pool is clientId=0 (master)
        trade = _make_trade(client_id=26)
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade], []]
        client.ib.client.clientId = 0  # different from the original placing client

        result = await pool_cancel_order(client, order_id=10, perm_id=12345,
                                        poll_interval=0.001)
        assert result["status"] == "error"
        assert result["code"] == "ORDER_CLIENT_MISMATCH"
        client.cancel_order.assert_not_called()
        # Never reconnect the shared session to acquire authority
        client.disconnect.assert_not_called()


# ---------------------------------------------------------------------------
# Modify via pool
# ---------------------------------------------------------------------------


class TestPoolModifyOrder:
    """Modify orders through pool instead of subprocess."""

    @pytest.mark.asyncio
    async def test_modify_price_returns_ok(self):
        """Modify price succeeds when IB confirms new price."""
        from api.pool_order_manage import pool_modify_order

        trade = _make_trade(lmt_price=5.0)
        modified = _make_trade(lmt_price=6.0)
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade], [modified]]
        client.ib.client.clientId = 26

        result = await pool_modify_order(
            client, order_id=10, perm_id=12345, new_price=6.0,
            poll_interval=0.001,
        )
        assert result["status"] == "ok"
        client.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_modify_returns_error_when_trade_not_found(self):
        """Modify fails when trade doesn't exist."""
        from api.pool_order_manage import pool_modify_order

        client = MagicMock()
        client.get_open_orders.return_value = []
        client.ib.client.clientId = 26

        result = await pool_modify_order(
            client, order_id=10, perm_id=0, new_price=6.0,
            poll_interval=0.001,
        )
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_modify_refuses_a_different_client_id(self):
        """A master sees another session’s order but cannot modify it."""
        from api.pool_order_manage import pool_modify_order

        trade = _make_trade(client_id=26, lmt_price=5.0)
        modified = _make_trade(client_id=26, lmt_price=6.0)
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade], [modified]]
        client.ib.client.clientId = 0

        result = await pool_modify_order(
            client, order_id=10, perm_id=12345, new_price=6.0,
            poll_interval=0.001,
        )
        assert result["status"] == "error"
        assert result["code"] == "ORDER_CLIENT_MISMATCH"
        client.place_order.assert_not_called()
        assert trade.order.lmtPrice == 5.0
        client.disconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_modify_clears_vol_fields_on_lmt_order(self):
        """Modify must reset volatility/volatilityType sentinels to prevent IB error 321."""
        from api.pool_order_manage import pool_modify_order

        trade = _make_trade(lmt_price=5.0)
        # Simulate IB populating VOL fields on open order snapshot
        trade.order.volatility = 0.25
        trade.order.volatilityType = 1

        modified = _make_trade(lmt_price=5.0)
        modified.order.totalQuantity = 50
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade], [modified]]
        client.ib.client.clientId = 26

        result = await pool_modify_order(
            client, order_id=10, perm_id=12345, new_quantity=50,
            poll_interval=0.001,
        )
        # Verify the VOL fields were reset before place_order was called
        assert trade.order.volatility == 1.7976931348623157e+308
        assert trade.order.volatilityType == 2147483647

    @pytest.mark.asyncio
    async def test_modify_rejects_non_limit_order(self):
        """Modify fails for non-LMT order types."""
        from api.pool_order_manage import pool_modify_order

        trade = _make_trade(order_type="MKT")
        client = MagicMock()
        client.get_open_orders.return_value = [trade]
        client.ib.client.clientId = 26

        result = await pool_modify_order(
            client, order_id=10, perm_id=12345, new_price=6.0,
            poll_interval=0.001,
        )
        assert result["status"] == "error"
        assert "LMT" in result["message"]


# ---------------------------------------------------------------------------
# §3.1 / §1#1: absence from open orders is not "Cancelled"; the outcome comes
# from this order's executions, using the shared vocabulary.
# ---------------------------------------------------------------------------


def _execution(perm_id=12345, shares=1, exec_id="e1"):
    fill = MagicMock()
    fill.execution.permId = perm_id
    fill.execution.shares = shares
    fill.execution.execId = exec_id
    fill.contract.secType = "OPT"
    return fill


def _gone_client(trade, executions=None, error=None):
    trade.contract.secType = "OPT"
    trade.fills = []
    trade.orderStatus.filled = 0
    client = MagicMock()
    client.get_open_orders.side_effect = [[trade], []]
    client.ib.client.clientId = 26
    if error is not None:
        client.get_executions.side_effect = error
    else:
        client.get_executions.return_value = executions or []
    return client


class TestPoolCancelOutcome:
    @pytest.mark.asyncio
    async def test_gone_with_execution_is_filled_not_cancelled(self):
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade(total_qty=1)
        client = _gone_client(trade, [_execution(shares=1)])

        result = await pool_cancel_order(client, order_id=10, perm_id=12345, poll_interval=0.001)

        assert result["status"] == "error"
        assert result["cancelOutcome"] == "filled"

    @pytest.mark.asyncio
    async def test_gone_with_unreadable_executions_is_unknown(self):
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade()
        client = _gone_client(trade, error=RuntimeError("gateway gone"))

        result = await pool_cancel_order(client, order_id=10, perm_id=12345, poll_interval=0.001)

        assert result["status"] == "error"
        assert result["cancelOutcome"] == "unknown"

    @pytest.mark.asyncio
    async def test_gone_with_no_execution_is_cancelled(self):
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade()
        client = _gone_client(trade, [_execution(perm_id=999)])

        result = await pool_cancel_order(client, order_id=10, perm_id=12345, poll_interval=0.001)

        assert result["status"] == "ok"
        assert result["cancelOutcome"] == "cancelled"

    @pytest.mark.asyncio
    async def test_inactive_after_cancel_settles(self):
        from api.pool_order_manage import pool_cancel_order

        trade = _make_trade()
        inactive = _make_trade(status="Inactive")
        inactive.contract.secType = "OPT"
        inactive.fills = []
        inactive.orderStatus.filled = 0
        client = MagicMock()
        client.get_open_orders.side_effect = [[trade], [inactive]]
        client.get_executions.return_value = []
        client.ib.client.clientId = 26

        result = await pool_cancel_order(client, order_id=10, perm_id=12345,
                                         max_wait=5.0, poll_interval=0.001)

        assert result["status"] == "ok"
        assert result["finalStatus"] == "Inactive"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["Inactive", "Rejected"])
    async def test_terminal_order_is_not_cancelled_or_modified(self, status):
        from api.pool_order_manage import pool_cancel_order, pool_modify_order

        trade = _make_trade(status=status)
        client = MagicMock()
        client.get_open_orders.return_value = [trade]
        client.ib.client.clientId = 26

        cancel = await pool_cancel_order(client, order_id=10, perm_id=12345, poll_interval=0.001)
        modify = await pool_modify_order(client, order_id=10, perm_id=12345, new_price=6.0,
                                         poll_interval=0.001)

        assert cancel["status"] == "error" and status in cancel["message"]
        assert modify["status"] == "error" and status in modify["message"]
        client.cancel_order.assert_not_called()
        client.place_order.assert_not_called()
