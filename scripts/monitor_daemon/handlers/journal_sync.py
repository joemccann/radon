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
    Any exception is captured into the handler result. The next interval
    will retry.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseHandler, et_session_date
from clients.ib_client import IBClient, DEFAULT_HOST
from clients.journal_basis import normalize_expiry_compact, prior_net_qty_for_contract
from utils.atomic_io import atomic_save, verified_load
from utils.exec_ids import exec_id_root

try:
    # Phase-3 dual-write: each new row also lands in Turso so app.radon.run
    # reads see fresh fills via the DB path, not just disk.
    from db.writer import upsert_journal_entry  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    upsert_journal_entry = None  # type: ignore[assignment]

try:
    # REL-011: fill_monitor mirrors partial fills under a synthetic
    # `fill-monitor:…` trade_id so a daemon restart cannot drop them.
    # Once the real execId rows land here, the mirror is a duplicate the
    # blotter cannot dedupe — this handler deletes covered mirrors.
    from db.writer import delete_journal_entry  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    delete_journal_entry = None  # type: ignore[assignment]

try:
    # Used to look up prior signed net qty per contract so closing sells
    # label as SELL_OPTION instead of SELL_TO_OPEN. Optional — hosts
    # without libsql fall back to prior_qty=0 (= old behaviour).
    from db.client import get_db  # type: ignore
except ImportError:  # pragma: no cover — DB layer optional in unit tests
    get_db = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

DEFAULT_IB_PORT = 4001
# "auto" rotates across SUBPROCESS_ID_RANGE on each cycle. See
# fill_monitor.py for the rationale.
DEFAULT_CLIENT_ID: int | str = "auto"
# Disk rows older than this are not retried by the reconciler — they're left
# to journal_rehydrate / the manual backfill script.
RECONCILE_WINDOW_DAYS = 14
# Synthetic trade_id written by fill_monitor._persist_fill_to_journal —
# con-/order-/date pin the execution's contract + order + ET session date,
# filled-N is the order's running fill total at detection time.
FILL_MONITOR_PREFIX = "fill-monitor:"
_FILL_MONITOR_TRADE_ID = re.compile(
    r"^fill-monitor:con-(?P<con>\d+):order-(?P<order>\d+):"
    r"(?P<date>\d{4}-\d{2}-\d{2}):filled-(?P<filled>\d+)$"
)


