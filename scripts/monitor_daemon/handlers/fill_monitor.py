#!/usr/bin/env python3
"""
Fill Monitor Handler - Monitors orders for partial/complete fills.

Features:
- Tracks all open orders
- Detects partial fills
- Detects complete fills (order disappears from open orders)
- Sends macOS notifications on fills
- Upserts detected fills to the Turso journal
"""

import subprocess
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from .base import BaseHandler, et_session_date
from clients.ib_client import IBClient, DEFAULT_HOST

try:
    # Mirror detected fills inline to the Turso journal table so a process
    # restart between detection and the next journal_sync cycle doesn't
    # silently drop the fill from the in-memory known_orders cache.
    from db.writer import upsert_journal_entry  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    upsert_journal_entry = None  # type: ignore[assignment]

def _identity_int(value: Any) -> int:
    """Coerce an IB identity field (conId/permId) to int, else 0.

    Keeps the synthetic journal key deterministic when a source hands back
    a non-numeric placeholder instead of the int IB provides in production.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


try:
    from db.client import get_db  # type: ignore
except ImportError:  # pragma: no cover
    get_db = None  # type: ignore[assignment]

try:
    from clients.journal_basis import (  # type: ignore
        normalize_expiry_compact,
        prior_net_qty_for_contract,
    )
except ImportError:  # pragma: no cover
    normalize_expiry_compact = None  # type: ignore[assignment]
    prior_net_qty_for_contract = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_IB_PORT = 4001
# "auto" rotates across SUBPROCESS_ID_RANGE (20-49) on each connect,
# surviving the common failure mode where a previous cycle's socket
# is sitting in CLOSE_WAIT on the same hardcoded ID. Hardcoded 70 was
# replaced 2026-05-20 — when IB Gateway briefly hiccupped (pool
# reconnect, 2FA window, network blip), the next handler cycle hit
# "client id already in use" on the stale socket and surfaced as
# "Failed to connect to IB on 127.0.0.1:4001 after 1 attempt(s)" in
# the service-health banner, persisting until the half-open socket
# timed out. See feedback_ib_client_id_ranges.md.
DEFAULT_CLIENT_ID: int | str = "auto"


class FillMonitorHandler(BaseHandler):
    """Monitor orders for fills."""

    name = "fill_monitor"
    interval_seconds = 60  # Check every minute
    service_name = "fill-monitor"  # structural heartbeat via BaseHandler.run()

    def __init__(
        self,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: "int | str" = DEFAULT_CLIENT_ID,
        send_notifications: bool = True
    ):
        super().__init__()
        self.ib_port = ib_port
        self.client_id = client_id
        self.send_notifications = send_notifications

        # Track known order states: {order_id: {filled, symbol, etc}}
        self.known_orders: Dict[int, Dict] = {}

    def execute(self) -> Dict[str, Any]:
        """
        Check all open orders for fills.

        Returns:
            Dict with orders, fills, and changes detected
        """
        result = {
            "orders": [],
            "fills": [],
            "completed": [],
            "new_orders": 0,
            "partial_fills": 0,
            "complete_fills": 0,
            "timestamp": datetime.now().isoformat()
        }

        client = IBClient()

        try:
            client.connect(host=DEFAULT_HOST, port=self.ib_port, client_id=self.client_id)
            logger.debug("Connected to IB")

            # Fetch all open orders
            trades = client.get_open_orders()
            current_order_ids = set()
            
            for trade in trades:
                order = trade.order
                status = trade.orderStatus
                contract = trade.contract
                
                order_id = order.orderId
                current_order_ids.add(order_id)
                
                order_info = {
                    "order_id": order_id,
                    "symbol": contract.symbol,
                    "contract": contract.localSymbol,
                    "action": order.action,
                    "quantity": int(order.totalQuantity),
                    "filled": int(status.filled),
                    "remaining": int(status.remaining),
                    "status": status.status,
                    "limit": order.lmtPrice if order.lmtPrice else None,
                    "avg_fill_price": status.avgFillPrice if status.avgFillPrice else None
                }
                
                result["orders"].append(order_info)
                
                # Check if this is a new order
                if order_id not in self.known_orders:
                    result["new_orders"] += 1
                    logger.info(f"New order detected: #{order_id} {order.action} {contract.symbol}")
                
                # Check for new fills
                elif order_id in self.known_orders:
                    prev_filled = self.known_orders[order_id].get("filled", 0)
                    current_filled = int(status.filled)
                    
                    if current_filled > prev_filled:
                        newly_filled = current_filled - prev_filled
                        result["partial_fills"] += 1
                        
                        fill_info = {
                            "order_id": order_id,
                            "symbol": contract.symbol,
                            "contract": contract.localSymbol,
                            "action": order.action,
                            "newly_filled": newly_filled,
                            "total_filled": current_filled,
                            "remaining": int(status.remaining),
                            "avg_price": status.avgFillPrice,
                            # Prior fill state so the journal write can price
                            # this increment from the cost delta, not the
                            # running average (REL-011 / R-010).
                            "prev_filled": prev_filled,
                            "prev_avg_price": self.known_orders[order_id].get("avg_fill_price"),
                        }
                        result["fills"].append(fill_info)
                        
                        logger.info(
                            f"Fill detected: #{order_id} {order.action} {newly_filled}x "
                            f"{contract.symbol} @ ${status.avgFillPrice:.2f}"
                        )

                        # Mirror to journal table inline. A process restart
                        # between this detection and the next journal_sync
                        # cycle would otherwise lose the fill — only Flex
                        # rehydrate could recover it.
                        self._persist_fill_to_journal(fill_info, contract, order, status)

                        # Send notification
                        if self.send_notifications:
                            self._notify_fill(fill_info)
                
                # Update known state
                self.known_orders[order_id] = order_info
            
            # Check for completed orders (no longer in open orders).
            # REL-009 / R-016: "vanished from the snapshot" is NOT proof of
            # a fill — cancels vanish too, and a degraded session returns
            # an empty snapshot for every order at once.
            completed_ids = set(self.known_orders.keys()) - current_order_ids

            if completed_ids and not trades:
                accounts = client.get_managed_accounts()
                if not accounts:
                    # Connected-but-degraded (post-restart pre-auth): the
                    # empty snapshot is meaningless. Keep every baseline so
                    # partials during the blip are detected after recovery.
                    result["error"] = (
                        "session degraded (no managed accounts) — open-order "
                        "snapshot untrusted; completion check skipped"
                    )
                    logger.warning(result["error"])
                    return result

            filled_order_ids = None
            if completed_ids:
                try:
                    filled_order_ids = {
                        f.execution.orderId for f in client.get_fills()
                    }
                except Exception as e:  # noqa: BLE001 — defer, don't guess
                    result["error"] = (
                        f"execution cross-check unavailable ({e}); "
                        f"completion judgment deferred"
                    )
                    logger.warning(result["error"])
                    return result

            for order_id in completed_ids:
                prev_order = self.known_orders[order_id]
                genuinely_filled = order_id in (filled_order_ids or set())

                completed_info = {
                    "order_id": order_id,
                    "symbol": prev_order.get("symbol"),
                    "contract": prev_order.get("contract"),
                    "action": prev_order.get("action"),
                    "quantity": prev_order.get("quantity"),
                    "filled": prev_order.get("filled"),
                    "status": "COMPLETED" if genuinely_filled else "CANCELLED_OR_GONE",
                }
                result["completed"].append(completed_info)

                if genuinely_filled:
                    result["complete_fills"] += 1
                    logger.info(f"Order completed: #{order_id} {prev_order.get('symbol')}")
                    if self.send_notifications:
                        self._notify_complete(completed_info)
                else:
                    result["cancelled_or_gone"] = result.get("cancelled_or_gone", 0) + 1
                    logger.info(
                        f"Order gone without execution (cancelled/expired): "
                        f"#{order_id} {prev_order.get('symbol')}"
                    )

                # Remove from tracking (gone either way)
                del self.known_orders[order_id]
            
        except Exception as e:
            logger.error(f"Fill monitor error: {e}")
            result["error"] = str(e)
        finally:
            client.disconnect()
            logger.debug("Disconnected from IB")
        
        return result
    
    @staticmethod
    def _side_to_action(side_label: str, sec_type: str, prior_qty: float = 0.0) -> str:
        """Map order side + prior signed qty → journal action label.

        Mirrors journal_sync._side_to_action so fill_monitor and journal_sync
        do not diverge (buy-to-cover short → BUY_TO_CLOSE, sell-to-close long
        → SELL_OPTION).
        """
        upper = (side_label or "").upper()
        if upper == "BUY":
            if sec_type == "STK":
                return "BUY"
            return "BUY_TO_CLOSE" if prior_qty < 0 else "BUY_OPTION"
        if upper == "SELL":
            if sec_type == "STK":
                return "SELL"
            return "SELL_OPTION" if prior_qty > 0 else "SELL_TO_OPEN"
        return "BUY" if sec_type == "STK" else "BUY_OPTION"

    @staticmethod
    def _structure_label(action: str, sec_type: str, strike: Any, right: Any, expiry: Any) -> str:
        type_label = {"STK": "Stock", "OPT": "Option", "BAG": "Spread"}.get(sec_type, sec_type)
        a = (action or "").upper()
        if a in ("BUY", "BUY_OPTION", "BUY_TO_OPEN"):
            side_label = "Long"
        elif a == "SELL_TO_OPEN":
            side_label = "Short"
        else:
            side_label = "Closed"
        if sec_type in ("OPT", "BAG") and strike is not None and right:
            right_label = "Call" if right in ("C", "CALL") else "Put" if right in ("P", "PUT") else right
            exp = str(expiry) if expiry else ""
            if len(exp) == 8 and exp.isdigit():
                expiry_iso = f"{exp[0:4]}-{exp[4:6]}-{exp[6:8]}"
            else:
                expiry_iso = exp
            try:
                strike_f = float(strike)
                strike_val = int(strike_f) if strike_f.is_integer() else strike_f
            except (TypeError, ValueError):
                strike_val = strike
            suffix = f" {expiry_iso}" if expiry_iso else ""
            return f"{side_label} {right_label} ${strike_val}{suffix}"
        return f"{side_label} {type_label} ({sec_type})"

    def _lookup_prior_qty(self, contract: Any) -> float:
        if get_db is None or prior_net_qty_for_contract is None:
            return 0.0
        ticker = getattr(contract, "symbol", None)
        if not ticker:
            return 0.0
        sec_type = getattr(contract, "secType", "STK") or "STK"
        try:
            db = get_db()
            expiry = getattr(contract, "lastTradeDateOrContractMonth", None)
            if normalize_expiry_compact is not None:
                expiry = normalize_expiry_compact(expiry)
            return float(
                prior_net_qty_for_contract(
                    db,
                    ticker=ticker,
                    sec_type=sec_type,
                    strike=getattr(contract, "strike", None),
                    right=getattr(contract, "right", None),
                    expiry=expiry,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("fill_monitor: prior-qty lookup failed for %s: %s", ticker, exc)
            return 0.0

    def _persist_fill_to_journal(
        self,
        fill_info: Dict,
        contract: Any,
        order: Any,
        status: Any,
    ) -> None:
        """Mirror a detected partial fill to the Turso journal table.

        Failures are logged and swallowed — the journal_sync handler runs
        every 300s and the next Flex rehydrate is the canonical recovery
        path. The DB write must NEVER crash the handler.
        """
        if upsert_journal_entry is None:
            return

        order_id = fill_info["order_id"]
        total_filled = fill_info["total_filled"]
        # ET session date, not host wall-clock — a UTC host stamps evening
        # fills onto the next day, skewing the trade_id key + row date (T-023).
        fill_date = et_session_date()
        # Synthetic trade_id: each progressive fill state gets its own row.
        # Real ib_exec_id would be ideal but is not exposed on order status.
        # The key MUST carry contract + session identity: bare orderIds
        # restart per session/clientId, and JOURNAL_UPSERT_SQL is ON
        # CONFLICT(trade_id) DO UPDATE — a colliding key destructively
        # overwrites another order's journal row (T-013). conId pins the
        # contract, permId pins the order across sessions, the date pins
        # the day; re-detecting the SAME fill state stays idempotent.
        con_id = _identity_int(getattr(contract, "conId", 0))
        perm_id = _identity_int(getattr(order, "permId", 0))
        order_key = perm_id or order_id
        trade_id = (
            f"fill-monitor:con-{con_id}:order-{order_key}:"
            f"{fill_date}:filled-{total_filled}"
        )

        sec_type = getattr(contract, "secType", "STK") or "STK"
        side_label = "BUY" if str(order.action).upper() == "BUY" else "SELL"
        prior_qty = self._lookup_prior_qty(contract)
        action = self._side_to_action(side_label, sec_type, prior_qty=prior_qty)

        newly_filled = fill_info.get("newly_filled", 0)
        increment_price = self._increment_price(fill_info)
        multiplier = 100 if sec_type in ("OPT", "BAG") else 1
        total_cost = float(newly_filled) * float(increment_price) * multiplier
        filled_at = fill_date

        strike = getattr(contract, "strike", None) if sec_type in ("OPT", "BAG") else None
        right = getattr(contract, "right", None) if sec_type in ("OPT", "BAG") else None
        expiry = getattr(contract, "lastTradeDateOrContractMonth", None) if sec_type in ("OPT", "BAG") else None
        if normalize_expiry_compact is not None:
            expiry = normalize_expiry_compact(expiry)

        structure = self._structure_label(action, sec_type, strike, right, expiry)

        payload: Dict[str, Any] = {
            "date": filled_at,
            "ticker": fill_info.get("symbol", ""),
            "structure": structure,
            "decision": "FILL_MONITOR_AUTO_IMPORT",
            "action": action,
            "fill_price": round(float(increment_price), 4),
            "total_cost": round(total_cost, 4),
            "ib_exec_id": trade_id,
            "order_id": order_id,
            "total_filled": total_filled,
            "newly_filled": newly_filled,
            "notes": f"Imported from fill_monitor on {filled_at}",
        }
        if sec_type in ("OPT", "BAG"):
            payload["contracts"] = int(abs(newly_filled))
            if strike is not None:
                try:
                    payload["strike"] = float(strike)
                except (TypeError, ValueError):
                    pass
            if right:
                payload["right"] = right
            if expiry:
                payload["expiry"] = expiry
        else:
            payload["shares"] = int(abs(newly_filled))

        try:
            upsert_journal_entry(trade_id, payload, filled_at=filled_at)
        except Exception as exc:  # noqa: BLE001 — never crash on DB write failure
            logger.warning("fill_monitor: journal upsert failed: %s", exc)

    @staticmethod
    def _increment_price(fill_info: Dict) -> float:
        """Per-unit price of THIS increment, from the running-average delta.

        IB order status only exposes the running ``avgFillPrice``; the
        increment's true cost is ``total×avg_now − prev_total×avg_prev``.
        Falls back to the running average when there is no prior fill or the
        prior average was never captured (restored pre-fix state).
        """
        avg_price = float(fill_info.get("avg_price") or 0.0)
        newly_filled = float(fill_info.get("newly_filled") or 0)
        prev_filled = float(fill_info.get("prev_filled") or 0)
        prev_avg = float(fill_info.get("prev_avg_price") or 0.0)
        if newly_filled <= 0 or (prev_filled > 0 and prev_avg <= 0):
            return avg_price
        total_filled = float(fill_info.get("total_filled") or (prev_filled + newly_filled))
        return (total_filled * avg_price - prev_filled * prev_avg) / newly_filled

    def _notify_fill(self, fill: Dict) -> None:
        """Send macOS notification for a fill."""
        title = f"Order Fill: {fill['symbol']}"
        message = (
            f"{fill['action']} {fill['newly_filled']}x {fill['contract']} "
            f"@ ${fill.get('avg_price', 0):.2f}"
        )
        self._send_notification(title, message)
    
    def _notify_complete(self, completed: Dict) -> None:
        """Send macOS notification for completed order."""
        title = f"Order Complete: {completed['symbol']}"
        message = f"{completed['action']} {completed['filled']}x {completed['contract']}"
        self._send_notification(title, message)
    
    def _send_notification(self, title: str, message: str) -> None:
        """Send macOS notification via osascript."""
        try:
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=5
            )
        except Exception as e:
            logger.warning(f"Failed to send notification: {e}")
    
    def get_state(self) -> Dict[str, Any]:
        """Get state including known orders."""
        state = super().get_state()
        # Convert int keys to strings for JSON
        state["known_orders"] = {
            str(k): v for k, v in self.known_orders.items()
        }
        return state
    
    def set_state(self, state: Dict[str, Any]) -> None:
        """Restore state including known orders."""
        super().set_state(state)
        known = state.get("known_orders", {})
        # Convert string keys back to ints
        self.known_orders = {
            int(k): v for k, v in known.items()
        }
