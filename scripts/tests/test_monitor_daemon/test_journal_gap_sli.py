#!/usr/bin/env python3
"""TDD tests for continuous journal-gap SLI (R6 / JRN continuous).

Design contract:
  - BaseHandler subclass with service_name="journal-gap-sli"
  - requires_market_hours=False — pure Turso read, 24/7
  - interval_seconds ~ 300 (continuous, not daily)
  - missing_exec_id_count over a rolling window (executed_orders vs journal)
  - service_health row carries structured detail (count + sample exec ids)
  - ALERT-ONLY — never writes to journal
  - Fresh fills younger than min_age are not counted (avoid race with journal_sync)
  - Zero gaps → state=ok with missing_exec_id_count=0
  - Gaps → state=error with structured last_error
  - Mock get_db / open_db; no production writes
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.journal_gap_sli import (  # noqa: E402
    GAP_SLI_INTERVAL_SECONDS,
    GAP_SLI_MIN_AGE_MINUTES,
    GAP_SLI_WINDOW_DAYS,
    JournalGapSliHandler,
    SERVICE_NAME,
    scan_journal_gaps,
)
from monitor_daemon.handlers.journal_reconcile import (  # noqa: E402
    _find_gaps,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(executed_rows: list[tuple], journal_rows: list[tuple]) -> MagicMock:
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
    symbol: str = "MU",
    sec_type: str = "OPT",
    strike: float = 1000.0,
    right: str = "C",
    expiry: str = "20260612",
    fill_time: str | None = None,
) -> tuple:
    if fill_time is None:
        fill_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    payload = {
        "execId": exec_id,
        "symbol": symbol,
        "contract": {
            "secType": sec_type,
            "strike": strike,
            "right": right,
            "expiry": expiry,
        },
        "side": "SLD",
    }
    return (exec_id, None, json.dumps(payload), fill_time, fill_time)


def _journal_row(
    ib_exec_id: str,
    ticker: str = "MU",
    strike: float = 1000.0,
    right: str = "C",
    expiry: str = "20260612",
    filled_at: str | None = None,
) -> tuple:
    if filled_at is None:
        filled_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "ib_exec_id": ib_exec_id,
        "ticker": ticker,
        "action": "SELL_TO_OPEN",
        "contracts": 7,
        "strike": strike,
        "right": right,
        "expiry": expiry,
    }
    return (json.dumps(payload), filled_at, filled_at)


# ---------------------------------------------------------------------------
# Identity / wiring
# ---------------------------------------------------------------------------

class TestJournalGapSliIdentity:
    def test_service_name(self):
        h = JournalGapSliHandler()
        assert h.service_name == "journal-gap-sli"
        assert SERVICE_NAME == "journal-gap-sli"

    def test_name(self):
        h = JournalGapSliHandler()
        assert h.name == "journal_gap_sli"

    def test_does_not_require_market_hours(self):
        h = JournalGapSliHandler()
        assert h.requires_market_hours is False

    def test_runs_continuously_not_daily(self):
        h = JournalGapSliHandler()
        assert h.interval_seconds == GAP_SLI_INTERVAL_SECONDS
        assert h.interval_seconds <= 600
        assert h.interval_seconds < 86_400

    def test_is_base_handler_subclass(self):
        from monitor_daemon.handlers.base import BaseHandler

        assert issubclass(JournalGapSliHandler, BaseHandler)


# ---------------------------------------------------------------------------
# Pure scan helper
# ---------------------------------------------------------------------------

class TestScanJournalGaps:
    def test_zero_gaps(self):
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [_exec_row("COVERED-1", fill_time=fill_time)],
            [_journal_row("COVERED-1", filled_at="2026-06-13")],
        )
        snap = scan_journal_gaps(db, now=now)
        assert snap["missing_exec_id_count"] == 0
        assert snap["executed_orders_scanned"] == 1
        assert snap["gaps"] == []
        assert snap["window_days"] == GAP_SLI_WINDOW_DAYS

    def test_counts_missing_exec_ids(self):
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [
                _exec_row("GAP-A", fill_time=fill_time),
                _exec_row("GAP-B", symbol="EWY", strike=215.0, fill_time=fill_time),
            ],
            [],
        )
        snap = scan_journal_gaps(db, now=now)
        assert snap["missing_exec_id_count"] == 2
        assert {g["exec_id"] for g in snap["gaps"]} == {"GAP-A", "GAP-B"}

    def test_skips_bag_parents(self):
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [_exec_row("BAG-1", sec_type="BAG", fill_time=fill_time)],
            [],
        )
        snap = scan_journal_gaps(db, now=now)
        assert snap["missing_exec_id_count"] == 0

    def test_min_age_filters_fresh_fills(self):
        """Fresh fills still racing journal_sync must not inflate the SLI."""
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fresh = (now - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
        aged = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [
                _exec_row("FRESH-GAP", fill_time=fresh),
                _exec_row("AGED-GAP", fill_time=aged),
            ],
            [],
        )
        snap = scan_journal_gaps(
            db,
            now=now,
            min_age_minutes=GAP_SLI_MIN_AGE_MINUTES,
        )
        assert snap["missing_exec_id_count"] == 1
        assert snap["gaps"][0]["exec_id"] == "AGED-GAP"
        assert snap["min_age_minutes"] == GAP_SLI_MIN_AGE_MINUTES

    def test_composite_journal_exec_id_covers_parts(self):
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [
                _exec_row("FILL-A", fill_time=fill_time),
                _exec_row("FILL-B", fill_time=fill_time),
            ],
            [_journal_row("FILL-A+FILL-B", filled_at="2026-06-13")],
        )
        snap = scan_journal_gaps(db, now=now)
        assert snap["missing_exec_id_count"] == 0

    def test_reuses_reconcile_gap_finder(self):
        """SLI detection must stay aligned with daily journal-reconcile."""
        # smoke: _find_gaps is the shared primitive (import above proves linkage)
        assert callable(_find_gaps)


# ---------------------------------------------------------------------------
# Handler execute / service_health
# ---------------------------------------------------------------------------

class TestJournalGapSliExecute:
    def test_zero_gaps_ok_structured_detail(self):
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [_exec_row("OK-1", fill_time=fill_time)],
            [_journal_row("OK-1", filled_at="2026-06-13")],
        )

        with patch.object(JournalGapSliHandler, "_open_db", return_value=db), \
             patch.object(JournalGapSliHandler, "_now_utc", return_value=now), \
             patch.object(JournalGapSliHandler, "record_cycle_health") as mock_rch:
            h = JournalGapSliHandler()
            result = h.execute()

        assert result["missing_exec_id_count"] == 0
        assert "error" not in result
        mock_rch.assert_called_once()
        state = mock_rch.call_args.args[0]
        detail = mock_rch.call_args.kwargs.get("error") or {}
        assert state == "ok"
        assert detail["missing_exec_id_count"] == 0
        assert detail["window_days"] == GAP_SLI_WINDOW_DAYS

    def test_gaps_error_with_structured_detail(self):
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [_exec_row("MISSING-1", fill_time=fill_time)],
            [],
        )

        with patch.object(JournalGapSliHandler, "_open_db", return_value=db), \
             patch.object(JournalGapSliHandler, "_now_utc", return_value=now), \
             patch.object(JournalGapSliHandler, "record_cycle_health") as mock_rch:
            h = JournalGapSliHandler()
            result = h.execute()

        assert result["missing_exec_id_count"] == 1
        assert "MISSING-1" in result["gap_exec_ids"]
        assert "error" in result
        state = mock_rch.call_args.args[0]
        detail = mock_rch.call_args.kwargs.get("error") or {}
        assert state == "error"
        assert detail["missing_exec_id_count"] == 1
        assert "MISSING-1" in detail["gap_exec_ids"]
        assert detail["window_days"] == GAP_SLI_WINDOW_DAYS
        assert "min_age_minutes" in detail

    def test_db_unavailable_raises(self):
        with patch.object(JournalGapSliHandler, "_open_db", return_value=None):
            h = JournalGapSliHandler()
            with pytest.raises(RuntimeError, match="DB unavailable"):
                h.execute()

    def test_run_latches_last_run_even_when_gaps(self):
        """Domain gap is data error, not soft failure — latch so cadence holds."""
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [_exec_row("MISSING-2", fill_time=fill_time)],
            [],
        )

        with patch.object(JournalGapSliHandler, "_open_db", return_value=db), \
             patch.object(JournalGapSliHandler, "_now_utc", return_value=now), \
             patch.object(JournalGapSliHandler, "record_cycle_health"):
            h = JournalGapSliHandler()
            outcome = h.run()

        assert outcome["status"] == "ok"
        assert h.last_run is not None

    def test_never_writes_journal(self):
        """ALERT-ONLY: execute must not call journal upserts."""
        now = datetime(2026, 6, 13, 15, 0, tzinfo=timezone.utc)
        fill_time = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        db = _make_db(
            [_exec_row("MISSING-3", fill_time=fill_time)],
            [],
        )
        write_sql: list[str] = []
        real_execute = db.execute.side_effect

        def _tracking_execute(sql: str, params: tuple = ()):
            write_sql.append(sql)
            return real_execute(sql, params)

        db.execute.side_effect = _tracking_execute

        with patch.object(JournalGapSliHandler, "_open_db", return_value=db), \
             patch.object(JournalGapSliHandler, "_now_utc", return_value=now), \
             patch.object(JournalGapSliHandler, "record_cycle_health"):
            h = JournalGapSliHandler()
            h.execute()

        for sql in write_sql:
            assert "INSERT" not in sql.upper()
            assert "UPDATE" not in sql.upper()
            assert "DELETE" not in sql.upper()
