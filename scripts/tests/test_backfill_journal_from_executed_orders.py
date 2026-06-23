#!/usr/bin/env python3
"""DB-only tests for backfill_journal_from_executed_orders.py."""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

if "libsql_experimental" not in sys.modules:
    libsql_stub = types.ModuleType("libsql_experimental")
    libsql_stub.connect = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    sys.modules["libsql_experimental"] = libsql_stub

_MIGRATIONS = [
    _SCRIPTS_DIR / "db" / "migrations" / "0001_init.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0002_cash_flows.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0003_phase2_snapshots.sql",
    _SCRIPTS_DIR / "db" / "migrations" / "0004_orders_and_ephemeral.sql",
]


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [stmt.strip() for stmt in re.split(r";\s*$", stripped, flags=re.MULTILINE) if stmt.strip()]


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for migration in _MIGRATIONS:
        for stmt in _split_statements(migration.read_text(encoding="utf-8")):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                if "duplicate column" in str(exc):
                    continue
                raise
    conn.commit()
    return conn


def _patch_db(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    import db.client as client_mod

    monkeypatch.setattr(client_mod, "get_db", lambda: conn)
    monkeypatch.setattr(client_mod, "_cached", conn, raising=False)

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
        """
        INSERT OR REPLACE INTO executed_orders
          (exec_id, perm_id, payload, fill_time, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (exec_id, perm_id, json.dumps(payload), fill_time, "2026-06-08T23:28:13.669255Z"),
    )
    conn.commit()


def _insert_journal(conn: sqlite3.Connection, trade_id: str, payload: dict, filled_at: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO journal (trade_id, payload, filled_at, written_at)
        VALUES (?, ?, ?, ?)
        """,
        (trade_id, json.dumps(payload), filled_at, "2026-06-08T23:28:13.669255Z"),
    )
    conn.commit()


def _journal_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT trade_id, payload, filled_at FROM journal ORDER BY trade_id").fetchall()
    return [{"trade_id": row[0], "payload": json.loads(row[1]), "filled_at": row[2]} for row in rows]


def _import_backfill():
    if "backfill_journal_from_executed_orders" in sys.modules:
        return importlib.reload(sys.modules["backfill_journal_from_executed_orders"])
    import backfill_journal_from_executed_orders as mod

    return mod


MU_EXEC_ID = "0002920b.6a26c483.01.01"
MU_FILL_TIME = "2026-06-08T15:00:00+00:00"
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
    "time": MU_FILL_TIME,
    "exchange": "PHLX",
}

EWY_EXEC_ID = "000205d2.6a26a327.01.01"
EWY_FILL_TIME = "2026-06-08T15:04:13+00:00"
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
    "contract": {
        "symbol": "EWY",
        "secType": "OPT",
        "strike": 215.0,
        "right": "C",
        "lastTradeDateOrContractMonth": "20260717",
    },
}

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


class TestDbOnlyBackfill:
    def test_signature_has_no_flat_file_source_parameters(self):
        mod = _import_backfill()
        sig = inspect.signature(mod.backfill)

        assert "trade_log_path" not in sig.parameters
        assert "from_executed_orders" not in sig.parameters
        assert sig.parameters["dry_run"].default is True

    def test_dry_run_reconstructs_missing_mu_row_from_executed_orders(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=True)

        assert actions[0]["status"] == "dry_run_from_eo"
        assert len(_journal_rows(conn)) == 0

        payload = actions[0]["payload"]
        assert actions[0]["trade_id"] == MU_EXEC_ID
        assert actions[0]["filled_at"] == "2026-06-08"
        assert payload["ib_exec_id"] == MU_EXEC_ID
        assert payload["ticker"] == "MU"
        assert payload["action"] == "SELL_TO_OPEN"
        assert payload["contracts"] == 7
        assert payload["strike"] == 1000.0
        assert abs(payload["total_cost"] - 17505.2713) < 1e-4

    def test_execute_inserts_and_rerun_skips_by_journal_exec_id(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")

        mod = _import_backfill()
        first = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False)
        second = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False)

        assert first[0]["status"] == "inserted_from_eo"
        assert second[0]["status"] == "skipped"
        rows = _journal_rows(conn)
        assert len(rows) == 1
        assert rows[0]["trade_id"] == MU_EXEC_ID
        assert rows[0]["payload"]["action"] == "SELL_TO_OPEN"

    def test_skips_when_exec_id_is_part_of_composite_journal_id(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, MU_FILL_TIME)
        _insert_journal(
            conn,
            f"{MU_EXEC_ID}+other.exec.id.here",
            {"ticker": "MU", "ib_exec_id": f"{MU_EXEC_ID}+other.exec.id.here"},
            "2026-06-08",
        )

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_EXEC_ID], dry_run=False)

        assert actions[0]["status"] == "skipped"
        assert len(_journal_rows(conn)) == 1

    def test_window_scan_finds_recent_and_skips_old_rows(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        _insert_executed_order(conn, MU_EXEC_ID, MU_EXEC_PAYLOAD, recent)
        _insert_executed_order(conn, EWY_EXEC_ID, EWY_EXEC_PAYLOAD, old)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=None, dry_run=True)

        assert [action["exec_id"] for action in actions] == [MU_EXEC_ID]
        assert actions[0]["status"] == "dry_run_from_eo"

    def test_invalid_executed_order_payload_reports_reconstruction_failed(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        payload = dict(MU_EXEC_PAYLOAD)
        payload["execId"] = ""
        _insert_executed_order(conn, "bad.exec", payload, MU_FILL_TIME)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=["bad.exec"], dry_run=True)

        assert actions == [
            {
                "exec_id": "bad.exec",
                "status": "reconstruction_failed",
                "error": "Missing reconstructed trade id",
            }
        ]


