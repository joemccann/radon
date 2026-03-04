"""Comprehensive Interactive Brokers API client.

Wraps ``ib_insync.IB`` with connection management, order operations,
market data, portfolio queries, fill monitoring, and Flex Query support.

Usage::

    from clients.ib_client import IBClient

    with IBClient() as client:
        client.connect(client_name="ib_sync")
        positions = client.get_positions()

    # Or without context manager
    client = IBClient()
    client.connect(host="127.0.0.1", port=4001, client_id=1)
    try:
        orders = client.get_open_orders()
    finally:
        client.disconnect()
"""

from __future__ import annotations

import logging
import time
from typing import Any, List, Optional, Sequence

from ib_insync import IB, FlexReport, Option

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class IBError(Exception):
    """Base exception for all IB client errors."""


class IBConnectionError(IBError):
    """Raised when an IB connection cannot be established or is lost."""


class IBOrderError(IBError):
    """Raised when an order operation fails."""


class IBTimeoutError(IBError):
    """Raised when an operation times out."""


class IBContractError(IBError):
    """Raised when contract qualification or lookup fails."""


# ---------------------------------------------------------------------------
# Constants — re-exported from ib_connection for backward compat
# ---------------------------------------------------------------------------

CLIENT_IDS: dict = {
    "ib_order_manage": 0,
    "ib_sync": 0,
    "ib_orders": 11,
    "ib_reconcile": 0,
    "ib_order": 2,
    "ib_execute": 25,
    "ib_fill_monitor": 52,
    "exit_order_service": 60,
    "fetch_analyst_ratings": 99,
    "ib_realtime_server": 100,
}

DEFAULT_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 4001
DEFAULT_TWS_PORT = 7497

# IB error codes that are informational / non-critical
_INFO_CODES = frozenset({
    2104,  # Market data farm connection is OK
    2106,  # HMDS data farm connection is OK
    2108,  # Market data farm connection is inactive
    2158,  # Sec-def data farm connection is OK
})

# IB error codes that should be silently ignored (not user-relevant)
_IGNORE_CODES = frozenset({
    10358,  # Reuters Fundamentals subscription inactive — auto-fallback
})

# IB error codes indicating connectivity issues
_CONNECTIVITY_CODES = frozenset({
    1100,  # Connectivity between IB and TWS has been lost
    1101,  # Connectivity restored — data lost
    1102,  # Connectivity restored — data maintained
})

logger = logging.getLogger("ib_client")


# ---------------------------------------------------------------------------
# IBClient
# ---------------------------------------------------------------------------


