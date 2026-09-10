"""Comprehensive Interactive Brokers API client.

Wraps ``ib_insync.IB`` with connection management, order operations,
market data, portfolio queries, fill monitoring, and Flex Query support.

This is not a Rust port. IBClient is wait-and-forward on the TWS socket;
speed work is event-driven completion with hard caps (``wait_until``),
not a language swap.

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

import asyncio
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Union

from dotenv import load_dotenv
from ib_insync import IB, FlexReport, Option

# Load root .env so IB_GATEWAY_HOST/PORT are available before defaults are computed.
# .env.ib-mode (managed by scripts/ib mode) overlays it so a single toggle there
# wins over any host/mode set in .env — no rewriting .env per session.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
load_dotenv(_PROJECT_ROOT / ".env.ib-mode", override=True)

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


# ib_insync's own `timeout` is the ONLY path that sends
# cancelHistoricalData(reqId) — an outer wait_for that fires first just
# cancels the local coroutine and leaks one of IB's ~50 simultaneous
# historical slots. So the request carries ib_insync's deadline and the
# outer guard sits this much later, firing only if that cancel itself wedges.
HISTORICAL_CANCEL_GRACE_SECS = 5.0


class IBContractError(IBError):
    """Raised when contract qualification or lookup fails."""


# ---------------------------------------------------------------------------
# Constants — re-exported from ib_connection for backward compat
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Client ID Range Allocation
# ---------------------------------------------------------------------------
# IB allows client IDs 0-999. We partition the space into non-overlapping
# zones so persistent services and on-demand scripts never collide.
#
#   0-9     Pool (persistent connections held by FastAPI IBPool)
#   10-19   WS relay (persistent, ib_realtime_server.js)
#   20-49   Subprocess scripts (on-demand, auto-allocated with retry)
#   50-69   Scanners (CRI/VCG ad-hoc pools)
#   70-89   Daemons (fill monitor, exit service)
#   90-99   Standalone CLI (manual runs)
#
POOL_ID_RANGE = (0, 9)
RELAY_ID_RANGE = (10, 19)
SUBPROCESS_ID_RANGE = (20, 49)

# Pool roles — persistent connections held by FastAPI IBPool
# IDs 3-5 avoid collisions with stale clientId 0 reservations after unclean restarts
POOL_ROLES: dict = {
    "sync": 3,
    "orders": 4,
    "data": 5,
}

# Legacy registry — kept for backward compat with scripts that use client_name
CLIENT_IDS: dict = {
    "ib_order_manage": 20,     # subprocess range (auto-allocate preferred)
    "ib_sync": 3,              # pool role
    "ib_orders": 4,            # pool role
    "ib_reconcile": 21,        # subprocess range
    "ib_order": 22,            # subprocess range
    "ib_execute": 23,          # subprocess range
    "ib_fill_monitor": 70,     # daemon range
    "exit_order_service": 71,  # daemon range
    "fetch_analyst_ratings": 90, # standalone CLI range
    "ib_place_order": 24,      # subprocess range
    "vcg_scanner": 50,         # scanner range
    "cri_scanner": 5,          # pool data role
    "ib_realtime_server": 10,  # relay range
}

DEFAULT_HOST = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
DEFAULT_GATEWAY_PORT = int(os.environ.get("IB_GATEWAY_PORT", "4001"))
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

# IB error codes for pacing violations — retry with exponential backoff
_PACING_CODES = frozenset({
    162,   # Historical market data pacing violation
    366,   # No historical data query found for ticker id
})

# Max retries per reqId for pacing violations
_MAX_PACING_RETRIES = 3

# IB error codes for invalid contracts — don't retry
_INVALID_CONTRACT_CODES = frozenset({
    200,   # No security definition has been found
    354,   # Requested market data is not subscribed
})

logger = logging.getLogger("ib_client")

# IB unset / DBL_MAX — same sentinel ib_insync and TWS use for "no value yet"
UNSET_DOUBLE = 1.7976931348623157e+308
_WAIT_POLL_S = 0.05


def is_valid_ib_number(val: Any) -> bool:
    """True when ``val`` is a finite number and not IB's unset sentinel."""
    if val is None:
        return False
    try:
        num = float(val)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(num):
        return False
    if num == UNSET_DOUBLE or num >= 1e307:
        return False
    return True


