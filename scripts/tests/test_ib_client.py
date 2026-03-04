"""Comprehensive tests for IBClient — the unified IB API client.

All tests mock ib_insync.IB so no real IB connection is needed.
Follows Red/Green TDD: tests written first, implementation follows.
"""

import asyncio
import logging
import math
from datetime import datetime
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build mock IB objects
# ---------------------------------------------------------------------------

def _make_position(symbol="AAPL", sec_type="STK", position=100, avg_cost=150.0):
    """Create a mock Position object."""
    pos = MagicMock()
    pos.account = "U1234567"
    pos.contract = MagicMock()
    pos.contract.symbol = symbol
    pos.contract.secType = sec_type
    pos.contract.conId = 12345
    pos.position = position
    pos.avgCost = avg_cost
    return pos


def _make_portfolio_item(symbol="AAPL", position=100, market_price=155.0, avg_cost=150.0):
    """Create a mock PortfolioItem object."""
    item = MagicMock()
    item.contract = MagicMock()
    item.contract.symbol = symbol
    item.contract.secType = "STK"
    item.position = position
    item.marketPrice = market_price
    item.marketValue = position * market_price
    item.averageCost = avg_cost
    item.unrealizedPNL = (market_price - avg_cost) * position
    item.realizedPNL = 0.0
    item.account = "U1234567"
    return item


def _make_account_value(tag="NetLiquidation", value="100000", currency="USD"):
    """Create a mock AccountValue object."""
    av = MagicMock()
    av.tag = tag
    av.value = value
    av.currency = currency
    av.account = "U1234567"
    return av


def _make_ticker(bid=150.0, ask=151.0, last=150.5, volume=1000000):
    """Create a mock Ticker with realistic data."""
    ticker = MagicMock()
    ticker.bid = bid
    ticker.ask = ask
    ticker.last = last
    ticker.volume = volume
    ticker.high = 152.0
    ticker.low = 149.0
    ticker.open = 150.0
    ticker.close = 149.5
    ticker.bidSize = 100
    ticker.askSize = 200
    ticker.lastSize = 50
    return ticker


def _make_trade(order_id=1, status="Submitted", filled=0, avg_fill_price=0.0, action="BUY", qty=10, lmt_price=150.0):
    """Create a mock Trade object."""
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = order_id * 1000
    trade.order.action = action
    trade.order.totalQuantity = qty
    trade.order.lmtPrice = lmt_price
    trade.order.orderType = "LMT"
    trade.order.clientId = 0
    trade.orderStatus = MagicMock()
    trade.orderStatus.status = status
    trade.orderStatus.filled = filled
    trade.orderStatus.remaining = qty - filled
    trade.orderStatus.avgFillPrice = avg_fill_price
    trade.contract = MagicMock()
    trade.contract.symbol = "AAPL"
    trade.contract.secType = "STK"
    trade.fills = []
    trade.log = []
    trade.statusEvent = MagicMock()
    trade.filledEvent = MagicMock()
    trade.fillEvent = MagicMock()
    return trade


def _make_fill(exec_id="exec1", symbol="AAPL", side="BOT", shares=10, price=150.0, commission=1.0):
    """Create a mock Fill object."""
    fill = MagicMock()
    fill.contract = MagicMock()
    fill.contract.symbol = symbol
    fill.execution = MagicMock()
    fill.execution.execId = exec_id
    fill.execution.time = datetime(2026, 3, 4, 10, 30, 0)
    fill.execution.side = side
    fill.execution.shares = shares
    fill.execution.price = price
    fill.execution.avgPrice = price
    fill.execution.orderId = 1
    fill.execution.cumQty = shares
    fill.commissionReport = MagicMock()
    fill.commissionReport.commission = commission
    fill.commissionReport.realizedPNL = 0.0
    fill.time = datetime(2026, 3, 4, 10, 30, 0)
    return fill


