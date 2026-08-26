#!/usr/bin/env python3
"""R-077: tolerated hash drift must converge, and rehash paging must not skip ties.

Two defects from the audit:
  1. ``upsert_position_execution_fact``'s tolerated-hash path (economics
     match, pinned hash differs) returned False without updating anything,
     so late IB enrichment (perm_id, order_ref) never reached the ledger
     columns and the row stayed permanently mismatched-but-tolerated.
  2. ``rehash_position_execution_facts.find_stale_rows`` paged with
     LIMIT/OFFSET over a non-unique ``ORDER BY ingested_at``; rows tied on
     ingested_at have no stable order across queries, so ties spanning a
     page boundary could be skipped (or double-counted).
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0037_position_return_capital.sql"


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [stmt.strip() for stmt in re.split(r";\s*$", stripped, flags=re.MULTILINE) if stmt.strip()]


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()
    return conn


BASE_EXECUTION = {
    "execId": "0001f4e8.6899acbd.01.01",
    "acctNumber": "U1234567",
    "conId": 637533641,
    "side": "BOT",
    "shares": 2.0,
    "price": 4.15,
    "time": "2026-08-14T14:31:02+00:00",
    "orderRef": "vcg-entry-1",
    "currency": "USD",
    "multiplier": 100,
    "contract": {"conId": 637533641, "symbol": "MU", "secType": "OPT"},
}


def _row(conn: sqlite3.Connection) -> tuple:
    return conn.execute(
        "SELECT payload_sha256, perm_id, order_ref, payload "
        "FROM position_execution_facts WHERE exec_id = ?",
        (BASE_EXECUTION["execId"],),
    ).fetchone()


class TestToleratedHashConverges:
    def test_late_enrichment_reaches_the_ledger_columns(self):
        from db.writer import _execution_identity_hash, upsert_position_execution_fact
        from position_return_capital import normalize_execution

        conn = _fresh_db()
        assert upsert_position_execution_fact(dict(BASE_EXECUTION), db=conn) is True

        enriched = dict(BASE_EXECUTION)
        enriched["permId"] = 987654321
        upsert_position_execution_fact(enriched, db=conn)

        stored_hash, perm_id, order_ref, payload = _row(conn)
        assert perm_id == 987654321
        assert order_ref == "vcg-entry-1"
        assert json.loads(payload)["permId"] == 987654321
        assert stored_hash == _execution_identity_hash(normalize_execution(enriched))

    def test_converged_row_is_idempotent_on_the_next_sync(self):
        from db.writer import upsert_position_execution_fact

        conn = _fresh_db()
        upsert_position_execution_fact(dict(BASE_EXECUTION), db=conn)
        enriched = dict(BASE_EXECUTION)
        enriched["permId"] = 987654321
        upsert_position_execution_fact(enriched, db=conn)

        assert upsert_position_execution_fact(dict(enriched), db=conn) is False
        assert _row(conn)[1] == 987654321

    def test_economic_conflict_still_raises(self):
        from db.writer import upsert_position_execution_fact

        conn = _fresh_db()
        upsert_position_execution_fact(dict(BASE_EXECUTION), db=conn)

        conflicting = dict(BASE_EXECUTION)
        conflicting["price"] = 9.99
        with pytest.raises(ValueError, match="execution fact conflict"):
            upsert_position_execution_fact(conflicting, db=conn)
        # The stored row is untouched by a refused write.
        assert json.loads(_row(conn)[3])["price"] == 4.15

    def test_avg_price_restatement_reaches_the_price_column(self):
        """T-098: replay storage supplies avgPrice, which the conflict gate
        deliberately ignores, so the tolerated branch must carry the derived
        price into the denormalized column or the row never converges."""
        from db.writer import upsert_position_execution_fact

        conn = _fresh_db()
        replay = {k: v for k, v in BASE_EXECUTION.items() if k != "price"}
        assert upsert_position_execution_fact(dict(replay, avgPrice=4.15), db=conn) is True

        assert upsert_position_execution_fact(dict(replay, avgPrice=9.99), db=conn) is True

        price, payload = conn.execute(
            "SELECT price, payload FROM position_execution_facts WHERE exec_id = ?",
            (BASE_EXECUTION["execId"],),
        ).fetchone()
        assert json.loads(payload)["avgPrice"] == 9.99
        assert price == 9.99

    def test_multiplier_restatement_reaches_the_multiplier_column(self):
        from db.writer import upsert_position_execution_fact

        conn = _fresh_db()
        assert upsert_position_execution_fact(dict(BASE_EXECUTION), db=conn) is True

        assert upsert_position_execution_fact(dict(BASE_EXECUTION, multiplier=1), db=conn) is True

        multiplier, payload = conn.execute(
            "SELECT multiplier, payload FROM position_execution_facts WHERE exec_id = ?",
            (BASE_EXECUTION["execId"],),
        ).fetchone()
        assert json.loads(payload)["multiplier"] == 1
        assert multiplier == 1


class _TieShufflingDb:
    """Simulates Hrana's non-deterministic tie order: every query re-sorts
    ingested_at ties differently — the exact failure mode that makes
    LIMIT/OFFSET over a non-unique ORDER BY skip rows across page
    boundaries. A keyset scan on the unique PK triple is immune."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.calls = 0

    def execute(self, sql: str, params: tuple = ()):
        self.calls += 1
        cursor = MagicMock(spec=["fetchall"])
        flip = self.calls % 2 == 1

        def tie_shuffled():
            return sorted(
                self.rows,
                key=lambda r: (r["ingested_at"], r["exec_id"] if flip else _rev(r["exec_id"])),
            )

        if "OFFSET" in sql.upper():
            limit, offset = params
            page = tie_shuffled()[offset: offset + int(limit)]
        else:
            limit = int(params[-1])
            cursor_key = tuple(params[:-1])
            ordered = sorted(
                self.rows, key=lambda r: (r["account_id"], r["exec_id"], r["revision"])
            )
            if len(cursor_key) == 3:
                ordered = [
                    r
                    for r in ordered
                    if (r["account_id"], r["exec_id"], r["revision"]) > cursor_key
                ]
            page = ordered[: int(limit)]
        cursor.fetchall.return_value = [
            (r["account_id"], r["exec_id"], r["revision"], r["payload_sha256"], r["payload"])
            for r in page
        ]
        return cursor


