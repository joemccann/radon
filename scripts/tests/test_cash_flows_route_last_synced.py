"""Regression: /cash-flows must surface `last_synced_at` so the UI can
explain IBKR Flex's once-per-day publication cadence (T+1 settlement
lag) to operators who initiate a withdrawal and don't see it land in
the panel until the next morning's pull.

Background — `feedback_flex_cash_transaction_lag.md`:
    IBKR Flex's CashTransaction section publishes once per day with a
    ~1-day lag. A withdrawal initiated on day N appears in Flex on the
    morning of day N+1. The daemon syncs once per ET trading day at
    17:00 ET. Operators have no way to tell from the panel whether
    they're seeing today's data or yesterday's — they assume the panel
    is broken when a fresh withdrawal doesn't appear.

The route's `last_synced_at` is computed as the max `synced_at` across
the rows returned (after the date-cutoff + type filter). The UI uses
this to render a "synced Xh ago — Flex publishes daily (T+1)" lozenge.

When the table is empty (or all rows fall outside the lookback), the
field is null and the UI falls back to a neutral message.
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
    _SCRIPTS_DIR / "db" / "migrations" / "0002_cash_flows.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def app_with_inmem_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple]:
    """FastAPI TestClient with `api.db_http.hrana_execute` patched to in-mem
    SQLite. The route reads Turso over the bounded hrana HTTP pipeline (sync
    libsql is banned in the API process — DUR-09); the fake preserves the
    rows-as-tuples contract.

    Auth bypass mirrors `test_api_flow_cache.py:app_client`: server.py
    auto-loads .env at import time so CLERK_JWKS_URL is set; we must
    delenv AFTER import to drop the runtime auth check.
    """
    # check_same_thread=False — TestClient runs the route handler in a
    # different thread than the fixture-creation thread. sqlite3's default
    # thread-confinement would raise from inside the route and the
    # `try/except` falls through to the JSON file fallback, returning [].
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    for migration in _MIGRATIONS:
        sql = migration.read_text(encoding="utf-8")
        for stmt in _split_statements(sql):
            conn.execute(stmt)
    conn.commit()

    import api.db_http as db_http_mod

    def fake_hrana_execute(sql: str, args=(), timeout=None):
        return conn.execute(sql, tuple(args)).fetchall()

    monkeypatch.setattr(db_http_mod, "hrana_execute", fake_hrana_execute)

    from fastapi.testclient import TestClient
    from api import server
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)

    try:
        yield TestClient(server.app), conn
    finally:
        conn.close()


def _insert(conn: sqlite3.Connection, *, txn_id: str, date: str, type_: str, amount: float, synced_at: str) -> None:
    conn.execute(
        """
        INSERT INTO cash_flows (id, date, type, amount, currency, description, raw_type, synced_at)
        VALUES (?, ?, ?, ?, 'USD', NULL, ?, ?)
        """,
        (txn_id, date, type_, amount, type_, synced_at),
    )
    conn.commit()


class TestLastSyncedAt:
    def test_returns_max_synced_at_across_returned_rows(self, app_with_inmem_db):
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="t1", date="2026-05-04", type_="Withdrawal", amount=-35_000,
                synced_at="2026-05-05T21:02:00Z")
        _insert(conn, txn_id="t2", date="2026-05-08", type_="Withdrawal", amount=-72_000,
                synced_at="2026-05-09T21:02:00Z")
        _insert(conn, txn_id="t3", date="2026-05-15", type_="Dividend", amount=245.5,
                synced_at="2026-05-20T21:02:34Z")

        resp = client.get("/cash-flows?days=90")
        assert resp.status_code == 200
        body = resp.json()
        # Strict equality — the field IS the max `synced_at` over the result
        # set, used by the UI to compute "synced Xh ago".
        assert body["last_synced_at"] == "2026-05-20T21:02:34Z"

    def test_returns_null_when_no_rows(self, app_with_inmem_db):
        client, _ = app_with_inmem_db
        resp = client.get("/cash-flows?days=90")
        assert resp.status_code == 200
        body = resp.json()
        assert body["rows"] == []
        # null (None in JSON-decoded Python) — not empty string, not 0
        assert body["last_synced_at"] is None

    def test_type_filter_does_not_widen_last_synced_at(self, app_with_inmem_db):
        """When the operator filters by `Withdrawal`, the lozenge should
        reflect the freshest withdrawal sync — not the freshest sync
        across all categories. Otherwise a stale withdrawal looks
        misleadingly fresh next to a recent dividend pull.
        """
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="w1", date="2026-05-04", type_="Withdrawal", amount=-35_000,
                synced_at="2026-05-05T21:02:00Z")
        _insert(conn, txn_id="d1", date="2026-05-15", type_="Dividend", amount=245.5,
                synced_at="2026-05-20T21:02:34Z")

        resp = client.get("/cash-flows?days=90&types=Withdrawal")
        assert resp.status_code == 200
        body = resp.json()
        # Only Withdrawal row survives → its synced_at wins.
        assert body["last_synced_at"] == "2026-05-05T21:02:00Z"

    def test_summary_and_count_unchanged_when_last_synced_at_added(self, app_with_inmem_db):
        """The new field is additive — pre-existing route consumers must
        keep seeing the same `count` / `summary` / `from_date` shape.
        """
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="w1", date="2026-05-04", type_="Withdrawal", amount=-35_000,
                synced_at="2026-05-05T21:02:00Z")
        _insert(conn, txn_id="w2", date="2026-05-08", type_="Withdrawal", amount=-72_000,
                synced_at="2026-05-09T21:02:00Z")

        resp = client.get("/cash-flows?days=90")
        body = resp.json()
        assert body["count"] == 2
        assert body["summary"]["withdrawals"] == -107_000
        assert body["summary"]["deposits"] == 0
        assert body["summary"]["net"] == -107_000
        assert "from_date" in body


def _insert_service_health(
    conn: sqlite3.Connection,
    *,
    state: str,
    last_attempt_finished_at: str,
    last_error: str | None = None,
) -> None:
    """Seed a service_health row for the cash-flow-sync daemon."""
    conn.execute(
        """
        INSERT INTO service_health
            (service, state, last_attempt_finished_at, last_error, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(service) DO UPDATE SET
            state = excluded.state,
            last_attempt_finished_at = excluded.last_attempt_finished_at,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        ("cash-flow-sync", state, last_attempt_finished_at, last_error, last_attempt_finished_at),
    )
    conn.commit()