class IBClient:
    """High-level Interactive Brokers API client.

    Wraps ``ib_insync.IB`` with:
    - Connection lifecycle (connect / disconnect / reconnect)
    - Context manager support
    - Client ID registry lookup
    - Portfolio, order, market-data, execution, and Flex Query operations
    - Structured logging
    - Retry logic for transient connection errors
    - Graceful handling of known IB error codes
    """

    def __init__(self) -> None:
        self._ib = IB()
        self.logger = logging.getLogger("ib_client")
        self._last_host: str = DEFAULT_HOST
        self._last_port: int = DEFAULT_GATEWAY_PORT
        self._last_client_id: int = 0
        self._last_timeout: int = 10
        self._last_error: Optional[tuple] = None

        # Wire up error callback
        self._ib.errorEvent += self._on_error

    # -- properties ---------------------------------------------------------

    @property
    def ib(self) -> IB:
        """Return the underlying ``ib_insync.IB`` instance."""
        return self._ib

    # -- connection lifecycle -----------------------------------------------

    def connect(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_GATEWAY_PORT,
        client_id: Optional[int] = None,
        client_name: Optional[str] = None,
        timeout: int = 10,
        max_retries: int = 1,
    ) -> None:
        """Connect to TWS / IB Gateway.

        Args:
            host: IB Gateway / TWS host.
            port: IB Gateway / TWS port.
            client_id: Explicit client ID override.
            client_name: Lookup client ID from ``CLIENT_IDS`` registry.
            timeout: Connection timeout in seconds.
            max_retries: Number of attempts before giving up.

        Raises:
            ValueError: If *client_name* is not in the registry and no
                *client_id* override is given.
            IBConnectionError: If the connection cannot be established.
        """
        # Resolve client ID
        if client_id is None and client_name is not None:
            if client_name not in CLIENT_IDS:
                raise ValueError(
                    f"Unknown client name '{client_name}'. "
                    f"Known names: {sorted(CLIENT_IDS.keys())}"
                )
            client_id = CLIENT_IDS[client_name]
        elif client_id is None:
            client_id = 0

        self._last_host = host
        self._last_port = port
        self._last_client_id = client_id
        self._last_timeout = timeout

        attempt = 0
        last_exc: Optional[Exception] = None
        while attempt < max_retries:
            attempt += 1
            try:
                self._ib.connect(host, port, clientId=client_id, timeout=timeout)
                self.logger.info(
                    "Connected to IB on %s:%s (clientId=%s)",
                    host, port, client_id,
                )
                return
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "Connection attempt %d/%d failed: %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    time.sleep(min(attempt, 5))

        raise IBConnectionError(
            f"Failed to connect to IB on {host}:{port} after "
            f"{max_retries} attempt(s): {last_exc}"
        )

    def disconnect(self) -> None:
        """Disconnect from IB. Safe to call when not connected."""
        if self._ib.isConnected():
            self._ib.disconnect()
            self.logger.info("Disconnected from IB")
        else:
            self.logger.debug("disconnect() called but already disconnected")

    def reconnect(self) -> None:
        """Disconnect and reconnect using the last connection parameters."""
        self.logger.info("Reconnecting to IB (%s:%s)", self._last_host, self._last_port)
        self.disconnect()
        self.connect(
            host=self._last_host,
            port=self._last_port,
            client_id=self._last_client_id,
            timeout=self._last_timeout,
        )

    def is_connected(self) -> bool:
        """Return ``True`` if connected to IB."""
        return self._ib.isConnected()

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> "IBClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.disconnect()

    # -- connection guard ---------------------------------------------------

    def _require_connection(self) -> None:
        """Raise if not connected."""
        if not self.is_connected():
            raise IBConnectionError("Not connected to IB. Call connect() first.")

    # -- error handling -----------------------------------------------------

    def _on_error(self, reqId: Any, errorCode: Any, errorString: str, contract: Any = None) -> None:
        """Handle IB error/warning callbacks.

        This is wired into ``ib.errorEvent``.
        """
        code = int(errorCode) if errorCode else 0

        if code in _IGNORE_CODES:
            self.logger.debug("IB info %d (ignored): %s", code, errorString)
            return

        if code in _INFO_CODES:
            self.logger.info("IB info %d: %s", code, errorString)
            return

        if code in _CONNECTIVITY_CODES:
            self.logger.warning("IB connectivity %d: %s", code, errorString)
            return

        # Store last error for operations to check
        self._last_error = (code, errorString)
        self.logger.error("IB error %d: %s", code, errorString)

    # -- portfolio operations -----------------------------------------------

    def get_positions(self) -> list:
        """Return current positions (``ib.positions()``)."""
        self._require_connection()
        return self._ib.positions()

    def get_portfolio(self, account: str = "") -> list:
        """Return portfolio items (``ib.portfolio()``)."""
        self._require_connection()
        return self._ib.portfolio(account)

    def get_account_summary(self, group: str = "", tags: Optional[List[str]] = None) -> list:
        """Return account summary values."""
        self._require_connection()
        return self._ib.accountSummary(account=group)

    # -- order operations ---------------------------------------------------

    def place_order(self, contract: Any, order: Any) -> Any:
        """Place an order and return the ``Trade`` object.

        Raises:
            IBOrderError: If the order placement fails.
        """
        self._require_connection()
        try:
            trade = self._ib.placeOrder(contract, order)
            self.logger.info(
                "Placed order: %s %s %s @ %s (orderId=%s)",
                order.action,
                order.totalQuantity,
                contract.symbol if hasattr(contract, "symbol") else contract,
                getattr(order, "lmtPrice", "MKT"),
                trade.order.orderId,
            )
            return trade
        except Exception as exc:
            raise IBOrderError(f"Failed to place order: {exc}") from exc

    def place_bracket_order(
        self,
        contract: Any,
        action: str,
        quantity: float,
        limit_price: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> list:
        """Place a bracket order (parent + take-profit + stop-loss).

        Returns:
            List of ``Trade`` objects ``[parent, take_profit, stop_loss]``.
        """
        self._require_connection()
        try:
            bracket = self._ib.bracketOrder(
                action, quantity, limit_price, take_profit_price, stop_loss_price,
            )
            trades = []
            for order in bracket:
                trade = self._ib.placeOrder(contract, order)
                trades.append(trade)
            self.logger.info(
                "Placed bracket order: %s %s %s limit=%.2f TP=%.2f SL=%.2f",
                action, quantity, contract.symbol if hasattr(contract, "symbol") else contract,
                limit_price, take_profit_price, stop_loss_price,
            )
            return trades
        except Exception as exc:
            raise IBOrderError(f"Failed to place bracket order: {exc}") from exc

    def cancel_order(self, order: Any) -> Any:
        """Cancel an open order.

        Args:
            order: The ``Order`` object to cancel.

        Raises:
            IBOrderError: If the cancellation fails.
        """
        self._require_connection()
        try:
            result = self._ib.cancelOrder(order)
            self.logger.info("Cancelled order: orderId=%s", getattr(order, "orderId", "?"))
            return result
        except Exception as exc:
            raise IBOrderError(f"Failed to cancel order: {exc}") from exc

    def modify_order(self, contract: Any, order: Any, **kwargs: Any) -> Any:
        """Modify an existing order by updating fields and re-submitting.

        Supported kwargs: ``lmt_price``, ``total_quantity``, ``aux_price``, ``tif``.

        Raises:
            IBOrderError: If the modification fails.
        """
        self._require_connection()

        # Apply modifications
        if "lmt_price" in kwargs:
            order.lmtPrice = kwargs["lmt_price"]
        if "total_quantity" in kwargs:
            order.totalQuantity = kwargs["total_quantity"]
        if "aux_price" in kwargs:
            order.auxPrice = kwargs["aux_price"]
        if "tif" in kwargs:
            order.tif = kwargs["tif"]

        try:
            trade = self._ib.placeOrder(contract, order)
            self.logger.info(
                "Modified order: orderId=%s new fields=%s",
                getattr(order, "orderId", "?"),
                kwargs,
            )
            return trade
        except Exception as exc:
            raise IBOrderError(f"Failed to modify order: {exc}") from exc

    def get_open_orders(self) -> list:
        """Return all open orders across all clients.

        Uses ``reqAllOpenOrders`` (master client sees everything).
        """
        self._require_connection()
        self._ib.reqAllOpenOrders()
        self._ib.sleep(0.5)
        return self._ib.openTrades()

    def get_open_trades(self) -> list:
        """Return currently open trades."""
        self._require_connection()
        return self._ib.openTrades()

    def get_trades(self) -> list:
        """Return all trades (open + completed) for this session."""
        self._require_connection()
        return self._ib.trades()

    def get_order_status(
        self, order_id: Optional[int] = None, perm_id: Optional[int] = None,
    ) -> Optional[Any]:
        """Look up a trade by order ID or permanent ID.

        Returns:
            The matching ``Trade`` or ``None`` if not found.
        """
        self._require_connection()
        trades = self._ib.trades()

        # Prefer perm_id (globally unique)
        if perm_id is not None:
            for trade in trades:
                if trade.order.permId == perm_id:
                    return trade

        # Fallback to order_id
        if order_id is not None:
            for trade in trades:
                if trade.order.orderId == order_id:
                    return trade

        return None

    # -- market data --------------------------------------------------------

    def get_quote(self, contract: Any, snapshot: bool = False, generic_ticks: str = "") -> Any:
        """Request market data for a contract and return the ``Ticker``.

        Args:
            contract: The contract to request data for.
            snapshot: If ``True``, request a one-time snapshot.
            generic_ticks: Comma-separated generic tick IDs.
        """
        self._require_connection()
        ticker = self._ib.reqMktData(contract, generic_ticks, snapshot, False)
        if snapshot:
            self._ib.sleep(2)
        return ticker

    def cancel_market_data(self, contract: Any) -> None:
        """Cancel streaming market data for a contract."""
        self._require_connection()
        self._ib.cancelMktData(contract)

    def set_market_data_type(self, data_type: int) -> None:
        """Set market data type (1=Live, 2=Frozen, 3=Delayed, 4=Delayed-frozen)."""
        self._require_connection()
        self._ib.reqMarketDataType(data_type)

    def get_option_chain(self, symbol: str, exchange: str = "", sec_type: str = "STK") -> list:
        """Return option chain parameters for an underlying.

        Returns a list of ``OptionChain`` objects with expirations, strikes, etc.
        """
        self._require_connection()
        # IB needs underlying conId — qualify first if needed
        return self._ib.reqSecDefOptParams(symbol, exchange, sec_type, 0)

    def get_option_price(
        self, symbol: str, expiry: str, strike: float, right: str,
        exchange: str = "SMART", currency: str = "USD",
    ) -> Any:
        """Get a quote for a specific option contract.

        Creates, qualifies, and requests market data for the option.
        """
        self._require_connection()
        contract = Option(
            symbol=symbol,
            lastTradeDateOrContractMonth=expiry,
            strike=strike,
            right=right,
            exchange=exchange,
            currency=currency,
        )
        qualified = self._ib.qualifyContracts(contract)
        if not qualified:
            raise IBContractError(
                f"Could not qualify option: {symbol} {expiry} ${strike} {right}"
            )
        ticker = self._ib.reqMktData(qualified[0], "", False, False)
        self._ib.sleep(2)
        return ticker

    def qualify_contract(self, contract: Any) -> Any:
        """Qualify a single contract, filling in IB-assigned fields.

        Raises:
            IBContractError: If the contract cannot be qualified.
        """
        self._require_connection()
        results = self._ib.qualifyContracts(contract)
        if not results:
            raise IBContractError(
                f"Failed to qualify contract: {contract}"
            )
        return results[0]

    def qualify_contracts(self, *contracts: Any) -> list:
        """Qualify multiple contracts in a single call."""
        self._require_connection()
        return self._ib.qualifyContracts(*contracts)

    # -- execution / fill operations ----------------------------------------

    def get_executions(self, exec_filter: Any = None) -> list:
        """Return recent executions, optionally filtered."""
        self._require_connection()
        if exec_filter is not None:
            return self._ib.reqExecutions(exec_filter)
        return self._ib.reqExecutions()

    def get_fills(self) -> list:
        """Return recent fills for this session."""
        self._require_connection()
        return self._ib.fills()

    def wait_for_fill(self, trade: Any, timeout: int = 60, poll_interval: float = 1.0) -> Any:
        """Wait for a trade to fill, polling at ``poll_interval``.

        Args:
            trade: The ``Trade`` object to monitor.
            timeout: Maximum seconds to wait.
            poll_interval: Seconds between status checks.

        Returns:
            The ``Trade`` object once filled.

        Raises:
            IBTimeoutError: If the trade does not fill within *timeout*.
            IBOrderError: If the trade is cancelled or enters an error state.
        """
        self._require_connection()
        elapsed = 0.0
        while elapsed < timeout:
            self._ib.sleep(poll_interval)
            elapsed += poll_interval

            status = trade.orderStatus.status
            if status == "Filled":
                self.logger.info(
                    "Order filled: orderId=%s avg=%.2f qty=%s",
                    trade.order.orderId,
                    trade.orderStatus.avgFillPrice,
                    trade.orderStatus.filled,
                )
                return trade

            if status in ("Cancelled", "ApiCancelled"):
                raise IBOrderError(
                    f"Order cancelled (orderId={trade.order.orderId}): {status}"
                )

            if status == "Inactive":
                self.logger.warning(
                    "Order inactive: orderId=%s — may be rejected",
                    trade.order.orderId,
                )

        raise IBTimeoutError(
            f"Order not filled within {timeout}s (orderId={trade.order.orderId}, "
            f"status={trade.orderStatus.status})"
        )

    # -- historical data ----------------------------------------------------

    def get_historical_data(
        self,
        contract: Any,
        duration: str = "1 D",
        bar_size: str = "1 hour",
        what_to_show: str = "TRADES",
        use_rth: bool = True,
        end_date: str = "",
        keep_up_to_date: bool = False,
    ) -> list:
        """Request historical bar data.

        Args:
            contract: The contract to request data for.
            duration: Duration string (e.g. ``"1 D"``, ``"1 W"``, ``"1 M"``).
            bar_size: Bar size setting (e.g. ``"1 hour"``, ``"1 day"``).
            what_to_show: Data type (``TRADES``, ``MIDPOINT``, ``BID``, ``ASK``).
            use_rth: Regular trading hours only.
            end_date: End date/time (empty = now).
            keep_up_to_date: Keep the bars updated.
        """
        self._require_connection()
        return self._ib.reqHistoricalData(
            contract,
            endDateTime=end_date,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=1,
            keepUpToDate=keep_up_to_date,
        )

    # -- contract details ---------------------------------------------------

    def get_contract_details(self, contract: Any) -> list:
        """Return full contract details (``ContractDetails`` list)."""
        self._require_connection()
        return self._ib.reqContractDetails(contract)

    # -- Flex Query ---------------------------------------------------------

    def run_flex_query(self, query_id: int, token: str) -> Any:
        """Execute an IB Flex Query and return the ``FlexReport``.

        Args:
            query_id: The Flex Query ID from IB Account Management.
            token: The Flex Web Service token.

        Returns:
            The ``FlexReport`` object.

        Raises:
            IBError: If the Flex query fails.
        """
        try:
            report = FlexReport(token=token, queryId=query_id)
            self.logger.info("Flex query %d executed successfully", query_id)
            return report
        except Exception as exc:
            raise IBError(f"Flex query {query_id} failed: {exc}") from exc

    # -- utility ------------------------------------------------------------

    def sleep(self, seconds: float) -> None:
        """Sleep while processing IB events (``ib.sleep()``)."""
        self._ib.sleep(seconds)
