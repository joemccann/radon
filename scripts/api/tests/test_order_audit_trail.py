"""REL-019 (R-022): server-side order audit trail.

Every order outcome that today only exists in the operator's browser —
successful submit, IB rejection, cancel, modify, and the indeterminate
gateway-restart case — must land one append-only ``order_events`` row plus
one INFO log line, keyed by orderRef/permId. The write is best-effort: an
audit failure must NEVER change the HTTP response of a live-money order.

The DB seam faked here is ``api.order_audit.hrana_execute`` (the bounded
hrana HTTP transport — the API process never touches sync libsql).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
MIGRATIONS_DIR = SCRIPTS_DIR / "db" / "migrations"
MIGRATION_PATH = MIGRATIONS_DIR / "0038_order_events.sql"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def trusted_client(monkeypatch):
    from scripts.api import auth, server

    monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
    monkeypatch.setattr(server, "test_mode", False)
    return TestClient(server.app), server


@pytest.fixture
def captured_events(monkeypatch):
    """Fake the bounded hrana transport under api.order_audit; capture args."""
    from api import order_audit

    captured: list[tuple] = []
    monkeypatch.setattr(
        order_audit, "hrana_execute", lambda sql, args, **kw: captured.append(args)
    )
    return captured


def _successful_place_result() -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        error=None,
        data={
            "status": "ok",
            "orderId": 42,
            "permId": 420042,
            "orderRef": "radon-testref",
            "initialStatus": "Submitted",
            "message": "BUY 1 AAPL @ $200.00 — Submitted",
        },
    )


_STOCK_ORDER = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 1,
    "limitPrice": 200.0,
    "tif": "DAY",
}

# Column order of ORDER_EVENT_INSERT_SQL.
EVENT_TYPE, ORDER_REF, ORDER_ID, PERM_ID, SYMBOL, ACTION, QUANTITY, LIMIT_PRICE, STATUS, DETAIL = range(10)


def _fake_recovery(result):
    async def fake(*_args, **_kwargs):
        return result

    return fake


class TestPlaceSuccess:
    def test_records_submitted_event_with_order_ref_and_perm_id(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(_successful_place_result())
        )

        response = client.post("/orders/place", json=_STOCK_ORDER)

        assert response.status_code == 200
        assert len(captured_events) == 1
        row = captured_events[0]
        assert row[EVENT_TYPE] == "submitted"
        assert row[ORDER_REF] == "radon-testref"
        assert row[ORDER_ID] == 42
        assert row[PERM_ID] == 420042
        assert row[SYMBOL] == "AAPL"
        assert row[ACTION] == "BUY"
        assert row[QUANTITY] == 1.0
        assert row[LIMIT_PRICE] == 200.0
        assert row[STATUS] == "Submitted"

    def test_emits_info_log_without_account_numbers(
        self, trusted_client, captured_events, monkeypatch, caplog
    ):
        client, server = trusted_client
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(_successful_place_result())
        )

        with caplog.at_level(logging.INFO, logger="radon.order_audit"):
            client.post("/orders/place", json=_STOCK_ORDER)

        info_lines = [
            r.getMessage()
            for r in caplog.records
            if r.name == "radon.order_audit" and r.levelno == logging.INFO
        ]
        assert any(
            "submitted" in line and "radon-testref" in line and "420042" in line
            for line in info_lines
        )
        assert not any(re.search(r"\bU\d{7,}\b", line) for line in info_lines)


class TestPlaceRejection:
    def test_ib_rejection_records_rejected_event(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        rejection = SimpleNamespace(
            ok=True,
            error=None,
            data={
                "status": "error",
                "message": "Order rejected — margin",
                "ib_error_code": 201,
                "orderRef": "radon-rejref",
            },
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(rejection)
        )

        response = client.post("/orders/place", json=_STOCK_ORDER)

        assert response.status_code == 502
        assert len(captured_events) == 1
        row = captured_events[0]
        assert row[EVENT_TYPE] == "rejected"
        assert row[ORDER_REF] == "radon-rejref"
        assert row[SYMBOL] == "AAPL"
        detail = json.loads(row[DETAIL])
        assert detail["ib_error_code"] == 201

    def test_rejection_detail_scrubs_account_numbers(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        rejection = SimpleNamespace(
            ok=True,
            error=None,
            data={
                "status": "error",
                "message": "Order rejected",
                "account": "U1234567",
                "accountId": "U1234567",
            },
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(rejection)
        )

        client.post("/orders/place", json=_STOCK_ORDER)

        assert len(captured_events) == 1
        assert "U1234567" not in (captured_events[0][DETAIL] or "")


class TestPlaceIndeterminate:
    def test_gateway_restart_no_retry_records_indeterminate_event(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        indeterminate = SimpleNamespace(
            ok=False,
            error=(
                "IB Gateway was restarted after the placement attempt failed. "
                "The order was NOT automatically retried — the first attempt may "
                "have reached IB before the failure. Check open orders before re-placing."
            ),
            data=None,
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(indeterminate)
        )

        response = client.post("/orders/place", json=_STOCK_ORDER)

        # Merged REL-006 contract: indeterminate failures answer 504
        # ORDER_INDETERMINATE (with orderRef), never a retryable-looking 502.
        assert response.status_code == 504
        assert len(captured_events) == 1
        row = captured_events[0]
        assert row[EVENT_TYPE] == "indeterminate"
        assert row[SYMBOL] == "AAPL"
        assert "NOT automatically retried" in (row[DETAIL] or "")

    def test_subprocess_timeout_records_indeterminate_event(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        timeout = SimpleNamespace(
            ok=False, error="Script timed out after 25.0s", data=None
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(timeout)
        )

        response = client.post("/orders/place", json=_STOCK_ORDER)

        # Merged REL-006 contract: indeterminate failures answer 504
        # ORDER_INDETERMINATE (with orderRef), never a retryable-looking 502.
        assert response.status_code == 504
        assert len(captured_events) == 1
        assert captured_events[0][EVENT_TYPE] == "indeterminate"

    def test_pre_transmit_infra_failure_records_nothing(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        infra = SimpleNamespace(
            ok=False, error="Script not found: ib_place_order.py", data=None
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(infra)
        )

        response = client.post("/orders/place", json=_STOCK_ORDER)

        assert response.status_code == 502
        assert captured_events == []


class TestCancelModify:
    def test_cancel_success_records_cancelled_event(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        result = SimpleNamespace(
            ok=True,
            error=None,
            data={"status": "ok", "orderId": 7, "message": "Order cancelled (orderId=7)"},
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(result)
        )

        response = client.post("/orders/cancel", json={"orderId": 7, "permId": 700})

        assert response.status_code == 200
        assert len(captured_events) == 1
        row = captured_events[0]
        assert row[EVENT_TYPE] == "cancelled"
        assert row[ORDER_ID] == 7
        assert row[PERM_ID] == 700

    def test_modify_success_records_modified_event(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        result = SimpleNamespace(
            ok=True,
            error=None,
            data={"status": "ok", "orderId": 9, "message": "Order modified (orderId=9)"},
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(result)
        )

        response = client.post(
            "/orders/modify",
            json={"orderId": 9, "permId": 900, "newPrice": 1.25, "newQuantity": 2},
        )

        assert response.status_code == 200
        assert len(captured_events) == 1
        row = captured_events[0]
        assert row[EVENT_TYPE] == "modified"
        assert row[ORDER_ID] == 9
        detail = json.loads(row[DETAIL])
        assert detail["newPrice"] == 1.25
        assert detail["newQuantity"] == 2

    def test_cancel_failure_records_nothing(
        self, trusted_client, captured_events, monkeypatch
    ):
        client, server = trusted_client
        result = SimpleNamespace(
            ok=True, error=None, data={"status": "error", "message": "Trade not found"}
        )
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(result)
        )

        response = client.post("/orders/cancel", json={"orderId": 7})

        assert response.status_code == 502
        assert captured_events == []


class TestBestEffort:
    def test_audit_write_failure_does_not_change_place_response(
        self, trusted_client, monkeypatch, caplog
    ):
        from api import order_audit

        client, server = trusted_client
        monkeypatch.setattr(
            server, "_run_ib_script_with_recovery", _fake_recovery(_successful_place_result())
        )

        def boom(*_args, **_kwargs):
            raise RuntimeError("turso is down")

        monkeypatch.setattr(order_audit, "hrana_execute", boom)

        with caplog.at_level(logging.WARNING, logger="radon.order_audit"):
            response = client.post("/orders/place", json=_STOCK_ORDER)

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert any(
            r.levelno == logging.WARNING and r.name == "radon.order_audit"
            for r in caplog.records
        )

    def test_writer_append_order_event_never_raises(self, monkeypatch):
        import db.hrana_http as hrana_http
        import db.writer as writer

        def boom(*_args, **_kwargs):
            raise RuntimeError("turso is down")

        monkeypatch.setattr(hrana_http, "hrana_execute", boom)
        assert writer.append_order_event("submitted", order_ref="radon-x") is False

    def test_writer_append_order_event_writes_row(self, monkeypatch):
        import db.hrana_http as hrana_http
        import db.writer as writer

        captured = {}

        def fake(sql, args, timeout=None):
            captured["sql"], captured["args"] = sql, args

        monkeypatch.setattr(hrana_http, "hrana_execute", fake)
        assert (
            writer.append_order_event(
                "cancelled", order_ref="radon-y", order_id=5, perm_id=55
            )
            is True
        )
        assert "INSERT INTO order_events" in captured["sql"]
        assert captured["args"][EVENT_TYPE] == "cancelled"
        assert captured["args"][ORDER_REF] == "radon-y"


class TestMigrationContract:
    def test_migration_file_exists(self):
        assert MIGRATION_PATH.exists(), "0038_order_events.sql missing"

    def test_migration_creates_table_and_indexes(self):
        sql = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS order_events" in sql
        assert "idx_order_events_order_ref" in sql
        assert "idx_order_events_created_at" in sql

    def test_migration_ends_with_schema_migrations_footer(self):
        """Pin the footer convention (audit flagged 0026 for missing it).

        Neighbor migrations (0035, 0037) end with:
            INSERT OR IGNORE INTO schema_migrations (version, applied_at)
            VALUES (N, datetime('now'));
        """
        sql = MIGRATION_PATH.read_text(encoding="utf-8").strip()
        footer = re.compile(
            r"INSERT OR IGNORE INTO schema_migrations \(version, applied_at\)\s*"
            r"VALUES \(38, datetime\('now'\)\);$"
        )
        assert footer.search(sql), "migration must end with the schema_migrations footer"