def _rev(text: str) -> str:
    return text[::-1]


class TestRehashKeysetPaging:
    def test_ingested_at_ties_across_page_boundaries_are_never_skipped(self, monkeypatch):
        import rehash_position_execution_facts as rehash_mod

        rows = [
            {
                "account_id": "U1234567",
                "exec_id": f"exec-{i:04d}",
                "revision": 1,
                "payload_sha256": "stale",
                "payload": json.dumps({"exec_id": f"exec-{i:04d}"}),
                "ingested_at": "2026-08-14T00:00:00Z",  # ALL tied
            }
            for i in range(25)
        ]
        monkeypatch.setattr(rehash_mod, "PAGE_SIZE", 10)

        import db.writer as writer_mod
        import position_return_capital as prc_mod

        monkeypatch.setattr(
            prc_mod, "normalize_execution", lambda payload: dict(payload)
        )
        monkeypatch.setattr(
            writer_mod,
            "_execution_identity_hash",
            lambda item: f"fresh-{item['exec_id']}",
        )

        stale = rehash_mod.find_stale_rows(_TieShufflingDb(rows))

        found = [row["exec_id"] for row in stale]
        assert sorted(found) == [f"exec-{i:04d}" for i in range(25)]
        assert len(found) == len(set(found)), "a tie was double-counted across pages"


STALE_HASH = "0" * 64


def _seed_stale_facts(conn: sqlite3.Connection, layout: dict[str, int]) -> list[tuple[str, str]]:
    """Insert one stale-hash fact per (account, exec) with every ingested_at tied.

    Exec ids restart at exec-0000 in every account so the second account's
    rows sort BELOW the first page's cursor on exec_id alone — the shape a
    column-wise (AND-form) keyset predicate skips and a row-value
    comparison does not.
    """
    keys: list[tuple[str, str]] = []
    for account_id, count in layout.items():
        for i in range(count):
            exec_id = f"exec-{i:04d}"
            payload = dict(BASE_EXECUTION, execId=exec_id, acctNumber=account_id)
            conn.execute(
                "INSERT INTO position_execution_facts "
                "(account_id, exec_id, revision, payload_sha256, perm_id, order_ref, "
                "con_id, side, quantity, price, multiplier, currency, filled_at, "
                "payload, ingested_at) "
                "VALUES (?, ?, 1, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_id, exec_id, STALE_HASH, payload["orderRef"],
                    payload["conId"], payload["side"], payload["shares"],
                    payload["price"], payload["multiplier"], payload["currency"],
                    payload["time"], json.dumps(payload), "2026-08-14T00:00:00Z",
                ),
            )
            keys.append((account_id, exec_id))
    conn.commit()
    return keys


class TestRehashKeysetPagingRealSqlite:
    """T-086: the keyset predicate is executed by real sqlite, not re-implemented
    by the fake — a column-wise AND predicate drops the second account."""

    def test_two_accounts_with_overlapping_exec_ids_are_all_found(self, monkeypatch):
        import rehash_position_execution_facts as rehash_mod

        conn = _fresh_db()
        expected = _seed_stale_facts(conn, {"U1111111": 13, "U2222222": 12})
        assert len(expected) == 25
        monkeypatch.setattr(rehash_mod, "PAGE_SIZE", 10)

        stale = rehash_mod.find_stale_rows(conn)

        found = [(row["account_id"], row["exec_id"]) for row in stale]
        assert len(found) == len(set(found)), "a row was double-counted across pages"
        assert sorted(found) == sorted(expected)
