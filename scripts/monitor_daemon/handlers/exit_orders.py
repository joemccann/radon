#!/usr/bin/env python3
"""
Exit Orders Handler - Places pending exit orders when IB will accept them.

IB rejects limit orders >40% from current market price. This handler:
- Monitors pending exit orders in the Turso journal
- Checks current market prices
- Places orders when they're within the 40% threshold
- Updates the Turso journal with order IDs
- Reconciles placed orders against IB open orders + executions

Each leg (``exit_orders.target`` / ``exit_orders.stop``) moves only through
``transition_exit_leg``:

    PENDING   --placed-->    PLACED
    PLACED    --filled-->    FILLED
    PLACED    --cancelled--> CANCELLED   (alerted unless an OCA sibling filled)
    PLACED    --expired-->   EXPIRED
    CANCELLED --requeued-->  PENDING     (operator re-place: requeue_exit_leg)
    EXPIRED   --requeued-->  PENDING
"""

import json
import logging
import re
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, NamedTuple, Optional

from ib_insync import Option, LimitOrder, StopLimitOrder, StopOrder

from .base import BaseHandler, et_session_date
from clients.ib_client import IBClient, DEFAULT_HOST
from utils import ib_order_status

logger = logging.getLogger(__name__)

DEFAULT_IB_PORT = 4001
# "auto" rotates across SUBPROCESS_ID_RANGE on connect. See
# fill_monitor.py for the rationale (CLOSE_WAIT survival on prev cycle's
# socket, feedback_ib_client_id_ranges.md).
DEFAULT_CLIENT_ID: int | str = "auto"

try:
    # REL-108 / NF-2: native libSQL can pin the GIL past a handler deadline.
    from knowledge.http_db import Connection as get_db
except ImportError:  # pragma: no cover - DB layer optional in unit tests
    get_db = None  # type: ignore[assignment]