class TestSyncStatus:
    """The `sync_status` payload surfaces the daemon's most recent attempt
    state on the response so the lozenge can explain WHY a synced-Xh-ago
    reading is stale — most commonly an IBKR Flex throttle.
    """

    def test_surfaces_throttle_state_with_next_attempt_at(self, app_with_inmem_db):
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="w1", date="2026-05-08", type_="Withdrawal", amount=-72_000,
                synced_at="2026-05-20T21:02:34Z")
        _insert_service_health(
            conn,
            state="error",
            last_attempt_finished_at="2026-05-21T21:00:35Z",
            last_error=(
                '{"message": "ERR: cash flow fetch failed: Flex throttle (code 1001): '
                'Statement could not be generated at this time. Please try again shortly.", '
                '"next_attempt_at": "2026-05-22T21:00:35.990665+00:00"}'
            ),
        )

        resp = client.get("/cash-flows?days=90")
        body = resp.json()
        sync_status = body.get("sync_status")
        assert sync_status is not None, body
        assert sync_status["state"] == "error"
        assert sync_status["is_throttled"] is True
        assert sync_status["next_attempt_at"] == "2026-05-22T21:00:35.990665+00:00"
        # Concise, user-facing — not the raw IBKR sentence.
        assert sync_status["error_summary"] == "Flex throttled by IBKR"
        # last_attempt_at is the daemon's last try, distinct from
        # last_synced_at (which is the freshest row's synced_at).
        assert sync_status["last_attempt_at"] == "2026-05-21T21:00:35Z"
        assert body["last_synced_at"] == "2026-05-20T21:02:34Z"

    def test_ok_state_does_not_flag_throttle(self, app_with_inmem_db):
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="w1", date="2026-05-08", type_="Withdrawal", amount=-72_000,
                synced_at="2026-05-20T21:02:34Z")
        _insert_service_health(
            conn,
            state="ok",
            last_attempt_finished_at="2026-05-21T21:02:34Z",
            last_error=None,
        )

        resp = client.get("/cash-flows?days=90")
        body = resp.json()
        sync_status = body["sync_status"]
        assert sync_status["state"] == "ok"
        assert sync_status["is_throttled"] is False
        assert sync_status["error_summary"] is None
        assert sync_status["next_attempt_at"] is None

    def test_generic_error_is_surfaced_but_not_flagged_as_throttle(self, app_with_inmem_db):
        """Non-Flex-throttle errors (timeouts, network blips, etc.) still
        surface on the lozenge but should NOT carry the throttle tag —
        the operator needs to distinguish "IBKR is throttling us" from
        "something else broke" because the resolution paths differ.
        """
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="w1", date="2026-05-08", type_="Withdrawal", amount=-72_000,
                synced_at="2026-05-20T21:02:34Z")
        _insert_service_health(
            conn,
            state="error",
            last_attempt_finished_at="2026-05-21T21:00:35Z",
            last_error=(
                '{"message": "cash_flow_sync timed out after 180s", '
                '"next_attempt_at": "2026-05-21T21:05:35Z"}'
            ),
        )

        resp = client.get("/cash-flows?days=90")
        body = resp.json()
        sync_status = body["sync_status"]
        assert sync_status["state"] == "error"
        assert sync_status["is_throttled"] is False
        assert sync_status["error_summary"] == "cash_flow_sync timed out after 180s"
        assert sync_status["next_attempt_at"] == "2026-05-21T21:05:35Z"

    def test_no_service_health_row_returns_unknown_state(self, app_with_inmem_db):
        client, conn = app_with_inmem_db
        _insert(conn, txn_id="w1", date="2026-05-08", type_="Withdrawal", amount=-72_000,
                synced_at="2026-05-20T21:02:34Z")
        # No service_health insert.

        resp = client.get("/cash-flows?days=90")
        body = resp.json()
        sync_status = body["sync_status"]
        assert sync_status["state"] == "unknown"
        assert sync_status["is_throttled"] is False
        assert sync_status["error_summary"] is None