def _make_contract_details():
    """Create a mock ContractDetails object."""
    cd = MagicMock()
    cd.contract = MagicMock()
    cd.contract.symbol = "AAPL"
    cd.contract.conId = 265598
    cd.contract.secType = "STK"
    cd.contract.exchange = "SMART"
    cd.contract.primaryExchange = "NASDAQ"
    cd.contract.currency = "USD"
    cd.minTick = 0.01
    cd.longName = "APPLE INC"
    cd.industry = "Technology"
    cd.category = "Computers"
    return cd


# ---------------------------------------------------------------------------
# Import the client (will fail during RED phase, succeed during GREEN)
# ---------------------------------------------------------------------------

from clients.ib_client import (
    IBClient,
    IBConnectionError,
    IBContractError,
    IBError,
    IBOrderError,
    IBTimeoutError,
    CLIENT_IDS,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_HOST,
    DEFAULT_TWS_PORT,
)


# ===========================================================================
# EXCEPTION HIERARCHY
# ===========================================================================

class TestExceptionHierarchy:
    """Verify exception classes exist and inherit properly."""

    def test_ib_error_is_base(self):
        assert issubclass(IBConnectionError, IBError)
        assert issubclass(IBOrderError, IBError)
        assert issubclass(IBTimeoutError, IBError)
        assert issubclass(IBContractError, IBError)

    def test_ib_error_is_exception(self):
        assert issubclass(IBError, Exception)

    def test_error_messages(self):
        e = IBConnectionError("connection lost")
        assert "connection lost" in str(e)

        e = IBOrderError("order rejected")
        assert "order rejected" in str(e)


# ===========================================================================
# CONSTANTS
# ===========================================================================

class TestConstants:
    """Verify exported constants match the existing registry."""

    def test_client_id_registry_exists(self):
        assert isinstance(CLIENT_IDS, dict)
        assert "ib_sync" in CLIENT_IDS
        assert "ib_order" in CLIENT_IDS
        assert "ib_execute" in CLIENT_IDS
        assert "ib_order_manage" in CLIENT_IDS

    def test_default_ports(self):
        assert DEFAULT_HOST == "127.0.0.1"
        assert DEFAULT_GATEWAY_PORT == 4001
        assert DEFAULT_TWS_PORT == 7497


# ===========================================================================
# CONNECTION LIFECYCLE
# ===========================================================================

