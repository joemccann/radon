"""Chronos-2 DB layer — writer + reader coverage on in-memory sqlite.

Loads migrations 0001 + 0014 + 0015 into sqlite :memory: (same pattern as
scripts/tests/test_watchdog/conftest.py) and monkeypatches both
db.client.get_db and db.writer.get_db to that connection. No network, no
torch / chronos dependency.
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
    _SCRIPTS_DIR / "db" / "migrations" / "0015_forecast_snapshots.sql",
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
    import db.writer as writer_mod

    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    monkeypatch.setattr(writer_mod, "get_db", lambda: conn)

    try:
        yield conn
    finally:
        conn.close()


def test_upsert_and_read_ticker_flow_history(db_conn: sqlite3.Connection) -> None:
    from db.writer import get_ticker_flow_history, upsert_ticker_flow_history

    upsert_ticker_flow_history(
        "spy",
        "2026-06-18",
        flow_strength=0.7,
        dp_direction="bullish",
        buy_ratio=0.6,
        num_prints=120,
        total_premium=1_000_000.0,
        total_volume=50_000,
    )
    rows = get_ticker_flow_history("SPY")
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "SPY"
    assert row["date"] == "2026-06-18"
    assert row["flow_strength"] == 0.7
    assert row["dp_direction"] == "bullish"
    assert row["buy_ratio"] == 0.6
    assert row["num_prints"] == 120
    assert row["total_premium"] == 1_000_000.0
    assert row["total_volume"] == 50_000
    assert row["recorded_at"]


def test_upsert_ticker_flow_history_replaces_same_key(db_conn: sqlite3.Connection) -> None:
    from db.writer import get_ticker_flow_history, upsert_ticker_flow_history

    upsert_ticker_flow_history("SPY", "2026-06-18", flow_strength=0.1)
    upsert_ticker_flow_history("SPY", "2026-06-18", flow_strength=0.9)
    rows = get_ticker_flow_history("SPY")
    assert len(rows) == 1
    assert rows[0]["flow_strength"] == 0.9


def test_get_ticker_flow_history_ascending_by_date(db_conn: sqlite3.Connection) -> None:
    from db.writer import get_ticker_flow_history, upsert_ticker_flow_history

    for date, strength in [
        ("2026-06-18", 0.3),
        ("2026-06-16", 0.1),
        ("2026-06-17", 0.2),
    ]:
        upsert_ticker_flow_history("QQQ", date, flow_strength=strength)

    rows = get_ticker_flow_history("QQQ")
    dates = [r["date"] for r in rows]
    assert dates == ["2026-06-16", "2026-06-17", "2026-06-18"]
    assert [r["flow_strength"] for r in rows] == [0.1, 0.2, 0.3]


def test_get_ticker_flow_history_respects_lookback(db_conn: sqlite3.Connection) -> None:
    from db.writer import get_ticker_flow_history, upsert_ticker_flow_history

    for day in range(1, 6):
        upsert_ticker_flow_history("IWM", f"2026-06-0{day}", flow_strength=float(day))

    rows = get_ticker_flow_history("IWM", lookback_days=3)
    assert len(rows) == 3
    assert [r["date"] for r in rows] == ["2026-06-03", "2026-06-04", "2026-06-05"]


def test_history_lookback_keeps_latest_250_rows(db_conn: sqlite3.Connection) -> None:
    from db.writer import get_ticker_flow_history, upsert_ticker_flow_history

    for day in range(300):
        upsert_ticker_flow_history(
            "SPY", f"2025-01-01T00:00:00Z-{day:03d}", flow_strength=float(day)
        )

    rows = get_ticker_flow_history("SPY", lookback_days=250)

    assert len(rows) == 250
    assert [row["flow_strength"] for row in rows] == list(map(float, range(50, 300)))


def test_upsert_and_read_forecast_snapshot(db_conn: sqlite3.Connection) -> None:
    from db.writer import upsert_forecast_snapshot

    payload = {
        "model_id": "amazon/chronos-2",
        "horizon": 3,
        "quantiles": {"0.5": [1.0, 2.0, 3.0]},
        "median": [1.0, 2.0, 3.0],
    }
    upsert_forecast_snapshot(
        "spy",
        "flow_strength",
        "2026-06-18T12:00:00Z",
        3,
        "amazon/chronos-2",
        payload,
    )

    cursor = db_conn.execute(
        "SELECT ticker, metric, scan_time, horizon, model_id, payload "
        "FROM forecast_snapshots"
    )
    rows = cursor.fetchall()
    assert len(rows) == 1
    ticker, metric, scan_time, horizon, model_id, stored_payload = rows[0]
    assert ticker == "SPY"
    assert metric == "flow_strength"
    assert scan_time == "2026-06-18T12:00:00Z"
    assert horizon == 3
    assert model_id == "amazon/chronos-2"

    import json

    assert json.loads(stored_payload) == payload


def test_upsert_forecast_snapshot_replaces_same_key(db_conn: sqlite3.Connection) -> None:
    from db.writer import upsert_forecast_snapshot

    upsert_forecast_snapshot(
        "SPY", "flow_strength", "2026-06-18T12:00:00Z", 3, "amazon/chronos-2", {"v": 1}
    )
    upsert_forecast_snapshot(
        "SPY", "flow_strength", "2026-06-18T12:00:00Z", 3, "amazon/chronos-2", {"v": 2}
    )

    rows = db_conn.execute("SELECT payload FROM forecast_snapshots").fetchall()
    assert len(rows) == 1

    import json

    assert json.loads(rows[0][0]) == {"v": 2}
