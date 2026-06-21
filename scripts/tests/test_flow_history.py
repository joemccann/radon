"""Tests for forecasting.flow_history accrual + reader (section C).

Runs against an in-memory SQLite loaded with migrations 0001 + 0014, with
both db.client.get_db and db.writer.get_db patched to the same connection.
Passes WITHOUT torch / chronos installed (no engine imports here).
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0014_ticker_flow_history.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db_conn(monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        sql = migration.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc):
                    continue
                raise
    conn.commit()

    import db.client as client_mod
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)

    import db.writer as writer_mod
    monkeypatch.setattr(writer_mod, "get_db", lambda: conn)

    try:
        yield conn
    finally:
        conn.close()


def test_record_daily_flow_writes_readable_row(db_conn):
    from forecasting.flow_history import flow_series_for, record_daily_flow

    record_daily_flow("AAPL", "2026-06-18", {"strength": 55.0, "direction": "ACCUMULATION"})

    dates, values = flow_series_for("AAPL")
    assert dates == ["2026-06-18"]
    assert values == [55.0]


def test_record_daily_flow_maps_all_scan_fields(db_conn):
    from forecasting.flow_history import record_daily_flow

    record_daily_flow(
        "MSFT",
        "2026-06-18",
        {
            "flow_strength": 70.0,
            "buy_ratio": 0.62,
            "num_prints": 240,
            "dp_direction": "DISTRIBUTION",
            "total_premium": 1_250_000.0,
            "total_volume": 9001,
        },
    )

    row = db_conn.execute(
        "SELECT flow_strength, buy_ratio, num_prints, dp_direction, total_premium, total_volume "
        "FROM ticker_flow_history WHERE ticker = 'MSFT'"
    ).fetchone()
    assert row == (70.0, 0.62, 240, "DISTRIBUTION", 1_250_000.0, 9001)


def test_flow_series_for_ascending_and_drops_none(db_conn):
    from forecasting.flow_history import flow_series_for, record_daily_flow

    record_daily_flow("NVDA", "2026-06-18", {"strength": 30.0})
    record_daily_flow("NVDA", "2026-06-16", {"strength": 10.0})
    record_daily_flow("NVDA", "2026-06-17", {"strength": None})  # dropped
    record_daily_flow("NVDA", "2026-06-19", {"strength": 40.0})

    dates, values = flow_series_for("NVDA")
    assert dates == ["2026-06-16", "2026-06-18", "2026-06-19"]
    assert values == [10.0, 30.0, 40.0]


def test_record_daily_flow_swallows_writer_errors(db_conn, monkeypatch, capsys):
    import db.writer as writer_mod
    from forecasting import flow_history

    def _boom(*args, **kwargs):
        raise RuntimeError("turso unreachable")

    monkeypatch.setattr(writer_mod, "upsert_ticker_flow_history", _boom)

    # Must NOT raise.
    flow_history.record_daily_flow("TSLA", "2026-06-18", {"strength": 99.0})

    err = capsys.readouterr().err
    assert "record_daily_flow" in err
    assert "TSLA" in err