class TestExecutedOrderPayloadCoverage:
    def test_ewy_reconstruction_preserves_option_fields(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, EWY_EXEC_ID, EWY_EXEC_PAYLOAD, EWY_FILL_TIME)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[EWY_EXEC_ID], dry_run=True)
        payload = actions[0]["payload"]

        assert payload["action"] == "SELL_TO_OPEN"
        assert payload["ticker"] == "EWY"
        assert payload["contracts"] == 10
        assert payload["strike"] == 215.0
        assert payload["right"] == "C"

    def test_vix_p10_buy_option_from_executed_orders(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[VIX_P10_EXEC_ID], dry_run=True)
        payload = actions[0]["payload"]

        assert actions[0]["status"] == "dry_run_from_eo"
        assert actions[0]["filled_at"] == "2026-05-22"
        assert payload["action"] == "BUY_OPTION"
        assert payload["ticker"] == "VIX"
        assert payload["contracts"] == 1
        assert payload["strike"] == 10.0
        assert payload["right"] == "P"
        assert payload["expiry"] == "20260616"
        assert abs(payload["total_cost"] - 2.1507) < 1e-4

    def test_mu_p800_buy_option_from_executed_orders(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_P800_EXEC_ID, MU_P800_EO_PAYLOAD, MU_P800_FILL_TIME)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_P800_EXEC_ID], dry_run=True)
        payload = actions[0]["payload"]

        assert actions[0]["filled_at"] == "2026-05-29"
        assert payload["action"] == "BUY_OPTION"
        assert payload["ticker"] == "MU"
        assert payload["contracts"] == 5
        assert payload["strike"] == 800.0
        assert payload["right"] == "P"
        assert abs(payload["total_cost"] - 29502.2412) < 1e-4

    def test_mu_c1050_sell_to_open_uses_prior_state_from_journal(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, MU_C1050_108_EXEC_ID, MU_C1050_108_EO_PAYLOAD, MU_C1050_108_FILL_TIME)
        _insert_journal(
            conn,
            "0002920b.6a19d5a9.01.01",
            {
                "ticker": "MU",
                "action": "SELL_TO_OPEN",
                "contracts": 3,
                "strike": 1050.0,
                "right": "C",
                "expiry": "20260717",
                "ib_exec_id": "0002920b.6a19d5a9.01.01",
            },
            "2026-05-29",
        )
        _insert_journal(
            conn,
            "0002920b.6a2b0bc6.01.01",
            {
                "ticker": "MU",
                "action": "SELL_TO_OPEN",
                "contracts": 5,
                "strike": 1050.0,
                "right": "C",
                "expiry": "20260717",
                "ib_exec_id": "0002920b.6a2b0bc6.01.01",
            },
            "2026-06-11",
        )

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_C1050_108_EXEC_ID], dry_run=True)
        payload = actions[0]["payload"]

        assert payload["action"] == "SELL_TO_OPEN"
        assert payload["contracts"] == 2
        assert payload["ticker"] == "MU"
        assert payload["strike"] == 1050.0
        assert payload["right"] == "C"
        assert abs(payload["total_cost"] - 21601.848) < 1e-4

    def test_fill_time_sort_updates_prior_state_within_one_run(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        exec_id_110 = "0002920b.6a19d5a9.01.01"
        fill_time_110 = "2026-05-29T15:12:32Z"
        eo_110 = {
            "execId": exec_id_110,
            "contract": {
                "symbol": "MU",
                "secType": "OPT",
                "strike": 1050.0,
                "right": "C",
                "lastTradeDateOrContractMonth": "20260717",
            },
            "side": "SLD",
            "quantity": 3.0,
            "avgPrice": 110.0,
            "commission": 2.7844,
            "time": fill_time_110,
        }
        _insert_executed_order(conn, MU_C1050_108_EXEC_ID, MU_C1050_108_EO_PAYLOAD, MU_C1050_108_FILL_TIME)
        _insert_executed_order(conn, exec_id_110, eo_110, fill_time_110)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[MU_C1050_108_EXEC_ID, exec_id_110], dry_run=True)
        by_id = {action["exec_id"]: action for action in actions}

        assert by_id[exec_id_110]["payload"]["action"] == "SELL_TO_OPEN"
        assert by_id[MU_C1050_108_EXEC_ID]["payload"]["action"] == "SELL_TO_OPEN"
        assert list(by_id) == [exec_id_110, MU_C1050_108_EXEC_ID]

    def test_structure_label_comes_from_journal_sync_logic(self, monkeypatch):
        conn = _fresh_db()
        _patch_db(conn, monkeypatch)
        _insert_executed_order(conn, VIX_P10_EXEC_ID, VIX_P10_EO_PAYLOAD, VIX_P10_FILL_TIME)

        mod = _import_backfill()
        actions = mod.backfill(conn, exec_ids=[VIX_P10_EXEC_ID], dry_run=True)
        structure = actions[0]["payload"]["structure"]

        assert "Put" in structure
        assert "$10" in structure
        assert "2026-06-16" in structure


class TestPytestGuard:
    def test_raises_under_pytest_current_test_without_override(self, monkeypatch):
        monkeypatch.delenv("RADON_DB_TEST_WRITE_OK", raising=False)
        assert "PYTEST_CURRENT_TEST" in __import__("os").environ

        mod = _import_backfill()
        with pytest.raises(RuntimeError, match="RADON_DB_TEST_WRITE_OK"):
            mod._assert_not_under_pytest()

    def test_passes_when_override_set(self, monkeypatch):
        monkeypatch.setenv("RADON_DB_TEST_WRITE_OK", "1")

        mod = _import_backfill()
        mod._assert_not_under_pytest()