def ticker_has_quote(ticker: Any) -> bool:
    """True when a ticker has a two-sided book or a `last`.

    R-089: `close` used to satisfy this, and so did `marketPrice()`, which
    falls back to `close` itself. IB delivers the CLOSE tick (type 9) in the
    first burst after `reqMktData` — hundreds of ms before a two-sided book
    on anything illiquid, and the only prompt tick under
    `reqMarketDataType(4)`. Since `wait_until` returns on the first
    satisfied poll, the sync proceeded with bid/ask still NaN and
    `_resolve_market_price` stamped the PRIOR SESSION CLOSE as the live
    mark. Nothing in the web app reads `marketPriceIsCalculated`, so that
    stale mark rendered identically to a live one.

    A close-only ticker now burns the full cap and reports False; the
    caller falls back to close deliberately instead of by accident.
    """
    if ticker is None:
        return False
    bid = getattr(ticker, "bid", None)
    ask = getattr(ticker, "ask", None)
    if (
        is_valid_ib_number(bid)
        and is_valid_ib_number(ask)
        and float(bid) > 0
        and float(ask) > 0
    ):
        return True
    last = getattr(ticker, "last", None)
    return bool(is_valid_ib_number(last) and float(last) > 0)


def pnl_is_ready(pnl: Any) -> bool:
    """True when account or single PnL has a first valid daily or unrealized tick."""
    if pnl is None:
        return False
    return is_valid_ib_number(getattr(pnl, "dailyPnL", None)) or is_valid_ib_number(
        getattr(pnl, "unrealizedPnL", None)
    )


def pnl_daily_is_ready(pnl: Any) -> bool:
    """True only once `dailyPnL` itself is valid.

    R-111: IB commonly delivers a valid `unrealizedPnL` with `dailyPnL`
    still at DBL_MAX right after connect and outside RTH. Phase 6 consumes
    dailyPnL ONLY and writes None when unset, so waiting on the looser
    predicate intermittently blanks TODAY'S P&L.
    """
    if pnl is None:
        return False
    return is_valid_ib_number(getattr(pnl, "dailyPnL", None))


def positions_snapshot_is_ready(ended: bool, positions: Any) -> bool:
    """True when positionEnd fired AND every row carries a finite avgCost.

    R-118: the old inline predicate was `ended and all(valid avgCost ...)`.
    `connectionClosed()` empties the positions cache and `all([])` is
    vacuously True, so a socket drop mid-wait reported SUCCESS.
    """
    if not ended:
        return False
    rows = list(positions or [])
    if not rows:
        return False
    return all(is_valid_ib_number(getattr(pos, "avgCost", None)) for pos in rows)


