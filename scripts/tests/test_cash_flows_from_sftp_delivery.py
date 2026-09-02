"""Cash flows come from the sFTP-delivered statement, not a daily SendRequest.

`radon-flex-pull.timer` (Tue..Sat 07:30 ET) decrypts IBKR's delivered Activity
statement and runs `cash_flow_sync --from-file` on it. That is the only path
that writes `cash_flows`. The monitor daemon's `cash_flow_sync` handler still
spawned `scripts.cash_flow_sync` with no source every morning at 08:00 ET; the
CLI exits `EXIT_FLEX_SEND_DISABLED` ("Flex Web Service is file-ingest only"),
the handler recorded it as a `cash-flow-sync` error, and the Orders page showed
that banner over rows the sFTP path had already synced hours earlier.

Two invariants:

  1. `create_daemon()` registers no `CashFlowSyncHandler`.
  2. The sFTP ingest's activity branch heartbeats `cash-flow-sync` itself, so
     the watchdog's daily window and the `/cash-flows` lozenge follow the path
     that actually writes the rows.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import flex_delivery_ingest as ingest  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ACTIVITY = FIXTURES / "cash_transactions_flex_ytd_detail_sample.xml"


class TestDaemonNoLongerRunsCashFlowSync:
    def test_create_daemon_does_not_register_the_handler(self):
        tree = ast.parse((SCRIPTS / "monitor_daemon" / "run.py").read_text(encoding="utf-8"))
        registered = {
            arg.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            for arg in node.args
            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
        }
        assert registered, "failed to parse create_daemon() registrations"
        assert "CashFlowSyncHandler" not in registered


@pytest.fixture
def claims(monkeypatch):
    monkeypatch.setattr(ingest, "claim_flex_delivery", lambda *_a, **_k: True)
    monkeypatch.setattr(ingest, "release_flex_delivery", lambda _d: True, raising=False)
    monkeypatch.setattr(ingest, "flex_delivery_status", lambda _d: None, raising=False)
    monkeypatch.setattr(ingest, "mark_flex_delivery_applied", lambda _d: True, raising=False)


@pytest.fixture
def heartbeats(monkeypatch):
    from db import writer

    rows: list[tuple] = []
    monkeypatch.setattr(
        writer,
        "record_service_health",
        lambda service, state, **kw: rows.append((service, state, kw)),
    )
    return rows


@pytest.fixture
def activity(tmp_path):
    xml_text = ACTIVITY.read_text()
    path = tmp_path / "activity.xml"
    path.write_text(xml_text)
    return xml_text, str(path)


class TestSftpIngestOwnsTheCashFlowHeartbeat:
    def test_a_successful_file_sync_heartbeats_ok(self, monkeypatch, claims, heartbeats, activity):
        import cash_flow_sync
        import perf_twr_builder

        monkeypatch.setattr(cash_flow_sync, "main", lambda _argv: 0)
        monkeypatch.setattr(perf_twr_builder, "build_and_persist", lambda **_k: {"status": "ok"})

        xml_text, path = activity
        assert ingest.ingest_xml(xml_text, source_path=path)["ok"] is True

        cash = [row for row in heartbeats if row[0] == "cash-flow-sync"]
        assert len(cash) == 1
        _, state, kw = cash[0]
        assert state == "ok"
        assert kw.get("finished_at")
        assert kw.get("error") is None

    def test_a_failed_file_sync_heartbeats_error_with_the_exit_code(
        self, monkeypatch, claims, heartbeats, activity
    ):
        import cash_flow_sync
        import perf_twr_builder

        monkeypatch.setattr(cash_flow_sync, "main", lambda _argv: cash_flow_sync.EXIT_WRITE_ERROR)
        twr_calls: list[dict] = []
        monkeypatch.setattr(
            perf_twr_builder,
            "build_and_persist",
            lambda **kwargs: (twr_calls.append(kwargs), {"status": "ok"})[1],
        )

        xml_text, path = activity
        assert ingest.ingest_xml(xml_text, source_path=path)["ok"] is False
        assert twr_calls == []

        cash = [row for row in heartbeats if row[0] == "cash-flow-sync"]
        assert len(cash) == 1
        _, state, kw = cash[0]
        assert state == "error"
        assert kw["error"]["cash_exit"] == cash_flow_sync.EXIT_WRITE_ERROR
        assert "cash_flow_sync" in kw["error"]["message"]

    def test_a_trades_statement_does_not_touch_the_cash_flow_row(self, monkeypatch, claims, heartbeats):
        import journal_rehydrate

        monkeypatch.setattr(
            journal_rehydrate, "rehydrate", lambda **_k: {"ok": True, "imported": 0, "skipped": 0}
        )
        trades = (FIXTURES / "flex_trade_confirm_sample.xml").read_text()
        assert ingest.ingest_xml(trades, source_path="trades.xml")["ok"] is True
        assert [row for row in heartbeats if row[0] == "cash-flow-sync"] == []
