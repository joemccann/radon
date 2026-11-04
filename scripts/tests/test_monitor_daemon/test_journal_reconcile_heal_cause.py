"""journal-reconcile heal only clears a gap error (audit §3.3, C4).

heal_journal_reconcile_if_recovered runs from other writers (journal_sync,
the backfill script). It flipped the journal-reconcile row to ok whenever
it read ``error`` and found no gaps, whatever caused the error, so a DB
outage or crash recorded by the reconcile handler was painted green by a
writer that never diagnosed it. The handler now records the error's cause
and the heal clears only ``cause == "journal_gap"``.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from test_monitor_daemon.test_journal_reconcile import _exec_row, _make_db
from monitor_daemon.handlers.journal_reconcile import (
    GAP_CAUSE,
    JournalReconcileHandler,
    heal_journal_reconcile_if_recovered,
)


def _db(state, last_error):
    db = MagicMock()

    def execute(sql, params=()):
        cursor = MagicMock(spec=["fetchall"])
        if "service_health" in sql:
            cursor.fetchall.return_value = [(state, last_error)]
        else:
            cursor.fetchall.return_value = []
        return cursor

    db.execute.side_effect = execute
    return db


def _heal(db):
    with patch("monitor_daemon.handlers.journal_reconcile.record_service_health") as rec:
        return heal_journal_reconcile_if_recovered(db), rec


def test_does_not_heal_an_error_another_cause_recorded():
    healed, rec = _heal(_db("error", json.dumps({"message": "journal-reconcile: DB unavailable"})))
    assert healed is False
    rec.assert_not_called()


def test_does_not_heal_an_error_without_a_recorded_cause():
    healed, rec = _heal(_db("error", None))
    assert healed is False
    rec.assert_not_called()


def test_heals_a_gap_error_once_gaps_are_covered():
    healed, rec = _heal(_db("error", json.dumps({"message": "1 executed fill(s) missing", "cause": GAP_CAUSE})))
    assert healed is True
    assert rec.call_args[0][:2] == ("journal-reconcile", "ok")


def test_handler_records_the_gap_cause():
    db = _make_db([_exec_row("GAP-1", "MU")], [])
    with patch.object(JournalReconcileHandler, "_open_db", return_value=db), \
         patch.object(JournalReconcileHandler, "_alert_on_gaps"), \
         patch("monitor_daemon.handlers.base.BaseHandler.record_cycle_health") as rch:
        JournalReconcileHandler().run()
    error = rch.call_args.kwargs["error"]
    assert error["cause"] == GAP_CAUSE
    assert "missing from journal" in error["message"]
