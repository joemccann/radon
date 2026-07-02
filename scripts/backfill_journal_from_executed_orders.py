#!/usr/bin/env python3
"""Backfill missing journal rows from executed_orders.

JRN-01 root-cause: the real-time journal_sync handler's Turso dual-write
threw a transient Hrana stream-not-found on 2026-06-08 15:04:13 UTC and
swallowed the exception. The legacy disk mirror succeeded, but libsql dedup
then saw the exec_ids as already-processed on every subsequent pass, so the
gap is permanent without manual repair.

This repair path treats Turso as canonical. Missing journal rows are rebuilt
from the `executed_orders` payload using journal_sync's labeling logic (same
action/structure/total_cost formulas as live ingest), with prior position
state read from `journal`.

This script is the surgical repair path.  It:
  1. Reads executed_orders rows over a bounded window (default 7 days,
     matching reconcile_window_days from the RCA) — or accepts explicit
     exec_ids on the command line.
  2. Checks journal for each exec_id (exact-match on ib_exec_id embedded
     in the payload, same key the live ingest path writes).
  3. Reconstructs the payload from the executed order row using the live ingest
     conversion logic.
  4. Calls upsert_journal_entry() from db.writer — the identical writer the
     live ingest uses — so the row is byte-for-byte what organic ingest
     would have produced.
  5. --dry-run is DEFAULT TRUE.  Pass --execute to actually write.

Safety guards
─────────────
- Refuses to run under PYTEST_CURRENT_TEST without RADON_DB_TEST_WRITE_OK=1
  (feedback_test_pollution_to_production).
- Idempotent: re-checks journal immediately before each insert; skips if
  already present.
- --execute mode requires explicit flag; dry-run never opens a write
  transaction.
Usage
─────
Dry-run (default — reads prod, writes nothing):
  python3 scripts/backfill_journal_from_executed_orders.py

Specify exec_ids:
  python3 scripts/backfill_journal_from_executed_orders.py \\
      0002920b.6a26c483.01.01 000205d2.6a26a327.01.01

Execute (writes to prod — operator must review dry-run first):
  python3 scripts/backfill_journal_from_executed_orders.py --execute \\
      0002920b.6a26c483.01.01 000205d2.6a26a327.01.01

Repair specific gaps:
  python3 scripts/backfill_journal_from_executed_orders.py \\
      0000fb35.6a10834c.01.01 0001108f.6a19b7e9.01.01 0002920b.6a2b2035.01.01

  Add --execute to write for real after reviewing the dry-run output.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── sys.path bootstrap ────────────────────────────────────────────────────────
# Support two call conventions:
#   • python3 scripts/backfill_journal_from_executed_orders.py   (from repo root)
#   • python3 backfill_journal_from_executed_orders.py           (from scripts/)
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ── env load ──────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv

    _ENV = Path(_SCRIPTS_DIR.parent / "web" / ".env")
    if _ENV.exists():
        load_dotenv(str(_ENV))
except ImportError:
    pass

# ── logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("backfill_journal")

# ── constants ─────────────────────────────────────────────────────────────────
RECONCILE_WINDOW_DAYS = 7


# ── pytest guard ──────────────────────────────────────────────────────────────

def _assert_not_under_pytest() -> None:
    """Refuse to run live DB writes from inside a pytest session.

    Mirrors the guard in db.client.get_db() (feedback_test_pollution_to_production).
    An explicit opt-in (RADON_DB_TEST_WRITE_OK=1) bypasses it for the DB-client
    level; here we add a second layer at the script entry-point for belt-and-
    suspenders protection against accidental prod writes during testing.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("RADON_DB_TEST_WRITE_OK"):
        raise RuntimeError(
            "backfill_journal: refusing to run under PYTEST_CURRENT_TEST "
            "without RADON_DB_TEST_WRITE_OK=1 "
            "(feedback_test_pollution_to_production)"
        )


# ── journal exec-id coverage ──────────────────────────────────────────────────

