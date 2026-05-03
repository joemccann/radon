#!/usr/bin/env python3
"""
Journal Sync Handler — Live trade-log replenishment from in-session fills.

Fits between the two endpoints of the trade-log refresh story:

    cold start / multi-day gap → journal_rehydrate.py (Flex Query, ≤365d)
    intraday, same socket      → this handler (ib_insync session cache, fast)

Because the monitor daemon is a long-lived process, ``client.get_fills()``
returns every execution observed since the daemon connected — no 24h
server-side window to fall off, no missing-day risk.

Each row appended carries an ``ib_exec_id`` so we dedupe against the
journal_rehydrate path on the next pass.

Failure mode:
    Any exception is captured into the handler result; ``trade_log.json``
    stays untouched. The next interval will retry.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseHandler
from clients.ib_client import IBClient, DEFAULT_HOST
from utils.atomic_io import atomic_save, verified_load

try:
    # Phase-3 dual-write: each new row also lands in Turso so app.radon.run
    # reads see fresh fills via the DB path, not just disk.
    from db.writer import upsert_journal_entry  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    upsert_journal_entry = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_TRADE_LOG = Path(__file__).parent.parent.parent.parent / "data" / "trade_log.json"
DEFAULT_IB_PORT = 4001
DEFAULT_CLIENT_ID = 72  # daemon range (70-89)


class JournalSyncHandler(BaseHandler):
    """Append fresh IB executions to ``trade_log.json``."""

    name = "journal_sync"
    interval_seconds = 300
    requires_market_hours = True

    def __init__(
        self,
        trade_log_path: Optional[Path] = None,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: int = DEFAULT_CLIENT_ID,
    ):
        super().__init__()
        self.trade_log_path = trade_log_path or DEFAULT_TRADE_LOG
        self.ib_port = ib_port
        self.client_id = client_id

    # -- public ------------------------------------------------------------

    def execute(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "imported": 0,
            "skipped": 0,
            "fills_seen": 0,
            "timestamp": datetime.now().isoformat(),
        }

        client = IBClient()
        try:
            client.connect(host=DEFAULT_HOST, port=self.ib_port, client_id=self.client_id)
            fills = client.get_fills()
        except Exception as exc:  # noqa: BLE001 — surface every failure
            logger.warning("journal_sync: IB fetch failed: %s", exc)
            result["error"] = str(exc)
            try:
                client.disconnect()
            except Exception:
                pass
            return result
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

        result["fills_seen"] = len(fills)
        if not fills:
            return result

        try:
            existing = self._load_existing()
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: failed to load trade_log: %s", exc)
            result["error"] = f"trade_log read failed: {exc}"
            return result

        candidates = self._fills_to_entries(fills, existing)
        result["imported"] = len(candidates)
        result["skipped"] = result["fills_seen"] - len(candidates)

        if candidates:
            existing["trades"].extend(candidates)
            atomic_save(str(self.trade_log_path), existing)
            self._dual_write(candidates)

        return result

    def _dual_write(self, candidates: List[Dict[str, Any]]) -> None:
        """Mirror new rows to the Turso ``journal`` table.

        Failures are logged and swallowed — the canonical source remains
        ``trade_log.json``; DB drift is repaired by the next bootstrap or
        rehydrate run.
        """
        if upsert_journal_entry is None:
            return
        for entry in candidates:
            try:
                upsert_journal_entry(
                    str(entry.get("ib_exec_id")),
                    entry,
                    filled_at=entry.get("filled_at") or entry.get("date"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("journal_sync: DB upsert failed: %s", exc)

    # -- internals ---------------------------------------------------------

    def _load_existing(self) -> Dict[str, Any]:
        try:
            data = verified_load(str(self.trade_log_path))
        except FileNotFoundError:
            data = {"trades": []}
        if "trades" not in data or not isinstance(data["trades"], list):
            data["trades"] = []
        return data

    def _existing_exec_ids(self, trades: List[Dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for trade in trades:
            exec_id = trade.get("ib_exec_id")
            if not exec_id:
                continue
            ids.add(str(exec_id))
            for part in str(exec_id).split("+"):
                if part:
                    ids.add(part)
        return ids

    def _fills_to_entries(
        self,
        fills: List[Any],
        existing: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        seen_ids = self._existing_exec_ids(existing["trades"])
        next_id = max((t.get("id", 0) for t in existing["trades"]), default=0) + 1

        rows: List[Dict[str, Any]] = []
        for fill in fills:
            entry = self._fill_to_entry(fill, next_id)
            if entry is None:
                continue
            if str(entry["ib_exec_id"]) in seen_ids:
                continue
            seen_ids.add(str(entry["ib_exec_id"]))
            rows.append(entry)
            next_id += 1
        return rows

    def _fill_to_entry(self, fill: Any, next_id: int) -> Optional[Dict[str, Any]]:
        execution = getattr(fill, "execution", None)
        contract = getattr(fill, "contract", None)
        commission_report = getattr(fill, "commissionReport", None)
        if execution is None or contract is None:
            return None

        exec_id = getattr(execution, "execId", None) or getattr(execution, "permId", None)
        if not exec_id:
            return None

        sec_type = getattr(contract, "secType", "STK")
        side = getattr(execution, "side", "")
        action = self._side_to_action(side, sec_type)
        if action is None:
            return None

        try:
            shares = Decimal(str(getattr(execution, "shares", 0) or 0))
        except Exception:
            shares = Decimal(0)
        try:
            price = float(getattr(execution, "price", 0) or 0)
        except Exception:
            price = 0.0

        commission = 0.0
        if commission_report is not None:
            try:
                commission = float(getattr(commission_report, "commission", 0) or 0)
            except Exception:
                commission = 0.0

        multiplier = 100 if sec_type in ("OPT", "BAG") else 1
        total_cost = float(shares) * price * multiplier + commission

        ib_time = getattr(execution, "time", None)
        date_str = ib_time.strftime("%Y-%m-%d") if isinstance(ib_time, datetime) else datetime.now().strftime("%Y-%m-%d")

        strike = getattr(contract, "strike", None) if sec_type in ("OPT", "BAG") else None
        right = getattr(contract, "right", None) if sec_type in ("OPT", "BAG") else None
        expiry = getattr(contract, "lastTradeDateOrContractMonth", None) if sec_type in ("OPT", "BAG") else None

        side_label = "BUY" if action in ("BUY", "BUY_OPTION") else "SELL"
        structure = self._structure_label(side_label, sec_type, strike, right, expiry)

        entry: Dict[str, Any] = {
            "id": next_id,
            "date": date_str,
            "ticker": getattr(contract, "symbol", ""),
            "structure": structure,
            "decision": "IB_AUTO_IMPORT",
            "action": action,
            "fill_price": round(price, 4),
            "total_cost": round(total_cost, 4),
            "commission": round(commission, 4),
            "ib_exec_id": str(exec_id),
            "notes": f"Imported from IB session fills on {datetime.now().strftime('%Y-%m-%d')}",
        }

        abs_qty = int(abs(shares))
        if sec_type in ("OPT", "BAG"):
            entry["contracts"] = abs_qty
            if strike:
                entry["strike"] = float(strike)
            if right:
                entry["right"] = right
            if expiry:
                entry["expiry"] = expiry
        else:
            entry["shares"] = abs_qty

        return entry

    @staticmethod
    def _side_to_action(side: str, sec_type: str) -> Optional[str]:
        upper = (side or "").upper()
        if upper in ("BOT", "BUY"):
            return "BUY" if sec_type == "STK" else "BUY_OPTION"
        if upper in ("SLD", "SELL"):
            return "SELL" if sec_type == "STK" else "SELL_TO_OPEN"
        return None

    @staticmethod
    def _structure_label(side: str, sec_type: str, strike: Any, right: Any, expiry: Any) -> str:
        type_label = {"STK": "Stock", "OPT": "Option", "BAG": "Spread"}.get(sec_type, sec_type)
        side_label = "Long" if side == "BUY" else "Closed"
        if sec_type in ("OPT", "BAG") and strike and right:
            right_label = "Call" if right == "C" else "Put" if right == "P" else right
            if expiry and len(str(expiry)) == 8 and str(expiry).isdigit():
                expiry_iso = f"{str(expiry)[0:4]}-{str(expiry)[4:6]}-{str(expiry)[6:8]}"
            else:
                expiry_iso = str(expiry) if expiry else ""
            strike_val = int(strike) if float(strike).is_integer() else strike
            suffix = f" {expiry_iso}" if expiry_iso else ""
            return f"{side_label} {right_label} ${strike_val}{suffix}"
        return f"{side_label} {type_label} ({sec_type})"