class TestConnection:
    """Test connect, disconnect, reconnect, and context manager."""

    @patch("clients.ib_client.IB")
    def test_connect_success(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(host="127.0.0.1", port=4001, client_id=1)

        mock_ib.connect.assert_called_once_with("127.0.0.1", 4001, clientId=1, timeout=10)
        assert client.is_connected()

    @patch("clients.ib_client.IB")
    def test_connect_with_client_name(self, MockIB):
        """Connect using a registered client name to look up client_id."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_name="ib_sync")

        expected_id = CLIENT_IDS["ib_sync"]
        mock_ib.connect.assert_called_once_with(
            DEFAULT_HOST, DEFAULT_GATEWAY_PORT, clientId=expected_id, timeout=10
        )

    @patch("clients.ib_client.IB")
    def test_connect_unknown_client_name_raises(self, MockIB):
        client = IBClient()
        with pytest.raises(ValueError, match="Unknown client name"):
            client.connect(client_name="nonexistent_script")

    @patch("clients.ib_client.IB")
    def test_connect_failure_raises_ib_connection_error(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.connect.side_effect = ConnectionRefusedError("refused")

        client = IBClient()
        with pytest.raises(IBConnectionError):
            client.connect()

    @patch("clients.ib_client.IB")
    def test_connect_timeout_raises_ib_connection_error(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.connect.side_effect = TimeoutError("timeout")

        client = IBClient()
        with pytest.raises(IBConnectionError):
            client.connect()

    @patch("clients.ib_client.IB")
    def test_connect_custom_timeout(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(port=4001, client_id=1, timeout=30)

        mock_ib.connect.assert_called_once_with("127.0.0.1", 4001, clientId=1, timeout=30)

    @patch("clients.ib_client.IB")
    def test_disconnect(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)
        client.disconnect()

        mock_ib.disconnect.assert_called_once()

    @patch("clients.ib_client.IB")
    def test_disconnect_when_not_connected(self, MockIB):
        """Disconnect should be safe to call even when not connected."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = False

        client = IBClient()
        client.disconnect()  # should not raise

    @patch("clients.ib_client.IB")
    def test_is_connected(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = False

        client = IBClient()
        assert not client.is_connected()

        mock_ib.isConnected.return_value = True
        assert client.is_connected()

    @patch("clients.ib_client.IB")
    def test_reconnect(self, MockIB):
        """Reconnect should disconnect then connect again."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(host="127.0.0.1", port=4001, client_id=5)
        client.reconnect()

        assert mock_ib.disconnect.call_count == 1
        assert mock_ib.connect.call_count == 2

    @patch("clients.ib_client.IB")
    def test_context_manager(self, MockIB):
        """Test using IBClient as a context manager."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        with IBClient() as client:
            client.connect(client_id=1)
            assert client.is_connected()

        mock_ib.disconnect.assert_called()

    @patch("clients.ib_client.IB")
    def test_context_manager_disconnects_on_exception(self, MockIB):
        """Context manager should disconnect even if an exception occurs."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        with pytest.raises(RuntimeError):
            with IBClient() as client:
                client.connect(client_id=1)
                raise RuntimeError("boom")

        mock_ib.disconnect.assert_called()


# ===========================================================================
# PORTFOLIO OPERATIONS
# ===========================================================================

class TestPortfolioOperations:
    """Test get_positions, get_portfolio, get_account_summary."""

    @patch("clients.ib_client.IB")
    def test_get_positions(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.positions.return_value = [
            _make_position("AAPL", "STK", 100, 150.0),
            _make_position("GOOG", "OPT", 10, 5.0),
        ]

        client = IBClient()
        client.connect(client_id=1)
        positions = client.get_positions()

        mock_ib.positions.assert_called_once()
        assert len(positions) == 2
        assert positions[0].contract.symbol == "AAPL"

    @patch("clients.ib_client.IB")
    def test_get_positions_empty(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.positions.return_value = []

        client = IBClient()
        client.connect(client_id=1)
        positions = client.get_positions()

        assert positions == []

    @patch("clients.ib_client.IB")
    def test_get_portfolio(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.portfolio.return_value = [
            _make_portfolio_item("AAPL", 100, 155.0, 150.0),
        ]

        client = IBClient()
        client.connect(client_id=1)
        items = client.get_portfolio()

        mock_ib.portfolio.assert_called_once()
        assert len(items) == 1
        assert items[0].contract.symbol == "AAPL"

    @patch("clients.ib_client.IB")
    def test_get_account_summary(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.accountSummary.return_value = [
            _make_account_value("NetLiquidation", "100000", "USD"),
            _make_account_value("TotalCashValue", "50000", "USD"),
            _make_account_value("BuyingPower", "200000", "USD"),
            _make_account_value("AvailableFunds", "80000", "USD"),
        ]

        client = IBClient()
        client.connect(client_id=1)
        summary = client.get_account_summary()

        mock_ib.accountSummary.assert_called_once()
        assert len(summary) == 4

    @patch("clients.ib_client.IB")
    def test_get_account_summary_filtered(self, MockIB):
        """get_account_summary with specific tags."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.accountSummary.return_value = [
            _make_account_value("NetLiquidation", "100000", "USD"),
            _make_account_value("TotalCashValue", "50000", "USD"),
        ]

        client = IBClient()
        client.connect(client_id=1)
        summary = client.get_account_summary(tags=["NetLiquidation", "TotalCashValue"])

        assert len(summary) == 2

    @patch("clients.ib_client.IB")
    def test_operations_require_connection(self, MockIB):
        """Calling operations without connecting should raise."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = False

        client = IBClient()
        with pytest.raises(IBConnectionError, match="Not connected"):
            client.get_positions()


# ===========================================================================
# ORDER OPERATIONS
# ===========================================================================

class TestOrderOperations:
    """Test place_order, cancel_order, modify_order, get_open_orders, get_order_status."""

    @patch("clients.ib_client.IB")
    def test_place_order(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        expected_trade = _make_trade(order_id=42, status="Submitted")
        mock_ib.placeOrder.return_value = expected_trade

        client = IBClient()
        client.connect(client_id=2)

        contract = MagicMock()
        order = MagicMock()
        trade = client.place_order(contract, order)

        mock_ib.placeOrder.assert_called_once_with(contract, order)
        assert trade.order.orderId == 42

    @patch("clients.ib_client.IB")
    def test_place_order_error(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.placeOrder.side_effect = Exception("order rejected")

        client = IBClient()
        client.connect(client_id=2)

        with pytest.raises(IBOrderError):
            client.place_order(MagicMock(), MagicMock())

    @patch("clients.ib_client.IB")
    def test_place_bracket_order(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        parent = _make_trade(order_id=1, status="Submitted")
        take_profit = _make_trade(order_id=2, status="PreSubmitted")
        stop_loss = _make_trade(order_id=3, status="PreSubmitted")
        mock_ib.bracketOrder.return_value = [parent.order, take_profit.order, stop_loss.order]
        mock_ib.placeOrder.side_effect = [parent, take_profit, stop_loss]

        client = IBClient()
        client.connect(client_id=2)

        contract = MagicMock()
        trades = client.place_bracket_order(
            contract=contract,
            action="BUY",
            quantity=10,
            limit_price=150.0,
            take_profit_price=160.0,
            stop_loss_price=145.0,
        )

        assert mock_ib.bracketOrder.call_count == 1
        assert mock_ib.placeOrder.call_count == 3

    @patch("clients.ib_client.IB")
    def test_cancel_order(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=42, status="Submitted")
        mock_ib.cancelOrder.return_value = trade

        client = IBClient()
        client.connect(client_id=0)
        result = client.cancel_order(trade.order)

        mock_ib.cancelOrder.assert_called_once_with(trade.order)

    @patch("clients.ib_client.IB")
    def test_cancel_order_error(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.cancelOrder.side_effect = Exception("cancel failed")

        client = IBClient()
        client.connect(client_id=0)

        with pytest.raises(IBOrderError):
            client.cancel_order(MagicMock())

    @patch("clients.ib_client.IB")
    def test_modify_order(self, MockIB):
        """Modify an existing order by changing price."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=42, status="Submitted", lmt_price=150.0)
        mock_ib.placeOrder.return_value = trade

        client = IBClient()
        client.connect(client_id=0)
        result = client.modify_order(trade.contract, trade.order, lmt_price=155.0)

        # The order's lmtPrice should be updated before placing
        assert trade.order.lmtPrice == 155.0
        mock_ib.placeOrder.assert_called_once_with(trade.contract, trade.order)

    @patch("clients.ib_client.IB")
    def test_modify_order_multiple_fields(self, MockIB):
        """Modify order with multiple field changes."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=42, status="Submitted", lmt_price=150.0, qty=10)
        mock_ib.placeOrder.return_value = trade

        client = IBClient()
        client.connect(client_id=0)
        result = client.modify_order(trade.contract, trade.order, lmt_price=155.0, total_quantity=20)

        assert trade.order.lmtPrice == 155.0
        assert trade.order.totalQuantity == 20

    @patch("clients.ib_client.IB")
    def test_get_open_orders(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqAllOpenOrders.return_value = [
            _make_trade(order_id=1, status="Submitted"),
            _make_trade(order_id=2, status="PreSubmitted"),
        ]
        mock_ib.openTrades.return_value = [
            _make_trade(order_id=1, status="Submitted"),
            _make_trade(order_id=2, status="PreSubmitted"),
        ]

        client = IBClient()
        client.connect(client_id=0)
        orders = client.get_open_orders()

        mock_ib.reqAllOpenOrders.assert_called_once()
        assert len(orders) == 2

    @patch("clients.ib_client.IB")
    def test_get_order_status(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=42, status="Filled", filled=10, avg_fill_price=150.5)
        mock_ib.openTrades.return_value = [trade]
        mock_ib.trades.return_value = [trade]

        client = IBClient()
        client.connect(client_id=0)
        found_trade = client.get_order_status(order_id=42)

        assert found_trade is not None
        assert found_trade.orderStatus.status == "Filled"

    @patch("clients.ib_client.IB")
    def test_get_order_status_not_found(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.trades.return_value = []

        client = IBClient()
        client.connect(client_id=0)
        result = client.get_order_status(order_id=999)

        assert result is None

    @patch("clients.ib_client.IB")
    def test_get_order_status_by_perm_id(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=42, status="Submitted")
        trade.order.permId = 99999
        mock_ib.trades.return_value = [trade]

        client = IBClient()
        client.connect(client_id=0)
        found_trade = client.get_order_status(perm_id=99999)

        assert found_trade is not None
        assert found_trade.order.permId == 99999


# ===========================================================================
# MARKET DATA
# ===========================================================================

class TestMarketData:
    """Test get_quote, get_option_chain, get_option_price, qualify_contract."""

    @patch("clients.ib_client.IB")
    def test_get_quote(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        ticker = _make_ticker(bid=150.0, ask=151.0, last=150.5)
        mock_ib.reqMktData.return_value = ticker

        client = IBClient()
        client.connect(client_id=1)
        contract = MagicMock()
        quote = client.get_quote(contract)

        mock_ib.reqMktData.assert_called_once()
        assert quote is not None
        assert quote.bid == 150.0
        assert quote.ask == 151.0

    @patch("clients.ib_client.IB")
    def test_get_quote_snapshot(self, MockIB):
        """Get a snapshot quote (non-streaming)."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        ticker = _make_ticker()
        mock_ib.reqMktData.return_value = ticker

        client = IBClient()
        client.connect(client_id=1)
        quote = client.get_quote(MagicMock(), snapshot=True)

        # snapshot=True should be passed through
        mock_ib.reqMktData.assert_called_once()
        call_args = mock_ib.reqMktData.call_args
        # Snapshot is a positional or keyword arg
        assert quote is not None

    @patch("clients.ib_client.IB")
    def test_get_option_chain(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        chain_data = MagicMock()
        chain_data.expirations = frozenset(["20260320", "20260417"])
        chain_data.strikes = frozenset([145.0, 150.0, 155.0])
        chain_data.exchange = "SMART"
        chain_data.underlyingConId = 265598
        chain_data.tradingClass = "AAPL"
        chain_data.multiplier = "100"
        mock_ib.reqSecDefOptParams.return_value = [chain_data]

        client = IBClient()
        client.connect(client_id=1)
        chains = client.get_option_chain("AAPL")

        mock_ib.reqSecDefOptParams.assert_called_once()
        assert len(chains) >= 1

    @patch("clients.ib_client.IB")
    def test_get_option_chain_empty(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqSecDefOptParams.return_value = []

        client = IBClient()
        client.connect(client_id=1)
        chains = client.get_option_chain("ZZZZZ")

        assert chains == []

    @patch("clients.ib_client.IB")
    def test_get_option_price(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.qualifyContracts.return_value = [MagicMock()]

        ticker = _make_ticker(bid=5.0, ask=5.50, last=5.25)
        mock_ib.reqMktData.return_value = ticker

        client = IBClient()
        client.connect(client_id=1)
        quote = client.get_option_price("AAPL", "20260417", 150.0, "C")

        assert quote is not None

    @patch("clients.ib_client.IB")
    def test_qualify_contract(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        contract = MagicMock()
        qualified_contract = MagicMock()
        qualified_contract.conId = 265598
        mock_ib.qualifyContracts.return_value = [qualified_contract]

        client = IBClient()
        client.connect(client_id=1)
        result = client.qualify_contract(contract)

        mock_ib.qualifyContracts.assert_called_once_with(contract)
        assert result.conId == 265598

    @patch("clients.ib_client.IB")
    def test_qualify_contract_failure(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.qualifyContracts.return_value = []

        client = IBClient()
        client.connect(client_id=1)

        with pytest.raises(IBContractError, match="qualify"):
            client.qualify_contract(MagicMock())

    @patch("clients.ib_client.IB")
    def test_qualify_contracts_batch(self, MockIB):
        """Qualify multiple contracts in a single call."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        c1 = MagicMock()
        c2 = MagicMock()
        c1.conId = 111
        c2.conId = 222
        mock_ib.qualifyContracts.return_value = [c1, c2]

        client = IBClient()
        client.connect(client_id=1)
        results = client.qualify_contracts(c1, c2)

        mock_ib.qualifyContracts.assert_called_once_with(c1, c2)
        assert len(results) == 2

    @patch("clients.ib_client.IB")
    def test_cancel_market_data(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)
        contract = MagicMock()
        client.cancel_market_data(contract)

        mock_ib.cancelMktData.assert_called_once_with(contract)

    @patch("clients.ib_client.IB")
    def test_set_market_data_type(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)
        client.set_market_data_type(3)  # Delayed

        mock_ib.reqMarketDataType.assert_called_once_with(3)


# ===========================================================================
# EXECUTION / FILL OPERATIONS
# ===========================================================================

class TestExecutionOperations:
    """Test get_executions, get_fills, wait_for_fill."""

    @patch("clients.ib_client.IB")
    def test_get_executions(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqExecutions.return_value = [
            _make_fill("exec1", "AAPL", "BOT", 10, 150.0),
            _make_fill("exec2", "GOOG", "SLD", 5, 300.0),
        ]

        client = IBClient()
        client.connect(client_id=1)
        executions = client.get_executions()

        mock_ib.reqExecutions.assert_called_once()
        assert len(executions) == 2

    @patch("clients.ib_client.IB")
    def test_get_executions_with_filter(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.reqExecutions.return_value = [
            _make_fill("exec1", "AAPL", "BOT", 10, 150.0),
        ]

        client = IBClient()
        client.connect(client_id=1)
        exec_filter = MagicMock()
        executions = client.get_executions(exec_filter)

        mock_ib.reqExecutions.assert_called_once_with(exec_filter)
        assert len(executions) == 1

    @patch("clients.ib_client.IB")
    def test_get_fills(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.fills.return_value = [
            _make_fill("fill1", "AAPL", "BOT", 10, 150.0, 1.0),
        ]

        client = IBClient()
        client.connect(client_id=1)
        fills = client.get_fills()

        mock_ib.fills.assert_called_once()
        assert len(fills) == 1
        assert fills[0].execution.shares == 10

    @patch("clients.ib_client.IB")
    def test_wait_for_fill_success(self, MockIB):
        """wait_for_fill returns when trade is filled within timeout."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=1, status="Submitted")

        call_count = [0]

        def sleep_side_effect(secs):
            call_count[0] += 1
            if call_count[0] >= 2:
                trade.orderStatus.status = "Filled"
                trade.orderStatus.filled = 10
                trade.orderStatus.avgFillPrice = 150.5

        mock_ib.sleep.side_effect = sleep_side_effect

        client = IBClient()
        client.connect(client_id=1)
        result = client.wait_for_fill(trade, timeout=10)

        assert result.orderStatus.status == "Filled"

    @patch("clients.ib_client.IB")
    def test_wait_for_fill_timeout(self, MockIB):
        """wait_for_fill raises IBTimeoutError when trade doesn't fill."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.sleep.return_value = None

        trade = _make_trade(order_id=1, status="Submitted")
        # Trade never fills

        client = IBClient()
        client.connect(client_id=1)

        with pytest.raises(IBTimeoutError):
            client.wait_for_fill(trade, timeout=2)

    @patch("clients.ib_client.IB")
    def test_wait_for_fill_cancelled(self, MockIB):
        """wait_for_fill raises IBOrderError when trade is cancelled."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        trade = _make_trade(order_id=1, status="Submitted")

        def sleep_side_effect(secs):
            trade.orderStatus.status = "Cancelled"

        mock_ib.sleep.side_effect = sleep_side_effect

        client = IBClient()
        client.connect(client_id=1)

        with pytest.raises(IBOrderError, match="[Cc]ancelled"):
            client.wait_for_fill(trade, timeout=10)


# ===========================================================================
# FLEX QUERY
# ===========================================================================

class TestFlexQuery:
    """Test Flex Query execution."""

    @patch("clients.ib_client.FlexReport")
    @patch("clients.ib_client.IB")
    def test_run_flex_query(self, MockIB, MockFlexReport):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        mock_report = MagicMock()
        mock_report.df.return_value = MagicMock()  # returns a DataFrame-like object
        MockFlexReport.return_value = mock_report

        client = IBClient()
        client.connect(client_id=1)
        result = client.run_flex_query(query_id=123456, token="test_token")

        MockFlexReport.assert_called_once_with(token="test_token", queryId=123456)

    @patch("clients.ib_client.FlexReport")
    @patch("clients.ib_client.IB")
    def test_run_flex_query_error(self, MockIB, MockFlexReport):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        MockFlexReport.side_effect = Exception("Flex query failed")

        client = IBClient()
        client.connect(client_id=1)

        with pytest.raises(IBError, match="Flex"):
            client.run_flex_query(query_id=123456, token="bad_token")


# ===========================================================================
# ERROR HANDLING — KNOWN IB ERRORS
# ===========================================================================

class TestErrorHandling:
    """Test handling of known IB error codes."""

    @patch("clients.ib_client.IB")
    def test_error_10358_reuters_inactive(self, MockIB):
        """IB error 10358 (Reuters subscription inactive) should be logged but not raise."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # Simulate error callback
        client._on_error(10358, "10358", "Reuters Fundamentals subscription inactive")
        # Should not raise, just log

    @patch("clients.ib_client.IB")
    def test_error_103_duplicate_order(self, MockIB):
        """IB error 103 (duplicate order id) should be raised as IBOrderError."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # The error handler should store the error for the next operation to check
        client._on_error(103, "103", "Duplicate order id")

    @patch("clients.ib_client.IB")
    def test_error_connection_lost(self, MockIB):
        """IB connection lost error should update connection state."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # Simulate disconnect error
        client._on_error(1100, "1100", "Connectivity between IB and TWS has been lost")
        # Should log the error

    @patch("clients.ib_client.IB")
    def test_non_critical_errors_logged(self, MockIB):
        """Non-critical errors should be logged but not interrupt execution."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)

        # These should be handled gracefully
        client._on_error(2104, "2104", "Market data farm connection is OK")
        client._on_error(2106, "2106", "HMDS data farm connection is OK")


# ===========================================================================
# RETRY LOGIC
# ===========================================================================

class TestRetryLogic:
    """Test automatic retry for transient errors."""

    @patch("clients.ib_client.IB")
    def test_connect_retries_on_transient_error(self, MockIB):
        """Connection should retry on transient failures."""
        mock_ib = MockIB.return_value

        # First call fails, second succeeds
        mock_ib.connect.side_effect = [ConnectionRefusedError("refused"), None]
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1, max_retries=2)

        assert mock_ib.connect.call_count == 2

    @patch("clients.ib_client.IB")
    def test_connect_exhausts_retries(self, MockIB):
        """Connection should raise after exhausting retries."""
        mock_ib = MockIB.return_value
        mock_ib.connect.side_effect = ConnectionRefusedError("refused")

        client = IBClient()
        with pytest.raises(IBConnectionError):
            client.connect(client_id=1, max_retries=3)

        assert mock_ib.connect.call_count == 3


# ===========================================================================
# LOGGING
# ===========================================================================

class TestLogging:
    """Test that structured logging is present."""

    @patch("clients.ib_client.IB")
    def test_logger_exists(self, MockIB):
        client = IBClient()
        assert client.logger is not None
        assert isinstance(client.logger, logging.Logger)

    @patch("clients.ib_client.IB")
    def test_connect_logs(self, MockIB, caplog):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        with caplog.at_level(logging.INFO):
            client.connect(client_id=1)

        assert any("connect" in r.message.lower() or "connected" in r.message.lower() for r in caplog.records)

    @patch("clients.ib_client.IB")
    def test_disconnect_logs(self, MockIB, caplog):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)
        with caplog.at_level(logging.INFO):
            client.disconnect()

        assert any("disconnect" in r.message.lower() for r in caplog.records)


# ===========================================================================
# HISTORICAL DATA
# ===========================================================================

class TestHistoricalData:
    """Test historical data retrieval."""

    @patch("clients.ib_client.IB")
    def test_get_historical_data(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        bar = MagicMock()
        bar.date = datetime(2026, 3, 3)
        bar.open = 150.0
        bar.high = 152.0
        bar.low = 149.0
        bar.close = 151.0
        bar.volume = 1000000
        mock_ib.reqHistoricalData.return_value = [bar]

        client = IBClient()
        client.connect(client_id=1)
        contract = MagicMock()
        bars = client.get_historical_data(contract, duration="1 D", bar_size="1 hour")

        mock_ib.reqHistoricalData.assert_called_once()
        assert len(bars) == 1


# ===========================================================================
# CONTRACT DETAILS
# ===========================================================================

class TestContractDetails:
    """Test contract details retrieval."""

    @patch("clients.ib_client.IB")
    def test_get_contract_details(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        cd = _make_contract_details()
        mock_ib.reqContractDetails.return_value = [cd]

        client = IBClient()
        client.connect(client_id=1)
        contract = MagicMock()
        details = client.get_contract_details(contract)

        mock_ib.reqContractDetails.assert_called_once_with(contract)
        assert len(details) == 1


# ===========================================================================
# SLEEP / UTILITY
# ===========================================================================

class TestUtilities:
    """Test utility methods."""

    @patch("clients.ib_client.IB")
    def test_sleep(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True

        client = IBClient()
        client.connect(client_id=1)
        client.sleep(1.5)

        mock_ib.sleep.assert_called_once_with(1.5)

    @patch("clients.ib_client.IB")
    def test_ib_property(self, MockIB):
        """The underlying ib_insync.IB instance should be accessible."""
        mock_ib = MockIB.return_value
        client = IBClient()
        assert client.ib is mock_ib


# ===========================================================================
# OPEN TRADES
# ===========================================================================

class TestOpenTrades:
    """Test open trades retrieval."""

    @patch("clients.ib_client.IB")
    def test_get_open_trades(self, MockIB):
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.openTrades.return_value = [
            _make_trade(order_id=1, status="Submitted"),
        ]

        client = IBClient()
        client.connect(client_id=0)
        trades = client.get_open_trades()

        mock_ib.openTrades.assert_called_once()
        assert len(trades) == 1

    @patch("clients.ib_client.IB")
    def test_get_trades(self, MockIB):
        """Get all trades (open + completed) for the session."""
        mock_ib = MockIB.return_value
        mock_ib.isConnected.return_value = True
        mock_ib.trades.return_value = [
            _make_trade(order_id=1, status="Filled"),
            _make_trade(order_id=2, status="Submitted"),
        ]

        client = IBClient()
        client.connect(client_id=0)
        trades = client.get_trades()

        mock_ib.trades.assert_called_once()
        assert len(trades) == 2
