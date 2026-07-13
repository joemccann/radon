"""R5 partial: hang-risk writers ride bounded hrana, not sync libsql.

Pins that ``record_service_health``, ``upsert_journal_entry``, and
``upsert_portfolio_snapshot`` call into ``db.hrana_http`` (real socket
timeout) and never open ``get_db()``. Mocks only — no production Turso.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


@pytest.fixture
def writer_with_hrana(monkeypatch: pytest.MonkeyPatch):
    """Reload writer against a captured hrana_execute mock.

    Also stubs ``get_db`` to raise if anything accidentally falls through
    to sync libsql — the whole point of this migration.
    """
    import db.hrana_http as hrana_mod
    import db.writer as writer_mod
    import importlib

    calls: list[tuple[str, tuple]] = []

    def capture_execute(sql, args=(), timeout=None):
        calls.append((sql, tuple(args)))

    monkeypatch.setattr(hrana_mod, "hrana_execute", capture_execute)
    monkeypatch.setattr(
        writer_mod,
        "get_db",
        lambda: (_ for _ in ()).throw(
            AssertionError("sync get_db() must not run on bounded write paths")
        ),
        raising=False,
    )
    # Ensure lazy imports inside writer see the patched hrana module.
    importlib.reload(writer_mod)
    monkeypatch.setattr(hrana_mod, "hrana_execute", capture_execute)
    monkeypatch.setattr(
        writer_mod,
        "get_db",
        lambda: (_ for _ in ()).throw(
            AssertionError("sync get_db() must not run on bounded write paths")
        ),
        raising=False,
    )

    yield writer_mod, calls


class TestRecordServiceHealthUsesHrana:
    def test_routes_through_write_service_health_http_sql(self, writer_with_hrana):
        writer, calls = writer_with_hrana
        writer.record_service_health(
            "monitor-fill",
            "ok",
            started_at="2026-07-13T12:00:00Z",
            finished_at="2026-07-13T12:00:01Z",
        )
        assert len(calls) == 1
        sql, args = calls[0]
        assert "INSERT INTO service_health" in sql
        assert "ON CONFLICT(service) DO UPDATE" in sql
        assert args[0] == "monitor-fill"
        assert args[1] == "ok"
        assert args[2] == "2026-07-13T12:00:00Z"
        assert args[3] == "2026-07-13T12:00:01Z"

    def test_serializes_error_payload_as_json(self, writer_with_hrana):
        writer, calls = writer_with_hrana
        writer.record_service_health(
            "journal-sync",
            "error",
            error={"message": "stream not found"},
        )
        _sql, args = calls[0]
        assert args[1] == "error"
        assert json.loads(args[4])["message"] == "stream not found"

    def test_propagates_hrana_errors(self, monkeypatch: pytest.MonkeyPatch):
        import db.hrana_http as hrana_mod
        import db.writer as writer_mod
        from db.hrana_http import HranaHttpError

        monkeypatch.setattr(
            hrana_mod,
            "hrana_execute",
            MagicMock(side_effect=HranaHttpError("timeout")),
        )
        with pytest.raises(HranaHttpError, match="timeout"):
            writer_mod.record_service_health("x", "ok")


class TestUpsertJournalEntryUsesHrana:
    def test_executes_canonical_upsert_sql(self, writer_with_hrana):
        writer, calls = writer_with_hrana
        writer.upsert_journal_entry(
            "trade-1",
            {"ticker": "MU", "expiry": "20260718"},
            filled_at="2026-07-13",
        )
        assert len(calls) == 1
        sql, args = calls[0]
        assert sql.strip() == writer.JOURNAL_UPSERT_SQL.strip()
        assert args[0] == "trade-1"
        payload = json.loads(args[1])
        assert payload["ticker"] == "MU"
        assert payload["expiry"] == "20260718"
        assert args[2] == "2026-07-13"
        assert args[3]  # written_at ISO

    def test_normalizes_iso_expiry_to_compact(self, writer_with_hrana):
        writer, calls = writer_with_hrana
        writer.upsert_journal_entry("t2", {"expiry": "2026-07-18"})
        payload = json.loads(calls[0][1][1])
        assert payload["expiry"] == "20260718"

    def test_does_not_call_get_db(self, writer_with_hrana):
        writer, _calls = writer_with_hrana
        # get_db raises AssertionError if touched — success means no raise.
        writer.upsert_journal_entry("t3", {"ticker": "SPY"})


class TestUpsertPortfolioSnapshotUsesHrana:
    def test_executes_insert_or_replace(self, writer_with_hrana):
        writer, calls = writer_with_hrana
        writer.upsert_portfolio_snapshot(
            "2026-07-13T14:00:00Z",
            {"bankroll": 100_000, "positions": []},
        )
        assert len(calls) == 1
        sql, args = calls[0]
        assert sql.strip() == writer.PORTFOLIO_SNAPSHOT_UPSERT_SQL.strip()
        assert args[0] == "2026-07-13T14:00:00Z"
        assert json.loads(args[1])["bankroll"] == 100_000


class TestBoundedPathInventoryDocumented:
    """Smoke: client.py docstring lists the R5 bounded inventory."""

    def test_client_docstring_names_bounded_paths(self):
        import db.client as client_mod

        doc = client_mod.__doc__ or ""
        assert "BOUNDED VS PROCESS-BOUND" in doc
        assert "record_service_health" in doc
        assert "upsert_journal_entry" in doc
        assert "upsert_portfolio_snapshot" in doc
        assert "hrana" in doc.lower()
        assert "TimeoutStartSec" in doc or "RuntimeMaxSec" in doc