def _utc_now_iso() -> str:
    """UTC-Z timestamp (R-029): every other journal writer stamps UTC; a
    naive local stamp here broke lexicographic-chronological ordering
    under ORDER BY COALESCE(filled_at, written_at)."""
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ExitLegStatus(str, Enum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ExitLegEvent(str, Enum):
    PLACED = "placed"        # IB acknowledged the order, or it was adopted
    FILLED = "filled"        # executions cover the leg's size
    CANCELLED = "cancelled"  # gone from IB open orders without a full fill
    EXPIRED = "expired"      # gone after the option's expiry
    REQUEUED = "requeued"    # an ended leg goes back to PENDING for re-placement


_EXIT_LEG_TRANSITIONS = {
    (ExitLegStatus.PENDING, ExitLegEvent.PLACED): ExitLegStatus.PLACED,
    (ExitLegStatus.PLACED, ExitLegEvent.FILLED): ExitLegStatus.FILLED,
    (ExitLegStatus.PLACED, ExitLegEvent.CANCELLED): ExitLegStatus.CANCELLED,
    (ExitLegStatus.PLACED, ExitLegEvent.EXPIRED): ExitLegStatus.EXPIRED,
    (ExitLegStatus.CANCELLED, ExitLegEvent.REQUEUED): ExitLegStatus.PENDING,
    (ExitLegStatus.EXPIRED, ExitLegEvent.REQUEUED): ExitLegStatus.PENDING,
}


class InvalidExitLegTransition(ValueError):
    """The event is not allowed from the leg's current status."""


def exit_leg_status(section: Any) -> Optional[ExitLegStatus]:
    """The leg's status, or None for anything this handler does not own
    (missing section, legacy ``PENDING_MANUAL`` / ``ACTIVE`` rows)."""
    if not isinstance(section, dict):
        return None
    try:
        return ExitLegStatus(section.get("status"))
    except ValueError:
        return None


def transition_exit_leg(
    section: Any,
    event: ExitLegEvent,
    *,
    at: str,
    order_id: Any = None,
    perm_id: Any = None,
    filled_quantity: Any = None,
) -> Dict[str, Any]:
    """The only way a leg changes status. Pure: returns a new section.

    Raises ``InvalidExitLegTransition`` for any (status, event) pair not in
    the table, and for a PLACED event without an integer ``order_id``.
    """
    current = exit_leg_status(section)
    target = _EXIT_LEG_TRANSITIONS.get((current, event))
    if target is None:
        raise InvalidExitLegTransition(
            f"exit leg {current.value if current else None!s} cannot take "
            f"event {event.value}"
        )
    leg = dict(section)
    leg["status"] = target.value
    if event is ExitLegEvent.PLACED:
        if isinstance(order_id, bool) or not isinstance(order_id, int):
            raise InvalidExitLegTransition("a PLACED leg needs an integer order_id")
        leg["order_id"] = order_id
        leg["perm_id"] = perm_id
        leg["placed_at"] = at
    elif event is ExitLegEvent.REQUEUED:
        leg["previous_order_id"] = leg.get("order_id")
        leg["order_id"] = None
        leg["perm_id"] = None
        leg["requeued_at"] = at
    else:
        leg["closed_at"] = at
        leg["filled_quantity"] = filled_quantity
    return leg


class ExitLegScan(NamedTuple):
    pending: List[Dict[str, Any]]
    placed: List[Dict[str, Any]]


def _valid_broker_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value != 0


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
        # (journal_trade_id, order_type) -> {"order_id", "perm_id"}. The
        # journal_trade_id is never None: a row without one cannot record a
        # placement, so it is never placed. Healed (journal marked PLACED,
        # then reconciled against IB) once the DB write succeeds; dropped
        # once the row no longer has a PENDING leg to protect.
        self._unrecorded_placements: Dict[tuple, Dict[str, Any]] = {}
        # REL-185 (R-518): legs refused by the order limits, keyed by
        # (journal_trade_id, localSymbol, order_type) -> (contracts, price,
        # limit_price). The limit is re-checked every cycle; the latch only
        # stops a repeat refusal of the same parameters being reported as a
        # new failure. Persisted so a restart does not re-report it.
        self._limit_refusals: Dict[tuple, tuple] = {}
        # Set after a corrupt or missing state file: the next cycle rebuilds
        # the guard from the broker before placing anything.
        self._reseed_guard = False

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

    def _journal_rows(self, db: Any) -> list:
        """REL-108 / NF-2: insertion pages, complete-or-failed within 30s."""
        rows = []
        cursor = 0
        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('exit-order journal scan deadline exceeded')
            page = db.execute(
                'SELECT trade_id, payload, rowid AS journal_rowid, '
                'COALESCE(filled_at, written_at) AS sort_at FROM journal '
                'WHERE rowid > ? ORDER BY rowid LIMIT ?', (cursor, 200),
            ).fetchall()
            if time.monotonic() >= deadline:
                raise TimeoutError('exit-order journal scan deadline exceeded')
            rows.extend(page)
            if len(page) < 200:
                # Preserve the prior newest-first placement priority after
                # scanning by insertion identity, including lexical backfills.
                rows.sort(key=lambda row: self._row_value(row, 3, 'sort_at') or '', reverse=True)
                return rows
            next_cursor = self._row_value(page[-1], 2, 'journal_rowid')
            if type(next_cursor) is not int or next_cursor <= cursor:
                raise RuntimeError('exit-order journal cursor did not advance')
            cursor = next_cursor
    
    @staticmethod
    def _leg_info(journal_trade_id: Any, trade: Dict[str, Any], order_type: str,
                  section: Dict[str, Any], row_status: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "trade_id": trade.get("id"),
            "ticker": trade.get("ticker"),
            "structure": trade.get("structure"),
            "order_type": order_type,
            "status": section.get("status"),
            "order_id": section.get("order_id"),
            "perm_id": section.get("perm_id"),
            "target_price": section.get("price"),
            "contracts": section.get("contracts"),
            # R-085: the oversell guard needs the POSITION
            # size, not the exit leg's size. Fall back to the
            # leg when the row predates the field.
            "held_contracts": trade.get("contracts"),
            "contract_spec": section.get("contract_spec"),
            # Stop leg only: a journal limit makes it a STP LMT.
            "limit_price": section.get("limit_price"),
            "action": "SELL",  # Exit orders are sells
            "journal_trade_id": journal_trade_id,
            # Both legs' statuses, so a cancel can be read against its
            # OCA sibling's fill.
            "row_status": row_status,
        }

    def _load_exit_legs(self) -> ExitLegScan:
        """PENDING legs to place and PLACED legs to reconcile, from one
        journal scan.

        Raises on a failed read (REL-007 / R-019): a swallowed failure
        here returned [] and heartbeated ok while exit protection
        silently stopped being placed.
        """
        pending: List[Dict[str, Any]] = []
        placed: List[Dict[str, Any]] = []

        try:
            rows = self._journal_rows(self._open_db())

            for row in rows:
                journal_trade_id = self._row_value(row, 0, "trade_id")
                trade = self._decode_payload(self._row_value(row, 1, "payload"))
                if not trade:
                    continue
                exit_orders = trade.get("exit_orders", {})
                if not isinstance(exit_orders, dict):
                    continue
                row_status = {
                    order_type: (exit_orders.get(order_type) or {}).get("status")
                    for order_type in ("target", "stop")
                    if isinstance(exit_orders.get(order_type), dict)
                }

                for order_type in ("target", "stop"):
                    section = exit_orders.get(order_type, {})
                    status = exit_leg_status(section)
                    if status is ExitLegStatus.PENDING:
                        pending.append(self._leg_info(
                            journal_trade_id, trade, order_type, section, row_status
                        ))
                    elif status is ExitLegStatus.PLACED and journal_trade_id:
                        placed.append(self._leg_info(
                            journal_trade_id, trade, order_type, section, row_status
                        ))

        except Exception as e:
            logger.error(f"Failed to load journal exit orders: {e}")
            raise RuntimeError(f"journal exit-order load failed: {e}") from e

        return ExitLegScan(pending=pending, placed=placed)

    def _load_pending_orders(self) -> List[Dict]:
        """PENDING exit legs from the Turso journal (raises on a failed read)."""
        return self._load_exit_legs().pending
    
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

    # Ack polling mirrors the single-leg deadline in ib_place_order.py.
    # An unacknowledged order (permId==0, PendingSubmit) has an unknown
    # outcome, so the journal must not be marked PLACED (REL-002 / R-003).
    ACK_DEADLINE_SECONDS = 6.0
    ACK_POLL_STEP_SECONDS = 0.25
    _TERMINAL_FAILED_STATUSES = ib_order_status.TERMINAL_FAILED
    _ACCEPTED_STATUSES = frozenset({
        ib_order_status.SUBMITTED,
        ib_order_status.PRE_SUBMITTED,
        ib_order_status.FILLED_STATUS,
    })

    @staticmethod
    def _ack_perm_id(trade: Any) -> Optional[int]:
        """permId as a real int, or None for mock/None/junk/bool.

        ib_insync writes ``openOrder`` onto ``Order.permId`` and
        ``orderStatus`` onto ``OrderStatus.permId``. Either nonzero id is
        an acknowledgement (REL-334 / R-745, same rule as
        ``ib_place_order._trade_perm_id``). Reading only the order object
        cancelled a live protective sell whose status was still
        PendingSubmit, left the journal PENDING, and let the next cycle
        place another. A bool is an int subclass and is still junk
        (T-023)."""
        for source in (
            getattr(trade, "order", None),
            getattr(trade, "orderStatus", None),
        ):
            perm_id = getattr(source, "permId", None)
            if isinstance(perm_id, bool) or not isinstance(perm_id, int):
                continue
            if perm_id != 0:
                return perm_id
        return None

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
        perm_id: Optional[int] = None,
    ) -> bool:
        """Move the leg PENDING -> PLACED with the broker's order identity.

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
                rows = self._journal_rows(db)

            target_trade_id = None
            target_trade = None
            for row in rows:
                candidate_id = self._row_value(row, 0, "trade_id")
                candidate = self._decode_payload(self._row_value(row, 1, "payload"))
                if not candidate:
                    continue
                if (journal_trade_id and candidate_id == journal_trade_id) \
                        or candidate.get("id") == trade_id:
                    target_trade_id = candidate_id
                    target_trade = candidate
                    break

            if target_trade_id is None or target_trade is None:
                raise RuntimeError(f"journal trade not found for id {trade_id}")

            if not self._persist_leg_transition(
                db, target_trade_id, target_trade, order_type,
                ExitLegEvent.PLACED, order_id=order_id, perm_id=perm_id,
            ):
                return False

            logger.info(f"Updated journal: trade {trade_id} {order_type} -> order #{order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update journal trade: {e}")
            return False

    def _record_leg_event(self, journal_trade_id: Any, order_type: str,
                          event: ExitLegEvent, **fields: Any) -> bool:
        """Apply one transition to the persisted leg; False on any failure
        (row missing, transition refused, write lost)."""
        if not journal_trade_id:
            return False
        try:
            db = self._open_db()
            rows = db.execute(
                "SELECT trade_id, payload FROM journal WHERE trade_id = ?",
                (journal_trade_id,),
            ).fetchall()
            trade = None
            for row in rows:
                trade = self._decode_payload(self._row_value(row, 1, "payload"))
                if trade:
                    break
            if not trade:
                raise RuntimeError(f"journal trade {journal_trade_id} not found")
            return self._persist_leg_transition(
                db, journal_trade_id, trade, order_type, event, **fields
            )
        except Exception as e:
            logger.error(
                f"Failed to record {event.value} for {journal_trade_id} {order_type}: {e}"
            )
            return False

    def requeue_exit_leg(self, journal_trade_id: Any, order_type: str) -> bool:
        """Operator re-place path: a CANCELLED or EXPIRED leg goes back to
        PENDING, so the next cycle places it again. Refused for any other
        status (a FILLED leg has nothing left to protect)."""
        return self._record_leg_event(journal_trade_id, order_type, ExitLegEvent.REQUEUED)

    def _persist_leg_transition(self, db: Any, target_trade_id: Any,
                                target_trade: Dict[str, Any], order_type: str,
                                event: ExitLegEvent, **fields: Any) -> bool:
        """Write ``transition_exit_leg`` of the row's current leg, then
        verify it by read-back. Raises ``InvalidExitLegTransition``."""
        exit_orders = target_trade.get("exit_orders") or {}
        leg = transition_exit_leg(
            exit_orders.get(order_type), event, at=_utc_now_iso(), **fields
        )
        exit_orders[order_type] = leg
        target_trade["exit_orders"] = exit_orders
        trade_id = target_trade.get("id")

        db.execute(
            """
            UPDATE journal
            SET payload = ?, written_at = ?
            WHERE trade_id = ?
            """,
            (
                json.dumps(target_trade),
                _utc_now_iso(),
                target_trade_id,
            ),
        )
        # The production HTTP statement autocommits and closes its
        # stream. Only an injected transaction-bearing store needs this.
        if self.db is not None and hasattr(db, "commit"):
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
            if section.get("status") == leg["status"] \
                    and section.get("order_id") == leg.get("order_id"):
                verified = True
                break
        if not verified:
            logger.error(
                f"Journal update for trade {trade_id} {order_type} did not "
                f"persist (lost write) — treating as failed"
            )
            return False
        return True

    def get_state(self) -> Dict[str, Any]:
        """State including the unrecorded-placements guard (REL-003).

        The guard marks orders LIVE at IB whose journal write failed; it
        must survive daemon restarts or every deploy re-arms the T-010
        double-place. JSON-safe list form for daemon_state.json.
        """
        with self.state_lock:
            state = super().get_state()
            state["unrecorded_placements"] = [
                [journal_trade_id, order_type, entry["order_id"], entry.get("perm_id")]
                for (journal_trade_id, order_type), entry
                in self._unrecorded_placements.items()
            ]
            state["limit_refusals"] = [
                [*key, *params] for key, params in self._limit_refusals.items()
            ]
            return state

    def set_state(self, state: Dict[str, Any]) -> None:
        """Restore state including the unrecorded-placements guard and the
        limit-refusal latch. A legacy guard entry keyed on a null journal
        id is dropped: that row can never record a placement, so it is no
        longer placed, and the null key suppressed unrelated rows."""
        super().set_state(state)
        restored: Dict[tuple, Dict[str, Any]] = {}
        for entry in state.get("unrecorded_placements", []) or []:
            try:
                journal_trade_id, order_type, order_id, *rest = entry
            except (TypeError, ValueError):
                logger.warning(f"Skipping malformed guard entry: {entry!r}")
                continue
            if not journal_trade_id or not _valid_broker_id(order_id):
                logger.warning(f"Skipping guard entry without an identity: {entry!r}")
                continue
            perm_id = rest[0] if rest and _valid_broker_id(rest[0]) else None
            restored[(journal_trade_id, order_type)] = {
                "order_id": order_id, "perm_id": perm_id,
            }
        refusals: Dict[tuple, tuple] = {}
        for entry in state.get("limit_refusals", []) or []:
            if isinstance(entry, list) and len(entry) == 6 and entry[0]:
                refusals[tuple(entry[:3])] = tuple(entry[3:])
            else:
                logger.warning(f"Skipping malformed limit-refusal entry: {entry!r}")
        with self.state_lock:
            self._unrecorded_placements = restored
            self._limit_refusals = refusals

    def seed_state_after_corrupt_load(self) -> None:
        """The daemon state file was corrupt or missing, so the guard is
        gone. Rebuild it from the broker on the next cycle, before any
        placement (see ``_seed_guard_from_broker``)."""
        with self.state_lock:
            self._reseed_guard = True

    # A PendingCancel SELL can still fill, so it counts as working.
    _ACTIVE_ORDER_STATUSES = ib_order_status.WORKING

    @staticmethod
    def _limit_price_key(value: Any) -> Optional[float]:
        """Comparable limit price, or None when IB gave us nothing usable."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value != value or value <= 0:  # NaN / unpriced
            return None
        return round(float(value), 4)

    @classmethod
    def _working_price_key(cls, order: Any) -> Optional[float]:
        """The price that identifies a working exit leg: the trigger
        (``auxPrice``) for a stop, the limit for anything else. IB reports
        a STP order's ``lmtPrice`` as unset, so keying on it alone never
        matched a live stop and the next cycle placed a second one."""
        order_type = getattr(order, "orderType", None)
        if isinstance(order_type, str) and order_type.upper().startswith("STP"):
            return cls._limit_price_key(getattr(order, "auxPrice", None))
        return cls._limit_price_key(getattr(order, "lmtPrice", None))

    @staticmethod
    def _exit_order(order_info: Dict[str, Any], contracts: Any, oca_group: str) -> Any:
        """The broker order for one exit leg.

        The target is a SELL limit. The stop is a SELL stop triggered at
        the stop price (a stop-limit when the journal carries a limit): a
        SELL limit at a stop price below the market is marketable and
        closes the position on placement.
        """
        common = dict(tif="GTC", ocaGroup=oca_group, ocaType=1)
        price = order_info["target_price"]
        if order_info["order_type"] != "stop":
            return LimitOrder("SELL", contracts, price, **common)
        limit_price = order_info.get("limit_price")
        if limit_price is not None:
            return StopLimitOrder("SELL", contracts, limit_price, price, **common)
        return StopOrder("SELL", contracts, price, **common)

    @staticmethod
    def _is_stop_order(order: Any) -> bool:
        order_type = getattr(order, "orderType", None)
        return isinstance(order_type, str) and order_type.upper().startswith("STP")

    @classmethod
    def _find_working_order(cls, open_trades: list, ident: Dict[str, Any]) -> Any:
        """The working trade for a leg's broker identity, or None.

        permId names the order globally. orderId is per client id and this
        handler rotates client ids, so it is used only for a leg recorded
        before perm_id was stored.
        """
        perm_id = ident.get("perm_id")
        order_id = ident.get("order_id")
        for trade in open_trades:
            if getattr(getattr(trade, "orderStatus", None), "status", None) \
                    not in cls._ACTIVE_ORDER_STATUSES:
                continue
            order = getattr(trade, "order", None)
            if _valid_broker_id(perm_id):
                if getattr(order, "permId", None) == perm_id:
                    return trade
            elif _valid_broker_id(order_id) and getattr(order, "orderId", None) == order_id:
                return trade
        return None

    @staticmethod
    def _open_orders_proven(client: Any) -> bool:
        """REL-331 / R-742: a snapshot whose end event did not arrive is not
        the whole book, so an order missing from it is not proven gone."""
        flag = getattr(client, "_open_orders_snapshot_ended", None)
        return flag if isinstance(flag, bool) else True

    @staticmethod
    def _executed_quantity(executions: list, leg: Dict[str, Any]) -> float:
        perm_id = leg.get("perm_id")
        order_id = leg.get("order_id")
        total = 0.0
        for item in executions:
            execution = getattr(item, "execution", item)
            if _valid_broker_id(perm_id):
                if getattr(execution, "permId", None) != perm_id:
                    continue
            elif getattr(execution, "orderId", None) != order_id:
                continue
            shares = getattr(execution, "shares", None)
            if isinstance(shares, (int, float)) and not isinstance(shares, bool) \
                    and shares == shares:
                total += float(shares)
        return total

    @staticmethod
    def _contract_expired(leg: Dict[str, Any]) -> bool:
        spec = leg.get("contract_spec")
        expiry = re.sub(r"\D", "", str((spec or {}).get("expiry") or ""))
        if len(expiry) != 8:
            return False
        return expiry < et_session_date().replace("-", "")

    def _seed_guard_from_broker(self, pending: list, open_trades: list,
                                result: Dict[str, Any]) -> None:
        """After a corrupt or missing state file: a working SELL of the
        leg's type (STP vs LMT) in the row's OCA group is this handler's
        own placement even when its price no longer matches (IB tick
        normalisation). Arm the guard for it so the leg is healed to
        PLACED, never placed a second time."""
        for leg in pending:
            journal_trade_id = leg.get("journal_trade_id")
            if not journal_trade_id:
                continue
            key = (journal_trade_id, leg["order_type"])
            group = self._oca_group(journal_trade_id, leg.get("trade_id"))
            want_stop = leg["order_type"] == "stop"
            matches = [
                trade for trade in open_trades
                if getattr(trade.orderStatus, "status", None) in self._ACTIVE_ORDER_STATUSES
                and getattr(trade.order, "action", None) == "SELL"
                and str(getattr(trade.order, "ocaGroup", "")) == group
                and self._is_stop_order(trade.order) == want_stop
            ]
            if not matches:
                continue
            order = matches[0].order
            with self.state_lock:
                self._unrecorded_placements.setdefault(key, {
                    "order_id": order.orderId,
                    "perm_id": order.permId if _valid_broker_id(order.permId) else None,
                })
            logger.warning(
                f"Re-armed exit guard after state reset: {journal_trade_id} "
                f"{leg['order_type']} -> working order #{order.orderId}"
            )
            if len(matches) > 1:
                self._note_error(result, (
                    f"{len(matches)} working {leg['order_type']} orders in "
                    f"{group}; placement suppressed, reconcile at the broker"
                ))
        with self.state_lock:
            self._reseed_guard = False

    def _heal_guarded_legs(self, pending: list, open_trades: list, placed: list,
                           result: Dict[str, Any]) -> list:
        """Retry the journal write for each guarded leg instead of placing
        it. A healed leg joins ``placed`` so this cycle's reconciliation
        checks it against the broker. A failed heal is an error every
        cycle (it used to ``continue`` under an ok heartbeat)."""
        remaining = []
        for leg in pending:
            key = (leg.get("journal_trade_id"), leg["order_type"])
            with self.state_lock:
                entry = self._unrecorded_placements.get(key)
            if entry is None:
                remaining.append(leg)
                continue
            if self._update_journal_trade(
                leg["trade_id"], leg["order_type"], entry["order_id"],
                leg["journal_trade_id"], perm_id=entry.get("perm_id"),
            ):
                with self.state_lock:
                    self._unrecorded_placements.pop(key, None)
                placed.append(dict(
                    leg, status=ExitLegStatus.PLACED.value,
                    order_id=entry["order_id"], perm_id=entry.get("perm_id"),
                ))
                continue
            where = (
                "live" if self._find_working_order(open_trades, entry) is not None
                else "no longer working"
            )
            self._note_error(result, (
                f"exit order #{entry['order_id']} for {leg['ticker']} "
                f"{leg['order_type']} is {where} at IB but the journal row still "
                f"reads PENDING; write retry failed, re-place suppressed"
            ))
        return remaining

    def _reconcile_placed_legs(self, client: Any, placed: list, open_trades: list,
                               result: Dict[str, Any]) -> None:
        """PLACED -> FILLED | CANCELLED | EXPIRED for every leg IB no longer
        reports as working. A CANCELLED leg is an error (the position is
        unprotected) unless its OCA sibling filled, which is what OCA does.

        Executions cover the current IB session only, so a fill between the
        last cycle of one day and the first of the next reads as CANCELLED:
        an alert that over-reports, never a silent gap or a re-place.
        """
        gone = [leg for leg in placed if self._find_working_order(open_trades, leg) is None]
        if not gone:
            return
        if not self._open_orders_proven(client):
            self._note_error(result, (
                f"open-order snapshot incomplete; {len(gone)} placed exit "
                f"leg(s) not reconciled this cycle"
            ))
            return
        try:
            executions = list(client.get_executions())
        except Exception as e:
            self._note_error(result, (
                f"executions unavailable ({e}); {len(gone)} placed exit "
                f"leg(s) not reconciled this cycle"
            ))
            return

        decided: Dict[tuple, tuple] = {}
        for leg in gone:
            filled = self._executed_quantity(executions, leg)
            size = leg.get("contracts")
            if isinstance(size, (int, float)) and not isinstance(size, bool) \
                    and size > 0 and filled >= size:
                event = ExitLegEvent.FILLED
            elif self._contract_expired(leg):
                event = ExitLegEvent.EXPIRED
            else:
                event = ExitLegEvent.CANCELLED
            decided[(leg["journal_trade_id"], leg["order_type"])] = (event, filled)

        for leg in gone:
            journal_trade_id, order_type = leg["journal_trade_id"], leg["order_type"]
            event, filled = decided[(journal_trade_id, order_type)]
            sibling = "stop" if order_type == "target" else "target"
            sibling_filled = (
                decided.get((journal_trade_id, sibling), (None,))[0] is ExitLegEvent.FILLED
                or (leg.get("row_status") or {}).get(sibling) == ExitLegStatus.FILLED.value
            )
            label = f"#{leg.get('order_id')} for {leg.get('ticker')} {order_type}"
            if not self._record_leg_event(
                journal_trade_id, order_type, event, filled_quantity=filled
            ):
                self._note_error(result, (
                    f"exit order {label} is no longer working at IB; could not "
                    f"record it as {event.value}"
                ))
                continue
            result.setdefault("reconciled", []).append({
                "journal_trade_id": journal_trade_id,
                "order_type": order_type,
                "order_id": leg.get("order_id"),
                "event": event.value,
                "filled_quantity": filled,
            })
            if event is ExitLegEvent.CANCELLED and not sibling_filled:
                self._note_error(result, (
                    f"exit order {label} is no longer working at IB and did not "
                    f"fill ({filled:g} of {leg.get('contracts')} executed); "
                    f"position may be unprotected"
                ))
                logger.error(result["errors"][-1])

    def _active_sell_index(self, open_trades: list) -> Dict[Any, list]:
        """Working SELL orders at IB, grouped by conId and localSymbol.

        Broker truth: before placing an exit, a working SELL on the same
        contract AT THE SAME LIMIT PRICE is a prior cycle's placement
        (crash between place and journal-update, lost write, restarted
        daemon) — adopt it, never duplicate it.

        T-055: the price is part of the identity. A target and a stop are
        two SELL limits on ONE contract, so a contract-only key hands the
        working target to the stop candidate and the stop never reaches
        IB.
        """
        index: Dict[Any, list] = {}
        for trade in open_trades:
            order = getattr(trade, "order", None)
            if getattr(order, "action", None) != "SELL":
                continue
            if getattr(trade.orderStatus, "status", None) not in self._ACTIVE_ORDER_STATUSES:
                continue
            contract = getattr(trade, "contract", None)
            con_id = getattr(contract, "conId", None)
            local_symbol = getattr(contract, "localSymbol", None)
            entry = (self._working_price_key(order), trade)
            if isinstance(con_id, int) and con_id:
                index.setdefault(("conid", con_id), []).append(entry)
            if isinstance(local_symbol, str) and local_symbol:
                index.setdefault(("local", local_symbol), []).append(entry)

        return index

    @staticmethod
    def _working_sells_for(index: Dict[Any, list], contract: Any) -> list:
        con_id = getattr(contract, "conId", None)
        if isinstance(con_id, int) and ("conid", con_id) in index:
            return index[("conid", con_id)]
        local_symbol = getattr(contract, "localSymbol", None)
        if isinstance(local_symbol, str) and ("local", local_symbol) in index:
            return index[("local", local_symbol)]
        return []

    @classmethod
    def _working_sell_quantity(cls, index: Dict[Any, list], contract: Any,
                               oca_group: Optional[str] = None) -> Optional[float]:
        """Unfilled SELL quantity working at IB on this contract that is
        NOT already OCA-linked to the row being placed.

        A sibling exit leg in the same OCA group cannot fill alongside
        this one — counting it would refuse the stop whenever the target
        is live (T-055). Anything else (a manual TWS order, a legacy
        pre-OCA placement, another row) does count.

        ``None`` means at least one such order's remaining size is
        unreadable — the caller must fail safe, never treat it as zero.
        """
        total = 0.0
        for _price, trade in cls._working_sells_for(index, contract):
            order = getattr(trade, "order", None)
            if oca_group is not None and \
                    str(getattr(order, "ocaGroup", "")) == oca_group:
                continue
            quantity = getattr(order, "totalQuantity", None)
            filled = getattr(getattr(trade, "orderStatus", None), "filled", 0)
            if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
                return None
            if quantity != quantity:  # NaN
                return None
            if isinstance(filled, bool) or not isinstance(filled, (int, float)) \
                    or filled != filled:
                filled = 0
            total += max(0.0, float(quantity) - float(filled))
        return total

    @staticmethod
    def _note_error(result: dict, message: str) -> None:
        """Accumulate per-leg errors (REL-185 / R-518): a limit refusal must
        not be overwritten by a later leg's ack error."""
        errors = result.setdefault("errors", [])
        errors.append(message)
        result["error"] = errors[0] if len(errors) == 1 else (
            f"{len(errors)} leg errors; first: {errors[0]}"
        )

    @staticmethod
    def _oca_group(journal_trade_id: Any, trade_id: Any) -> str:
        """One group per journal row: filling the target cancels that
        row's stop, and never another position's exit."""
        return f"radon-exit-{journal_trade_id if journal_trade_id is not None else trade_id}"

    def _find_active_sell(self, index: Dict[Any, list], contract: Any,
                          limit_price: Any) -> tuple:
        """(adoptable trade, identity-unresolved) for one exit candidate.

        Identity-unresolved means a working SELL sits on this contract but
        IB reported no usable limit price, so it can be neither matched to
        this leg nor ruled out as a duplicate — the caller defers.
        """
        working = self._working_sells_for(index, contract)
        if not working:
            return None, False

        wanted = self._limit_price_key(limit_price)
        if wanted is not None:
            for price, trade in working:
                if price == wanted:
                    return trade, False

        if any(price is None for price, _ in working):
            return None, True
        return None, False


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

        # Kill switch (REL-004): halted = no automated placements either.
        # Deliberate operator state, not an error — heartbeat stays ok.
        try:
            from trading_halt import is_trading_halted

            if is_trading_halted():
                result["halted"] = True
                logger.warning("Trading halted — exit-order placement suspended")
                return result
        except ImportError:
            pass

        # Both guards, at the placement funnel — the halt refuses first (it is a
        # deliberate operator state), the limits bound what a non-halted cycle
        # may send. R-427.
        from order_limits import check_order_limits

        try:
            scan = self._load_exit_legs()
        except Exception as e:
            # Soft failure: error heartbeat, retry next cycle (REL-007).
            result["error"] = str(e)
            return result

        # A guard whose row no longer has a PENDING leg protects nothing:
        # the row was placed, ended, or deleted. Drop it so it cannot
        # suppress anything later.
        pending_keys = {(leg["journal_trade_id"], leg["order_type"]) for leg in scan.pending}
        with self.state_lock:
            for key in [k for k in self._unrecorded_placements if k not in pending_keys]:
                logger.info(f"Dropping exit guard {key}: leg is no longer PENDING")
                del self._unrecorded_placements[key]

        if not scan.pending and not scan.placed:
            logger.debug("No pending or placed exit orders")
            return result

        pending = scan.pending
        placed = list(scan.placed)
        client = IBClient()

        try:
            client.connect(host=DEFAULT_HOST, port=self.ib_port, client_id=self.client_id)
            logger.debug("Connected to IB")

            # REL-003: broker truth before any placement. If the working-
            # order snapshot is unavailable we cannot rule out a live
            # duplicate — defer every placement this cycle (fail-safe).
            try:
                open_trades = list(client.get_open_orders())
                active_sells = self._active_sell_index(open_trades)
            except Exception as e:
                result["error"] = (
                    f"open-order cross-check unavailable ({e}); "
                    f"exit placements deferred this cycle"
                )
                logger.warning(result["error"])
                return result

            if self._reseed_guard:
                self._seed_guard_from_broker(pending, open_trades, result)
            pending = self._heal_guarded_legs(pending, open_trades, placed, result)
            self._reconcile_placed_legs(client, placed, open_trades, result)
            result["orders_checked"] = len(pending)

            for order_info in pending:
                ticker = order_info["ticker"]

                # The journal row is addressed by trade_id. Without one the
                # PLACED write cannot land, so a placement here would be an
                # order no cycle can account for (and its guard would key on
                # None, suppressing every other id-less row's leg).
                if not order_info.get("journal_trade_id"):
                    result["orders_skipped"] += 1
                    result["skipped"].append({
                        "ticker": ticker,
                        "order_type": order_info["order_type"],
                        "reason": "no_journal_identity",
                    })
                    self._note_error(result, (
                        f"{ticker} {order_info['order_type']} exit leg sits on a "
                        f"journal row with no trade_id; not placed"
                    ))
                    continue
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
                    live, unresolved = self._find_active_sell(
                        active_sells, contract, target_price
                    )
                    if live is None and unresolved:
                        result["orders_skipped"] += 1
                        result["skipped"].append({
                            "ticker": ticker,
                            "contract": contract.localSymbol,
                            "order_type": order_info["order_type"],
                            "reason": "working_sell_price_unknown",
                        })
                        self._note_error(result, (
                            f"working SELL on {contract.localSymbol} has no "
                            f"readable limit price; {order_info['order_type']} "
                            f"placement deferred (cannot rule out a duplicate)"
                        ))
                        logger.warning(result["errors"][-1])
                        continue
                    if live is not None:
                        live_order_id = live.order.orderId
                        live_perm_id = self._ack_perm_id(live)
                        adopted = self._update_journal_trade(
                            order_info["trade_id"],
                            order_info["order_type"],
                            live_order_id,
                            order_info.get("journal_trade_id"),
                            perm_id=live_perm_id,
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
                                order_info["journal_trade_id"],
                                order_info["order_type"],
                            )
                            with self.state_lock:
                                self._unrecorded_placements[guard_key] = {
                                    "order_id": live_order_id, "perm_id": live_perm_id,
                                }
                            self._note_error(result, (
                                f"live exit order #{live_order_id} found for "
                                f"{ticker} {order_info['order_type']} but journal "
                                f"adoption write failed; re-place suppressed"
                            ))
                            logger.error(result["errors"][-1])
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
                        # R-085: price-keyed adoption cannot recognise a
                        # working SELL at a different limit (the sibling
                        # exit leg, a manual TWS edit, an IB tick
                        # normalisation), so it would place a SECOND
                        # full-size SELL. Broker truth bounds the total.
                        held = order_info.get("held_contracts")
                        if not isinstance(held, (int, float)) or isinstance(held, bool):
                            held = contracts
                        oca_group = self._oca_group(
                            order_info.get("journal_trade_id"),
                            order_info.get("trade_id"),
                        )
                        working_qty = self._working_sell_quantity(
                            active_sells, contract, oca_group
                        )
                        if working_qty is None or working_qty + contracts > held:
                            detail = (
                                "unreadable" if working_qty is None
                                else f"{working_qty:g}"
                            )
                            result["orders_skipped"] += 1
                            result["skipped"].append({
                                "ticker": ticker,
                                "contract": contract.localSymbol,
                                "order_type": order_info["order_type"],
                                "reason": "oversell_guard",
                                "working_quantity": working_qty,
                                "held_contracts": held,
                            })
                            result["error"] = (
                                f"refusing {order_info['order_type']} on "
                                f"{contract.localSymbol}: {detail} working SELL "
                                f"+ {contracts} new would exceed {held} held"
                            )
                            logger.error(result["error"])
                            continue

                        # Place the order. Both exit legs of one journal
                        # row share an OCA group so the first fill cancels
                        # the sibling at IB, not one cycle later here.
                        exit_order = self._exit_order(order_info, contracts, oca_group)

                        # R-427: this was the only order-placing path left in
                        # the repo with the halt and NOT the limits, and unlike
                        # exit_order_service.py it is definitely running — a
                        # handler inside radon-monitor.service. The exit leg is
                        # sized from the existing position, so the fat-finger
                        # class is bounded in practice, but the notional branch
                        # was skipped entirely and a corrupt position size
                        # reached IB unbounded.
                        # T-412: journal_trade_id is NULL on legacy rows, so
                        # keying on it alone collapses every id-less leg onto
                        # (None, "target") and one refusal suppressed an
                        # unrelated position's exit. localSymbol is the
                        # qualified broker identity (symbol+expiry+strike+
                        # right) and is always present here.
                        refusal_key = (
                            order_info.get("journal_trade_id"),
                            contract.localSymbol,
                            order_info["order_type"],
                        )
                        refusal_params = (contracts, target_price, order_info.get("limit_price"))
                        limit_params = {
                            "type": "option",
                            "quantity": contracts,
                            "symbol": ticker,
                            "limitPrice": target_price,
                        }
                        if order_info["order_type"] == "stop":
                            limit_params["stopPrice"] = target_price
                            if order_info.get("limit_price") is not None:
                                limit_params["limitPrice"] = order_info["limit_price"]
                        # Re-checked every cycle: a raised cap must release
                        # the leg even though its parameters never changed.
                        violation = check_order_limits(limit_params)
                        with self.state_lock:
                            if violation is None:
                                self._limit_refusals.pop(refusal_key, None)
                                already_refused = False
                            else:
                                already_refused = (
                                    self._limit_refusals.get(refusal_key) == refusal_params
                                )
                                self._limit_refusals[refusal_key] = refusal_params
                        if already_refused:
                            # Already reported with these exact parameters.
                            result["orders_skipped"] += 1
                            result["skipped"].append({
                                "ticker": ticker,
                                "contract": contract.localSymbol,
                                "reason": "limit_refused_previously",
                            })
                            # T-410: the latch only suppresses the redundant
                            # re-place, not the fact that the position is
                            # still unprotected. Every cycle must heartbeat
                            # error until the refusal clears.
                            self._note_error(result, (
                                f"exit order still refused by the order limit "
                                f"({ticker} {order_info['order_type']}); "
                                f"position remains unprotected"
                            ))
                            continue
                        if violation:
                            logger.warning(
                                "Order limit refused the exit order for %s: %s",
                                ticker, violation["message"],
                            )
                            result["orders_failed"] = result.get("orders_failed", 0) + 1
                            result.setdefault("failed", []).append({
                                "ticker": ticker,
                                "contract": contract.localSymbol,
                                "error": violation["message"],
                            })
                            # T-313: the position stays unprotected, so
                            # this cycle must heartbeat error, not ok.
                            self._note_error(result, (
                                f"exit order refused by the order limit "
                                f"({ticker} {order_info['order_type']}): "
                                f"{violation['message']}"
                            ))
                            continue

                        trade = client.place_order(contract, exit_order)

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
                            self._note_error(result, (
                                f"exit order not acknowledged by IB "
                                f"({ticker} {order_info['order_type']}: "
                                f"{ack_status or 'no status'}); journal left PENDING"
                            ))
                            logger.warning(result["errors"][-1])
                            continue

                        order_id = trade.order.orderId
                        perm_id = self._ack_perm_id(trade)

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
                            perm_id=perm_id,
                        )
                        if not recorded:
                            guard_key = (
                                order_info["journal_trade_id"],
                                order_info["order_type"],
                            )
                            with self.state_lock:
                                self._unrecorded_placements[guard_key] = {
                                    "order_id": order_id, "perm_id": perm_id,
                                }
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