def bind_event(event: Any, handler: Callable) -> Callable[[], None]:
    """Subscribe ``handler`` to an ib_insync Event. Returns an unbind callable.

    MagicMock and other non-Event objects raise TypeError on ``+=``; treat
    that as no event so the caller times out instead of flooring a sleep.
    """
    try:
        event += handler
    except TypeError:
        return lambda: None

    def unbind() -> None:
        try:
            event.__isub__(handler)
        except TypeError:
            pass

    return unbind


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

        # Active streaming market-data subscriptions (introspection; pruned
        # by cancel_market_data / clear_subscriptions)
        self._subscriptions: List[Dict[str, Any]] = []

        # Pacing violation retry tracking (reqId -> count)
        self._pacing_retries: Dict[int, int] = {}

        # Invalid contracts — callers can check before requesting
        self._failed_contracts: Set[Any] = set()

        # Wire up error callback
        self._ib.errorEvent += self._on_error

    # -- properties ---------------------------------------------------------

    @property
    def ib(self) -> IB:
        """Return the underlying ``ib_insync.IB`` instance."""
        return self._ib

    @property
    def failed_contracts(self) -> Set[Any]:
        """Return the set of contracts that returned invalid from IB."""
        return self._failed_contracts

    # -- connection lifecycle -----------------------------------------------

    def connect(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_GATEWAY_PORT,
        client_id: Optional[Union[int, str]] = None,
        client_name: Optional[str] = None,
        timeout: int = 3,
        max_retries: int = 1,
    ) -> None:
        """Connect to TWS / IB Gateway.

        Args:
            host: IB Gateway / TWS host.
            port: IB Gateway / TWS port.
            client_id: Explicit client ID (int), ``"auto"`` for range-based
                allocation from :data:`SUBPROCESS_ID_RANGE`, or *None*.
            client_name: Lookup client ID from ``CLIENT_IDS`` registry, or
                from ``POOL_ROLES`` if the name matches a pool role.
            timeout: Connection timeout in seconds (default 3s for fast
                fallback when Gateway is unreachable).
            max_retries: Number of attempts before giving up (applies per-ID
                for explicit IDs; ignored for ``"auto"`` which retries across
                the full range).

        Raises:
            ValueError: If *client_name* is not in the registry and no
                *client_id* override is given.
            IBConnectionError: If the connection cannot be established.
        """
        # Resolve client ID
        if client_id == "auto":
            return self._connect_auto_allocate(host, port, timeout)

        if client_id is None and client_name is not None:
            # Check pool roles first, then legacy registry
            if client_name in POOL_ROLES:
                client_id = POOL_ROLES[client_name]
            elif client_name in CLIENT_IDS:
                client_id = CLIENT_IDS[client_name]
            else:
                raise ValueError(
                    f"Unknown client name '{client_name}'. "
                    f"Known names: {sorted(set(list(CLIENT_IDS.keys()) + list(POOL_ROLES.keys())))}"
                )
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

    def _connect_auto_allocate(
        self, host: str, port: int, timeout: int,
    ) -> None:
        """Try each ID in SUBPROCESS_ID_RANGE starting from a random offset.

        On 'client id is already in use' errors, rotates to the next ID.
        Non-conflict errors abort immediately.
        """
        import random

        lo, hi = SUBPROCESS_ID_RANGE
        range_size = hi - lo + 1
        start = random.randint(lo, hi)

        for i in range(range_size):
            cid = lo + (start - lo + i) % range_size
            try:
                self._ib.connect(host, port, clientId=cid, timeout=timeout)
                self._last_host = host
                self._last_port = port
                self._last_client_id = cid
                self._last_timeout = timeout
                self.logger.info(
                    "Connected to IB on %s:%s (clientId=%s, auto-allocated)",
                    host, port, cid,
                )
                return
            except Exception as exc:
                if "client id is already in use" in str(exc).lower():
                    self.logger.debug(
                        "Client ID %d in use, trying next", cid,
                    )
                    continue
                # Non-conflict error — don't rotate, just fail
                raise IBConnectionError(
                    f"Failed to connect to IB on {host}:{port} after "
                    f"1 attempt(s): {exc}"
                ) from exc

        raise IBConnectionError(
            f"Failed to connect to IB on {host}:{port}: "
            f"all client IDs {lo}-{hi} in use"
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

    def wait_until(
        self,
        predicate: Callable[[], bool],
        timeout: float,
        poll: float = _WAIT_POLL_S,
    ) -> bool:
        """Poll ``predicate`` while draining IB events. Cap at ``timeout``.

        The cap is a ``time.monotonic()`` deadline captured at entry, because
        ``ib.sleep(secs)`` runs the event loop for AT LEAST ``secs`` and a
        blocked handler can stretch one step far past ``poll``. The loop is
        additionally bounded to ``ceil(timeout / poll)`` steps so an instant
        (mocked) sleep still terminates.
        Returns True as soon as ``predicate`` is true; False on timeout.
        """
        if timeout <= 0:
            return bool(predicate())
        if predicate():
            return True
        deadline = time.monotonic() + timeout
        max_steps = math.ceil(timeout / poll)
        for _ in range(max_steps):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._ib.sleep(min(poll, remaining))
            if predicate():
                return True
            # R-118: a socket that drops mid-wait will never satisfy the
            # predicate. Burning the full cap in silence made a degraded
            # gateway byte-identical to a healthy one.
            if not self.is_connected():
                logger.warning(
                    "wait_until abandoned after %.2fs: IB socket closed",
                    timeout - (deadline - time.monotonic()),
                )
                return False
        return bool(predicate())

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

        # Pacing violations — track per reqId, cap at max retries
        if code in _PACING_CODES:
            rid = int(reqId) if reqId else 0
            current = self._pacing_retries.get(rid, 0)
            if current < _MAX_PACING_RETRIES:
                self._pacing_retries[rid] = current + 1
                self.logger.warning(
                    "IB pacing violation %d (reqId=%s, retry %d/%d): %s",
                    code, rid, current + 1, _MAX_PACING_RETRIES, errorString,
                )
            else:
                self.logger.error(
                    "IB pacing violation %d (reqId=%s) — max retries exhausted: %s",
                    code, rid, errorString,
                )
            return

        # Invalid contracts — add to failed set, no retry
        if code in _INVALID_CONTRACT_CODES:
            if contract is not None:
                self._failed_contracts.add(contract)
            self.logger.warning(
                "IB invalid contract %d (reqId=%s): %s (contract=%s)",
                code, reqId, errorString, contract,
            )
            return

        # Store last error for operations to check
        self._last_error = (code, errorString)
        self.logger.error("IB error %d: %s", code, errorString)

    # -- subscription management --------------------------------------------
    #
    # NOTE (REL-014): IBClient deliberately has NO auto-reconnect handler on
    # disconnectedEvent. Recovery is owned by higher layers: FastAPI pool
    # acquire-time reconnect, monitor-daemon per-cycle reconnects, and the
    # relay stale-tick ladder. A backoff loop inside an ib_insync event
    # callback would block the event loop.

    def clear_subscriptions(self) -> None:
        """Clear all tracked subscriptions."""
        self._subscriptions.clear()
        self.logger.debug("Cleared all tracked subscriptions")

    # -- portfolio operations -----------------------------------------------

    def get_positions(self, refresh: bool = True) -> list:
        """Return current positions.

        By default forces a fresh ``reqPositions`` round-trip before reading
        the ib_insync cache, then waits briefly for TWS to push the updated
        snapshot. This eliminates the "fresh contract count, stale avgCost"
        window we hit when a fill lands seconds before sync: the
        ``positionEvent`` for the new fills updates ``pos.position``
        immediately but ``pos.avgCost`` lags by a tick or two while TWS
        recomputes the running VWAP server-side. ``ib.positions()`` alone
        returns the cache, so without the refresh the sync writes a
        mismatched (size_new / avg_old) pair into the portfolio snapshot. See
        feedback_ib_position_cache_stale_avgcost.md (forthcoming).

        Set ``refresh=False`` for tight read loops where you've already
        forced a refresh on a parent call and want to avoid the wait.
        The default is the safe one.

        Wait is event-driven: ``positionEndEvent`` AND finite ``avgCost``
        on every row, hard-capped at 1s. Size can arrive before VWAP;
        returning on the first ``positionEvent`` reintroduces the stale
        avgCost snapshot. No event (unit mocks) falls through to the cap.
        """
        self._require_connection()
        if refresh:
            try:
                ended = False

                def _on_end() -> None:
                    nonlocal ended
                    ended = True

                unbind = bind_event(getattr(self._ib, "positionEndEvent", None), _on_end)
                try:
                    self._ib.reqPositions()

                    def _ready() -> bool:
                        if not ended:
                            return False  # don't read the cache before end
                        return positions_snapshot_is_ready(
                            ended, self._ib.positions()
                        )

                    if not self.wait_until(_ready, timeout=1.0):
                        logger.warning(
                            "get_positions: positionEnd/avgCost wait lapsed — "
                            "returning a possibly stale cache"
                        )
                finally:
                    unbind()
            except Exception as exc:
                # Don't fail the whole sync if reqPositions itself errors —
                # fall back to the (possibly slightly stale) cache.
                self.logger.warning(
                    "reqPositions refresh failed (%s); falling back to cache",
                    exc,
                )
        return self._ib.positions()

    def get_portfolio(self, account: str = "") -> list:
        """Return portfolio items (``ib.portfolio()``)."""
        self._require_connection()
        return self._ib.portfolio(account)

    def get_account_summary(self, group: str = "", tags: Optional[List[str]] = None) -> list:
        """Return account summary values."""
        self._require_connection()
        return self._ib.accountSummary(account=group)

    def get_pnl(self, account: str = "") -> Any:
        """Request P&L for account. Returns PnL with dailyPnL, unrealizedPnL, realizedPnL."""
        self._require_connection()
        pnl = self._ib.reqPnL(account)
        if not self.wait_until(lambda: pnl_daily_is_ready(pnl), timeout=2.0):
            logger.warning("get_pnl: no dailyPnL tick within 2.0s")
        return pnl

    def cancel_pnl(self, pnl_obj: Any) -> None:
        """Cancel P&L subscription."""
        if pnl_obj:
            self._ib.cancelPnL(pnl_obj)

    def get_pnl_single(self, account: str, con_id: int) -> Any:
        """Request per-position P&L via reqPnLSingle.

        Returns a PnLSingle with dailyPnL, unrealizedPnL, realizedPnL,
        value, and position. IB correctly handles intraday additions
        (e.g., buying more contracts today) — the dailyPnL reflects
        overnight-held contracts' mark-to-close plus intraday fills.
        """
        self._require_connection()
        pnl = self._ib.reqPnLSingle(account, "", con_id)
        self.wait_until(lambda: pnl_daily_is_ready(pnl), timeout=0.5)
        return pnl

    def cancel_pnl_single(self, account: str, con_id: int) -> None:
        """Cancel per-position P&L subscription."""
        try:
            self._ib.cancelPnLSingle(account, "", con_id)
        except Exception:
            pass  # ignore cancel errors

    # -- order operations ---------------------------------------------------

    def place_order(self, contract: Any, order: Any) -> Any:
        """Place an order and return the ``Trade`` object.

        Raises:
            IBOrderError: If the order placement fails.
        """
        self._require_connection()
        # LAST funnel. Every caller is expected to check the limits itself and
        # report the refusal in its own vocabulary, but this is the one place a
        # new call site cannot go around — a guard that lives only in the caller
        # is the inversion ib_place_order.py was restructured to avoid. Pure
        # function over a process-local snapshot (app_preferences contract: no
        # inline I/O, never raises), so double-checking costs nothing. R-427/R-428.
        from order_limits import check_order_limits, check_quantity_limit  # noqa: PLC0415

        # A BAG's leg detail (strikes, ratios) is not derivable here — a
        # `comboLeg` carries a conId, not a strike — and `check_order_limits`
        # fails CLOSED on a combo whose legs it cannot read. Refusing every
        # combo at the transport would break legitimate placement, so the funnel
        # applies the bound it CAN compute (the stricter contract-quantity cap,
        # which is the fat-finger class) and leaves the leg-ratio and max-loss
        # branches to the caller, which has the legs. R-427/R-428.
        sec_type = str(getattr(contract, "secType", "") or "")
        if sec_type == "BAG":
            violation = check_quantity_limit(getattr(order, "totalQuantity", 0))
        else:
            violation = check_order_limits({
                "type": "stock" if sec_type == "STK" else "option",
                "quantity": getattr(order, "totalQuantity", 0),
                "symbol": getattr(contract, "symbol", ""),
                "limitPrice": getattr(order, "lmtPrice", None),
            })
        if violation:
            raise IBOrderError(violation["message"])
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

    def what_if_order(self, contract: Any, order: Any, timeout: float = 8.0) -> Any:
        """Read-only IB what-if margin preview — returns the ``OrderState`` from
        IB's pre-trade risk engine WITHOUT routing the order (no transmit, no
        permId).

        Bounded with ``asyncio.wait_for`` because ib_insync has no per-request
        timeout: ``whatIfOrderAsync`` blocks forever on a gateway that is logged
        in but awaiting 2FA (see ``feedback_ib_insync_no_request_timeouts``).

        Raises:
            asyncio.TimeoutError: if IB does not answer within ``timeout``.
            IBOrderError: on any other failure.
        """
        self._require_connection()
        import asyncio

        async def _run() -> Any:
            return await asyncio.wait_for(
                self._ib.whatIfOrderAsync(contract, order), timeout=timeout
            )

        try:
            return self._ib.run(_run())
        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            raise IBOrderError(f"Failed what-if margin preview: {exc}") from exc

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
        # REL-186 (R-479): the bracket funnel carries the same guards as
        # place_order. Halt first — three resting legs placed during a halt
        # are exactly what the kill switch exists to refuse.
        from trading_halt import get_halt_state, is_trading_halted  # noqa: PLC0415

        if is_trading_halted():
            reason = get_halt_state().get("reason", "manual halt")
            raise IBOrderError(f"TRADING HALTED — bracket not placed ({reason})")
        try:
            bracket = self._ib.bracketOrder(
                action, quantity, limit_price, take_profit_price, stop_loss_price,
            )
            trades = []
            for order in bracket:
                # Each leg routes through place_order, so the server-side
                # limits (quantity, notional, price sanity) apply per leg and
                # a new guard added there covers brackets automatically.
                trade = self.place_order(contract, order)
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

        # REL-186: the modify re-submit is a placement at the wire, so it takes
        # the same last-funnel bound as place_order — secType-aware, with the
        # BAG carve-out (leg detail is not derivable from comboLegs). R-427/R-428.
        from order_limits import check_order_limits, check_quantity_limit  # noqa: PLC0415

        sec_type = str(getattr(contract, "secType", "") or "")
        if sec_type == "BAG":
            violation = check_quantity_limit(getattr(order, "totalQuantity", 0))
        else:
            violation = check_order_limits({
                "type": "stock" if sec_type == "STK" else "option",
                "quantity": getattr(order, "totalQuantity", 0),
                "symbol": getattr(contract, "symbol", ""),
                "limitPrice": getattr(order, "lmtPrice", None),
            })
        if violation:
            raise IBOrderError(violation["message"])

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

    def get_managed_accounts(self) -> list:
        """Accounts visible on this session — empty means the session is
        connected-but-degraded (post-restart pre-auth), not "no orders".
        """
        self._require_connection()
        return list(self._ib.managedAccounts() or [])

    def global_cancel(self) -> None:
        """Cancel every working order across all clients (kill switch).

        ``reqGlobalCancel`` is only honored fully for the master client
        (clientId 0) — callers must connect as master first.
        """
        self._require_connection()
        self._ib.reqGlobalCancel()
        # R-110: on a freshly-connected master with no openOrder pushes yet,
        # openTrades() is empty and the predicate is true before the first
        # sleep — the kill switch returned with reqGlobalCancel still in the
        # transport buffer. Always drain at least one step.
        self._ib.sleep(_WAIT_POLL_S)
        if not self.wait_until(lambda: not self._ib.openTrades(), timeout=0.5):
            logger.warning("global_cancel: orders still working after the drain wait")

    def get_open_orders(self) -> list:
        """Return all open orders across all clients.

        Uses ``reqAllOpenOrders`` (master client sees everything).
        Waits for ``openOrderEndEvent``, hard-capped at 0.5s. Empty book
        is valid — do not return just because ``openTrades()`` is empty.
        """
        self._require_connection()
        ended = False

        def _on_end() -> None:
            nonlocal ended
            ended = True

        unbind = bind_event(getattr(self._ib, "openOrderEndEvent", None), _on_end)
        try:
            self._ib.reqAllOpenOrders()
            self.wait_until(lambda: ended, timeout=0.5)
        finally:
            unbind()
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
            self.wait_until(lambda: ticker_has_quote(ticker), timeout=2.0)
        else:
            # Track active streaming subscription
            self._subscriptions.append({
                "contract": contract,
                "generic_ticks": generic_ticks,
            })
        return ticker

    def cancel_market_data(self, contract: Any) -> None:
        """Cancel streaming market data for a contract and untrack it."""
        self._require_connection()
        self._ib.cancelMktData(contract)
        self._subscriptions = [
            sub for sub in self._subscriptions if sub["contract"] != contract
        ]

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
        self.wait_until(lambda: ticker_has_quote(ticker), timeout=2.0)
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
        timeout: float = 15.0,
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
            timeout: Seconds to wait for the initial bar set. ib_insync has
                no per-request timeout; 2FA otherwise blocks forever.
        """
        self._require_connection()

        async def _run() -> Any:
            return await asyncio.wait_for(
                self._ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime=end_date,
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=what_to_show,
                    useRTH=use_rth,
                    formatDate=1,
                    keepUpToDate=keep_up_to_date,
                    timeout=timeout,
                ),
                timeout=timeout + HISTORICAL_CANCEL_GRACE_SECS,
            )

        started = time.monotonic()
        try:
            bars = self._ib.run(_run())
        except asyncio.TimeoutError as exc:
            raise IBTimeoutError(
                f"Historical data timed out after {timeout}s "
                "(ib_insync's own cancel did not return)"
            ) from exc

        # ib_insync does not raise on its own timeout: it cancels the reqId,
        # clears the container and returns it. Empty-and-slow is that path;
        # empty-and-fast is a contract with genuinely no bars.
        returned_rows = len(bars) if bars is not None else 0
        if returned_rows == 0 and (time.monotonic() - started) >= timeout:
            if keep_up_to_date:
                # cancelHistoricalData(reqId) does not end the SUBSCRIPTION
                # ib_insync registered, so a keepUpToDate timeout leaks a
                # subscriber for the life of the pooled client.
                try:
                    self._ib.cancelHistoricalData(bars)
                except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                    self.logger.warning(
                        "historical subscription cleanup failed: %s", exc
                    )
            raise IBTimeoutError(f"Historical data timed out after {timeout}s")
        return bars

    def get_head_timestamp(
        self,
        contract: Any,
        what_to_show: str = "TRADES",
        use_rth: bool = True,
    ) -> Any:
        """Return IB's earliest available timestamp for a contract."""
        self._require_connection()
        return self._ib.reqHeadTimeStamp(
            contract,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,
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
        # ABOVE the try, deliberately. The guard and the 1025 embargo check it
        # invokes are POLICY, not transport: wrapping them made a by-design
        # block and a real token lockout indistinguishable from a Flex outage,
        # and `portfolio_performance` then degraded to the stale blotter cache
        # warning "Live IB Flex Query unavailable" for both. R-353.
        from utils.flex_send import assert_sendrequest_permitted

        assert_sendrequest_permitted(allowed=False)
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
