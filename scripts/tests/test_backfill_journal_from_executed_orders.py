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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