def _journal_covered_exec_ids(db: Any) -> set[str]:
    """Build the set of individual exec_id parts that are already in journal.

    A journal payload carries ib_exec_id; for multi-fill orders the live
    rehydrate path joins parts with '+'.  We split every stored ib_exec_id
    on '+' so individual parts are treated as covered.
    """
    covered: set[str] = set()
    rows = db.execute(
        "SELECT payload FROM journal WHERE payload LIKE '%ib_exec_id%'"
    ).fetchall()
    for (raw,) in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        exec_id = payload.get("ib_exec_id")
        if not exec_id:
            continue
        covered.add(str(exec_id))
        for part in str(exec_id).split("+"):
            part = part.strip()
            if part:
                covered.add(part)
    return covered


def _exec_id_in_journal(db: Any, exec_id: str) -> bool:
    """Idempotency check: true if exec_id is already covered in journal."""
    covered = _journal_covered_exec_ids(db)
    return exec_id in covered


# ── executed_orders scan ──────────────────────────────────────────────────────

def _fetch_executed_orders(
    db: Any,
    *,
    exec_ids: Optional[List[str]] = None,
    window_days: int = RECONCILE_WINDOW_DAYS,
) -> List[Dict[str, Any]]:
    """Return executed_orders rows to inspect.

    When exec_ids is given, fetch only those rows.
    Otherwise scan the last window_days using fill_time.
    """
    if exec_ids:
        placeholders = ",".join("?" * len(exec_ids))
        rows = db.execute(
            f"SELECT exec_id, payload, fill_time FROM executed_orders "
            f"WHERE exec_id IN ({placeholders})",
            tuple(exec_ids),
        ).fetchall()
    else:
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(days=window_days))
            .isoformat()
            .replace("+00:00", "Z")
        )
        rows = db.execute(
            "SELECT exec_id, payload, fill_time FROM executed_orders "
            "WHERE fill_time >= ? ORDER BY fill_time ASC",
            (cutoff,),
        ).fetchall()

    result = []
    for exec_id, raw_payload, fill_time in rows:
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
        except (json.JSONDecodeError, TypeError):
            log.warning("executed_orders row %s has unparseable payload — skipping", exec_id)
            continue
        result.append({"exec_id": exec_id, "payload": payload, "fill_time": fill_time})
    return result


# ── journal row construction ───────────────────────────────────────────────────


class _EOFillShim:
    """Duck-typed shim that makes an executed_orders payload look like an ib_insync Fill.

    JournalSyncHandler._fill_to_entry() expects a fill with .execution, .contract,
    and .commissionReport attributes.  This shim adapts an EO payload dict into
    that shape so we reuse the exact same labeling/structure/total_cost logic
    without duplicating it here.
    """

    class _Execution:
        __slots__ = ("execId", "side", "shares", "price", "time")

        def __init__(self, exec_id: str, side: str, quantity: float, avg_price: float, time: datetime):
            self.execId = exec_id
            self.side = side
            self.shares = quantity
            self.price = avg_price
            self.time = time

    class _Contract:
        __slots__ = ("symbol", "secType", "strike", "right", "lastTradeDateOrContractMonth")

        def __init__(self, symbol: str, sec_type: str, strike: Any, right: Any, expiry: Any):
            self.symbol = symbol
            self.secType = sec_type
            self.strike = strike
            self.right = right
            self.lastTradeDateOrContractMonth = expiry

    class _CommissionReport:
        __slots__ = ("commission",)

        def __init__(self, commission: float):
            self.commission = commission

    def __init__(self, eo_row: Dict[str, Any]) -> None:
        payload = eo_row.get("payload", eo_row)
        contract_dict = payload.get("contract", {})

        exec_id = str(payload.get("execId") or "")
        side = str(payload.get("side") or "")
        quantity = float(payload.get("quantity") or 0)
        avg_price = float(payload.get("avgPrice") or 0)
        commission = float(payload.get("commission") or 0)

        raw_time = payload.get("time")
        fill_time = _parse_fill_time(raw_time)

        symbol = str(contract_dict.get("symbol") or payload.get("symbol") or "")
        sec_type = str(contract_dict.get("secType") or "OPT")
        strike = contract_dict.get("strike")
        right = contract_dict.get("right")
        expiry = contract_dict.get("lastTradeDateOrContractMonth") or contract_dict.get("expiry")

        self.execution = self._Execution(exec_id, side, quantity, avg_price, fill_time)
        self.contract = self._Contract(symbol, sec_type, strike, right, expiry)
        self.commissionReport = self._CommissionReport(commission)