class JournalSyncHandler(BaseHandler):
    """Append fresh IB executions to the Turso journal."""

    name = "journal_sync"
    interval_seconds = 300
    requires_market_hours = True
    # Two more 300s cycles after the 16:00 ET close sweep fills that land in
    # the session's final seconds. The per-cycle IB connection only sees the
    # current day's executions, so without grace a fill between the last
    # in-hours cycle and the close is never journaled (15:59:52 / 15:58:04
    # incidents). Consumed by the daemon gate via market_state().
    post_close_grace_minutes = 15
    service_name = "journal-sync"  # structural heartbeat via BaseHandler.run()

    def __init__(
        self,
        trade_log_path: Optional[Path] = None,
        ib_port: int = DEFAULT_IB_PORT,
        client_id: "int | str" = DEFAULT_CLIENT_ID,
    ):
        super().__init__()
        self.trade_log_path = trade_log_path
        self.ib_port = ib_port
        self.client_id = client_id

    # -- public ------------------------------------------------------------

    def execute(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "imported": 0,
            "skipped": 0,
            "superseded": 0,
            "fills_seen": 0,
            "reconciled": 0,
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

        db = self._open_db()

        try:
            if self.trade_log_path is None:
                # Production wiring: the journal table IS the dedupe source.
                # An EMPTY table is a legitimate state (fresh host, first
                # session) — only a failed READ may abort the cycle (T-015).
                existing = self._load_existing_from_journal(db, allow_empty=True)
                recovery = None
            else:
                existing, recovery = self._load_existing_with_recovery(db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: failed to load journal state: %s", exc)
            prefix = "trade_log read failed" if self.trade_log_path is not None else "journal read failed"
            result["error"] = f"{prefix}: {exc}"
            return result

        if recovery:
            result["recovered_trade_log_from_journal"] = recovery["journal_rows"]
            result["salvaged_trade_log_rows"] = recovery["salvaged_rows"]
            backup = self._backup_corrupt_trade_log()
            if backup is not None:
                result["trade_log_recovery_backup"] = backup.name
            try:
                atomic_save(str(self.trade_log_path), existing)
            except Exception as exc:  # noqa: BLE001
                logger.warning("journal_sync: failed to repair trade_log mirror: %s", exc)
                result["error"] = f"trade_log repair failed: {exc}"
                return result

        # Phase 1: reconcile disk rows whose DB upsert previously failed.
        # This re-attempts any exec_id on disk that is absent from the journal
        # table, so a single transient Turso failure cannot permanently drop a fill.
        result["reconciled"] = (
            self._reconcile_db_missing(existing, db)
            if self.trade_log_path is not None
            else 0
        )
        if result["reconciled"]:
            self._maybe_heal_reconcile(db)

        written: List[Dict[str, Any]] = []
        if fills:
            candidates, corrections = self._fills_to_entries(fills, existing, db=db)
            result["imported"] = len(candidates)
            result["superseded"] = len(corrections)
            result["skipped"] = result["fills_seen"] - len(candidates) - len(corrections)

            if candidates or corrections:
                # Corrections carry the superseded row's ib_exec_id, so the upsert
                # REPLACES that journal row instead of adding a second one.
                written, failed = self._dual_write(candidates + corrections)
                if failed:
                    result["db_write_failures"] = failed
                    result["error"] = (
                        f"{failed} journal upsert(s) failed this cycle "
                        f"(write-only Turso failure; will re-import from "
                        f"get_fills() next cycle)"
                    )
                if self.trade_log_path is not None:
                    existing["trades"].extend(candidates)
                    self._apply_corrections(existing, corrections)
                    atomic_save(str(self.trade_log_path), existing)
                # A live fill that filled a previously-missing exec_id may have
                # closed the gap the daily journal-reconcile flagged.
                self._maybe_heal_reconcile(db)

        result["mirror_superseded"] = self._supersede_fill_monitor_mirrors(
            db, existing.get("trades", []), written
        )
        return result

    def _reconcile_db_missing(
        self,
        existing: Dict[str, Any],
        db: Any,
    ) -> int:
        """Re-upsert disk rows whose DB write previously failed and was swallowed.

        Queries the journal table for the set of exec_ids already confirmed in DB,
        then walks recent disk rows and re-attempts the upsert for any that are
        missing. Bounded to RECONCILE_WINDOW_DAYS to keep work O(recent rows).
        Returns the count of rows re-upserted this cycle.
        """
        if upsert_journal_entry is None or db is None:
            return 0

        from datetime import date as _date  # avoid shadowing datetime in module scope

        journal_covered = self._journal_exec_ids_from_db(db)
        cutoff_date = (_date.today() - timedelta(days=RECONCILE_WINDOW_DAYS))

        reconciled = 0
        for trade in existing.get("trades", []):
            row_date_str = trade.get("date") or ""
            try:
                row_date = datetime.strptime(row_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if row_date < cutoff_date:
                continue

            exec_id = str(trade.get("ib_exec_id") or "").strip()
            if not exec_id:
                continue

            parts = [exec_id] + [p for p in exec_id.split("+") if p]
            if any(p in journal_covered for p in parts):
                continue

            try:
                upsert_journal_entry(
                    exec_id,
                    trade,
                    filled_at=trade.get("filled_at") or trade.get("date"),
                )
                logger.info("journal_sync: reconciled disk-only row %s", exec_id)
                reconciled += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("journal_sync: reconcile upsert failed for %s: %s", exec_id, exc)

        return reconciled

    @staticmethod
    def _maybe_heal_reconcile(db: Any) -> None:
        """JRN-03: after writing journal rows, clear the daily journal-reconcile
        error row if its flagged gaps are now covered — so the watchdog stops
        re-alerting a gap that's already repaired, rather than waiting ~24h for
        the next reconcile pass. Cheap (gated on the error state) and best-effort.
        """
        if db is None:
            return
        try:
            from .journal_reconcile import heal_journal_reconcile_if_recovered

            heal_journal_reconcile_if_recovered(db)
        except Exception as exc:  # noqa: BLE001 — never break a write path on a heal probe
            logger.warning("journal_sync: reconcile heal check failed: %s", exc)

    @staticmethod
    def _journal_exec_ids_from_db(db: Any) -> set[str]:
        """Return the set of exec_id parts confirmed in the journal table.

        Queries trade_id (the primary key, which equals ib_exec_id for live-ingest
        rows) and splits any '+'-joined composites so individual part IDs are
        also marked as covered.
        """
        import json as _json

        covered: set[str] = set()
        try:
            rows = db.execute("SELECT trade_id FROM journal").fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: journal coverage query failed: %s", exc)
            return covered
        for row in rows:
            raw = row[0] if isinstance(row, (tuple, list)) else getattr(row, "trade_id", None)
            if not raw:
                continue
            raw_str = str(raw)
            # Handle legacy mocks / payloads stored as JSON: extract ib_exec_id.
            if raw_str.startswith("{"):
                try:
                    payload = _json.loads(raw_str)
                    raw_str = str(payload.get("ib_exec_id") or raw_str)
                except Exception:  # noqa: BLE001
                    pass
            covered.add(raw_str)
            for part in raw_str.split("+"):
                part = part.strip()
                if part:
                    covered.add(part)
        return covered

    def _dual_write(self, candidates: List[Dict[str, Any]]) -> tuple:
        """Mirror new rows to the Turso ``journal`` table.

        Returns (written_entries, failed_count). Failures are logged AND
        counted so the cycle can heartbeat error (REL-007 / R-020) — in
        production (trade_log_path=None) there is no per-cycle reconcile
        retry. Only entries whose upsert SUCCEEDED may count as coverage
        when superseding fill_monitor mirror rows (REL-011). Recovery for
        failures remains the next cycle's ``get_fills()`` (dedupe source
        is the DB, so an unwritten fill re-imports).
        """
        written: List[Dict[str, Any]] = []
        failed = 0
        if upsert_journal_entry is None:
            return written, failed
        for entry in candidates:
            try:
                upsert_journal_entry(
                    str(entry.get("ib_exec_id")),
                    entry,
                    filled_at=entry.get("filled_at") or entry.get("date"),
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("journal_sync: DB upsert failed: %s", exc)
                continue
            written.append(entry)
        return written, failed

    def _supersede_fill_monitor_mirrors(
        self,
        db: Any,
        existing_trades: List[Dict[str, Any]],
        written: List[Dict[str, Any]],
    ) -> int:
        """Delete fill_monitor mirror rows whose fills are covered by execId rows.

        fill_monitor journals each detected partial fill under a synthetic
        ``fill-monitor:con-…:order-…:{date}:filled-N`` key so a daemon restart
        between detection and this handler's next cycle cannot lose the fill.
        Once the REAL executions (keyed by IB execId, carrying con_id/perm_id/
        session date) are in the journal, the mirror is a duplicate that
        ``fromJournal.ts:dedupeJournalRows`` cannot merge (REL-011 / R-010).

        A mirror is deleted only when the execId rows for the same contract +
        order + session date cover its running fill count ``N`` — a failed
        upsert therefore never deletes the only record of a fill.
        """
        if db is None or delete_journal_entry is None:
            return 0
        mirrors = self._fill_monitor_mirror_rows(db)
        if not mirrors:
            return 0

        covered = self._exec_qty_by_order(list(existing_trades) + list(written))
        deleted = 0
        for trade_id, order_key, filled_count in mirrors:
            if covered.get(order_key, 0.0) < filled_count:
                continue
            try:
                delete_journal_entry(trade_id)
            except Exception as exc:  # noqa: BLE001 — never crash the cycle
                logger.warning("journal_sync: mirror supersede failed for %s: %s", trade_id, exc)
                continue
            logger.info("journal_sync: superseded fill_monitor mirror %s", trade_id)
            deleted += 1
        return deleted

    @staticmethod
    def _fill_monitor_mirror_rows(db: Any) -> List[tuple]:
        """Parse fill_monitor mirror trade_ids → (trade_id, (con, order, date), N)."""
        mirrors: List[tuple] = []
        try:
            rows = db.execute(
                "SELECT trade_id FROM journal WHERE trade_id LIKE 'fill-monitor:%'"
            ).fetchall()
            for row in rows:
                raw = row[0] if isinstance(row, (tuple, list)) else getattr(row, "trade_id", None)
                match = _FILL_MONITOR_TRADE_ID.match(str(raw or ""))
                if match is None:
                    continue
                order_key = (int(match["con"]), int(match["order"]), match["date"])
                mirrors.append((str(raw), order_key, int(match["filled"])))
        except Exception as exc:  # noqa: BLE001 — scan is best-effort
            logger.warning("journal_sync: fill-monitor mirror scan failed: %s", exc)
            return []
        return mirrors

    @classmethod
    def _exec_qty_by_order(cls, trades: List[Dict[str, Any]]) -> Dict[tuple, float]:
        """(con_id, perm_id, date) → total qty across execId journal rows.

        Deduped on exec-id root so a correction row queued this cycle does
        not double-count the existing row it replaces.
        """
        by_root: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            exec_id = str(trade.get("ib_exec_id") or "").strip()
            if not exec_id or exec_id.startswith(FILL_MONITOR_PREFIX):
                continue
            root, _correction = exec_id_root(exec_id)
            by_root[root or exec_id] = trade

        covered: Dict[tuple, float] = {}
        for trade in by_root.values():
            order_key = cls._trade_order_key(trade)
            if order_key is None:
                continue
            qty_raw = trade.get("contracts", trade.get("shares"))
            try:
                qty = abs(float(qty_raw))
            except (TypeError, ValueError):
                continue
            covered[order_key] = covered.get(order_key, 0.0) + qty
        return covered

    @staticmethod
    def _trade_order_key(trade: Dict[str, Any]) -> Optional[tuple]:
        """The (con_id, perm_id, date) identity a fill-monitor key pins."""
        try:
            con_id = int(float(trade.get("con_id")))  # type: ignore[arg-type]
            perm_id = int(float(trade.get("perm_id")))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        date = str(trade.get("date") or "").strip()
        if not date:
            return None
        return (con_id, perm_id, date)

    @staticmethod
    def _apply_corrections(
        existing: Dict[str, Any],
        corrections: List[Dict[str, Any]],
    ) -> None:
        """Overwrite superseded rows in the disk mirror — never append.

        A correction keeps the superseded row's ``ib_exec_id`` (= its Turso
        trade_id), so the mirror stays a faithful copy of the journal table.
        """
        by_exec_id = {str(trade.get("ib_exec_id")): trade for trade in existing["trades"]}
        for entry in corrections:
            target = by_exec_id.get(str(entry.get("ib_exec_id")))
            if target is None:
                existing["trades"].append(entry)
                continue
            target.clear()
            target.update(entry)

    # -- internals ---------------------------------------------------------

    def _load_existing_with_recovery(self, db: Any) -> tuple[Dict[str, Any], Optional[Dict[str, int]]]:
        try:
            return self._load_existing(), None
        except Exception as exc:  # noqa: BLE001
            if not self._is_recoverable_trade_log_error(exc):
                raise

            journal_data = self._load_existing_from_journal(db)
            if not journal_data.get("trades"):
                raise

            salvaged_data = self._load_unverified_trade_log()
            recovered = self._merge_trade_logs(journal_data, salvaged_data)
            if not recovered.get("trades"):
                raise

            logger.warning(
                "journal_sync: recovered trade_log mirror after %s "
                "(journal_rows=%s, salvaged_rows=%s)",
                exc,
                len(journal_data.get("trades", [])),
                len(salvaged_data.get("trades", [])),
            )
            return recovered, {
                "journal_rows": len(journal_data.get("trades", [])),
                "salvaged_rows": len(salvaged_data.get("trades", [])),
            }

    def _load_existing(self) -> Dict[str, Any]:
        try:
            data = verified_load(str(self.trade_log_path))
        except FileNotFoundError:
            data = {"trades": []}
        return self._normalize_trade_log(data)

    @staticmethod
    def _is_recoverable_trade_log_error(exc: Exception) -> bool:
        if isinstance(exc, json.JSONDecodeError):
            return True
        return isinstance(exc, ValueError) and "Checksum mismatch" in str(exc)

    def _load_existing_from_journal(self, db: Any, allow_empty: bool = False) -> Dict[str, Any]:
        """Load the trade set from the journal table.

        ``allow_empty=False`` (trade_log RECOVERY): an empty journal cannot
        rebuild a mirror, so it raises. ``allow_empty=True`` (production
        no-mirror wiring): empty means "no prior fills" and returns an empty
        trade set — raising here bricked the importer's first-ever cycle.
        """
        if db is None:
            raise RuntimeError("journal DB unavailable for trade_log recovery")
        try:
            rows = db.execute(
                """
                SELECT trade_id, payload, filled_at, written_at
                FROM journal
                ORDER BY COALESCE(filled_at, written_at), written_at
                """
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"journal DB recovery query failed: {exc}") from exc

        trades: List[Dict[str, Any]] = []
        for row in rows:
            trade_id = self._row_value(row, 0, "trade_id")
            payload_raw = self._row_value(row, 1, "payload")
            filled_at = self._row_value(row, 2, "filled_at")
            written_at = self._row_value(row, 3, "written_at")
            try:
                payload = self._decode_journal_payload(payload_raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("journal_sync: skipped invalid journal payload %s: %s", trade_id, exc)
                continue
            if not isinstance(payload, dict):
                continue

            trade = dict(payload)
            if trade_id and not trade.get("ib_exec_id"):
                trade["ib_exec_id"] = str(trade_id)
            if filled_at and not trade.get("filled_at"):
                trade["filled_at"] = str(filled_at)
            if not trade.get("date"):
                timestamp = filled_at or written_at
                if timestamp:
                    trade["date"] = str(timestamp)[:10]
            trades.append(trade)

        if not trades and not allow_empty:
            raise RuntimeError("journal table is empty; cannot recover trade_log mirror")
        return self._normalize_trade_log({"trades": trades})

    def _load_unverified_trade_log(self) -> Dict[str, Any]:
        """Best-effort salvage for valid JSON whose checksum no longer matches."""
        try:
            with self.trade_log_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: unverified trade_log salvage failed: %s", exc)
            return {"trades": []}
        return self._normalize_trade_log(data if isinstance(data, dict) else {"trades": []})

    @staticmethod
    def _decode_journal_payload(payload_raw: Any) -> Dict[str, Any]:
        if isinstance(payload_raw, dict):
            return payload_raw
        if isinstance(payload_raw, bytes):
            payload_raw = payload_raw.decode("utf-8")
        if isinstance(payload_raw, str):
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("journal payload is not an object")

    @staticmethod
    def _row_value(row: Any, index: int, attr: str) -> Any:
        if isinstance(row, (tuple, list)):
            return row[index] if len(row) > index else None
        return getattr(row, attr, None)

    def _merge_trade_logs(self, primary: Dict[str, Any], secondary: Dict[str, Any]) -> Dict[str, Any]:
        merged: List[Dict[str, Any]] = []
        seen_exec_ids: set[str] = set()
        for source in (primary, secondary):
            for trade in source.get("trades", []):
                if not isinstance(trade, dict):
                    continue
                exec_id = str(trade.get("ib_exec_id") or "").strip()
                parts = [exec_id] + [part.strip() for part in exec_id.split("+") if part.strip()]
                if exec_id and any(part in seen_exec_ids for part in parts):
                    continue
                merged.append(dict(trade))
                for part in parts:
                    if part:
                        seen_exec_ids.add(part)
        return self._normalize_trade_log({"trades": merged})

    @staticmethod
    def _normalize_trade_log(data: Dict[str, Any]) -> Dict[str, Any]:
        trades_raw = data.get("trades") if isinstance(data, dict) else []
        trades = [dict(trade) for trade in trades_raw if isinstance(trade, dict)] if isinstance(trades_raw, list) else []

        used_ids: set[int] = set()
        next_id = 1
        for trade in trades:
            raw_id = trade.get("id")
            try:
                trade_id = int(raw_id)
            except (TypeError, ValueError):
                trade_id = 0
            if trade_id <= 0 or trade_id in used_ids:
                while next_id in used_ids:
                    next_id += 1
                trade_id = next_id
            trade["id"] = trade_id
            used_ids.add(trade_id)
            next_id = max(next_id, trade_id + 1)
        return {"trades": trades}

    def _backup_corrupt_trade_log(self) -> Optional[Path]:
        if not self.trade_log_path.exists():
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.trade_log_path.with_name(f"{self.trade_log_path.name}.corrupt.{stamp}")
        suffix = 1
        while backup.exists():
            backup = self.trade_log_path.with_name(f"{self.trade_log_path.name}.corrupt.{stamp}.{suffix}")
            suffix += 1
        try:
            shutil.copy2(self.trade_log_path, backup)
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: failed to back up corrupt trade_log: %s", exc)
            return None
        return backup

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

    @staticmethod
    def _correction_index(trades: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Map exec-id root → the already-imported row that owns it.

        ``ib_exec_id`` stays the row's stable identity (its Turso trade_id);
        ``ib_exec_id_corrected`` records the highest correction absorbed, so a
        re-delivered correction is skipped instead of re-written every cycle.
        Ids with no IB correction suffix — including composites — are absent
        and keep plain exact-match dedupe.
        """
        index: Dict[str, Dict[str, Any]] = {}
        for trade in trades:
            exec_id = str(trade.get("ib_exec_id") or "").strip()
            if not exec_id:
                continue
            root, correction = exec_id_root(trade.get("ib_exec_id_corrected") or exec_id)
            if correction <= 0:
                continue
            known = index.get(root)
            if known is None or correction > known["correction"]:
                index[root] = {"correction": correction, "row": trade, "in_cycle": False}
        return index

    @staticmethod
    def _trade_signed_qty(trade: Dict[str, Any]) -> float:
        """Signed qty of an already-written journal row (BUY +, SELL/CLOSED −).

        Mirrors ``clients.journal_basis._signed_qty`` — the same convention
        that produced the prior-qty this rebases.
        """
        qty_raw = trade.get("contracts")
        if qty_raw is None:
            qty_raw = trade.get("shares")
        try:
            qty = abs(float(qty_raw))
        except (TypeError, ValueError):
            return 0.0
        action = str(trade.get("action") or "").upper()
        if action.startswith("BUY"):
            return qty
        if action.startswith("SELL") or action.startswith("SHORT") or action == "CLOSED":
            return -qty
        return 0.0

    def _fills_to_entries(
        self,
        fills: List[Any],
        existing: Dict[str, Any],
        *,
        db: Any = None,
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Split this cycle's fills into (new rows, correction rows).

        A correction row SUPERSEDES an execution already in the journal: IB
        re-delivers a corrected execution as ``<root>.02`` against the
        original ``<root>.01``, so it must replace that row's quantity/cost
        under the SAME trade_id, never append a second row (T-035). A
        lower-or-equal correction number is a stale re-delivery and is
        dropped.
        """
        seen_ids = self._existing_exec_ids(existing["trades"])
        corrected_roots = self._correction_index(existing["trades"])
        next_id = max((t.get("id", 0) for t in existing["trades"]), default=0) + 1

        # Per-contract running signed net qty. Seeded lazily from the
        # journal DB on first sight of each contract, then mutated in
        # place as we walk this cycle's fills so a back-to-back sell of
        # the same long still reads as a close.
        if db is None:
            db = self._open_db()
        prior_state: Dict[str, float] = {}

        rows: List[Dict[str, Any]] = []
        corrections: List[Dict[str, Any]] = []
        # Walk fills in EXECUTION-TIME order, not delivery order: IB can
        # replay executions out of order (corrections after a reconnect,
        # cross-session replay), and labelling a close before its own
        # opener sees prior_qty=0 → SELL_TO_OPEN phantom short (T-014).
        # Same rule as journal_rehydrate.py / the backfill importer. The
        # sort is stable, so unknown-time fills keep delivery order.
        for fill in sorted(fills, key=self._fill_exec_sort_key):
            exec_id = self._fill_exec_id(fill)
            root, correction = exec_id_root(exec_id)
            superseded = corrected_roots.get(root) if correction > 0 else None
            if superseded is not None:
                if correction <= superseded["correction"]:
                    continue  # stale re-delivery of an already-corrected fill
            elif exec_id and exec_id in seen_ids:
                continue

            contract_key = self._fill_contract_key(fill)
            if contract_key and contract_key not in prior_state:
                prior_state[contract_key] = self._lookup_prior_qty(db, fill)

            prior_qty = prior_state.get(contract_key, 0.0) if contract_key else 0.0
            if superseded is not None:
                # The correction REPLACES the earlier fill, so it must be
                # labelled against the position as it stood BEFORE that fill —
                # otherwise a corrected sell reads its own predecessor as
                # already-closed and relabels to SELL_TO_OPEN.
                prior_qty -= self._trade_signed_qty(superseded["row"])

            entry = self._fill_to_entry(fill, next_id, prior_qty=prior_qty)
            if entry is None:
                continue

            if superseded is None:
                rows.append(entry)
                next_id += 1
                seen_ids.add(str(entry["ib_exec_id"]))
            else:
                entry = self._as_correction_of(entry, superseded, exec_id)
                if not superseded["in_cycle"]:
                    corrections.append(entry)
            if correction > 0:
                corrected_roots[root] = {
                    "correction": correction,
                    "row": entry,
                    "in_cycle": True,
                }

            if contract_key:
                prior_state[contract_key] = prior_qty + self._fill_signed_qty(fill)
        return rows, corrections

    @staticmethod
    def _as_correction_of(
        entry: Dict[str, Any],
        superseded: Dict[str, Any],
        exec_id: str,
    ) -> Dict[str, Any]:
        """Re-key a freshly built entry onto the row it supersedes.

        Rows produced earlier in THIS cycle are rewritten in place (they are
        already queued for the write), so a second correction of the same
        execution never queues a second row.
        """
        target = superseded["row"]
        entry["id"] = target.get("id", entry["id"])
        entry["ib_exec_id"] = str(target.get("ib_exec_id") or entry["ib_exec_id"])
        entry["ib_exec_id_corrected"] = str(exec_id)
        if not superseded["in_cycle"]:
            return entry
        target.clear()
        target.update(entry)
        return target

    @staticmethod
    def _open_db() -> Any:
        """Open the Turso/libsql client used for prior-qty lookups.

        Returns ``None`` when the DB layer is unavailable (unit tests,
        hosts without libsql) — callers must treat that as "no prior
        history, default prior_qty=0".
        """
        if get_db is None:
            return None
        try:
            return get_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: prior-qty DB unavailable: %s", exc)
            return None

    @staticmethod
    def _fill_exec_sort_key(fill: Any) -> tuple:
        """Numeric, exception-proof ordering key for a fill's exec time.

        Unknown/unparseable times sort first (0, 0.0) and, thanks to sort
        stability, retain their delivery order among themselves — the
        import path must never crash on a malformed timestamp.
        """
        t = getattr(getattr(fill, "execution", None), "time", None)
        if t is None:
            return (0, 0.0)
        try:
            return (1, t.timestamp())
        except (AttributeError, TypeError, ValueError, OSError, OverflowError):
            return (0, 0.0)

    @staticmethod
    def _fill_exec_id(fill: Any) -> str:
        """The fill's journal identity: IB execId, else permId, else empty."""
        execution = getattr(fill, "execution", None)
        if execution is None:
            return ""
        exec_id = getattr(execution, "execId", None) or getattr(execution, "permId", None)
        return str(exec_id) if exec_id else ""

    @staticmethod
    def _fill_contract_key(fill: Any) -> Optional[str]:
        contract = getattr(fill, "contract", None)
        if contract is None:
            return None
        ticker = str(getattr(contract, "symbol", "") or "").strip().upper()
        if not ticker:
            return None
        sec_type = getattr(contract, "secType", "STK") or "STK"
        if sec_type == "STK":
            return f"{ticker}|STK"
        strike = getattr(contract, "strike", None)
        right = getattr(contract, "right", None)
        expiry = getattr(contract, "lastTradeDateOrContractMonth", None)
        return f"{ticker}|{sec_type}|{strike}|{right}|{expiry}"

    @staticmethod
    def _fill_signed_qty(fill: Any) -> float:
        execution = getattr(fill, "execution", None)
        if execution is None:
            return 0.0
        try:
            qty = abs(float(getattr(execution, "shares", 0) or 0))
        except (TypeError, ValueError):
            return 0.0
        side = str(getattr(execution, "side", "") or "").upper()
        if side in ("BOT", "BUY"):
            return qty
        if side in ("SLD", "SELL"):
            return -qty
        return 0.0

    def _lookup_prior_qty(self, db: Any, fill: Any) -> float:
        if db is None:
            return 0.0
        contract = getattr(fill, "contract", None)
        if contract is None:
            return 0.0
        ticker = getattr(contract, "symbol", None)
        if not ticker:
            return 0.0
        sec_type = getattr(contract, "secType", "STK") or "STK"
        try:
            return prior_net_qty_for_contract(
                db,
                ticker=ticker,
                sec_type=sec_type,
                strike=getattr(contract, "strike", None),
                right=getattr(contract, "right", None),
                expiry=getattr(contract, "lastTradeDateOrContractMonth", None),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("journal_sync: prior-qty lookup failed for %s: %s", ticker, exc)
            return 0.0

    def _fill_to_entry(self, fill: Any, next_id: int, prior_qty: float = 0.0) -> Optional[Dict[str, Any]]:
        execution = getattr(fill, "execution", None)
        contract = getattr(fill, "contract", None)
        commission_report = getattr(fill, "commissionReport", None)
        if execution is None or contract is None:
            return None

        exec_id = self._fill_exec_id(fill)
        if not exec_id:
            return None

        sec_type = getattr(contract, "secType", "STK")
        side = getattr(execution, "side", "")
        action = self._side_to_action(side, sec_type, prior_qty=prior_qty)
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

        multiplier_raw = getattr(contract, "multiplier", None)
        if not isinstance(multiplier_raw, (str, int, float)):
            multiplier_raw = None
        try:
            multiplier = float(multiplier_raw or (100 if sec_type in ("OPT", "BAG") else 1))
        except (TypeError, ValueError):
            multiplier = 100 if sec_type in ("OPT", "BAG") else 1
        total_cost = float(shares) * price * multiplier + commission

        ib_time = getattr(execution, "time", None)
        # ET session date, never a bare strftime on the (UTC-aware) exec
        # time — a fill after ~20:00 ET otherwise lands on the next
        # calendar day and shifts the basis cutoff + same-day P&L (T-023).
        date_str = et_session_date(ib_time if isinstance(ib_time, datetime) else None)
        filled_at = ib_time.isoformat() if isinstance(ib_time, datetime) else str(ib_time or "")

        strike = getattr(contract, "strike", None) if sec_type in ("OPT", "BAG") else None
        right = getattr(contract, "right", None) if sec_type in ("OPT", "BAG") else None
        expiry = getattr(contract, "lastTradeDateOrContractMonth", None) if sec_type in ("OPT", "BAG") else None
        # executed_orders-sourced fills (the backfill shim) carry ISO expiry;
        # journal rows must be compact or fromJournal.ts lot-matching splits.
        expiry = normalize_expiry_compact(expiry)

        structure = self._structure_label(action, sec_type, strike, right, expiry)

        account_id = getattr(execution, "acctNumber", "")
        account_id = account_id if isinstance(account_id, str) else ""
        con_id = getattr(contract, "conId", None)
        con_id = con_id if isinstance(con_id, (int, float, str)) else None
        perm_id = getattr(execution, "permId", None)
        perm_id = perm_id if isinstance(perm_id, int) else None
        order_ref = getattr(execution, "orderRef", "")
        order_ref = order_ref if isinstance(order_ref, str) else ""
        currency = getattr(contract, "currency", "")
        currency = currency if isinstance(currency, str) else ""
        commission_currency = getattr(commission_report, "currency", "") if commission_report is not None else ""
        commission_currency = commission_currency if isinstance(commission_currency, str) else ""

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
            "execution_time": filled_at,
            "account_id": account_id,
            "con_id": con_id,
            "perm_id": perm_id,
            "order_ref": order_ref,
            "currency": currency,
            "commission_currency": commission_currency,
            "multiplier": multiplier,
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
    def _side_to_action(side: str, sec_type: str, prior_qty: float = 0.0) -> Optional[str]:
        """Map (side, sec_type, prior_qty) → action label.

        ``prior_qty`` is the contract's signed net position before this
        fill, derived from already-imported journal rows. Without it, a
        SELL that closes a prior long was mislabeled ``SELL_TO_OPEN``
        (the "open short" label) instead of ``SELL_OPTION`` (close
        long). ``fromJournal.ts`` treats those differently —
        SELL_TO_OPEN sets isOpen=true with net_quantity=-qty (phantom
        new short), SELL_OPTION sets isOpen=false with net_quantity=0.
        Companion to ``_resolve_action`` in journal_rehydrate.py.
        """
        upper = (side or "").upper()
        if upper in ("BOT", "BUY"):
            if sec_type == "STK":
                return "BUY"
            # Covering a short (prior signed qty negative) is a close.
            # fromJournal treats BUY_TO_CLOSE as closed; bare BUY_OPTION is open long.
            return "BUY_TO_CLOSE" if prior_qty < 0 else "BUY_OPTION"
        if upper in ("SLD", "SELL"):
            if sec_type == "STK":
                # STK has no OPEN/CLOSE distinction.
                return "SELL"
            return "SELL_OPTION" if prior_qty > 0 else "SELL_TO_OPEN"
        return None

    @staticmethod
    def _structure_label(action: str, sec_type: str, strike: Any, right: Any, expiry: Any) -> str:
        type_label = {"STK": "Stock", "OPT": "Option", "BAG": "Spread"}.get(sec_type, sec_type)
        # Opens: Long / Short. Closes: Closed (SELL_OPTION, BUY_TO_CLOSE, CLOSED).
        a = (action or "").upper()
        if a in ("BUY", "BUY_OPTION", "BUY_TO_OPEN"):
            side_label = "Long"
        elif a == "SELL_TO_OPEN":
            side_label = "Short"
        else:
            side_label = "Closed"
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
