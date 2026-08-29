"""Regression tests for canonical Turso reader helpers."""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Import-time, process-wide and never undone, so this stub is visible to every
# test module collected alongside this one. `not in sys.modules` is true
# whenever the real driver merely has not been imported YET, which is the
# normal case, so the stub displaced a driver that was installed all along:
# test_flex_delivery_claim_writer.py exists to exercise the claim against a
# REAL libsql connection and got MagicMocks instead, taking `rowcount` and
# `fetchall()` with it. Stub only when the driver is genuinely absent.
try:  # noqa: SIM105 - the ImportError is the condition, not an accident
    import libsql_experimental  # noqa: F401
except ImportError:
    _libsql_stub = types.ModuleType("libsql_experimental")
    _libsql_stub.connect = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["libsql_experimental"] = _libsql_stub

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0004_orders_and_ephemeral.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0025_journal_effective_at_index.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def conn() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        for stmt in _split_statements(migration.read_text(encoding="utf-8")):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc):
                    continue
                raise
    db.commit()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def readers():
    import db.readers as readers_mod
    return importlib.reload(readers_mod)


def test_reads_latest_portfolio_snapshot(readers, conn):
    conn.execute(
        "INSERT INTO portfolio_snapshots (taken_at, payload) VALUES (?, ?)",
        ("2026-06-22T10:00:00Z", json.dumps({"positions": [{"ticker": "OLD"}]})),
    )
    conn.execute(
        "INSERT INTO portfolio_snapshots (taken_at, payload) VALUES (?, ?)",
        ("2026-06-23T10:00:00Z", json.dumps({"positions": [{"ticker": "NEW"}]})),
    )
    conn.commit()

    assert readers.read_latest_portfolio_snapshot(conn) == {"positions": [{"ticker": "NEW"}]}


def test_reads_positions_from_latest_portfolio_snapshot(readers, conn):
    conn.execute(
        "INSERT INTO portfolio_snapshots (taken_at, payload) VALUES (?, ?)",
        ("2026-06-23T10:00:00Z", json.dumps({"positions": [{"ticker": "AAPL"}, "bad"]})),
    )
    conn.commit()

    assert readers.read_portfolio_positions(conn) == [{"ticker": "AAPL"}]


def test_reads_open_orders_from_table(readers, conn):
    conn.execute(
        "INSERT INTO open_orders (perm_id, payload, updated_at) VALUES (?, ?, ?)",
        (9001, json.dumps({"permId": 9001, "symbol": "AAPL"}), "2026-06-23T10:00:00Z"),
    )
    conn.commit()

    assert readers.read_open_orders(conn) == [{"permId": 9001, "symbol": "AAPL"}]


def test_read_open_orders_drops_prior_session_day(readers, conn):
    conn.execute(
        "INSERT INTO open_orders (perm_id, payload, updated_at) VALUES (?, ?, ?)",
        (
            1857171561,
            json.dumps({"permId": 1857171561, "tif": "DAY", "symbol": "CBRS P230"}),
            "2026-08-13T19:59:51Z",
        ),
    )
    conn.execute(
        "INSERT INTO open_orders (perm_id, payload, updated_at) VALUES (?, ?, ?)",
        (
            2128244184,
            json.dumps({"permId": 2128244184, "tif": "GTC", "symbol": "META P560"}),
            "2026-08-13T19:59:51Z",
        ),
    )
    conn.commit()

    assert [row["permId"] for row in readers.read_open_orders(conn)] == [2128244184]


