#!/usr/bin/env python3
"""TDD tests for backfill_journal_from_executed_orders.py (JRN-01).

Red/Green sequence:
  1. Build the correct journal payload from the known MU executed_orders row:
     - action == SELL_TO_OPEN (prior_qty was -8, so this SLD adds to a short)
     - contracts == 7
     - strike == 1000.0
     - total_cost == 17505.2713
     - ib_exec_id carried in payload
  2. Idempotency: skips when journal already has the row.
  3. Dry-run: writes nothing regardless of gaps.
  4. Pytest guard: refuses RADON_DB_TEST_WRITE_OK=0 under PYTEST_CURRENT_TEST.
  5. EWY sibling row: contracts==10, strike==215.0, action==SELL_TO_OPEN.

All DB interaction is via in-memory SQLite; never touches real Turso.

libsql_experimental is NOT available on the laptop dev environment (Python 3.9).
We stub it in sys.modules before any db.client import so the module-level
`import libsql_experimental as libsql` doesn't raise ImportError.
"""

from __future__ import annotations

import importlib
import json
import re
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ── path bootstrap ────────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ── stub libsql_experimental before any db.client import ─────────────────────
# libsql_experimental is a C extension only available on the prod Python 3.13
# environment.  We inject a minimal stub so db.client imports cleanly and tests
# can then monkeypatch get_db() to return an in-memory sqlite3 connection.
if "libsql_experimental" not in sys.modules:
    _libsql_stub = types.ModuleType("libsql_experimental")
    _libsql_stub.connect = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["libsql_experimental"] = _libsql_stub


_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0002_cash_flows.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0003_phase2_snapshots.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0004_orders_and_ephemeral.sql",
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


def _fresh_db() -> sqlite3.Connection:
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
    return conn


def _patch_db(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make db.client.get_db() and db.writer's imported get_db return in-memory conn."""
    import db.client as client_mod
    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)
    # Reload writer so it picks up the patched get_db.
    import db.writer as writer_mod
    importlib.reload(writer_mod)