def _parse_fill_time(raw: Any) -> datetime:
    """Parse an EO fill time string into a datetime, falling back to now."""
    if isinstance(raw, datetime):
        return raw
    if not raw:
        return datetime.now()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(str(raw), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return datetime.now()


def _build_journal_row_from_executed_order(
    eo_row: Dict[str, Any],
    db: Any,
    prior_qty: float = 0.0,
) -> tuple[str, Dict[str, Any], str]:
    """Return (trade_id, journal_payload, filled_at) reconstructed from an EO row.

    Reuses JournalSyncHandler._fill_to_entry() so the action/structure/total_cost
    fields are produced by the identical logic as live ingest — not hand-fabricated.
    prior_qty must be seeded from the journal at the time of this fill (caller
    responsibility, accumulated in fill_time ASC order).
    """
    from monitor_daemon.handlers.journal_sync import JournalSyncHandler  # noqa: WPS433

    shim = _EOFillShim(eo_row)

    # _fill_to_entry is an instance method; instantiate without running __init__.
    handler = JournalSyncHandler.__new__(JournalSyncHandler)

    # Use a dummy next_id; the journal table key is trade_id not id.
    entry = handler._fill_to_entry(shim, next_id=0, prior_qty=prior_qty)
    if entry is None or not entry.get("ib_exec_id"):
        raise ValueError("Missing reconstructed trade id")

    trade_id = str(entry["ib_exec_id"])
    filled_at = entry.get("filled_at") or entry.get("date")
    return trade_id, entry, filled_at


# ── core logic ────────────────────────────────────────────────────────────────

def backfill(
    db: Any,
    *,
    exec_ids: Optional[List[str]] = None,
    window_days: int = RECONCILE_WINDOW_DAYS,
    dry_run: bool = True,
) -> List[Dict[str, Any]]:
    """Find and (optionally) insert missing journal rows.

    Returns a list of action records:
        {"exec_id": ..., "status": "inserted_from_eo"|"skipped"|
                          "dry_run_from_eo"|"reconstruction_failed", ...}

    Fills are reconstructed from the executed_orders payload using
    journal_sync's labeling logic (same action/structure/total_cost as live
    ingest). prior_qty is accumulated in fill_time ASC order within this run
    so labels are correct.
    """
    from db.writer import upsert_journal_entry  # noqa: WPS433

    executed = _fetch_executed_orders(db, exec_ids=exec_ids, window_days=window_days)
    log.info("executed_orders rows to inspect: %d", len(executed))

    # Sort by fill_time ASC so prior_qty accumulates correctly when multiple
    # fills for the same contract are processed (critical for MU C1050 @110/@108).
    executed = sorted(executed, key=lambda r: r.get("fill_time") or "")

    covered = _journal_covered_exec_ids(db)

    # Accumulate prior_qty per contract as we insert rows within this run,
    # mirroring _fills_to_entries' in-cycle prior_state mutation.
    from clients.journal_basis import prior_net_qty_for_contract  # noqa: WPS433
    prior_state: Dict[str, float] = {}

    actions = []
    for row in executed:
        exec_id = row["exec_id"]

        if exec_id in covered:
            log.debug("  SKIP  %s — already in journal", exec_id)
            actions.append({"exec_id": exec_id, "status": "skipped"})
            continue

        prior_qty = _get_prior_qty_for_eo_row(db, row, prior_state, prior_net_qty_for_contract)
        try:
            trade_id, journal_payload, filled_at = _build_journal_row_from_executed_order(
                row,
                db,
                prior_qty=prior_qty,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("  ERR   %s — failed to reconstruct from executed_orders: %s", exec_id, exc)
            actions.append({"exec_id": exec_id, "status": "reconstruction_failed", "error": str(exc)})
            continue

        if dry_run:
            log.info(
                "  DRY   %s — would insert trade_id=%s filled_at=%s from_eo=%s\n"
                "        payload=%s",
                exec_id,
                trade_id,
                filled_at,
                True,
                json.dumps(journal_payload),
            )
            action = {
                "exec_id": exec_id,
                "status": "dry_run_from_eo",
                "trade_id": trade_id,
                "filled_at": filled_at,
                "payload": journal_payload,
            }
            actions.append(action)
        else:
            # Re-check immediately before insert for idempotency under races.
            if _exec_id_in_journal(db, exec_id):
                log.info("  SKIP  %s — appeared in journal between scan and insert", exec_id)
                actions.append({"exec_id": exec_id, "status": "skipped"})
                continue

            upsert_journal_entry(trade_id, journal_payload, filled_at)
            log.info(
                "  INSERT %s — trade_id=%s filled_at=%s ticker=%s action=%s contracts=%s from_eo=%s",
                exec_id,
                trade_id,
                filled_at,
                journal_payload.get("ticker"),
                journal_payload.get("action"),
                journal_payload.get("contracts"),
                True,
            )
            action = {
                "exec_id": exec_id,
                "status": "inserted_from_eo",
                "trade_id": trade_id,
                "filled_at": filled_at,
                "payload": journal_payload,
            }
            actions.append(action)

        # Update in-run prior_state so subsequent fills for the same contract
        # see the updated position (mirrors _fills_to_entries' mutation).
        _update_prior_state_for_eo_row(prior_state, row, journal_payload)

    return actions


def _eo_contract_key(eo_row: Dict[str, Any]) -> Optional[str]:
    """Build a per-contract key from an executed_orders row for prior_state tracking."""
    payload = eo_row.get("payload", eo_row)
    contract = payload.get("contract", {})
    symbol = str(contract.get("symbol") or payload.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    sec_type = str(contract.get("secType") or "OPT").upper()
    if sec_type == "STK":
        return f"{symbol}|STK"
    strike = contract.get("strike")
    right = contract.get("right")
    expiry = contract.get("lastTradeDateOrContractMonth") or contract.get("expiry")
    return f"{symbol}|{sec_type}|{strike}|{right}|{expiry}"


def _get_prior_qty_for_eo_row(
    db: Any,
    eo_row: Dict[str, Any],
    prior_state: Dict[str, float],
    prior_net_qty_fn: Any,
) -> float:
    """Return prior signed net qty for an EO row's contract.

    Seeds from the journal DB on first encounter, then uses in-run accumulated
    state for subsequent fills of the same contract within this backfill run.

    The DB seed is bounded to journal rows STRICTLY BEFORE the fill's own
    date (``before=`` cutoff): a retroactive backfill must reconstruct the
    position AS OF the fill, and an unbounded sum counts LATER closing rows
    already in the journal, flipping the action label (2026-07-02 incident:
    2026-06-25 opening sells read the 2026-06-26 closing buys as prior +5
    and would have written SELL_OPTION instead of SELL_TO_OPEN).
    """
    key = _eo_contract_key(eo_row)
    if key is None:
        return 0.0
    if key in prior_state:
        return prior_state[key]
    # Seed from journal DB, bounded at this fill's timestamp.
    payload = eo_row.get("payload", eo_row)
    contract = payload.get("contract", {})
    symbol = str(contract.get("symbol") or payload.get("symbol") or "").strip().upper()
    sec_type = str(contract.get("secType") or "OPT")
    strike = contract.get("strike")
    right = contract.get("right")
    expiry = contract.get("lastTradeDateOrContractMonth") or contract.get("expiry")
    fill_time = eo_row.get("fill_time") or payload.get("time")
    try:
        qty = prior_net_qty_fn(
            db,
            ticker=symbol,
            sec_type=sec_type,
            strike=strike,
            right=right,
            expiry=expiry,
            before=str(fill_time) if fill_time else None,
        )
    except Exception:  # noqa: BLE001
        qty = 0.0
    prior_state[key] = qty
    return qty


def _update_prior_state_for_eo_row(
    prior_state: Dict[str, float],
    eo_row: Dict[str, Any],
    journal_payload: Dict[str, Any],
) -> None:
    """Update in-run prior_state after a row is committed."""
    key = _eo_contract_key(eo_row)
    if key is None:
        return
    contracts = float(journal_payload.get("contracts") or journal_payload.get("shares") or 0)
    action = str(journal_payload.get("action") or "")
    if action.startswith("BUY"):
        delta = contracts
    elif action.startswith("SELL") or action.startswith("SHORT") or action == "CLOSED":
        delta = -contracts
    else:
        delta = 0.0
    prior_state[key] = prior_state.get(key, 0.0) + delta


# ── entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing journal rows from executed_orders."
    )
    parser.add_argument(
        "exec_ids",
        nargs="*",
        metavar="EXEC_ID",
        help=(
            "Specific exec_ids to backfill.  Omit to scan the last "
            f"{RECONCILE_WINDOW_DAYS} days of executed_orders."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually write to Turso.  Default is dry-run (read-only).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=RECONCILE_WINDOW_DAYS,
        metavar="N",
        help=f"Look-back window when no exec_ids given (default {RECONCILE_WINDOW_DAYS}).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dry_run = not args.execute

    _assert_not_under_pytest()

    from db.client import get_db  # noqa: WPS433

    db = get_db()

    mode = "DRY-RUN (read-only)" if dry_run else "EXECUTE (will write to Turso)"
    log.info("backfill_journal  mode=%s  source=executed_orders", mode)

    actions = backfill(
        db,
        exec_ids=args.exec_ids or None,
        window_days=args.window_days,
        dry_run=dry_run,
    )

    inserted = [a for a in actions if a["status"] == "inserted_from_eo"]
    dry_run_rows = [a for a in actions if a["status"] == "dry_run_from_eo"]
    skipped = [a for a in actions if a["status"] == "skipped"]
    failed = [a for a in actions if a["status"] == "reconstruction_failed"]

    log.info(
        "Done — dry_run=%s inserted=%d would_insert=%d skipped=%d reconstruction_failed=%d",
        dry_run,
        len(inserted),
        len(dry_run_rows),
        len(skipped),
        len(failed),
    )

    # JRN-03: a real backfill may have closed the gap the daily journal-reconcile
    # handler flagged. Heal its service_health row now so the watchdog stops
    # re-alerting an already-repaired gap, instead of waiting ~24h for the next
    # reconcile pass. No-op unless the row is currently in error AND zero gaps
    # remain after this write.
    if not dry_run and inserted:
        try:
            from monitor_daemon.handlers.journal_reconcile import (
                heal_journal_reconcile_if_recovered,
            )

            if heal_journal_reconcile_if_recovered(db):
                log.info("journal-reconcile service_health healed error -> ok (gaps now covered)")
        except Exception as exc:  # noqa: BLE001
            log.warning("journal-reconcile heal check failed: %s", exc)

    if dry_run and dry_run_rows:
        print("\n=== ROWS THAT WOULD BE INSERTED ===")
        for a in dry_run_rows:
            print(
                f"\nexec_id   : {a['exec_id']} [reconstructed from executed_orders]\n"
                f"trade_id  : {a['trade_id']}\n"
                f"filled_at : {a['filled_at']}\n"
                f"payload   : {json.dumps(a['payload'], indent=2)}"
            )
        print("\n=== END DRY-RUN ===")
    elif dry_run and not dry_run_rows:
        print("\n=== DRY-RUN: no gaps found — journal already covers all scanned exec_ids ===")


if __name__ == "__main__":
    main()
