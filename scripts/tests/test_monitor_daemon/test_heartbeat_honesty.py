#!/usr/bin/env python3
"""REL-007 — heartbeat honesty (RELIABILITY_AUDIT.md R-019, R-020).

Two silent-degradation paths reported green while the money path was
down:

  - exit_orders._load_pending_orders swallowed a Turso read failure and
    returned [] — the cycle heartbeated ok while exit protection
    silently stopped being placed.
  - journal_sync._dual_write swallowed upsert failures while execute()
    counted every candidate as "imported" — a write-only Turso outage
    lost fills behind a green heartbeat (production has no per-cycle
    retry: trade_log_path=None dead-gates the reconcile step).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
from monitor_daemon.handlers.journal_sync import JournalSyncHandler


class _RaisingDb:
    def execute(self, *a, **k):
        raise RuntimeError("hrana 502: upstream forward failed")


class TestExitOrdersLoadFailureHonesty:
    def test_pending_load_failure_surfaces_error(self, tmp_path, monkeypatch):
        import trading_halt

        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        with patch(
            "monitor_daemon.handlers.exit_orders.IBClient",
            side_effect=AssertionError("no IB work when the journal is unreadable"),
        ):
            handler = ExitOrdersHandler(db=_RaisingDb())
            result = handler.execute()

        assert "error" in result
        assert "journal" in result["error"].lower() or "load" in result["error"].lower()
        assert result.get("orders_placed", 0) == 0


class TestJournalSyncUpsertFailureHonesty:
    def _handler(self):
        # trade_log_path=None mirrors production wiring (run.py).
        return JournalSyncHandler(trade_log_path=None)

    def test_upsert_failures_counted_and_surfaced(self, monkeypatch):
        handler = self._handler()

        calls = {"n": 0}

        def failing_upsert(*a, **k):
            calls["n"] += 1
            raise RuntimeError("hrana 502")

        monkeypatch.setattr(
            "monitor_daemon.handlers.journal_sync.upsert_journal_entry",
            failing_upsert,
        )
        candidates = [
            {"ib_exec_id": "0001.aa", "ticker": "GOOG", "date": "2026-08-07"},
            {"ib_exec_id": "0001.bb", "ticker": "MU", "date": "2026-08-07"},
        ]
        written, failed = handler._dual_write(candidates)
        assert calls["n"] == 2
        assert written == []  # merged REL-011 contract: list of successes
        assert failed == 2

    def test_execute_surfaces_dual_write_failures(self, monkeypatch):
        """Production wiring (trade_log_path=None): a cycle whose journal
        upserts all fail must NOT heartbeat green with imported=N."""
        handler = self._handler()

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            client.get_fills.return_value = [MagicMock()]

            monkeypatch.setattr(handler, "_open_db", lambda: MagicMock())
            monkeypatch.setattr(
                handler, "_load_existing_from_journal",
                lambda db, allow_empty=True: {"trades": []},
            )
            candidate = {"ib_exec_id": "0001.aa", "ticker": "GOOG"}
            monkeypatch.setattr(
                handler, "_fills_to_entries",
                lambda fills, existing, db=None: ([candidate], []),
            )
            monkeypatch.setattr(
                "monitor_daemon.handlers.journal_sync.upsert_journal_entry",
                MagicMock(side_effect=RuntimeError("hrana 502")),
            )
            monkeypatch.setattr(handler, "_maybe_heal_reconcile", lambda db: None)

            result = handler.execute()

        assert "error" in result
        assert result.get("db_write_failures") == 1

    def test_partial_failure_reports_split(self, monkeypatch):
        handler = self._handler()

        def flaky_upsert(exec_id, *a, **k):
            if exec_id == "0001.bb":
                raise RuntimeError("hrana 502")

        monkeypatch.setattr(
            "monitor_daemon.handlers.journal_sync.upsert_journal_entry",
            flaky_upsert,
        )
        written, failed = handler._dual_write([
            {"ib_exec_id": "0001.aa"},
            {"ib_exec_id": "0001.bb"},
        ])
        assert [e["ib_exec_id"] for e in written] == ["0001.aa"]
        assert failed == 1
