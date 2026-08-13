#!/usr/bin/env python3
"""Re-derive payload_sha256 for position_execution_facts rows.

The stored hash is a fingerprint of the row's own canonical payload, so
recomputing it from that payload is a re-derivation, not a rewrite of
history: no row's facts change, only the fingerprint that indexes them.

Needed whenever the identity-hash contract moves. On 2026-08-13 commit
4eaaf5e9 added `revision` / `source_exec_id` to normalize_execution while
upsert_position_execution_fact hashed "every field except commission", so
70 of 76 stored facts hashed differently overnight and orders-sync aborted
with "execution fact conflict" on the first pre-existing exec_id it
re-synced — freezing executed_orders and open_orders. Pinning the hash to
_LIFECYCLE_HASH_FIELDS restores those 70; the 6 rows written under the
transient scheme are repaired here.

--dry-run is DEFAULT TRUE. Pass --execute to write.

Usage
─────
  python3 scripts/rehash_position_execution_facts.py
  python3 scripts/rehash_position_execution_facts.py --execute
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from dotenv import load_dotenv

    for _candidate in (_SCRIPTS_DIR.parent / ".env", _SCRIPTS_DIR.parent / "web" / ".env"):
        if _candidate.exists():
            load_dotenv(str(_candidate))
except ImportError:  # pragma: no cover - dotenv optional
    pass

logging.basicConfig(
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("rehash_execution_facts")

# Reads a bounded page at a time — the direct-to-cloud Hrana pipeline 502s on
# unbounded reads of payload-carrying tables (scripts/CLAUDE.md §Hrana bounding).
PAGE_SIZE = 200


def _assert_not_under_pytest() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("RADON_DB_TEST_WRITE_OK"):
        raise RuntimeError(
            "rehash_position_execution_facts: refusing to run under "
            "PYTEST_CURRENT_TEST without RADON_DB_TEST_WRITE_OK=1 "
            "(feedback_test_pollution_to_production)"
        )


def find_stale_rows(db: Any) -> list[dict[str, Any]]:
    """Rows whose stored hash disagrees with the current pinned contract."""
    from db.writer import _execution_identity_hash
    from position_return_capital import normalize_execution

    stale: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = db.execute(
            "SELECT account_id, exec_id, revision, payload_sha256, payload "
            "FROM position_execution_facts ORDER BY ingested_at LIMIT ? OFFSET ?",
            (PAGE_SIZE, offset),
        ).fetchall()
        if not page:
            break
        for account_id, exec_id, revision, stored_hash, payload in page:
            try:
                item = normalize_execution(json.loads(payload))
            except Exception as exc:  # noqa: BLE001
                log.warning("  SKIP  %s — payload does not normalize: %s", exec_id, exc)
                continue
            expected = _execution_identity_hash(item)
            if expected != stored_hash:
                stale.append(
                    {
                        "account_id": account_id,
                        "exec_id": exec_id,
                        "revision": revision,
                        "stored": stored_hash,
                        "expected": expected,
                    }
                )
        offset += PAGE_SIZE
    return stale


def rehash(db: Any, *, dry_run: bool = True) -> list[dict[str, Any]]:
    """Re-derive stale fingerprints. Returns the rows acted on."""
    stale = find_stale_rows(db)
    log.info("rows needing re-derivation: %d", len(stale))
    for row in stale:
        if dry_run:
            log.info(
                "  DRY   %s rev=%s  %s -> %s",
                row["exec_id"], row["revision"], row["stored"][:12], row["expected"][:12],
            )
            continue
        db.execute(
            "UPDATE position_execution_facts SET payload_sha256 = ? "
            "WHERE account_id = ? AND exec_id = ? AND revision = ?",
            (row["expected"], row["account_id"], row["exec_id"], row["revision"]),
        )
        log.info(
            "  FIXED %s rev=%s  %s -> %s",
            row["exec_id"], row["revision"], row["stored"][:12], row["expected"][:12],
        )
    if not dry_run and stale:
        db.commit()
    return stale


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-derive payload_sha256 for position_execution_facts rows."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the re-derived hashes (default is a dry run).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    _assert_not_under_pytest()

    from db.client import get_db

    db = get_db()
    acted = rehash(db, dry_run=not args.execute)
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log.info("Done — %s, rows=%d", mode, len(acted))


if __name__ == "__main__":
    main()
