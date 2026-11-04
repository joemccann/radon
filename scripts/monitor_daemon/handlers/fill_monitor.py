#!/usr/bin/env python3
"""
Fill Monitor Handler - Monitors orders for partial/complete fills.

Features:
- Tracks all open orders
- Detects partial fills
- Detects complete fills (order disappears from open orders)
- Sends macOS notifications on fills
- Upserts detected fills to the Turso journal
- Mirrors the IB open+executed snapshot into Turso so /orders drops
  WORKING rows on fill without waiting for the 5-min orders-sync loop
- session_window=equity_ext (04:00-20:00 ET); outsideRth stocks fill after RTH
"""

import subprocess
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Optional

from .base import BaseHandler, et_session_date
from utils.outcome_metrics import record_outcome
from clients.ib_client import IBClient, DEFAULT_HOST
from utils.journal_action import classify_fill, structure_side
from utils.ib_order_status import TERMINAL_FAILED, CancelOutcome, normalize as normalize_status

try:
    # Mirror detected fills inline to the Turso journal table so a process
    # restart between detection and the next journal_sync cycle doesn't
    # silently drop the fill from the in-memory known_orders cache.
    from db.writer import upsert_journal_entry  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    upsert_journal_entry = None  # type: ignore[assignment]

try:
    # /orders reads open_orders + executed_orders, not the journal. Mirror
    # the IB snapshot on fill so EXT fills drop from WORKING without
    # waiting for the RTH-gated 5-min orders-sync loop.
    from ib_orders import (  # type: ignore
        build_orders_data as build_orders_data_for_mirror,
        snapshot_time as snapshot_time_for_mirror,
        fetch_executed_orders as fetch_executed_orders_for_mirror,
        fetch_open_orders as fetch_open_orders_for_mirror,
        save_orders as save_orders_snapshot,
    )
    from db.writer import count_open_orders as count_open_orders_for_mirror  # type: ignore
except ImportError:  # pragma: no cover
    build_orders_data_for_mirror = None  # type: ignore[assignment]
    snapshot_time_for_mirror = None  # type: ignore[assignment]
    fetch_executed_orders_for_mirror = None  # type: ignore[assignment]
    fetch_open_orders_for_mirror = None  # type: ignore[assignment]
    save_orders_snapshot = None  # type: ignore[assignment]
    count_open_orders_for_mirror = None  # type: ignore[assignment]

def _identity_int(value: Any) -> int:
    """Coerce an IB identity field (conId/permId) to int, else 0.

    Keeps the synthetic journal key deterministic when a source hands back
    a non-numeric placeholder instead of the int IB provides in production.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _strict_id(value: Any) -> int:
    """A positive int IB identity, else 0 (placeholders never coerce)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def order_key(order: Any) -> Optional[int]:
    """known_orders key: permId, the one id unique across clients.

    orderId restarts per client and every manual TWS order carries 0 (or a
    negative id), so keying on it merged unrelated orders. Before IB assigns
    a permId an API order (positive orderId) is keyed by orderId and
    re-keyed once the permId arrives; an order with neither is untracked.
    """
    return _strict_id(getattr(order, "permId", None)) or _strict_id(getattr(order, "orderId", None)) or None


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


# Result-row status per completion outcome (CANCELLED_OR_GONE: no execution
# and no terminal status recorded, so cancel vs expire cannot be told apart).
_COMPLETION_STATUS = {
    CancelOutcome.FILLED: "COMPLETED",
    CancelOutcome.PARTIALLY_FILLED_THEN_CANCELLED: "PARTIALLY_FILLED_THEN_CANCELLED",
    CancelOutcome.CANCELLED: "CANCELLED",
    CancelOutcome.UNKNOWN: "CANCELLED_OR_GONE",
}


