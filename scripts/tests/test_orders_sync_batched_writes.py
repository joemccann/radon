"""Orders-sync writes are batched and diffed, never per-fill round trips.

The orders-sync cycle re-sent every fill in IB's session snapshot as one
``INSERT OR REPLACE`` + commit into executed_orders, then one SELECT (and
sometimes an INSERT/UPDATE + commit) into position_execution_facts. Over the
direct-to-cloud Hrana transport each statement is a sequential round trip
(~55-117 ms measured from prod ``recorded_at`` spreads), so a 106-fill day
spent 11.7 s per pass and a 385-fill day could not finish inside the 30 s
subprocess timeout. These tests pin the bounded shape: chunked reads, only
changed rows written, chunked multi-row writes, one commit — with the fact
ledger's tolerated-drift and conflict semantics unchanged.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATIONS = (
    _SCRIPTS_DIR / "db" / "migrations" / "0004_orders_and_ephemeral.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0037_position_return_capital.sql",
)


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


class CountingConnection:
    """sqlite3 connection proxy that records every statement and commit."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.statements: list[str] = []
        self.commits = 0

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        assert isinstance(params, tuple), "libsql binding accepts tuple params only"
        self.statements.append(" ".join(sql.split()))
        return self._conn.execute(sql, params)

    def commit(self) -> None:
        self.commits += 1
        self._conn.commit()

    def reset(self) -> None:
        self.statements.clear()
        self.commits = 0

    def writes(self) -> list[str]:
        return [s for s in self.statements if not s.upper().startswith("SELECT")]

    def raw(self) -> sqlite3.Connection:
        return self._conn


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> Iterator[CountingConnection]:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for migration in _MIGRATIONS:
        for stmt in _split_statements(migration.read_text(encoding="utf-8")):
            conn.execute(stmt)
    conn.commit()
    counting = CountingConnection(conn)
    import db.writer as writer_mod

    monkeypatch.setitem(sys.modules, "db.writer", writer_mod)
    monkeypatch.setattr(writer_mod, "get_db", lambda: counting)
    try:
        yield counting
    finally:
        conn.close()


def _fill(index: int, **overrides: Any) -> dict[str, Any]:
    fill = {
        "execId": f"0001f4e8.{index:08x}.01.01",
        "account_id": "U0000001",
        "permId": 70_000 + index,
        "orderRef": f"radon-{index}",
        "side": "BOT",
        "quantity": 2.0,
        "price": 4.15,
        "avgPrice": 4.15,
        "time": "2026-10-09T14:31:02+00:00",
        "currency": "USD",
        "multiplier": 100,
        "contract": {"conId": 600_000 + index, "symbol": "MU", "secType": "OPT"},
    }
    fill.update(overrides)
    return fill


def _order_rows(fills: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any], str, int | None]]:
    return [(f["execId"], f, f["time"], f.get("permId")) for f in fills]


def _recorded_at(db: CountingConnection) -> dict[str, str]:
    return dict(db.raw().execute("SELECT exec_id, recorded_at FROM executed_orders").fetchall())


# ── executed_orders ──────────────────────────────────────────────────


class TestExecutedOrdersBatch:
    def test_session_write_is_bounded_and_commits_once(self, db):
        import db.writer as writer

        fills = [_fill(i) for i in range(385)]  # 2026-09-14 peak day
        written = writer.upsert_executed_orders_for_session(_order_rows(fills))

        assert written == 385
        assert len(db.statements) <= 6, db.statements
        assert db.commits == 1
        stored = db.raw().execute("SELECT COUNT(*) FROM executed_orders").fetchone()[0]
        assert stored == 385

    def test_unchanged_rows_are_skipped(self, db):
        import db.writer as writer

        rows = _order_rows([_fill(i) for i in range(106)])
        writer.upsert_executed_orders_for_session(rows)
        before = _recorded_at(db)
        db.reset()

        written = writer.upsert_executed_orders_for_session(rows)

        assert written == 0
        assert db.writes() == []
        assert _recorded_at(db) == before

    def test_only_changed_rows_are_rewritten(self, db):
        import db.writer as writer

        fills = [_fill(i) for i in range(5)]
        writer.upsert_executed_orders_for_session(_order_rows(fills))
        db.raw().execute("UPDATE executed_orders SET recorded_at = 'old'")
        db.raw().commit()
        db.reset()

        changed = dict(fills[2], realizedPNL=2000.0, realizedPNLSource="journal")
        fills[2] = changed
        written = writer.upsert_executed_orders_for_session(_order_rows(fills))

        assert written == 1
        assert len(db.writes()) == 1
        payload = db.raw().execute(
            "SELECT payload FROM executed_orders WHERE exec_id = ?", (changed["execId"],)
        ).fetchone()[0]
        assert json.loads(payload)["realizedPNL"] == 2000.0
        stamps = _recorded_at(db)
        assert stamps[changed["execId"]] != "old"
        assert sorted(v for k, v in stamps.items() if k != changed["execId"]) == ["old"] * 4

    def test_late_perm_id_reaches_the_row(self, db):
        import db.writer as writer

        fill = _fill(1)
        writer.upsert_executed_orders_for_session([(fill["execId"], fill, fill["time"], None)])
        writer.upsert_executed_orders_for_session([(fill["execId"], fill, fill["time"], 7001)])

        perm_id = db.raw().execute("SELECT perm_id FROM executed_orders").fetchone()[0]
        assert perm_id == 7001

    def test_empty_session_touches_nothing(self, db):
        import db.writer as writer

        assert writer.upsert_executed_orders_for_session([]) == 0
        assert db.statements == []
        assert db.commits == 0