def _insert_executed_order(
    conn: sqlite3.Connection,
    exec_id: str,
    payload: dict,
    fill_time: str,
    perm_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO executed_orders (exec_id, perm_id, payload, fill_time, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (exec_id, perm_id, json.dumps(payload), fill_time, "2026-06-08T23:28:13.669255Z"),
    )
    conn.commit()


def _insert_journal(
    conn: sqlite3.Connection,
    trade_id: str,
    payload: dict,
    filled_at: str,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO journal (trade_id, payload, filled_at, written_at) "
        "VALUES (?, ?, ?, ?)",
        (trade_id, json.dumps(payload), filled_at, "2026-06-08T23:28:13.669255Z"),
    )
    conn.commit()


def _journal_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT trade_id, payload, filled_at FROM journal").fetchall()
    return [{"trade_id": r[0], "payload": json.loads(r[1]), "filled_at": r[2]} for r in rows]


# ── canonical test fixtures ───────────────────────────────────────────────────

# Exact payload from trade_log.json id 604 (RCA canonical_journal_payload)
MU_EXEC_ID = "0002920b.6a26c483.01.01"
MU_FILL_TIME = "2026-06-08T15:00:00+00:00"
MU_DISK_ROW: dict[str, Any] = {
    "id": 604,
    "date": "2026-06-08",
    "ticker": "MU",
    "structure": "Closed Call $1000 2026-06-12",
    "decision": "IB_AUTO_IMPORT",
    "action": "SELL_TO_OPEN",
    "fill_price": 25.0,
    "total_cost": 17505.2713,
    "commission": 5.2713,
    "ib_exec_id": MU_EXEC_ID,
    "notes": "Imported from IB session fills on 2026-06-08",
    "contracts": 7,
    "strike": 1000.0,
    "right": "C",
    "expiry": "20260612",
}

MU_EXEC_PAYLOAD = {
    "execId": MU_EXEC_ID,
    "symbol": "MU C1000",
    "contract": {
        "conId": 879417508,
        "symbol": "MU",
        "secType": "OPT",
        "strike": 1000.0,
        "right": "C",
        "expiry": "2026-06-12",
    },
    "side": "SLD",
    "quantity": 7.0,
    "avgPrice": 25.0,
    "commission": 5.2713,
    "realizedPNL": 0.0,
    "time": "2026-06-08T15:00:00+00:00",
    "exchange": "PHLX",
}

# EWY sibling row from trade_log.json id 603
EWY_EXEC_ID = "000205d2.6a26a327.01.01"
EWY_FILL_TIME = "2026-06-08T15:04:13+00:00"
EWY_DISK_ROW: dict[str, Any] = {
    "id": 603,
    "date": "2026-06-08",
    "ticker": "EWY",
    "structure": "Closed Call $215 2026-07-17",
    "decision": "IB_AUTO_IMPORT",
    "action": "SELL_TO_OPEN",
    "fill_price": 10.0,
    "total_cost": 10007.2214,
    "commission": 7.2214,
    "ib_exec_id": EWY_EXEC_ID,
    "notes": "Imported from IB session fills on 2026-06-08",
    "contracts": 10,
    "strike": 215.0,
    "right": "C",
    "expiry": "20260717",
}

EWY_EXEC_PAYLOAD = {
    "execId": EWY_EXEC_ID,
    "symbol": "EWY C215",
    "side": "SLD",
    "quantity": 10.0,
    "avgPrice": 10.0,
    "commission": 7.2214,
    "realizedPNL": 0.0,
    "time": EWY_FILL_TIME,
    "exchange": "PHLX",
}


def _make_trade_log_json(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "trade_log.json"
    p.write_text(json.dumps({"trades": rows}), encoding="utf-8")
    return p


# ── module import helper ──────────────────────────────────────────────────────

def _import_backfill():
    """Import (or reload) the backfill module with libsql stub in place."""
    if "backfill_journal_from_executed_orders" in sys.modules:
        return importlib.reload(sys.modules["backfill_journal_from_executed_orders"])
    import backfill_journal_from_executed_orders as mod
    return mod


# ── test classes ──────────────────────────────────────────────────────────────


class TestPayloadConstruction:
    """Verify the MU row is built exactly as the RCA specifies."""

    def test_action_is_sell_to_open(self, monkeypatch, tmp_path):
        """MU prior_qty was -8 (short), so SLD 7 adds to short = SELL_TO_OPEN."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        assert len(actions) == 1
        assert actions[0]["status"] == "dry_run"
        payload = actions[0]["payload"]
        assert payload["action"] == "SELL_TO_OPEN", (
            f"Expected SELL_TO_OPEN, got {payload['action']!r}. "
            "MU prior_qty=-8 (short book), so SLD 7 opens more short."
        )

    def test_contracts_equals_seven(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        payload = actions[0]["payload"]
        assert payload["contracts"] == 7, f"Expected contracts=7, got {payload['contracts']}"

    def test_strike_is_1000(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        payload = actions[0]["payload"]
        assert payload["strike"] == 1000.0, f"Expected strike=1000.0, got {payload['strike']}"

    def test_total_cost_matches_rca(self, monkeypatch, tmp_path):
        """total_cost = 7 * 25.0 * 100 + 5.2713 = 17505.2713."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        payload = actions[0]["payload"]
        assert abs(payload["total_cost"] - 17505.2713) < 1e-4, (
            f"total_cost mismatch: {payload['total_cost']}"
        )

    def test_ib_exec_id_carried_in_payload(self, monkeypatch, tmp_path):
        """Payload must carry ib_exec_id so reconciliation and dedup work."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        payload = actions[0]["payload"]
        assert payload.get("ib_exec_id") == MU_EXEC_ID, (
            f"ib_exec_id not in payload or wrong value: {payload.get('ib_exec_id')!r}"
        )

    def test_trade_id_equals_exec_id(self, monkeypatch, tmp_path):
        """trade_id passed to upsert_journal_entry must equal the exec_id."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        assert actions[0]["trade_id"] == MU_EXEC_ID

    def test_filled_at_is_date_string(self, monkeypatch, tmp_path):
        """filled_at must come from disk_row['date'] matching RCA: '2026-06-08'."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        assert actions[0]["filled_at"] == "2026-06-08"

    def test_ticker_is_mu(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        assert actions[0]["payload"]["ticker"] == "MU"


class TestIdempotency:
    """Re-running after a successful insert must be a no-op."""

    def test_skips_when_journal_already_has_exec_id(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        # Pre-seed journal with the row already present.
        _insert_journal(conn, MU_EXEC_ID, MU_DISK_ROW, "2026-06-08")

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False, trade_log_path=trade_log)

        assert len(actions) == 1
        assert actions[0]["status"] == "skipped", (
            f"Expected skipped, got {actions[0]['status']!r}. "
            "Double-insert would corrupt the journal."
        )

        # Only 1 row in journal (the pre-seeded one).
        assert len(_journal_rows(conn)) == 1

    def test_skips_when_exec_id_is_part_of_composite_ib_exec_id(self, monkeypatch, tmp_path):
        """Exec-id that appears as a part of a '+'-joined composite is treated as covered."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        # Journal has a composite ib_exec_id that includes MU_EXEC_ID as a part.
        composite_row = dict(MU_DISK_ROW)
        composite_row["ib_exec_id"] = MU_EXEC_ID + "+other.exec.id.here"
        _insert_journal(conn, composite_row["ib_exec_id"], composite_row, "2026-06-08")

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False, trade_log_path=trade_log)

        assert actions[0]["status"] == "skipped"


class TestDryRun:
    """Dry-run must NEVER write to journal regardless of what gaps exist."""

    def test_dry_run_writes_nothing_when_gap_exists(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        assert actions[0]["status"] == "dry_run"
        assert len(_journal_rows(conn)) == 0, (
            "dry_run=True must not write anything to journal"
        )

    def test_dry_run_returns_full_payload_for_review(self, monkeypatch, tmp_path):
        """Caller can inspect the exact row that would be inserted."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        action = actions[0]
        assert "payload" in action
        assert "trade_id" in action
        assert "filled_at" in action

    def test_dry_run_is_default(self, monkeypatch, tmp_path):
        """Calling backfill() without dry_run keyword must default to dry-run."""
        mod = _import_backfill()
        import inspect
        sig = inspect.signature(mod.backfill)
        assert sig.parameters["dry_run"].default is True, (
            "dry_run must default to True to prevent accidental prod writes"
        )


class TestPytestGuard:
    """Script must refuse to run prod writes from inside a pytest session."""

    def test_raises_under_pytest_current_test_without_override(self, monkeypatch):
        # PYTEST_CURRENT_TEST is always set when pytest runs — we confirm
        # the guard fires when RADON_DB_TEST_WRITE_OK is absent.
        monkeypatch.delenv("RADON_DB_TEST_WRITE_OK", raising=False)
        # PYTEST_CURRENT_TEST is already set by pytest itself.
        assert "PYTEST_CURRENT_TEST" in __import__("os").environ

        mod = _import_backfill()
        with pytest.raises(RuntimeError, match="RADON_DB_TEST_WRITE_OK"):
            mod._assert_not_under_pytest()

    def test_passes_when_override_set(self, monkeypatch):
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")

        mod = _import_backfill()
        mod._assert_not_under_pytest()  # must not raise


class TestExecuteMode:
    """--execute actually inserts via upsert_journal_entry (uses in-memory SQLite)."""

    def test_inserts_mu_row_when_missing(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        # Override the pytest guard for this test's explicit real-SQLite write.
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False, trade_log_path=trade_log)

        assert actions[0]["status"] == "inserted"
        rows = _journal_rows(conn)
        assert len(rows) == 1
        row = rows[0]
        assert row["trade_id"] == MU_EXEC_ID
        assert row["payload"]["action"] == "SELL_TO_OPEN"
        assert row["payload"]["contracts"] == 7
        assert row["payload"]["strike"] == 1000.0
        assert abs(row["payload"]["total_cost"] - 17505.2713) < 1e-4
        assert row["payload"]["ib_exec_id"] == MU_EXEC_ID
        assert row["filled_at"] == "2026-06-08"

    def test_insert_is_idempotent_on_rerun(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")

        mod = _import_backfill()
        # First run inserts.
        actions1 = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False, trade_log_path=trade_log)
        assert actions1[0]["status"] == "inserted"

        # Second run must skip.
        actions2 = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False, trade_log_path=trade_log)
        assert actions2[0]["status"] == "skipped"

        # Still only one journal row.
        assert len(_journal_rows(conn)) == 1


class TestEWYSiblingRow:
    """EWY C215 SLD 10 dropped in the same incident; verify its payload."""

    def test_ewy_action_is_sell_to_open(self, monkeypatch, tmp_path):
        """EWY C215 — prior_qty < 0, so SLD 10 = SELL_TO_OPEN."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, EWY_EXEC_ID, EWY_EXEC_PAYLOAD, EWY_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [EWY_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[EWY_EXEC_ID], dry_run=True, trade_log_path=trade_log)

        assert len(actions) == 1
        assert actions[0]["status"] == "dry_run"
        assert actions[0]["payload"]["action"] == "SELL_TO_OPEN"

    def test_ewy_contracts_is_ten(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, EWY_EXEC_ID, EWY_EXEC_PAYLOAD, EWY_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [EWY_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[EWY_EXEC_ID], dry_run=True, trade_log_path=trade_log)
        assert actions[0]["payload"]["contracts"] == 10

    def test_ewy_strike_is_215(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, EWY_EXEC_ID, EWY_EXEC_PAYLOAD, EWY_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [EWY_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[EWY_EXEC_ID], dry_run=True, trade_log_path=trade_log)
        assert actions[0]["payload"]["strike"] == 215.0


class TestWindowScan:
    """When no exec_ids given, scan executed_orders within window."""

    def test_finds_gap_within_window(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        # Use a recent fill_time that falls within the default 7-day window.
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, recent)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=None, dry_run=True, trade_log_path=trade_log)

        gap_actions = [a for a in actions if a["exec_id"] == MU_EXEC_ID]
        assert gap_actions, "Expected MU_EXEC_ID to appear as a gap in the window scan"
        assert gap_actions[0]["status"] == "dry_run"

    def test_skips_rows_outside_window(self, monkeypatch, tmp_path):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        # Old fill_time: 30 days ago (outside default 7-day window).
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, old)
        trade_log = _make_trade_log_json(tmp_path, [MU_DISK_ROW])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=None, dry_run=True, trade_log_path=trade_log)

        gap_actions = [a for a in actions if a["exec_id"] == MU_EXEC_ID]
        assert not gap_actions, "Old exec_id should not appear in a 7-day window scan"

    def test_no_disk_row_reports_status(self, monkeypatch, tmp_path):
        """When executed_orders has a gap but trade_log.json has no matching row."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, recent)
        # Empty trade_log
        trade_log = _make_trade_log_json(tmp_path, [])

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=None, dry_run=True, trade_log_path=trade_log)

        no_disk = [a for a in actions if a["status"] == "no_disk_row"]
        assert no_disk, "Should report no_disk_row when exec_id is missing from trade_log.json"


class TestBuildJournalRow:
    """Unit test for _build_journal_row helper in isolation."""

    def test_returns_trade_id_payload_filled_at(self):
        mod = _import_backfill()
        trade_id, payload, filled_at = mod._build_journal_row(MU_DISK_ROW)
        assert trade_id == MU_EXEC_ID
        assert payload is MU_DISK_ROW  # verbatim reference — same dict
        assert filled_at == "2026-06-08"

    def test_falls_back_to_date_when_no_filled_at(self):
        row = dict(MU_DISK_ROW)
        row.pop("filled_at", None)
        mod = _import_backfill()
        _, _, filled_at = mod._build_journal_row(row)
        assert filled_at == "2026-06-08"


# ── JRN-02: --from-executed-orders tests ─────────────────────────────────────
# These are the no_disk_row gaps: VIX P10, MU P800, MU C1050@108.
# The --from-executed-orders flag rebuilds the journal row from the EO payload
# using journal_sync's labeling logic (not hand-fabricated fields).
# These tests are RED until _build_journal_row_from_executed_order() + the
# from_executed_orders flag are implemented in backfill_journal_from_executed_orders.py.

# VIX P10 no_disk_row gap — RCA canonical payload
VIX_P10_EXEC_ID = "0000fb35.6a10834c.01.01"
VIX_P10_FILL_TIME = "2026-05-22T20:07:28Z"
VIX_P10_EO_PAYLOAD = {
    "execId": VIX_P10_EXEC_ID,
    "contract": {
        "conId": 817812324,
        "symbol": "VIX",
        "secType": "OPT",
        "strike": 10.0,
        "right": "P",
        "lastTradeDateOrContractMonth": "20260616",
    },
    "side": "BOT",
    "quantity": 1.0,
    "avgPrice": 0.01,
    "commission": 1.1507,
    "time": VIX_P10_FILL_TIME,
    "exchange": "CBOE",
}

# MU P800 no_disk_row gap — RCA canonical payload
MU_P800_EXEC_ID = "0001108f.6a19b7e9.01.01"
MU_P800_FILL_TIME = "2026-05-29T19:58:53Z"
MU_P800_EO_PAYLOAD = {
    "execId": MU_P800_EXEC_ID,
    "contract": {
        "conId": 849278266,
        "symbol": "MU",
        "secType": "OPT",
        "strike": 800.0,
        "right": "P",
        "lastTradeDateOrContractMonth": "20260717",
    },
    "side": "BOT",
    "quantity": 5.0,
    "avgPrice": 59.0,
    "commission": 2.2412,
    "time": MU_P800_FILL_TIME,
    "exchange": "PSE",
}

# MU C1050 @108 no_disk_row gap — RCA canonical payload
MU_C1050_108_EXEC_ID = "0002920b.6a2b2035.01.01"
MU_C1050_108_FILL_TIME = "2026-06-11T19:59:08Z"
MU_C1050_108_EO_PAYLOAD = {
    "execId": MU_C1050_108_EXEC_ID,
    "contract": {
        "conId": 877967302,
        "symbol": "MU",
        "secType": "OPT",
        "strike": 1050.0,
        "right": "C",
        "lastTradeDateOrContractMonth": "20260717",
    },
    "side": "SLD",
    "quantity": 2.0,
    "avgPrice": 108.0,
    "commission": 1.848,
    "time": MU_C1050_108_FILL_TIME,
    "exchange": "PHLX",
}


def _insert_eo_with_contract_payload(conn, exec_id, eo_payload, fill_time):
    """Insert an executed_orders row with the given payload dict."""
    _insert_executed_order(conn, exec_id, eo_payload, fill_time)


class TestFromExecutedOrdersFlag:
    """--from-executed-orders: rebuild journal row from EO payload when no disk row.

    These tests are RED until the feature is implemented.
    """

    def test_from_eo_flag_is_false_by_default(self, monkeypatch, tmp_path):
        """Calling backfill() without from_executed_orders must default to False."""
        mod = _import_backfill()
        import inspect
        sig = inspect.signature(mod.backfill)
        assert "from_executed_orders" in sig.parameters, (
            "backfill() must accept from_executed_orders parameter"
        )
        assert sig.parameters["from_executed_orders"].default is False, (
            "from_executed_orders must default to False (opt-in only)"
        )

    def test_no_disk_row_still_refuses_without_flag(self, monkeypatch, tmp_path):
        """Without --from-executed-orders, no_disk_row gaps must still be refused."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])  # empty disk

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[VIX_P10_EXEC_ID],
            dry_run=True,
            trade_log_path=trade_log,
            from_executed_orders=False,
        )

        assert len(actions) == 1
        assert actions[0]["status"] == "no_disk_row", (
            "Without --from-executed-orders, no_disk_row must still be refused"
        )

    def test_vix_p10_buy_option_from_eo(self, monkeypatch, tmp_path):
        """VIX P10 no_disk_row gap: BOT 1 @0.01 → BUY_OPTION, prior_qty=0."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])  # no disk row

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[VIX_P10_EXEC_ID],
            dry_run=True,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        assert len(actions) == 1
        a = actions[0]
        assert a["status"] in ("dry_run", "dry_run_from_eo"), (
            f"Expected dry_run or dry_run_from_eo, got {a['status']!r}"
        )
        p = a["payload"]
        assert p["action"] == "BUY_OPTION", f"Expected BUY_OPTION, got {p['action']!r}"
        assert p["ticker"] == "VIX"
        assert p["contracts"] == 1
        assert p["strike"] == 10.0
        assert p["right"] == "P"
        assert p["expiry"] == "20260616"
        # total_cost = 1 * 0.01 * 100 + 1.1507 = 2.1507
        assert abs(p["total_cost"] - 2.1507) < 1e-4, f"total_cost {p['total_cost']} != 2.1507"
        assert p["ib_exec_id"] == VIX_P10_EXEC_ID
        assert a["filled_at"] == "2026-05-22"

    def test_mu_p800_buy_option_from_eo(self, monkeypatch, tmp_path):
        """MU P800 no_disk_row gap: BOT 5 @59.0 → BUY_OPTION, prior_qty=0."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, MU_P800_EXEC_ID, MU_P800_EO_PAYLOAD, MU_P800_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[MU_P800_EXEC_ID],
            dry_run=True,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        assert len(actions) == 1
        p = actions[0]["payload"]
        assert p["action"] == "BUY_OPTION"
        assert p["ticker"] == "MU"
        assert p["contracts"] == 5
        assert p["strike"] == 800.0
        assert p["right"] == "P"
        # total_cost = 5 * 59.0 * 100 + 2.2412 = 29502.2412
        assert abs(p["total_cost"] - 29502.2412) < 1e-4, f"total_cost {p['total_cost']} != 29502.2412"
        assert actions[0]["filled_at"] == "2026-05-29"

    def test_mu_c1050_108_sell_to_open_from_eo_with_prior_state(self, monkeypatch, tmp_path):
        """MU C1050 @108: prior_qty=-8 (short), SLD 2 → SELL_TO_OPEN.

        The prior state must be seeded from the journal, not from disk.
        We pre-seed the journal with the @110 (3@SELL_TO_OPEN) and @95 (5@SELL_TO_OPEN)
        fills so prior_qty = -8, then verify @108 SLD 2 labels as SELL_TO_OPEN.
        """
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, MU_C1050_108_EXEC_ID, MU_C1050_108_EO_PAYLOAD, MU_C1050_108_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])

        # Seed journal: @110 SLD 3 + @95 SLD 5 = prior_qty = -8 for MU C1050 2026-07-17
        _insert_journal(conn, "0002920b.6a19d5a9.01.01", {
            "ticker": "MU", "action": "SELL_TO_OPEN", "contracts": 3,
            "total_cost": 33002.7844, "strike": 1050.0, "right": "C",
            "expiry": "20260717", "ib_exec_id": "0002920b.6a19d5a9.01.01",
        }, "2026-05-29")
        _insert_journal(conn, "0002920b.6a2b0bc6.01.01", {
            "ticker": "MU", "action": "SELL_TO_OPEN", "contracts": 5,
            "total_cost": 47502.0, "strike": 1050.0, "right": "C",
            "expiry": "20260717", "ib_exec_id": "0002920b.6a2b0bc6.01.01",
        }, "2026-06-11")

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[MU_C1050_108_EXEC_ID],
            dry_run=True,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        assert len(actions) == 1
        p = actions[0]["payload"]
        assert p["action"] == "SELL_TO_OPEN", (
            f"prior_qty=-8 (short), SLD 2 → still short, expected SELL_TO_OPEN, got {p['action']!r}"
        )
        assert p["contracts"] == 2
        assert p["ticker"] == "MU"
        assert p["strike"] == 1050.0
        assert p["right"] == "C"
        # total_cost = 2 * 108 * 100 + 1.848 = 21601.848
        assert abs(p["total_cost"] - 21601.848) < 1e-4, f"total_cost {p['total_cost']} != 21601.848"
        assert actions[0]["filled_at"] == "2026-06-11"

    def test_from_eo_idempotent_when_already_in_journal(self, monkeypatch, tmp_path):
        """Re-running with --from-executed-orders when row already in journal → skipped."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])

        # Pre-seed journal with the row already present.
        _insert_journal(conn, VIX_P10_EXEC_ID, {
            "ticker": "VIX", "action": "BUY_OPTION", "contracts": 1,
            "ib_exec_id": VIX_P10_EXEC_ID,
        }, "2026-05-22")

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[VIX_P10_EXEC_ID],
            dry_run=False,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        assert actions[0]["status"] == "skipped", (
            f"Already in journal → must skip, got {actions[0]['status']!r}"
        )
        assert len(_journal_rows(conn)) == 1  # only the pre-seeded row

    def test_from_eo_execute_writes_to_journal(self, monkeypatch, tmp_path):
        """--from-executed-orders --execute actually inserts the VIX P10 row."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[VIX_P10_EXEC_ID],
            dry_run=False,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        assert actions[0]["status"] in ("inserted", "inserted_from_eo"), (
            f"Expected inserted/inserted_from_eo, got {actions[0]['status']!r}"
        )
        rows = _journal_rows(conn)
        assert len(rows) == 1
        p = rows[0]["payload"]
        assert p["action"] == "BUY_OPTION"
        assert p["ticker"] == "VIX"
        assert rows[0]["trade_id"] == VIX_P10_EXEC_ID

    def test_from_eo_sorts_by_fill_time_for_prior_qty_accuracy(self, monkeypatch, tmp_path):
        """Multiple EO gaps for the same contract must be processed fill_time ASC.

        We insert MU C1050 @110 (no disk row, prior=0) and @108 (no disk row, prior=-3 after @110).
        Without correct ordering, @108 might see prior=0 and label wrong.
        This tests the ordering contract without seeding journal rows first.
        """
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)

        exec_id_110 = "0002920b.6a19d5a9.01.01"
        fill_time_110 = "2026-05-29T15:12:32Z"
        eo_110 = {
            "execId": exec_id_110,
            "contract": {
                "symbol": "MU", "secType": "OPT",
                "strike": 1050.0, "right": "C",
                "lastTradeDateOrContractMonth": "20260717",
            },
            "side": "SLD", "quantity": 3.0, "avgPrice": 110.0, "commission": 2.7844,
            "time": fill_time_110,
        }

        exec_id_108 = MU_C1050_108_EXEC_ID
        fill_time_108 = MU_C1050_108_FILL_TIME
        eo_108 = dict(MU_C1050_108_EO_PAYLOAD)

        _insert_eo_with_contract_payload(conn, exec_id_110, eo_110, fill_time_110)
        _insert_eo_with_contract_payload(conn, exec_id_108, eo_108, fill_time_108)
        trade_log = _make_trade_log_json(tmp_path, [])

        mod = _import_backfill()
        # Process both together — @110 first (fill_time ASC) then @108
        actions = mod.backfill(
            conn,
            exec_ids=[exec_id_110, exec_id_108],
            dry_run=True,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        actions_by_id = {a["exec_id"]: a for a in actions}
        # @110: prior=0, SLD → SELL_TO_OPEN
        assert actions_by_id[exec_id_110]["payload"]["action"] == "SELL_TO_OPEN"
        # @108: prior=-3 (after @110 processed), SLD → SELL_TO_OPEN
        assert actions_by_id[exec_id_108]["payload"]["action"] == "SELL_TO_OPEN"

    def test_from_eo_uses_structure_label_from_journal_sync(self, monkeypatch, tmp_path):
        """structure field must match journal_sync._structure_label output, not hand-crafted."""
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_eo_with_contract_payload(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)
        trade_log = _make_trade_log_json(tmp_path, [])

        mod = _import_backfill()
        actions = mod.backfill(
            conn,
            exec_ids=[VIX_P10_EXEC_ID],
            dry_run=True,
            trade_log_path=trade_log,
            from_executed_orders=True,
        )

        structure = actions[0]["payload"]["structure"]
        # JournalSyncHandler._structure_label("BUY", "OPT", 10.0, "P", "20260616")
        # → "Long Put $10 2026-06-16"
        assert "Put" in structure, f"structure must contain right label, got: {structure!r}"
        assert "$10" in structure, f"structure must contain strike, got: {structure!r}"
        assert "2026-06-16" in structure, f"structure must contain expiry ISO, got: {structure!r}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
