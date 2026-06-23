"""Verify ib_reconcile persists reconciliation reports to Turso."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture
def mock_writer(monkeypatch: pytest.MonkeyPatch):
    calls: list[dict[str, Any]] = []
    fake = types.ModuleType("db.writer")
    fake.upsert_reconciliation_log = lambda snapshot_at, payload: calls.append(  # type: ignore[attr-defined]
        {"snapshot_at": snapshot_at, "payload": payload}
    )
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    if "ib_reconcile" in sys.modules:
        del sys.modules["ib_reconcile"]
    yield calls


def test_save_reconciliation_report_writes_to_db(mock_writer):
    import ib_reconcile

    payload = {"snapshot_at": "2026-05-07T01:00:00Z", "diffs": []}
    ib_reconcile.save_reconciliation_report(payload)

    assert len(mock_writer) == 1
    assert mock_writer[0]["snapshot_at"] == "2026-05-07T01:00:00Z"


def test_save_reconciliation_report_falls_back_to_now_when_payload_missing_timestamp(mock_writer):
    import ib_reconcile

    ib_reconcile.save_reconciliation_report({"diffs": []})  # no snapshot_at / timestamp

    assert len(mock_writer) == 1
    assert mock_writer[0]["snapshot_at"]  # non-empty


def test_save_reconciliation_report_db_failure_does_not_break(monkeypatch: pytest.MonkeyPatch):
    fake = types.ModuleType("db.writer")
    fake.upsert_reconciliation_log = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "db.writer", fake)
    if "ib_reconcile" in sys.modules:
        del sys.modules["ib_reconcile"]
    import ib_reconcile

    ib_reconcile.save_reconciliation_report({"diffs": []})  # must not raise


def test_loaders_read_canonical_db_helpers(monkeypatch: pytest.MonkeyPatch):
    if "ib_reconcile" in sys.modules:
        del sys.modules["ib_reconcile"]
    import ib_reconcile

    monkeypatch.setattr(ib_reconcile, "read_journal_trades", lambda: [{"ticker": "AMD"}])
    monkeypatch.setattr(
        ib_reconcile,
        "read_latest_portfolio_snapshot",
        lambda: {"positions": [{"ticker": "AMD"}]},
    )

    assert ib_reconcile.load_trade_log() == {"trades": [{"ticker": "AMD"}]}
    assert ib_reconcile.load_portfolio_snapshot() == {"positions": [{"ticker": "AMD"}]}