# ── position_execution_facts ─────────────────────────────────────────


def _fact_rows(db: CountingConnection) -> dict[str, tuple]:
    rows = db.raw().execute(
        "SELECT exec_id, payload_sha256, perm_id, order_ref, price, ingested_at "
        "FROM position_execution_facts"
    ).fetchall()
    return {row[0]: row[1:] for row in rows}


class TestExecutionFactsBatch:
    def test_new_facts_are_inserted_in_bounded_statements(self, db):
        import db.writer as writer

        result = writer.upsert_position_execution_facts([_fill(i) for i in range(385)])

        assert result.written == 385
        assert result.failures == []
        assert len(db.statements) <= 6, db.statements
        assert db.commits == 1
        assert len(_fact_rows(db)) == 385

    def test_unchanged_facts_are_skipped(self, db):
        import db.writer as writer

        fills = [_fill(i) for i in range(106)]
        writer.upsert_position_execution_facts(fills)
        before = _fact_rows(db)
        db.reset()

        result = writer.upsert_position_execution_facts(fills)

        assert result.written == 0
        assert db.writes() == []
        assert _fact_rows(db) == before

    def test_tolerated_drift_converges_without_touching_ingested_at(self, db):
        import db.writer as writer

        fill = _fill(1, permId=None)
        writer.upsert_position_execution_facts([fill])
        [(old_hash, _, _, _, ingested_at)] = _fact_rows(db).values()

        enriched = dict(fill, permId=9_100_001)
        result = writer.upsert_position_execution_facts([enriched])

        assert result.written == 1
        assert result.failures == []
        [(new_hash, perm_id, _, _, new_ingested_at)] = _fact_rows(db).values()
        assert new_hash != old_hash
        assert perm_id == 9_100_001
        assert new_ingested_at == ingested_at

    def test_economic_conflict_is_reported_and_never_overwritten(self, db, caplog):
        import db.writer as writer

        fills = [_fill(i) for i in range(3)]
        writer.upsert_position_execution_facts(fills)
        before = _fact_rows(db)

        conflicting = dict(fills[1], price=9.99)
        newcomer = _fill(99)
        with caplog.at_level(logging.WARNING, logger=writer.logger.name):
            result = writer.upsert_position_execution_facts([fills[0], conflicting, fills[2], newcomer])

        assert result.written == 1
        [(exec_id, exc)] = result.failures
        assert exec_id == conflicting["execId"]
        assert isinstance(exc, ValueError)
        assert "execution fact conflict" in str(exc)
        assert "execution fact conflict" in caplog.text
        after = _fact_rows(db)
        assert after[conflicting["execId"]] == before[conflicting["execId"]]
        assert newcomer["execId"] in after

    def test_malformed_execution_is_a_per_row_failure(self, db):
        import db.writer as writer

        bad = _fill(5, side="HOLD")
        result = writer.upsert_position_execution_facts([bad, _fill(6)])

        assert result.written == 1
        [(exec_id, exc)] = result.failures
        assert exec_id == bad["execId"]
        assert isinstance(exc, ValueError)

    def test_single_row_wrapper_keeps_its_contract(self, db):
        import db.writer as writer

        fill = _fill(1)
        assert writer.upsert_position_execution_fact(fill) is True
        assert writer.upsert_position_execution_fact(fill) is False
        with pytest.raises(ValueError, match="execution fact conflict"):
            writer.upsert_position_execution_fact(dict(fill, price=9.99))


# ── orders-sync cycle ────────────────────────────────────────────────


def test_orders_sync_cycle_statement_count_is_bounded(db, monkeypatch, capsys):
    """Whole cycle against the real writer: ~3 statements per fill before."""
    import db.writer as writer

    monkeypatch.setattr(writer, "record_service_health", lambda *a, **kw: None)
    monkeypatch.setattr(writer, "replace_open_orders_for_session", lambda rows: None)
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
    if "ib_orders" in sys.modules:
        del sys.modules["ib_orders"]
    import ib_orders

    monkeypatch.setattr(ib_orders, "_overlay_journal_realized_pnl", lambda executed: None)
    fills = [_fill(i) for i in range(106)]
    data = {"last_sync": "2026-10-09T23:56:02Z", "open_orders": [], "executed_orders": fills}

    ib_orders._dual_write_orders_to_db(data)
    order_and_fact = [
        s for s in db.statements
        if "executed_orders" in s or "position_execution_facts" in s
    ]
    assert len(order_and_fact) <= 6, order_and_fact

    db.reset()
    ib_orders._dual_write_orders_to_db(data)
    steady = [
        s for s in db.statements
        if "executed_orders" in s or "position_execution_facts" in s
    ]
    assert all(s.upper().startswith("SELECT") for s in steady), steady
    assert len(steady) <= 2