def test_reads_executed_orders_from_table(readers, conn):
    conn.execute(
        """
        INSERT INTO executed_orders (exec_id, perm_id, payload, fill_time, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "exec-1",
            9001,
            json.dumps({"execId": "exec-1", "symbol": "AAPL"}),
            "2026-06-23T10:00:00Z",
            "2026-06-23T10:00:01Z",
        ),
    )
    conn.commit()

    assert readers.read_executed_orders(conn) == [
        {
            "exec_id": "exec-1",
            "payload": {"execId": "exec-1", "symbol": "AAPL"},
            "fill_time": "2026-06-23T10:00:00Z",
        }
    ]


def test_derives_structure_and_contract_dates_from_journal(readers, conn):
    payloads = [
        {
            "id": 7,
            "ticker": "PLTR",
            "date": "2026-03-24",
            "structure": "Risk Reversal (P$152.5/C$155.0)",
            "expiry": "20260327",
            "right": "C",
            "strike": 155,
        },
        {
            "id": 8,
            "ticker": "PLTR",
            "date": "2026-03-24",
            "structure": "Risk Reversal (P$152.5/C$155.0)",
            "expiry": "20260327",
            "right": "P",
            "strike": 152.5,
        },
        {
            "id": 9,
            "ticker": "AMD",
            "date": "2026-04-22",
            "structure": "Long Put $295",
            "expiry": "20260501",
            "right": "P",
            "strike": 295,
        },
    ]
    for idx, payload in enumerate(payloads):
        conn.execute(
            "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
            (f"trade-{idx}", json.dumps(payload), payload["date"], f"{payload['date']}T10:00:00Z"),
        )
    conn.commit()

    structure_dates, contract_dates = readers.read_journal_entry_date_maps(conn)

    assert structure_dates["PLTR|Risk Reversal (P$152.5/C$155.0)"] == "2026-03-24"
    assert contract_dates["PLTR|2026-03-27|C|155.0"] == "2026-03-24"
    assert contract_dates["PLTR|2026-03-27|P|152.5"] == "2026-03-24"
    assert "AMD|2026-05-08|C|330.0" not in contract_dates


def test_next_journal_numeric_id_comes_from_journal_payloads(readers, conn):
    for idx in [2, 7, "bad"]:
        conn.execute(
            "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
            (f"trade-{idx}", json.dumps({"id": idx}), "2026-06-23", "2026-06-23T10:00:00Z"),
        )
    conn.commit()

    assert readers.read_next_journal_numeric_id(conn) == 8


def test_next_journal_numeric_id_does_not_materialize_payloads(readers, conn, monkeypatch):
    """The next-id read must be a SQL aggregate, not a full journal
    deserialization — read_journal_trades is O(rows) and grows forever."""
    for idx in [2, 7]:
        conn.execute(
            "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
            (f"trade-{idx}", json.dumps({"id": idx}), "2026-06-23", "2026-06-23T10:00:00Z"),
        )
    conn.commit()

    def _boom(*_args, **_kwargs):
        raise AssertionError("read_next_journal_numeric_id must not read every journal payload")

    monkeypatch.setattr(readers, "read_journal_trades", _boom)

    assert readers.read_next_journal_numeric_id(conn) == 8

    conn.execute("DELETE FROM journal")
    conn.commit()
    assert readers.read_next_journal_numeric_id(conn) == 1


def test_journal_effective_order_rides_expression_index(readers, conn):
    """ORDER BY COALESCE(filled_at, written_at) must walk
    idx_journal_effective_at (migration 0025) instead of a full-scan +
    temp-B-tree sort that grows with trade history."""
    plan_rows = conn.execute(
        "EXPLAIN QUERY PLAN SELECT payload FROM journal "
        f"ORDER BY {readers.JOURNAL_EFFECTIVE_ORDER_SQL}"
    ).fetchall()
    plan = " ".join(str(row[-1]) for row in plan_rows)
    assert "idx_journal_effective_at" in plan
    assert "TEMP B-TREE" not in plan


def test_reads_watchlist_tickers_from_table(readers, conn):
    conn.execute(
        "INSERT INTO watchlist (ticker, sector, source, payload, last_seen) VALUES (?, ?, ?, ?, ?)",
        ("msft", "tech", "test", None, "2026-06-23T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO watchlist (ticker, sector, source, payload, last_seen) VALUES (?, ?, ?, ?, ?)",
        ("AAPL", "tech", "test", None, "2026-06-23T10:00:00Z"),
    )
    conn.commit()

    assert readers.read_watchlist_tickers(conn) == ["AAPL", "MSFT"]


def test_reads_watchlist_items_from_table_payload(readers, conn):
    conn.execute(
        "INSERT INTO watchlist (ticker, sector, source, payload, last_seen) VALUES (?, ?, ?, ?, ?)",
        (
            "MSFT",
            "Technology",
            "manual",
            json.dumps({"ticker": "msft", "sector": "Software", "note": "tracked"}),
            "2026-06-23T10:00:00Z",
        ),
    )
    conn.execute(
        "INSERT INTO watchlist (ticker, sector, source, payload, last_seen) VALUES (?, ?, ?, ?, ?)",
        ("AAPL", "Hardware", "manual", None, "2026-06-23T10:00:00Z"),
    )
    conn.commit()

    assert readers.read_watchlist_items(conn) == [
        {"ticker": "AAPL", "sector": "Hardware", "source": "manual"},
        {"ticker": "MSFT", "sector": "Software", "note": "tracked", "source": "manual"},
    ]
