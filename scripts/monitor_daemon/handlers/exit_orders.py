#!/usr/bin/env python3
"""
Exit Orders Handler - Places pending exit orders when IB will accept them.

IB rejects limit orders >40% from current market price. This handler:
- Monitors pending exit orders in the Turso journal
- Checks current market prices
- Places orders when they're within the 40% threshold
- Updates the Turso journal with order IDs
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ib_insync import Option, LimitOrder

from .base import BaseHandler
from clients.ib_client import IBClient, DEFAULT_HOST

logger = logging.getLogger(__name__)

DEFAULT_IB_PORT = 4001
# "auto" rotates across SUBPROCESS_ID_RANGE on connect. See
# fill_monitor.py for the rationale (CLOSE_WAIT survival on prev cycle's
# socket, feedback_ib_client_id_ranges.md).
DEFAULT_CLIENT_ID: int | str = "auto"

try:
    from db.client import get_db  # type: ignore
except ImportError:  # pragma: no cover - DB layer optional in unit tests
    get_db = None  # type: ignore[assignment]


class ExitOrdersHandler(BaseHandler):
    """Place pending exit orders when IB will accept them."""

    name = "exit_orders"
    interval_seconds = 300  # Check every 5 minutes
    service_name = "exit-orders"  # structural heartbeat via BaseHandler.run()

    def __init__(
        self,
        db: Any = None,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: "int | str" = DEFAULT_CLIENT_ID,
        max_gap_pct: float = 0.40
    ):
        super().__init__()
        self.db = db
        self.ib_port = ib_port
        self.client_id = client_id
        self.max_gap_pct = max_gap_pct
        # Orders that transmitted to IB but whose journal UPDATE failed —
        # the row still reads PENDING, so without this guard the next cycle
        # would place the SAME live order again (T-010). Keyed
        # (journal_trade_id, order_type) -> live order_id; healed (journal
        # marked PLACED) on a later cycle once the DB write succeeds.
        self._unrecorded_placements: Dict[tuple, int] = {}

    def _open_db(self) -> Any:
        if self.db is not None:
            return self.db
        if get_db is None:
            raise RuntimeError("journal DB unavailable")
        return get_db()

    @staticmethod
    def _row_value(row: Any, index: int, name: str) -> Any:
        if isinstance(row, (tuple, list)):
            return row[index] if len(row) > index else None
        return getattr(row, name, None)

    @staticmethod
    def _decode_payload(raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, str) or not raw:
            return None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    
    def _load_pending_orders(self) -> List[Dict]:
        """Load pending exit orders from the Turso journal."""
        pending = []

        try:
            rows = self._open_db().execute(
                """
                SELECT trade_id, payload
                FROM journal
                ORDER BY COALESCE(filled_at, written_at) DESC
                """
            ).fetchall()

            for row in rows:
                journal_trade_id = self._row_value(row, 0, "trade_id")
                trade = self._decode_payload(self._row_value(row, 1, "payload"))
                if not trade:
                    continue
                exit_orders = trade.get("exit_orders", {})

                for order_type in ("target", "stop"):
                    section = exit_orders.get(order_type, {})
                    if section.get("status") != "PENDING":
                        continue
                    guard_key = (journal_trade_id, order_type)
                    if guard_key in self._unrecorded_placements:
                        # Already live at IB from a prior cycle whose journal
                        # write failed. Never re-place; retry the journal
                        # write so the row eventually reads PLACED.
                        if self._update_journal_trade(
                            trade.get("id"),
                            order_type,
                            self._unrecorded_placements[guard_key],
                            journal_trade_id,
                        ):
                            del self._unrecorded_placements[guard_key]
                        continue
                    pending.append({
                        "trade_id": trade.get("id"),
                        "ticker": trade.get("ticker"),
                        "structure": trade.get("structure"),
                        "order_type": order_type,
                        "target_price": section.get("price"),
                        "contracts": section.get("contracts"),
                        "contract_spec": section.get("contract_spec"),
                        "action": "SELL",  # Exit orders are sells
                        "journal_trade_id": journal_trade_id,
                    })
                    
        except Exception as e:
            logger.error(f"Failed to load journal exit orders: {e}")
        
        return pending
    
    @staticmethod
    def _is_halted(ticker_data: Any) -> bool:
        """True when IB flags this contract as halted.

        ``ib_insync.Ticker.halted`` is ``nan`` until the tick arrives,
        ``0`` / ``-1`` while trading normally, and ``1`` / ``2`` when
        halted (general / volatility). Anything non-numeric means "no
        halt information", never "halted". A halt often leaves the last
        two-sided book intact, so without this gate the exit gets priced
        off a pre-halt mid.
        """
        halted = getattr(ticker_data, "halted", None)
        return isinstance(halted, (int, float)) and halted > 0

    def _can_place_order(self, current_price: float, target_price: float) -> bool:
        """Check if order is within IB's acceptable gap."""
        if current_price <= 0:
            return False

        gap_pct = abs(target_price - current_price) / current_price
        return gap_pct <= self.max_gap_pct

    # Ack polling mirrors the Order Placement Contract enforced by
    # ib_place_order.py (single-leg deadline): IB silently discards an
    # order still unacknowledged (permId==0, PendingSubmit) when the
    # placing client disconnects, so PLACED must never be written on an
    # unconfirmed trade (REL-002 / R-003).
    ACK_DEADLINE_SECONDS = 6.0
    ACK_POLL_STEP_SECONDS = 0.25
    _TERMINAL_FAILED_STATUSES = {"Rejected", "Cancelled", "ApiCancelled", "Inactive"}
    _ACCEPTED_STATUSES = {"Submitted", "PreSubmitted", "Filled"}

    @staticmethod
    def _ack_perm_id(trade: Any) -> Optional[int]:
        """permId as a real int, or None for mock/None/junk (T-023 lesson:
        never let a non-int truthy object count as an acknowledgement)."""
        perm_id = getattr(trade.order, "permId", None)
        return perm_id if isinstance(perm_id, int) and not isinstance(perm_id, bool) else None

    def _confirm_placement(self, client: Any, trade: Any) -> tuple:
        """Poll until IB acknowledges (nonzero permId or accepted status),
        terminally rejects, or the deadline lapses.

        Returns (confirmed: bool, last_status: str).
        """
        waited = 0.0
        while True:
            status = getattr(trade.orderStatus, "status", None)
            if status in self._TERMINAL_FAILED_STATUSES:
                return False, status
            perm_id = self._ack_perm_id(trade)
            if (perm_id is not None and perm_id != 0) or status in self._ACCEPTED_STATUSES:
                return True, status
            if waited >= self.ACK_DEADLINE_SECONDS:
                return False, status
            client.sleep(self.ACK_POLL_STEP_SECONDS)
            waited += self.ACK_POLL_STEP_SECONDS

    def _cancel_unconfirmed(self, client: Any, trade: Any) -> None:
        """Best-effort cancel of a never-acked order so it cannot go live
        after the handler moves on (a late ack would otherwise leave a
        live order against a journal row still reading PENDING)."""
        try:
            client.cancel_order(trade.order)
        except Exception as exc:  # noqa: BLE001 — cancel of a likely-dropped order
            logger.warning(f"Cancel of unconfirmed exit order failed: {exc}")
    
    def _update_journal_trade(
        self,
        trade_id: int,
        order_type: str,
        order_id: int,
        journal_trade_id: Optional[str] = None,
    ) -> bool:
        """Update the Turso journal row with a placed order ID.

        Returns True on success. A failed write MUST be visible to the
        caller — the order is already live at IB, and pretending the cycle
        was clean is how the same order gets placed twice (T-010).
        """
        try:
            db = self._open_db()
            rows = []
            if journal_trade_id:
                rows = db.execute(
                    "SELECT trade_id, payload FROM journal WHERE trade_id = ?",
                    (journal_trade_id,),
                ).fetchall()
            if not rows:
                rows = db.execute("SELECT trade_id, payload FROM journal").fetchall()

            target_trade_id = None
            target_trade = None
            for row in rows:
                candidate_id = self._row_value(row, 0, "trade_id")
                candidate = self._decode_payload(self._row_value(row, 1, "payload"))
                if not candidate:
                    continue
                if candidate_id == journal_trade_id or candidate.get("id") == trade_id:
                    target_trade_id = candidate_id
                    target_trade = candidate
                    break

            if target_trade_id is None or target_trade is None:
                raise RuntimeError(f"journal trade not found for id {trade_id}")

            exit_orders = target_trade.get("exit_orders", {})
            if order_type in exit_orders:
                exit_orders[order_type]["status"] = "PLACED"
                exit_orders[order_type]["order_id"] = order_id
                exit_orders[order_type]["placed_at"] = datetime.now().isoformat()
            target_trade["exit_orders"] = exit_orders

            db.execute(
                """
                UPDATE journal
                SET payload = ?, written_at = ?
                WHERE trade_id = ?
                """,
                (
                    json.dumps(target_trade),
                    datetime.now().isoformat(),
                    target_trade_id,
                ),
            )
            if hasattr(db, "commit"):
                db.commit()

            # Read-back verification (REL-003 / R-012): a concurrent
            # wholesale payload upsert can silently swallow this write.
            # Trusting an unverified write is how a PLACED row reverts to
            # PENDING and the same live order gets placed twice.
            verify_rows = db.execute(
                "SELECT trade_id, payload FROM journal WHERE trade_id = ?",
                (target_trade_id,),
            ).fetchall()
            verified = False
            for row in verify_rows:
                candidate = self._decode_payload(self._row_value(row, 1, "payload"))
                if not candidate:
                    continue
                section = candidate.get("exit_orders", {}).get(order_type, {})
                if section.get("status") == "PLACED" and section.get("order_id") == order_id:
                    verified = True
                    break
            if not verified:
                logger.error(
                    f"Journal update for trade {trade_id} {order_type} did not "
                    f"persist (lost write) — treating as failed"
                )
                return False

            logger.info(f"Updated journal: trade {trade_id} {order_type} -> order #{order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update journal trade: {e}")
            return False

    def get_state(self) -> Dict[str, Any]:
        """State including the unrecorded-placements guard (REL-003).

        The guard marks orders LIVE at IB whose journal write failed; it
        must survive daemon restarts or every deploy re-arms the T-010
        double-place. JSON-safe list form for daemon_state.json.
        """
        state = super().get_state()
        state["unrecorded_placements"] = [
            [journal_trade_id, order_type, order_id]
            for (journal_trade_id, order_type), order_id
            in self._unrecorded_placements.items()
        ]
        return state

    def set_state(self, state: Dict[str, Any]) -> None:
        """Restore state including the unrecorded-placements guard."""
        super().set_state(state)
        restored: Dict[tuple, int] = {}
        for entry in state.get("unrecorded_placements", []) or []:
            try:
                journal_trade_id, order_type, order_id = entry
                restored[(journal_trade_id, order_type)] = order_id
            except (TypeError, ValueError):
                logger.warning(f"Skipping malformed guard entry: {entry!r}")
        self._unrecorded_placements = restored

    _ACTIVE_ORDER_STATUSES = {"Submitted", "PreSubmitted", "PendingSubmit", "ApiPending"}

    def _active_sell_index(self, client: Any) -> Dict[Any, Any]:
        """Index of working SELL orders at IB keyed by conId and localSymbol.

        Broker truth: before placing an exit, any working SELL on the same
        contract means a prior cycle already placed it (crash between
        place and journal-update, lost write, restarted daemon) — adopt
        it, never duplicate it.
        """
        index: Dict[Any, Any] = {}
        for trade in client.get_open_orders():
            order = getattr(trade, "order", None)
            if getattr(order, "action", None) != "SELL":
                continue
            if getattr(trade.orderStatus, "status", None) not in self._ACTIVE_ORDER_STATUSES:
                continue
            contract = getattr(trade, "contract", None)
            con_id = getattr(contract, "conId", None)
            local_symbol = getattr(contract, "localSymbol", None)
            if isinstance(con_id, int) and con_id:
                index[("conid", con_id)] = trade
            if isinstance(local_symbol, str) and local_symbol:
                index[("local", local_symbol)] = trade

        return index

    def _find_active_sell(self, index: Dict[Any, Any], contract: Any) -> Optional[Any]:
        con_id = getattr(contract, "conId", None)
        if isinstance(con_id, int) and ("conid", con_id) in index:
            return index[("conid", con_id)]
        local_symbol = getattr(contract, "localSymbol", None)
        if isinstance(local_symbol, str) and ("local", local_symbol) in index:
            return index[("local", local_symbol)]
        return None
    
    def execute(self) -> Dict[str, Any]:
        """
        Check pending orders and place those within threshold.

        Returns:
            Dict with orders checked, placed, and skipped
        """
        result = {
            "orders_checked": 0,
            "orders_placed": 0,
            "orders_skipped": 0,
            "placed": [],
            "skipped": [],
            "timestamp": datetime.now().isoformat()
        }

        pending = self._load_pending_orders()

        if not pending:
            logger.debug("No pending exit orders")
            return result

        result["orders_checked"] = len(pending)

        client = IBClient()

        try:
            client.connect(host=DEFAULT_HOST, port=self.ib_port, client_id=self.client_id)
            logger.debug("Connected to IB")

            # REL-003: broker truth before any placement. If the working-
            # order snapshot is unavailable we cannot rule out a live
            # duplicate — defer every placement this cycle (fail-safe).
            try:
                active_sells = self._active_sell_index(client)
            except Exception as e:
                result["error"] = (
                    f"open-order cross-check unavailable ({e}); "
                    f"exit placements deferred this cycle"
                )
                logger.warning(result["error"])
                return result

            for order_info in pending:
                ticker = order_info["ticker"]
                target_price = order_info["target_price"]
                contracts = order_info.get("contracts", 1)
                spec = order_info.get("contract_spec", {})

                # Build contract
                if spec:
                    contract = Option(
                        symbol=spec.get("symbol", ticker),
                        lastTradeDateOrContractMonth=spec.get("expiry"),
                        strike=spec.get("strike"),
                        right=spec.get("right"),
                        exchange="SMART",
                        currency="USD"
                    )

                    qualified = client.qualify_contracts(contract)
                    if not qualified:
                        logger.warning(f"Could not qualify contract for {ticker}")
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "reason": "contract_qualification_failed"
                        })
                        continue

                    contract = qualified[0]

                    # REL-003: a working SELL already at IB for this
                    # contract is a prior cycle's placement whose journal
                    # write never landed — adopt it, never duplicate it.
                    live = self._find_active_sell(active_sells, contract)
                    if live is not None:
                        live_order_id = live.order.orderId
                        adopted = self._update_journal_trade(
                            order_info["trade_id"],
                            order_info["order_type"],
                            live_order_id,
                            order_info.get("journal_trade_id"),
                        )
                        if adopted:
                            result["orders_adopted"] = result.get("orders_adopted", 0) + 1
                            logger.warning(
                                f"Adopted live exit order #{live_order_id} for "
                                f"{ticker} {order_info['order_type']} (journal "
                                f"row was PENDING — prior write never landed)"
                            )
                        else:
                            guard_key = (
                                order_info.get("journal_trade_id"),
                                order_info["order_type"],
                            )
                            self._unrecorded_placements[guard_key] = live_order_id
                            result["error"] = (
                                f"live exit order #{live_order_id} found for "
                                f"{ticker} {order_info['order_type']} but journal "
                                f"adoption write failed; re-place suppressed"
                            )
                            logger.error(result["error"])
                        continue

                    # Get current price
                    ticker_data = client.get_quote(contract)
                    client.sleep(2)

                    bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else 0
                    ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else 0
                    mid = (bid + ask) / 2 if bid and ask else 0

                    client.cancel_market_data(contract)

                    if self._is_halted(ticker_data):
                        logger.warning(f"Trading halted for {contract.localSymbol}")
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "reason": "halted"
                        })
                        continue

                    if mid <= 0:
                        logger.warning(f"No market data for {contract.localSymbol}")
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "reason": "no_market_data"
                        })
                        continue

                    # Check if within threshold
                    if self._can_place_order(mid, target_price):
                        # Place the order
                        limit_order = LimitOrder(
                            action="SELL",
                            totalQuantity=contracts,
                            lmtPrice=target_price,
                            tif="GTC"
                        )

                        trade = client.place_order(contract, limit_order)

                        confirmed, ack_status = self._confirm_placement(client, trade)
                        if not confirmed:
                            if ack_status not in self._TERMINAL_FAILED_STATUSES:
                                self._cancel_unconfirmed(client, trade)
                            result["orders_failed"] = result.get("orders_failed", 0) + 1
                            result.setdefault("failed", []).append({
                                "ticker": ticker,
                                "contract": contract.localSymbol,
                                "order_type": order_info["order_type"],
                                "status": ack_status,
                            })
                            result["error"] = (
                                f"exit order not acknowledged by IB "
                                f"({ticker} {order_info['order_type']}: "
                                f"{ack_status or 'no status'}); journal left PENDING"
                            )
                            logger.warning(result["error"])
                            continue

                        order_id = trade.order.orderId

                        logger.info(
                            f"Placed exit order: SELL {contracts}x {contract.localSymbol} "
                            f"@ ${target_price:.2f} (Order #{order_id})"
                        )

                        result["orders_placed"] += 1
                        result["placed"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "order_id": order_id,
                            "price": target_price,
                            "current_mid": mid
                        })

                        # Update journal. On failure the order is LIVE but the
                        # row still says PENDING — arm the re-place guard and
                        # surface an error so the watchdog sees the cycle.
                        recorded = self._update_journal_trade(
                            order_info["trade_id"],
                            order_info["order_type"],
                            order_id,
                            order_info.get("journal_trade_id"),
                        )
                        if not recorded:
                            guard_key = (
                                order_info.get("journal_trade_id"),
                                order_info["order_type"],
                            )
                            self._unrecorded_placements[guard_key] = order_id
                            result["journal_update_failures"] = (
                                result.get("journal_update_failures", 0) + 1
                            )
                            result["error"] = (
                                "journal update failed after live placement "
                                f"(order #{order_id} {order_info['ticker']} "
                                f"{order_info['order_type']}); re-place suppressed"
                            )
                    else:
                        gap_pct = abs(target_price - mid) / mid * 100
                        logger.debug(
                            f"Skipping {ticker}: gap {gap_pct:.1f}% exceeds {self.max_gap_pct*100:.0f}% threshold"
                        )
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "target": target_price,
                            "current_mid": mid,
                            "gap_pct": gap_pct,
                            "reason": "gap_too_large"
                        })
                else:
                    logger.warning(f"No contract spec for {ticker}")
                    result["orders_skipped"] += 1
                    result["skipped"].append({
                        "ticker": ticker,
                        "reason": "no_contract_spec"
                    })

        except Exception as e:
            logger.error(f"Exit orders error: {e}")
            result["error"] = str(e)
        finally:
            client.disconnect()
            logger.debug("Disconnected from IB")
        
        return result