class FillMonitorHandler(BaseHandler):
    """Monitor orders for fills."""

    name = "fill_monitor"
    interval_seconds = 60  # Check every minute
    service_name = "fill-monitor"  # structural heartbeat via BaseHandler.run()
    session_window = "equity_ext"

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

        # Track known order states: {order_key: {filled, symbol, etc}}
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
            current_keys = set()

            for trade in trades:
                order = trade.order
                status = trade.orderStatus
                contract = trade.contract

                order_id = order.orderId
                key = order_key(order)
                if key is None:
                    continue
                perm_id = _strict_id(getattr(order, "permId", None))
                if perm_id and key not in self.known_orders and order_id in self.known_orders \
                        and not self.known_orders[order_id].get("perm_id"):
                    # IB assigned the permId since the last cycle: re-key.
                    self.known_orders[key] = self.known_orders.pop(order_id)
                current_keys.add(key)

                order_info = {
                    "order_id": order_id,
                    "perm_id": perm_id,
                    "con_id": _strict_id(getattr(contract, "conId", None)),
                    "sec_type": getattr(contract, "secType", None),
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
                if key not in self.known_orders:
                    result["new_orders"] += 1
                    logger.info(f"New order detected: #{order_id} {order.action} {contract.symbol}")

                # Check for new fills
                else:
                    prev_filled = self.known_orders[key].get("filled", 0)
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
                            "prev_avg_price": self.known_orders[key].get("avg_fill_price"),
                        }
                        result["fills"].append(fill_info)
                        record_outcome("fill_detected", "success")

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
                self.known_orders[key] = order_info

            # Check for completed orders (no longer in open orders).
            # REL-009 / R-016: "vanished from the snapshot" is NOT proof of
            # a fill — cancels vanish too, and a degraded session returns
            # an empty snapshot for every order at once.
            completed_ids = set(self.known_orders.keys()) - current_keys

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

            session_fills: list = []
            if completed_ids:
                try:
                    session_fills = list(client.get_fills())
                except Exception as e:  # noqa: BLE001 — defer, don't guess
                    result["error"] = (
                        f"execution cross-check unavailable ({e}); "
                        f"completion judgment deferred"
                    )
                    logger.warning(result["error"])
                    return result

            for key in completed_ids:
                prev_order = self.known_orders[key]
                order_id = prev_order.get("order_id", key)
                executions = self._executions_for(prev_order, key, session_fills)
                outcome = self._completion_outcome(prev_order, executions)

                completed_info = {
                    "order_id": order_id,
                    "symbol": prev_order.get("symbol"),
                    "contract": prev_order.get("contract"),
                    "action": prev_order.get("action"),
                    "quantity": prev_order.get("quantity"),
                    "filled": prev_order.get("filled"),
                    "status": _COMPLETION_STATUS[outcome],
                }
                result["completed"].append(completed_info)

                if executions:
                    result["complete_fills"] += 1
                    record_outcome("filled_order_removed", "success")
                    logger.info(f"Order completed: #{order_id} {prev_order.get('symbol')}")
                    self._persist_final_increment(prev_order, executions)
                    if self.send_notifications:
                        self._notify_complete(completed_info)
                else:
                    result["cancelled_or_gone"] = result.get("cancelled_or_gone", 0) + 1
                    logger.info(
                        f"Order gone without execution (cancelled/expired): "
                        f"#{order_id} {prev_order.get('symbol')}"
                    )

                # Remove from tracking (gone either way)
                del self.known_orders[key]

            if result["fills"] or result.get("complete_fills"):
                self._mirror_ib_orders_snapshot(client)
            
        except Exception as e:
            logger.error(f"Fill monitor error: {e}")
            result["error"] = str(e)
        finally:
            client.disconnect()
            logger.debug("Disconnected from IB")
        
        return result

    def _mirror_ib_orders_snapshot(self, client: Any) -> None:
        """Replace Turso open_orders + executed_orders from this IB session.

        Failures are logged and swallowed — the next orders-sync tick or
        evening execution sweep is the recovery path. Never crash the
        handler on a mirror miss.
        """
        if (
            save_orders_snapshot is None
            or fetch_open_orders_for_mirror is None
            or fetch_executed_orders_for_mirror is None
            or build_orders_data_for_mirror is None
        ):
            return
        try:
            # Read time BEFORE the fetch: orders this book against the
            # orders-sync writer's (C3).
            snapshot_at = snapshot_time_for_mirror() if snapshot_time_for_mirror else None
            open_orders = fetch_open_orders_for_mirror(client)
            # get_open_orders caps its openOrderEnd wait at 0.5s and returns
            # whatever arrived. The same cap that yields an EMPTY book yields a
            # TRUNCATED one, and `save_orders_snapshot` whole-replaces, so a
            # partial arrival silently dropped the tracked working orders it
            # omitted from the book that drives modify and cancel (R-610).
            # Any tracked order missing from the read means the read is not a
            # picture of the book — skip, the next tick is the recovery path.
            returned_ids = {
                order_key(SimpleNamespace(permId=row.get("permId"), orderId=row.get("orderId")))
                for row in open_orders or []
                if isinstance(row, dict)
            }
            missing = [oid for oid in self.known_orders if oid not in returned_ids]
            if missing:
                logger.warning(
                    "fill_monitor: IB open-orders read omits %d tracked working "
                    "order(s) %s — skipping mirror",
                    len(missing),
                    sorted(missing)[:10],
                )
                return
            if not open_orders:
                # An empty book over a non-empty stored snapshot is the same
                # truncation with nothing tracked in memory to detect it
                # (T-382, R-579).
                if (
                    count_open_orders_for_mirror is None
                    or count_open_orders_for_mirror() > 0
                ):
                    logger.warning(
                        "fill_monitor: IB returned empty open-orders book but "
                        "existing snapshot is non-empty — skipping mirror"
                    )
                    return
            executed = fetch_executed_orders_for_mirror(client)
            # No heartbeat: the orders-sync service row belongs to orders-sync.
            save_orders_snapshot(
                build_orders_data_for_mirror(open_orders, executed, snapshot_at=snapshot_at),
                heartbeat=False,
            )
        except Exception as exc:  # noqa: BLE001 — never crash on mirror failure
            logger.warning("fill_monitor: orders snapshot mirror failed: %s", exc)
    
    @staticmethod
    def _structure_label(action: str, sec_type: str, strike: Any, right: Any, expiry: Any) -> str:
        type_label = {"STK": "Stock", "OPT": "Option", "BAG": "Spread"}.get(sec_type, sec_type)
        side_label = structure_side(action)
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

    @staticmethod
    def _executions_for(prev_order: Dict, key: int, session_fills: list) -> list:
        """This order's session executions: by permId, else by orderId."""
        perm_id = prev_order.get("perm_id") or 0
        order_id = prev_order.get("order_id", key)
        matched = []
        for fill in session_fills:
            execution = getattr(fill, "execution", None)
            if perm_id:
                if _strict_id(getattr(execution, "permId", None)) == perm_id:
                    matched.append(fill)
            elif getattr(execution, "orderId", None) == order_id:
                matched.append(fill)
        return matched

    @staticmethod
    def _completion_outcome(prev_order: Dict, executions: list) -> CancelOutcome:
        """Name a vanished order's outcome from its executions + last status."""
        if executions:
            total = FillMonitorHandler._execution_total(executions)
            quantity = prev_order.get("quantity")
            if total is not None and isinstance(quantity, (int, float)) and 0 < total < quantity:
                return CancelOutcome.PARTIALLY_FILLED_THEN_CANCELLED
            return CancelOutcome.FILLED
        if normalize_status(prev_order.get("status")) in TERMINAL_FAILED:
            return CancelOutcome.CANCELLED
        return CancelOutcome.UNKNOWN

    @staticmethod
    def _execution_total(executions: list) -> Optional[float]:
        try:
            return sum(float(f.execution.shares) for f in executions)
        except (AttributeError, TypeError, ValueError):
            return None

    def _persist_final_increment(self, prev_order: Dict, executions: list) -> None:
        """Journal the fill increment that landed after the last cycle.

        Only for a single-contract order whose executions all name its
        conId (a combo's leg executions cannot be summed against the BAG).
        """
        perm_id = prev_order.get("perm_id") or 0
        con_id = prev_order.get("con_id") or 0
        if not perm_id or not con_id:
            return
        contracts = [getattr(f, "contract", None) for f in executions]
        if any(_strict_id(getattr(c, "conId", None)) != con_id for c in contracts):
            return
        try:
            total = sum(float(f.execution.shares) for f in executions)
            notional = sum(float(f.execution.shares) * float(f.execution.price) for f in executions)
        except (AttributeError, TypeError, ValueError):
            return
        prev_filled = int(prev_order.get("filled") or 0)
        if total <= prev_filled:
            return
        total_filled = int(total)
        fill_info = {
            "order_id": prev_order.get("order_id"),
            "symbol": prev_order.get("symbol"),
            "contract": prev_order.get("contract"),
            "action": prev_order.get("action"),
            "newly_filled": total_filled - prev_filled,
            "total_filled": total_filled,
            "avg_price": notional / total,
            "prev_filled": prev_filled,
            "prev_avg_price": prev_order.get("avg_fill_price"),
        }
        order = SimpleNamespace(
            action=prev_order.get("action"), permId=perm_id, orderId=prev_order.get("order_id"),
        )
        self._persist_fill_to_journal(fill_info, contracts[0], order, None)

    def _lookup_prior_qty(self, contract: Any) -> Optional[float]:
        """Signed prior journal qty, or None when it cannot be read.

        None refuses the label: defaulting to 0 wrote a close as a phantom
        SELL_TO_OPEN. journal_sync re-imports the execution next cycle.
        """
        if get_db is None or prior_net_qty_for_contract is None:
            return None
        ticker = getattr(contract, "symbol", None)
        if not ticker:
            return None
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
            return None

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
        if perm_id <= 0:
            # journal_sync supersedes a mirror only by (conId, permId, date);
            # an orderId-keyed mirror would be a permanent duplicate. The
            # execution itself is imported by journal_sync.
            logger.info("fill_monitor: order %s has no permId yet; mirror skipped", order_id)
            return
        trade_id = (
            f"fill-monitor:con-{con_id}:order-{perm_id}:"
            f"{fill_date}:filled-{total_filled}"
        )

        sec_type = getattr(contract, "secType", "STK") or "STK"
        newly_filled = fill_info.get("newly_filled", 0)
        label = classify_fill(
            getattr(order, "action", None), sec_type, self._lookup_prior_qty(contract), newly_filled
        )
        if label is None:
            logger.warning(
                "fill_monitor: no journal label for order %s (side %r, prior qty unknown "
                "or unreadable); journal_sync imports the execution",
                order_id, getattr(order, "action", None),
            )
            return
        action = label.action.value

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
        payload.update(label.payload_fields())
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
    
    # Fixed AppleScript source: the text rides argv, so fill fields are data
    # to the script, never part of it.
    _NOTIFY_SCRIPT = (
        "on run argv\n"
        "  display notification (item 1 of argv) with title (item 2 of argv)\n"
        "end run"
    )

    def _send_notification(self, title: str, message: str) -> None:
        """Send macOS notification via osascript."""
        try:
            subprocess.run(
                ["osascript", "-e", self._NOTIFY_SCRIPT, message, title],
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
