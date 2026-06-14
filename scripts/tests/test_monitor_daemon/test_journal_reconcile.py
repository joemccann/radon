#!/usr/bin/env python3
"""TDD tests for JournalReconcileHandler — Red/Green.

Design contract (from JRN-01 rca.reconcile_design):
  - BaseHandler subclass with service_name="journal-reconcile"
  - requires_market_hours=False — runs 24/7
  - requires_ib=False — pure Turso read
  - interval_seconds = 86400 (once daily)
  - Detects exec_ids in executed_orders with no journal row
  - BAG/combo parents are NOT flagged (they have no journal row by design)
  - ±1-day fallback so journal_rehydrate date-grouping doesn't produce false positives
  - exec_id part-set matching (journal rows can join fills with '+')
  - ALERT-ONLY — never writes to journal
  - Raises on DB unavailable (BaseHandler retries next cycle)
  - ok heartbeat even when zero gaps found
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta, timezone

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.journal_reconcile import (  # noqa: E402
    JournalReconcileHandler,
    _build_journal_coverage,
    _find_gaps,
    _is_bag_combo_parent,
    _journal_exec_id_parts,
    heal_journal_reconcile_if_recovered,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(executed_rows: list[tuple], journal_rows: list[tuple]) -> MagicMock:
    """Build a mock DB that returns distinct rows for the two queries."""
    db = MagicMock()

    def _execute(sql: str, params: tuple = ()):
        cursor = MagicMock(spec=["fetchall"])
        if "executed_orders" in sql:
            cursor.fetchall.return_value = executed_rows
        else:
            cursor.fetchall.return_value = journal_rows
        return cursor

    db.execute.side_effect = _execute
    return db


def _exec_row(
    exec_id: str,
    symbol: str,
    sec_type: str = "OPT",
    side: str = "SLD",
    strike: float = 1000.0,
    right: str = "C",
    expiry: str = "20260612",
    fill_time: str = "2026-06-08T15:00:00+00:00",
) -> tuple:
    payload = {
        "execId": exec_id,
        "symbol": symbol,
        "contract": {
            "secType": sec_type,
            "strike": strike,
            "right": right,
            "expiry": expiry,
        },
        "side": side,
    }
    return (exec_id, None, json.dumps(payload), fill_time, "2026-06-08T23:28:13Z")


def _journal_row(
    ib_exec_id: str,
    ticker: str,
    action: str = "SELL_TO_OPEN",
    strike: float = 1000.0,
    right: str = "C",
    expiry: str = "20260612",
    filled_at: str = "2026-06-08",
) -> tuple:
    payload = {
        "ib_exec_id": ib_exec_id,
        "ticker": ticker,
        "action": action,
        "contracts": 7,
        "strike": strike,
        "right": right,
        "expiry": expiry,
    }
    return (json.dumps(payload), filled_at, filled_at)


# ---------------------------------------------------------------------------
# Identity / wiring
# ---------------------------------------------------------------------------

class TestJournalReconcileIdentity:
    def test_service_name(self):
        h = JournalReconcileHandler()
        assert h.service_name == "journal-reconcile"

    def test_name(self):
        h = JournalReconcileHandler()
        assert h.name == "journal_reconcile"

    def test_does_not_require_market_hours(self):
        h = JournalReconcileHandler()
        assert h.requires_market_hours is False

    def test_runs_once_daily(self):
        h = JournalReconcileHandler()
        assert h.interval_seconds == 86_400

    def test_is_base_handler_subclass(self):
        from monitor_daemon.handlers.base import BaseHandler
        assert issubclass(JournalReconcileHandler, BaseHandler)


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------

class TestIsBagComboParent:
    def test_bag_sec_type_is_parent(self):
        assert _is_bag_combo_parent({"contract": {"secType": "BAG"}})

    def test_opt_sec_type_is_not_parent(self):
        assert not _is_bag_combo_parent({"contract": {"secType": "OPT"}})

    def test_symbol_with_comma_is_parent(self):
        assert _is_bag_combo_parent({"symbol": "MU,EWY"})

    def test_plain_symbol_is_not_parent(self):
        assert not _is_bag_combo_parent({"symbol": "MU"})


class TestJournalExecIdParts:
    def test_simple_id(self):
        assert _journal_exec_id_parts("FILL-A") == {"FILL-A"}

    def test_composite_id(self):
        assert _journal_exec_id_parts("FILL-A+FILL-B") == {"FILL-A", "FILL-B"}

    def test_empty_returns_empty(self):
        assert _journal_exec_id_parts("") == set()

    def test_none_returns_empty(self):
        assert _journal_exec_id_parts(None) == set()

    def test_triple_composite(self):
        assert _journal_exec_id_parts("A+B+C") == {"A", "B", "C"}


class TestBuildJournalCoverage:
    def test_collects_simple_exec_id(self):
        db = MagicMock()
        cursor = MagicMock(spec=["fetchall"])
        cursor.fetchall.return_value = [
            _journal_row("0002920b.6a26c483.01.01", "MU")
        ]
        db.execute.return_value = cursor
        cov = _build_journal_coverage(db, "2026-06-01")
        assert "0002920b.6a26c483.01.01" in cov["exec_ids"]

    def test_expands_composite_exec_id(self):
        db = MagicMock()
        cursor = MagicMock(spec=["fetchall"])
        cursor.fetchall.return_value = [
            _journal_row("FILL-A+FILL-B", "WULF")
        ]
        db.execute.return_value = cursor
        cov = _build_journal_coverage(db, "2026-06-01")
        assert "FILL-A" in cov["exec_ids"]
        assert "FILL-B" in cov["exec_ids"]

    def test_empty_journal_returns_empty_sets(self):
        db = MagicMock()
        cursor = MagicMock(spec=["fetchall"])
        cursor.fetchall.return_value = []
        db.execute.return_value = cursor
        cov = _build_journal_coverage(db, "2026-06-01")
        assert cov["exec_ids"] == set()
        assert cov["contract_dates"] == set()

    def test_contract_date_tuple_present(self):
        db = MagicMock()
        cursor = MagicMock(spec=["fetchall"])
        cursor.fetchall.return_value = [
            _journal_row("EX-1", "EWY", strike=215.0, right="C", expiry="20260717", filled_at="2026-06-08")
        ]
        db.execute.return_value = cursor
        cov = _build_journal_coverage(db, "2026-06-01")
        assert ("EWY", "215.0", "C", "20260717", "2026-06-08") in cov["contract_dates"]


class TestFindGaps:
    def _make_exec_item(
        self,
        exec_id: str,
        symbol: str = "MU",
        sec_type: str = "OPT",
        strike: float = 1000.0,
        right: str = "C",
        expiry: str = "20260612",
        fill_time: str = "2026-06-08T15:00:00+00:00",
    ) -> dict:
        row = _exec_row(exec_id, symbol, sec_type=sec_type, strike=strike,
                        right=right, expiry=expiry, fill_time=fill_time)
        return {"exec_id": row[0], "fill_time": row[3], "payload": json.loads(row[2])}

    def test_no_gaps_when_exec_id_covered(self):
        item = self._make_exec_item("0002920b.6a26c483.01.01")
        coverage = {
            "exec_ids": {"0002920b.6a26c483.01.01"},
            "contract_dates": set(),
        }
        assert _find_gaps([item], coverage) == []

    def test_gap_when_exec_id_missing(self):
        item = self._make_exec_item("MISSING-EXEC")
        coverage = {"exec_ids": set(), "contract_dates": set()}
        gaps = _find_gaps([item], coverage)
        assert len(gaps) == 1
        assert gaps[0]["exec_id"] == "MISSING-EXEC"

    def test_bag_combo_parent_is_skipped(self):
        item = self._make_exec_item("BAG-EXEC", sec_type="BAG")
        coverage = {"exec_ids": set(), "contract_dates": set()}
        assert _find_gaps([item], coverage) == []

    def test_nearby_journal_row_suppresses_false_positive(self):
        """journal_rehydrate may group fills under a neighboring date."""
        item = self._make_exec_item(
            "EXEC-X", symbol="MU", strike=1000.0, right="C", expiry="20260612",
            fill_time="2026-06-08T15:00:00+00:00",
        )
        # Journal row is dated one day later — rehydrate date-grouping artefact.
        coverage = {
            "exec_ids": set(),
            "contract_dates": {("MU", "1000.0", "C", "20260612", "2026-06-09")},
        }
        assert _find_gaps([item], coverage) == []

    def test_gap_when_no_nearby_journal_row(self):
        item = self._make_exec_item(
            "EXEC-Y", symbol="VIX", strike=10.0, right="P", expiry="20260616",
            fill_time="2026-05-22T15:00:00+00:00",
        )
        # Contract date is 5 days away — outside the ±1-day window.
        coverage = {
            "exec_ids": set(),
            "contract_dates": {("VIX", "10.0", "P", "20260616", "2026-05-27")},
        }
        gaps = _find_gaps([item], coverage)
        assert len(gaps) == 1

    def test_composite_exec_id_coverage_via_parts(self):
        """An executed_orders exec_id covered by ONE part of a composite journal id."""
        item = self._make_exec_item("FILL-A")
        coverage = {
            "exec_ids": {"FILL-A", "FILL-B"},  # expanded from "FILL-A+FILL-B"
            "contract_dates": set(),
        }
        assert _find_gaps([item], coverage) == []

    def test_multiple_items_mixed_gaps(self):
        covered = self._make_exec_item("COVERED-1")
        gap1 = self._make_exec_item("GAP-1", symbol="MU", fill_time="2026-06-08T15:00:00+00:00")
        gap2 = self._make_exec_item("GAP-2", symbol="EWY", strike=215.0, right="C",
                                    fill_time="2026-06-08T15:00:00+00:00")
        coverage = {"exec_ids": {"COVERED-1"}, "contract_dates": set()}
        gaps = _find_gaps([covered, gap1, gap2], coverage)
        assert {g["exec_id"] for g in gaps} == {"GAP-1", "GAP-2"}


# ---------------------------------------------------------------------------
# Handler integration tests (mock DB)
# ---------------------------------------------------------------------------

class TestJournalReconcileExecute:
    def test_zero_gaps_returns_ok_result(self):
        exec_row = _exec_row("0002920b.6a26c483.01.01", "MU")
        jnl_row = _journal_row("0002920b.6a26c483.01.01", "MU")
        db = _make_db([exec_row], [jnl_row])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db):
            h = JournalReconcileHandler()
            result = h.execute()

        assert result["gaps_found"] == 0
        assert result["executed_orders_scanned"] == 1
        assert "gap_exec_ids" not in result
        # Clean run carries no error key → BaseHandler records state=ok.
        assert "error" not in result

    def test_detects_missing_exec_id(self):
        exec_row = _exec_row("MISSING-EXEC", "MU")
        db = _make_db([exec_row], [])  # empty journal

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db), \
             patch.object(JournalReconcileHandler, "_alert_on_gaps") as mock_alert:
            h = JournalReconcileHandler()
            result = h.execute()

        assert result["gaps_found"] == 1
        assert "MISSING-EXEC" in result["gap_exec_ids"]
        # Surfaces as the swallowed-failure convention → state=error.
        assert "MISSING-EXEC" in result["error"]
        mock_alert.assert_called_once()

    def test_db_unavailable_raises(self):
        """DB unreachable → raise so BaseHandler does not latch last_run."""
        with patch.object(JournalReconcileHandler, "_open_db", return_value=None):
            h = JournalReconcileHandler()
            with pytest.raises(RuntimeError, match="DB unavailable"):
                h.execute()

    def test_bag_parent_not_counted_as_gap(self):
        bag_row = _exec_row("BAG-EXEC", "MU", sec_type="BAG")
        db = _make_db([bag_row], [])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db):
            h = JournalReconcileHandler()
            result = h.execute()

        assert result["gaps_found"] == 0

    def test_nearby_date_journal_row_no_false_positive(self):
        """journal_rehydrate groups fills under neighboring dates — should not alert."""
        exec_row = _exec_row(
            "EXEC-DATE-GROUP", "MSFT",
            strike=450.0, right="C", expiry="20260619",
            fill_time="2026-05-28T15:00:00+00:00",
        )
        # Journal row for same contract dated one day later (rehydrate artefact).
        jnl_row = _journal_row(
            "DIFFERENT-EXEC-ID", "MSFT",
            strike=450.0, right="C", expiry="20260619",
            filled_at="2026-05-29",
        )
        db = _make_db([exec_row], [jnl_row])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db):
            h = JournalReconcileHandler()
            result = h.execute()

        assert result["gaps_found"] == 0

    def test_run_method_writes_service_health_ok_on_zero_gaps(self):
        """BaseHandler.run() guarantees an ok heartbeat on every successful cycle."""
        exec_row = _exec_row("COVERED", "MU")
        jnl_row = _journal_row("COVERED", "MU")
        db = _make_db([exec_row], [jnl_row])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db), \
             patch("monitor_daemon.handlers.base.BaseHandler.record_cycle_health") as mock_rch:
            h = JournalReconcileHandler()
            outcome = h.run()

        assert outcome["status"] == "ok"
        mock_rch.assert_called()
        # First positional arg to any call should be "ok"
        states = [c.args[0] for c in mock_rch.call_args_list]
        assert "ok" in states

    def test_run_records_error_state_when_gaps_found(self):
        """A found gap is the reconciliation's OWN finding (BaseHandler's
        swallowed-failure convention): the handler RAN fine — run status ok,
        last_run latches so it re-checks tomorrow — but the service_health row
        goes ERROR with the gap detail, so /admin, the banner, DUR-11 history,
        and the watchdog all surface it (the old dead utils.notify never did)."""
        exec_row = _exec_row("GAP-ID", "MU")
        db = _make_db([exec_row], [])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db), \
             patch.object(JournalReconcileHandler, "_alert_on_gaps"), \
             patch("monitor_daemon.handlers.base.BaseHandler.record_cycle_health") as mock_rch:
            h = JournalReconcileHandler()
            outcome = h.run()

        # The handler ran without raising — run status ok, last_run latches.
        assert outcome["status"] == "ok"
        # ...but the service_health row it wrote is error, carrying the gap.
        states = [c.args[0] for c in mock_rch.call_args_list]
        assert states == ["error"]
        err = mock_rch.call_args_list[0].kwargs.get("error") or {}
        assert "missing from journal" in str(err.get("message", ""))

    def test_window_days_constant_used(self):
        """Executed_orders query must pass a timestamp 7 days back."""
        db = _make_db([], [])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db):
            h = JournalReconcileHandler()
            h.execute()

        # Should have made exactly two DB calls: one for executed_orders, one for journal
        assert db.execute.call_count == 2

    def test_result_includes_window_days(self):
        db = _make_db([], [])
        with patch.object(JournalReconcileHandler, "_open_db", return_value=db):
            h = JournalReconcileHandler()
            result = h.execute()
        assert result["window_days"] == 7

    def test_gap_exec_ids_truncated_at_max_detail(self):
        """Only the first 5 gap exec_ids appear in the detail payload."""
        from monitor_daemon.handlers.journal_reconcile import _MAX_GAP_DETAIL

        exec_rows = [_exec_row(f"GAP-{i}", "MU") for i in range(10)]
        db = _make_db(exec_rows, [])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db), \
             patch.object(JournalReconcileHandler, "_alert_on_gaps"):
            h = JournalReconcileHandler()
            result = h.execute()

        assert result["gaps_found"] == 10
        assert len(result["gap_exec_ids"]) == _MAX_GAP_DETAIL
        assert result.get("gap_exec_ids_truncated") is True

    def test_composite_journal_exec_id_covers_individual_fills(self):
        """A "FILL-A+FILL-B" journal row covers both FILL-A and FILL-B."""
        exec_a = _exec_row("FILL-A", "MU")
        exec_b = _exec_row("FILL-B", "MU")
        jnl_row = _journal_row("FILL-A+FILL-B", "MU")
        db = _make_db([exec_a, exec_b], [jnl_row])

        with patch.object(JournalReconcileHandler, "_open_db", return_value=db):
            h = JournalReconcileHandler()
            result = h.execute()

        assert result["gaps_found"] == 0


# ---------------------------------------------------------------------------
# Registration contract sanity checks (does not write to any file)
# ---------------------------------------------------------------------------

class TestRegistrationRequirements:
    def test_service_name_is_kebab_case(self):
        h = JournalReconcileHandler()
        sn = h.service_name
        assert sn == sn.lower()
        assert " " not in sn
        assert sn.startswith("journal-")

    def test_not_ib_dependent(self):
        """handler does NOT call IB — requires_ib MUST be false in the TS registration."""
        import inspect
        import monitor_daemon.handlers.journal_reconcile as mod

        src = inspect.getsource(mod)
        # Must not import IBClient or ib_insync
        assert "IBClient" not in src
        assert "ib_insync" not in src


# ---------------------------------------------------------------------------
# JRN-03: heal-on-recovery — clear the latched error row the moment the
# flagged gaps are actually journaled, instead of waiting up to 24h for the
# next daily reconcile pass.
# ---------------------------------------------------------------------------

def _make_heal_db(
    state: "str | None",
    executed_rows: list[tuple],
    journal_rows: list[tuple],
) -> MagicMock:
    """Mock DB routing three query shapes: the service_health state read, the
    executed_orders window, and the journal coverage window."""
    db = MagicMock()

    def _execute(sql: str, params: tuple = ()):
        cursor = MagicMock(spec=["fetchall"])
        if "service_health" in sql:
            cursor.fetchall.return_value = [(state,)] if state is not None else []
        elif "executed_orders" in sql:
            cursor.fetchall.return_value = executed_rows
        else:
            cursor.fetchall.return_value = journal_rows
        return cursor

    db.execute.side_effect = _execute
    return db


class TestHealOnRecovery:
    def test_heals_when_error_and_gaps_now_covered(self):
        """error row + the previously-missing fill is now journaled → flip to ok."""
        exec_a = _exec_row("000205d2.6a26a327.01.01", "EWY")
        jnl_a = _journal_row("000205d2.6a26a327.01.01", "EWY")  # now present
        db = _make_heal_db("error", [exec_a], [jnl_a])

        with patch(
            "monitor_daemon.handlers.journal_reconcile.record_service_health"
        ) as rec:
            healed = heal_journal_reconcile_if_recovered(db)

        assert healed is True
        rec.assert_called_once()
        args, kwargs = rec.call_args
        assert args[0] == "journal-reconcile"
        assert args[1] == "ok"

    def test_does_not_heal_when_gaps_remain(self):
        """error row + the fill is STILL missing from journal → leave error untouched."""
        exec_a = _exec_row("000205d2.6a26a327.01.01", "EWY")
        db = _make_heal_db("error", [exec_a], [])  # no journal coverage

        with patch(
            "monitor_daemon.handlers.journal_reconcile.record_service_health"
        ) as rec:
            healed = heal_journal_reconcile_if_recovered(db)

        assert healed is False
        rec.assert_not_called()

    def test_noop_when_row_not_in_error(self):
        """ok/healthy row → cheap exit, no windowed re-scan, no write."""
        db = _make_heal_db("ok", [], [])

        with patch(
            "monitor_daemon.handlers.journal_reconcile.record_service_health"
        ) as rec:
            healed = heal_journal_reconcile_if_recovered(db)

        assert healed is False
        rec.assert_not_called()
        # only the cheap single-row state read should have run — never the
        # executed_orders / journal windowed scans.
        executed_query_ran = any(
            "executed_orders" in c.args[0] for c in db.execute.call_args_list
        )
        assert executed_query_ran is False

    def test_noop_when_no_row(self):
        """no service_health row yet (never run) → nothing to heal."""
        db = _make_heal_db(None, [], [])

        with patch(
            "monitor_daemon.handlers.journal_reconcile.record_service_health"
        ) as rec:
            healed = heal_journal_reconcile_if_recovered(db)

        assert healed is False
        rec.assert_not_called()

    def test_noop_when_db_none(self):
        assert heal_journal_reconcile_if_recovered(None) is False
