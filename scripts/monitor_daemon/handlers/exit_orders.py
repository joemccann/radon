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
    
    def _can_place_order(self, current_price: float, target_price: float) -> bool:
        """Check if order is within IB's acceptable gap."""
        if current_price <= 0:
            return False
        
        gap_pct = abs(target_price - current_price) / current_price
        return gap_pct <= self.max_gap_pct
    
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
            logger.info(f"Updated journal: trade {trade_id} {order_type} -> order #{order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update journal trade: {e}")
            return False
    
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

                    # Get current price
                    ticker_data = client.get_quote(contract)
                    client.sleep(2)

                    bid = ticker_data.bid if ticker_data.bid and ticker_data.bid > 0 else 0
                    ask = ticker_data.ask if ticker_data.ask and ticker_data.ask > 0 else 0
                    mid = (bid + ask) / 2 if bid and ask else 0

                    client.cancel_market_data(contract)

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
                        client.sleep(1)

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
