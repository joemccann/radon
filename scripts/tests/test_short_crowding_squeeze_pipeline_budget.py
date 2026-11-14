"""PERF-056: squeeze-score upserts must be one Turso statement per cycle.

`_write_squeeze_rows` issued one `execute` per ticker. Over Hrana that is
one HTTP round-trip per row. `radon-equibles-short-crowding` caps the
universe at `MAX_TICKERS` (40); short-interest already batches through
`_chunked_upsert`. The squeeze path must share that budget.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import fetch_equibles_short_crowding as mod

MIGRATION = (
    Path(__file__).resolve().parents[1] / "db" / "migrations" / "0042_equibles_short_crowding.sql"
)
N_TICKERS = 40
SCAN_TIME = "2026-11-13T12:00:00Z"


class _CountDb:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.execute_calls = 0

    def execute(self, sql, args=()):
        self.execute_calls += 1
        return self._conn.execute(sql, args)

    def commit(self) -> None:
        self._conn.commit()


def _payload(n: int = N_TICKERS) -> dict:
    return {
        "entries": [
            {
                "ticker": f"T{i:02d}",
                "squeeze_score": 50.0 + i,
                "squeeze_rank": i + 1,
                "base_score": 40.0,
                "catalyst_boost": 5.0,
                "short_interest_pct": 4.2,
                "short_interest_pct_basis": "shares",
                "market_cap": 1_000_000_000.0,
                "factors": {"daysToCoverPercentile": 80.0},
                "settlement_date": "2026-11-04",
            }
            for i in range(n)
        ]
    }


def test_forty_squeeze_scores_are_one_statement(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    conn.executescript(MIGRATION.read_text())
    db = _CountDb(conn)
    monkeypatch.setattr("db.client.get_db", lambda: db)

    mod._write_squeeze_rows(_payload(), SCAN_TIME)

    assert db.execute_calls == 1, (
        f"Turso statements {db.execute_calls} "
        f"(want 1 multi-row INSERT for {N_TICKERS} squeeze scores)"
    )
    rows = conn.execute(
        "SELECT ticker, score, rank FROM equibles_squeeze_scores ORDER BY ticker"
    ).fetchall()
    assert len(rows) == N_TICKERS
    assert rows[0] == ("T00", 50.0, 1)
    assert rows[-1] == ("T39", 89.0, 40)
