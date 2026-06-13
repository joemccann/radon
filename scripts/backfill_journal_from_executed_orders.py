#!/usr/bin/env python3
"""Backfill missing journal rows from executed_orders.

JRN-01 root-cause: the real-time journal_sync handler's Turso dual-write
threw a transient Hrana stream-not-found on 2026-06-08 15:04:13 UTC and
swallowed the exception.  The disk write (trade_log.json) succeeded, but
the libsql dedup saw the exec_ids as already-processed on every subsequent
pass, so the gap is permanent without manual repair.

This script is the surgical repair path.  It:
  1. Reads executed_orders rows over a bounded window (default 7 days,
     matching reconcile_window_days from the RCA) — or accepts explicit
     exec_ids on the command line.
  2. Checks journal for each exec_id (exact-match on ib_exec_id embedded
     in the payload, same key the live ingest path writes).
  3. Sources the canonical payload from trade_log.json (disk) — the same
     dict the live _dual_write path would have passed to upsert_journal_entry.
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
DEFAULT_TRADE_LOG = _SCRIPTS_DIR.parent / "data" / "trade_log.json"


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


# ── disk fallback for canonical payload ───────────────────────────────────────

def _load_trade_log(trade_log_path: Path) -> List[Dict[str, Any]]:
    """Return the list of trade rows from trade_log.json, or [] if missing."""
    if not trade_log_path.exists():
        return []
    try:
        data = json.loads(trade_log_path.read_text(encoding="utf-8"))
        return data.get("trades", [])
    except Exception as exc:
        log.warning("trade_log.json read failed: %s", exc)
        return []


def _find_disk_row(trades: List[Dict[str, Any]], exec_id: str) -> Optional[Dict[str, Any]]:
    """Find the trade_log.json row whose ib_exec_id matches exec_id."""
    for trade in trades:
        stored = str(trade.get("ib_exec_id", ""))
        if stored == exec_id:
            return trade
        for part in stored.split("+"):
            if part.strip() == exec_id:
                return trade
    return None


# ── journal row construction ───────────────────────────────────────────────────

def _build_journal_row(
    disk_row: Dict[str, Any],
) -> tuple[str, Dict[str, Any], str]:
    """Return (trade_id, journal_payload, filled_at) from a disk trade row.

    The live journal_sync._dual_write path calls:
        upsert_journal_entry(str(entry.get("ib_exec_id")), entry, filled_at=entry.get("filled_at") or entry.get("date"))

    We replicate that exactly: trade_id = ib_exec_id, payload = the disk row
    verbatim (it IS what _dual_write would have passed), filled_at = disk
    row's date field.
    """
    trade_id = str(disk_row["ib_exec_id"])
    filled_at = disk_row.get("filled_at") or disk_row.get("date")
    return trade_id, disk_row, filled_at


# ── core logic ────────────────────────────────────────────────────────────────

def backfill(
    db: Any,
    *,
    exec_ids: Optional[List[str]] = None,
    window_days: int = RECONCILE_WINDOW_DAYS,
    dry_run: bool = True,
    trade_log_path: Path = DEFAULT_TRADE_LOG,
) -> List[Dict[str, Any]]:
    """Find and (optionally) insert missing journal rows.

    Returns a list of action records:
        {"exec_id": ..., "status": "inserted"|"skipped"|"no_disk_row"|"dry_run", ...}
    """
    from db.writer import upsert_journal_entry  # noqa: WPS433

    executed = _fetch_executed_orders(db, exec_ids=exec_ids, window_days=window_days)
    log.info("executed_orders rows to inspect: %d", len(executed))

    covered = _journal_covered_exec_ids(db)
    disk_trades = _load_trade_log(trade_log_path)
    log.info("trade_log.json rows loaded: %d", len(disk_trades))

    actions = []
    for row in executed:
        exec_id = row["exec_id"]

        if exec_id in covered:
            log.debug("  SKIP  %s — already in journal", exec_id)
            actions.append({"exec_id": exec_id, "status": "skipped"})
            continue

        disk_row = _find_disk_row(disk_trades, exec_id)
        if disk_row is None:
            log.warning("  GAP   %s — not in executed_orders AND not in trade_log.json", exec_id)
            actions.append({"exec_id": exec_id, "status": "no_disk_row"})
            continue

        trade_id, journal_payload, filled_at = _build_journal_row(disk_row)

        if dry_run:
            log.info(
                "  DRY   %s — would insert trade_id=%s filled_at=%s\n"
                "        payload=%s",
                exec_id,
                trade_id,
                filled_at,
                json.dumps(journal_payload),
            )
            actions.append(
                {
                    "exec_id": exec_id,
                    "status": "dry_run",
                    "trade_id": trade_id,
                    "filled_at": filled_at,
                    "payload": journal_payload,
                }
            )
        else:
            # Re-check immediately before insert for idempotency under races.
            if _exec_id_in_journal(db, exec_id):
                log.info("  SKIP  %s — appeared in journal between scan and insert", exec_id)
                actions.append({"exec_id": exec_id, "status": "skipped"})
                continue

            upsert_journal_entry(trade_id, journal_payload, filled_at)
            log.info(
                "  INSERT %s — trade_id=%s filled_at=%s ticker=%s action=%s contracts=%s",
                exec_id,
                trade_id,
                filled_at,
                journal_payload.get("ticker"),
                journal_payload.get("action"),
                journal_payload.get("contracts"),
            )
            actions.append(
                {
                    "exec_id": exec_id,
                    "status": "inserted",
                    "trade_id": trade_id,
                    "filled_at": filled_at,
                    "payload": journal_payload,
                }
            )

    return actions


# ── entry point ───────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill missing journal rows from executed_orders (JRN-01)."
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
    parser.add_argument(
        "--trade-log",
        type=Path,
        default=DEFAULT_TRADE_LOG,
        metavar="PATH",
        help="Path to trade_log.json (default: data/trade_log.json).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    dry_run = not args.execute

    _assert_not_under_pytest()

    from db.client import get_db  # noqa: WPS433

    db = get_db()

    mode = "DRY-RUN (read-only)" if dry_run else "EXECUTE (will write to Turso)"
    log.info("backfill_journal  mode=%s", mode)

    actions = backfill(
        db,
        exec_ids=args.exec_ids or None,
        window_days=args.window_days,
        dry_run=dry_run,
        trade_log_path=args.trade_log,
    )

    inserted = [a for a in actions if a["status"] == "inserted"]
    dry_run_rows = [a for a in actions if a["status"] == "dry_run"]
    skipped = [a for a in actions if a["status"] == "skipped"]
    no_disk = [a for a in actions if a["status"] == "no_disk_row"]

    log.info(
        "Done — dry_run=%s inserted=%d would_insert=%d skipped=%d no_disk_row=%d",
        dry_run,
        len(inserted),
        len(dry_run_rows),
        len(skipped),
        len(no_disk),
    )

    if dry_run and dry_run_rows:
        print("\n=== ROWS THAT WOULD BE INSERTED ===")
        for a in dry_run_rows:
            print(
                f"\nexec_id   : {a['exec_id']}\n"
                f"trade_id  : {a['trade_id']}\n"
                f"filled_at : {a['filled_at']}\n"
                f"payload   : {json.dumps(a['payload'], indent=2)}"
            )
        print("\n=== END DRY-RUN ===")
    elif dry_run and not dry_run_rows:
        print("\n=== DRY-RUN: no gaps found — journal already covers all scanned exec_ids ===")


if __name__ == "__main__":
    main()
