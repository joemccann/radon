#!/usr/bin/env python3
"""
Fill Monitor Handler - Monitors orders for partial/complete fills.

Features:
- Tracks all open orders
- Detects partial fills
- Detects complete fills (order disappears from open orders)
- Sends macOS notifications on fills
- Updates trade_log.json with fill data
"""

import json
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import BaseHandler
from clients.ib_client import IBClient, DEFAULT_HOST

try:
    # Mirror detected fills inline to the Turso journal table so a process
    # restart between detection and the next journal_sync cycle doesn't
    # silently drop the fill from the in-memory known_orders cache.
    from db.writer import upsert_journal_entry  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    upsert_journal_entry = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_TRADE_LOG = Path(__file__).parent.parent.parent.parent / "data" / "trade_log.json"
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
        trade_log_path: Optional[Path] = None,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: "int | str" = DEFAULT_CLIENT_ID,
        send_notifications: bool = True
    ):
        super().__init__()
        self.trade_log_path = trade_log_path or DEFAULT_TRADE_LOG
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
                            "avg_price": status.avgFillPrice
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
            
            # Check for completed orders (no longer in open orders)
            completed_ids = set(self.known_orders.keys()) - current_order_ids
            for order_id in completed_ids:
                prev_order = self.known_orders[order_id]
                result["complete_fills"] += 1
                
                completed_info = {
                    "order_id": order_id,
                    "symbol": prev_order.get("symbol"),
                    "contract": prev_order.get("contract"),
                    "action": prev_order.get("action"),
                    "quantity": prev_order.get("quantity"),
                    "filled": prev_order.get("filled"),
                    "status": "COMPLETED"
                }
                result["completed"].append(completed_info)
                
                logger.info(f"Order completed: #{order_id} {prev_order.get('symbol')}")
                
                # Send notification for complete fill
                if self.send_notifications:
                    self._notify_complete(completed_info)
                
                # Remove from tracking
                del self.known_orders[order_id]
            
        except Exception as e:
            logger.error(f"Fill monitor error: {e}")
            result["error"] = str(e)
        finally:
            client.disconnect()
            logger.debug("Disconnected from IB")
        
        return result
    
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
        # Synthetic trade_id: each progressive fill state gets its own row.
        # Real ib_exec_id would be ideal but is not exposed on order status.
        trade_id = f"fill-monitor:order-{order_id}:filled-{total_filled}"

        sec_type = getattr(contract, "secType", "STK")
        side_label = "BUY" if str(order.action).upper() == "BUY" else "SELL"
        action = side_label if sec_type == "STK" else (
            "BUY_OPTION" if side_label == "BUY" else "SELL_TO_OPEN"
        )

        avg_price = fill_info.get("avg_price") or 0.0
        newly_filled = fill_info.get("newly_filled", 0)
        multiplier = 100 if sec_type in ("OPT", "BAG") else 1
        total_cost = float(newly_filled) * float(avg_price) * multiplier
        filled_at = datetime.now().strftime("%Y-%m-%d")

        payload: Dict[str, Any] = {
            "date": filled_at,
            "ticker": fill_info.get("symbol", ""),
            "structure": f"{side_label} {sec_type}",
            "decision": "FILL_MONITOR_AUTO_IMPORT",
            "action": action,
            "fill_price": round(float(avg_price), 4),
            "total_cost": round(total_cost, 4),
            "ib_exec_id": trade_id,
            "order_id": order_id,
            "total_filled": total_filled,
            "newly_filled": newly_filled,
            "notes": f"Imported from fill_monitor on {filled_at}",
        }
        if sec_type in ("OPT", "BAG"):
            payload["contracts"] = int(abs(newly_filled))
        else:
            payload["shares"] = int(abs(newly_filled))

        try:
            upsert_journal_entry(trade_id, payload, filled_at=filled_at)
        except Exception as exc:  # noqa: BLE001 — never crash on DB write failure
            logger.warning("fill_monitor: journal upsert failed: %s", exc)

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
