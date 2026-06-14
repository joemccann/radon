#!/usr/bin/env python3
"""
Tests for monitor_daemon JournalSyncHandler — Red/Green TDD.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.journal_sync import JournalSyncHandler  # noqa: E402
from utils.atomic_io import atomic_save, verified_load  # noqa: E402


def _mock_fill(*, exec_id: str, symbol: str, side: str, shares: int, price: float,
               sec_type: str = "STK", strike: float | None = None, right: str | None = None,
               expiry: str | None = None, commission: float = 1.0,
               when: datetime | None = None) -> MagicMock:
    fill = MagicMock()
    fill.execution = MagicMock()
    fill.execution.execId = exec_id
    fill.execution.side = side
    fill.execution.shares = shares
    fill.execution.price = price
    fill.execution.time = when or datetime(2026, 4, 25, 10, 30, 0)

    fill.contract = MagicMock()
    fill.contract.symbol = symbol
    fill.contract.secType = sec_type
    fill.contract.strike = strike
    fill.contract.right = right
    fill.contract.lastTradeDateOrContractMonth = expiry

    fill.commissionReport = MagicMock()
    fill.commissionReport.commission = commission
    return fill


@pytest.fixture
def trade_log_path(tmp_path: Path) -> Path:
    path = tmp_path / "trade_log.json"
    atomic_save(str(path), {"trades": []})
    return path


class TestJournalSyncHandlerBasics:
    """Identity / wiring."""

    def test_handler_name(self):
        handler = JournalSyncHandler()
        assert handler.name == "journal_sync"

    def test_runs_every_five_minutes(self):
        handler = JournalSyncHandler()
        assert handler.interval_seconds == 300

    def test_requires_market_hours(self):
        handler = JournalSyncHandler()
        assert handler.requires_market_hours is True

    def test_uses_auto_client_id_allocation(self):
        # As of 2026-05-20 the daemon handlers no longer hardcode a
        # daemon-range clientID — they pass `client_id="auto"` to
        # IBClient.connect() which rotates across SUBPROCESS_ID_RANGE
        # (20-49) on every cycle. This survives the half-open-socket
        # case where the prior cycle's clientID is still in CLOSE_WAIT
        # on IB Gateway. See feedback_ib_client_id_ranges.md.
        handler = JournalSyncHandler()
        assert handler.client_id == "auto"


class TestJournalSyncHandlerExecute:
    """The actual append-only sync logic."""

    def test_no_fills_means_no_writes(self, trade_log_path):
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            with patch("monitor_daemon.handlers.journal_sync.atomic_save") as spy:
                result = handler.execute()

            assert result["imported"] == 0
            assert result["fills_seen"] == 0
            assert not spy.called

    def test_appends_new_fills_with_exec_id(self, trade_log_path):
        fill = _mock_fill(
            exec_id="0001.6541ABCD.01",
            symbol="URTY",
            side="BOT",
            shares=2000,
            price=55.997,
        )
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [fill]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert result["imported"] == 1
        loaded = verified_load(str(trade_log_path))
        assert len(loaded["trades"]) == 1
        row = loaded["trades"][0]
        assert row["ticker"] == "URTY"
        assert row["action"] == "BUY"
        assert row["ib_exec_id"] == "0001.6541ABCD.01"
        assert row["shares"] == 2000

    def test_skips_already_logged_fills(self, trade_log_path):
        # Pre-populate with the exec_id we're about to "see" again.
        atomic_save(
            str(trade_log_path),
            {
                "trades": [
                    {
                        "id": 1,
                        "date": "2026-04-25",
                        "ticker": "URTY",
                        "structure": "Long Stock (STK)",
                        "decision": "IB_AUTO_IMPORT",
                        "action": "BUY",
                        "ib_exec_id": "0001.DUP.01",
                        "shares": 2000,
                    }
                ]
            },
        )

        fill = _mock_fill(
            exec_id="0001.DUP.01",
            symbol="URTY",
            side="BOT",
            shares=2000,
            price=55.997,
        )
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [fill]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            with patch("monitor_daemon.handlers.journal_sync.atomic_save") as spy:
                result = handler.execute()

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert not spy.called

    def test_handles_option_fills(self, trade_log_path):
        fill = _mock_fill(
            exec_id="OPT-FILL-1",
            symbol="EWY",
            side="BOT",
            shares=25,
            price=2.0,
            sec_type="OPT",
            strike=130,
            right="P",
            expiry="20260313",
            commission=7.55,
        )
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [fill]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert result["imported"] == 1
        loaded = verified_load(str(trade_log_path))
        row = loaded["trades"][0]
        assert row["contracts"] == 25
        assert row["right"] == "P"
        assert row["strike"] == 130.0
        assert row["expiry"] == "20260313"
        assert "Put" in row["structure"]
        assert "$130" in row["structure"]

    def test_ib_failure_does_not_corrupt_log(self, trade_log_path):
        # Pre-load existing trade so we can verify it survives.
        atomic_save(
            str(trade_log_path),
            {"trades": [{"id": 99, "date": "2025-01-01", "ticker": "AAPL"}]},
        )
        before = verified_load(str(trade_log_path))

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.connect.side_effect = ConnectionError("Gateway down")
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert "error" in result
        after = verified_load(str(trade_log_path))
        assert before == after

    def test_disconnects_after_execution(self, trade_log_path):
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()

            mock_client.disconnect.assert_called()

    def test_dedupe_against_composite_exec_id(self, trade_log_path):
        # journal_rehydrate.py composes exec_ids with '+' for multi-fill orders.
        # The daemon must respect that join when comparing single-fill execs.
        atomic_save(
            str(trade_log_path),
            {
                "trades": [
                    {
                        "id": 1,
                        "date": "2026-04-25",
                        "ticker": "WULF",
                        "structure": "Long Call $17 2027-01-15",
                        "decision": "IB_AUTO_IMPORT",
                        "action": "BUY_OPTION",
                        "ib_exec_id": "FILL-A+FILL-B",
                        "contracts": 77,
                    }
                ]
            },
        )

        partial_fill = _mock_fill(
            exec_id="FILL-A",
            symbol="WULF",
            side="BOT",
            shares=8,
            price=5.20,
            sec_type="OPT",
            strike=17,
            right="C",
            expiry="20270115",
        )
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [partial_fill]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert result["imported"] == 0
        assert result["skipped"] == 1


class TestSellCloseLabeling:
    """Regression for 2026-05-22 SELL_TO_OPEN mislabel bug.

    Companion to ``test_sell_closing_prior_long_labels_as_sell_option`` in
    test_journal_rehydrate.py. ``fromJournal.ts`` treats SELL_TO_OPEN as
    opening (isOpen=true, net_quantity=-qty — phantom new short) and
    SELL_OPTION as closing (isOpen=false, net_quantity=0). The real-time
    handler must pick the right label by looking up the contract's prior
    signed position from the journal.
    """

    def _patch_get_db(self, db_mock):
        """Patch get_db to return the supplied mock DB."""
        return patch("monitor_daemon.handlers.journal_sync.get_db", return_value=db_mock)

    def _fake_db(self, rows):
        # Real libsql cursors expose fetchall(), NOT .rows (CTA-01): keep the
        # fake driver-faithful so a .rows regression fails here.
        result = MagicMock(spec=["fetchall"])
        result.fetchall.return_value = rows
        db = MagicMock()
        db.execute.return_value = result
        return db

    def _row(self, payload: dict, filled_at: str = "2026-04-15T10:00:00Z") -> tuple:
        # Driver-faithful tuple in SELECT order (payload, filled_at,
        # written_at) — real libsql rows are tuples, not dicts (CTA-01).
        return (json.dumps(payload), filled_at, filled_at)

    def test_sell_closing_prior_long_labels_as_sell_option(self, trade_log_path):
        """Prior BUY of 65 USAX $45 calls + today's SELL 65 = SELL_OPTION."""
        prior_rows = [
            self._row(
                {
                    "ticker": "USAX",
                    "action": "BUY_OPTION",
                    "contracts": 65,
                    "total_cost": 6630.0,
                    "right": "C",
                    "strike": 45.0,
                    "expiry": "20260619",
                }
            )
        ]
        db = self._fake_db(prior_rows)

        sell_close = _mock_fill(
            exec_id="CLOSE-SELL",
            symbol="USAX",
            side="SLD",
            shares=65,
            price=4.0,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
            when=datetime(2026, 5, 22, 14, 0, 0),
        )

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             self._patch_get_db(db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [sell_close]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert result["imported"] == 1
        loaded = verified_load(str(trade_log_path))
        new_row = next(t for t in loaded["trades"] if t["ib_exec_id"] == "CLOSE-SELL")
        assert new_row["action"] == "SELL_OPTION", (
            f"Expected SELL_OPTION (close long), got {new_row['action']}. "
            "Mislabel causes fromJournal.ts to mark the position isOpen=true "
            "with net_quantity=-65 (phantom new short) instead of "
            "isOpen=false with net_quantity=0 (correct close)."
        )

    def test_sell_with_no_prior_position_still_labels_sell_to_open(self, trade_log_path):
        """Counterpoint: no prior position → SELL_TO_OPEN stays."""
        db = self._fake_db([])  # empty journal

        sell_to_open = _mock_fill(
            exec_id="SHORT-OPEN",
            symbol="NEWNAME",
            side="SLD",
            shares=10,
            price=2.5,
            sec_type="OPT",
            strike=100.0,
            right="P",
            expiry="20260919",
        )

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             self._patch_get_db(db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [sell_to_open]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert result["imported"] == 1
        loaded = verified_load(str(trade_log_path))
        new_row = next(t for t in loaded["trades"] if t["ib_exec_id"] == "SHORT-OPEN")
        assert new_row["action"] == "SELL_TO_OPEN"

    def test_partial_close_of_long_labels_as_sell_option(self, trade_log_path):
        """Sell some but not all of a long position is still SELL_OPTION.
        prior_qty=65, sell 25 → remaining 40. The 25 sold are closing
        the long, not opening a short. (fromJournal.ts would otherwise
        produce isOpen=true with net_quantity=-25 — phantom short.)"""
        prior_rows = [
            self._row(
                {
                    "ticker": "USAX",
                    "action": "BUY_OPTION",
                    "contracts": 65,
                    "total_cost": 6630.0,
                    "right": "C",
                    "strike": 45.0,
                    "expiry": "20260619",
                }
            )
        ]
        db = self._fake_db(prior_rows)

        partial_close = _mock_fill(
            exec_id="PARTIAL-CLOSE",
            symbol="USAX",
            side="SLD",
            shares=25,
            price=4.0,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
        )

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             self._patch_get_db(db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [partial_close]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()

        loaded = verified_load(str(trade_log_path))
        new_row = next(t for t in loaded["trades"] if t["ib_exec_id"] == "PARTIAL-CLOSE")
        assert new_row["action"] == "SELL_OPTION"

    def test_short_cover_buy_stays_buy_option(self, trade_log_path):
        """Symmetry check: covering a short with a BUY keeps BUY_OPTION.
        fromJournal.ts does not distinguish open-long from cover-short
        on the buy side — both use the BUY/BUY_OPTION label, so this
        is a no-change case. Captures it explicitly to lock the
        behaviour against future "let's add BUY_TO_COVER" refactors."""
        prior_short = [
            self._row(
                {
                    "ticker": "NVDA",
                    "action": "SELL_TO_OPEN",
                    "contracts": 10,
                    "total_cost": 250.0,
                    "right": "P",
                    "strike": 80.0,
                    "expiry": "20260919",
                }
            )
        ]
        db = self._fake_db(prior_short)

        cover_buy = _mock_fill(
            exec_id="COVER-BUY",
            symbol="NVDA",
            side="BOT",
            shares=10,
            price=2.0,
            sec_type="OPT",
            strike=80.0,
            right="P",
            expiry="20260919",
        )

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             self._patch_get_db(db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [cover_buy]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()

        loaded = verified_load(str(trade_log_path))
        new_row = next(t for t in loaded["trades"] if t["ib_exec_id"] == "COVER-BUY")
        assert new_row["action"] == "BUY_OPTION"

    def test_two_sells_in_one_cycle_both_label_sell_option(self, trade_log_path):
        """Within a single execute() cycle the per-contract prior_state
        must mutate as fills are processed, so two sells of the same
        long both see prior_qty > 0 and both label SELL_OPTION.
        Without the in-cycle mutation, the second sell would see the
        DB-derived 65 prior + the first sell's -25 not yet applied,
        and label correctly only by luck."""
        prior_rows = [
            self._row(
                {
                    "ticker": "USAX",
                    "action": "BUY_OPTION",
                    "contracts": 65,
                    "total_cost": 6630.0,
                    "right": "C",
                    "strike": 45.0,
                    "expiry": "20260619",
                }
            )
        ]
        db = self._fake_db(prior_rows)

        sell_a = _mock_fill(
            exec_id="SELL-A",
            symbol="USAX",
            side="SLD",
            shares=25,
            price=4.0,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
            when=datetime(2026, 5, 22, 14, 0, 0),
        )
        sell_b = _mock_fill(
            exec_id="SELL-B",
            symbol="USAX",
            side="SLD",
            shares=25,
            price=4.10,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
            when=datetime(2026, 5, 22, 14, 5, 0),
        )

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             self._patch_get_db(db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [sell_a, sell_b]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()

        loaded = verified_load(str(trade_log_path))
        actions = {t["ib_exec_id"]: t["action"] for t in loaded["trades"] if t["ib_exec_id"] in {"SELL-A", "SELL-B"}}
        assert actions == {"SELL-A": "SELL_OPTION", "SELL-B": "SELL_OPTION"}


class TestReconcileDbMissing:
    """JRN-02: disk-on-DB reconciliation — re-attempts failed Turso upserts.

    The root bug: _dual_write swallows exceptions and disk-dedup then permanently
    skips the row. The fix: every execute() cycle computes the set of exec_ids
    present on disk but absent from the journal table, and retries their upsert.

    These tests are RED until _reconcile_db_missing() is added to journal_sync.py.
    """

    def _fake_db(self, journal_exec_ids: list[str]):
        """Return a mock DB whose journal table contains the given exec_ids."""
        rows = [(json.dumps({"ib_exec_id": eid}),) for eid in journal_exec_ids]
        result = MagicMock(spec=["fetchall"])
        result.fetchall.return_value = rows
        db = MagicMock()
        db.execute.return_value = result
        return db

    def _make_trade_log(self, path: Path, rows: list[dict]) -> None:
        atomic_save(str(path), {"trades": rows})

    def test_reconcile_retries_disk_row_absent_from_journal(self, trade_log_path):
        """A row on disk but not in journal must be re-upserted on next execute()."""
        from datetime import timedelta
        # Window-relative date: a fixed past date rots once it falls outside
        # RECONCILE_WINDOW_DAYS (the original "2026-05-29" failed in CI the day
        # it crossed the 14-day boundary while passing locally on the boundary).
        recent_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        disk_row = {
            "id": 580,
            "date": recent_date,
            "ticker": "MU",
            "structure": "Closed Call $1050 2026-07-17",
            "decision": "IB_AUTO_IMPORT",
            "action": "SELL_TO_OPEN",
            "fill_price": 110.0,
            "total_cost": 33002.7844,
            "commission": 2.7844,
            "ib_exec_id": "0002920b.6a19d5a9.01.01",
            "contracts": 3,
            "strike": 1050.0,
            "right": "C",
            "expiry": "20260717",
        }
        self._make_trade_log(trade_log_path, [disk_row])

        upserted: list[tuple] = []

        def fake_upsert(trade_id, entry, filled_at):
            upserted.append((trade_id, entry, filled_at))

        # DB journal is EMPTY (the upsert previously failed and was swallowed).
        empty_db = self._fake_db([])

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=empty_db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []  # no NEW fills this cycle
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert len(upserted) == 1, (
            "_reconcile_db_missing must upsert the disk row absent from journal. "
            f"Got {len(upserted)} upserts."
        )
        trade_id, entry, filled_at = upserted[0]
        assert trade_id == "0002920b.6a19d5a9.01.01"
        assert entry["ticker"] == "MU"
        assert entry["action"] == "SELL_TO_OPEN"
        assert result.get("reconciled", 0) == 1, (
            "execute() result must include reconciled count"
        )

    def test_reconcile_skips_disk_rows_already_in_journal(self, trade_log_path):
        """Rows whose exec_id is already in journal must NOT be re-upserted."""
        from datetime import timedelta
        # Within-window date so the skip is proven to come from journal-presence,
        # not from the row falling outside RECONCILE_WINDOW_DAYS.
        recent_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        exec_id = "0002920b.6a19d5a9.01.01"
        disk_row = {
            "id": 580,
            "date": recent_date,
            "ticker": "MU",
            "action": "SELL_TO_OPEN",
            "fill_price": 110.0,
            "total_cost": 33002.7844,
            "commission": 2.7844,
            "ib_exec_id": exec_id,
            "contracts": 3,
        }
        self._make_trade_log(trade_log_path, [disk_row])

        upserted: list[tuple] = []

        def fake_upsert(trade_id, entry, filled_at):
            upserted.append((trade_id, entry, filled_at))

        # DB journal already has this exec_id.
        db_with_row = self._fake_db([exec_id])

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=db_with_row), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert len(upserted) == 0, (
            "Row already in journal should not be re-upserted"
        )
        assert result.get("reconciled", 0) == 0

    def test_reconcile_respects_window_days(self, trade_log_path):
        """Disk rows older than RECONCILE_WINDOW_DAYS are skipped by the reconciler."""
        from datetime import timedelta
        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        old_disk_row = {
            "id": 1,
            "date": old_date,
            "ticker": "MU",
            "action": "SELL_TO_OPEN",
            "ib_exec_id": "old.exec.id.far.outside.window",
            "contracts": 1,
        }
        recent_disk_row = {
            "id": 2,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "ticker": "MU",
            "action": "SELL_TO_OPEN",
            "ib_exec_id": "recent.exec.id.inside.window",
            "contracts": 1,
        }
        self._make_trade_log(trade_log_path, [old_disk_row, recent_disk_row])

        upserted_ids: list[str] = []

        def fake_upsert(trade_id, entry, filled_at):
            upserted_ids.append(trade_id)

        empty_db = self._fake_db([])

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=empty_db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()

        assert "old.exec.id.far.outside.window" not in upserted_ids, (
            "Rows older than RECONCILE_WINDOW_DAYS must be skipped"
        )
        assert "recent.exec.id.inside.window" in upserted_ids, (
            "Recent disk-only rows must be reconciled"
        )

    def test_reconcile_does_not_duplicate_new_candidates(self, trade_log_path):
        """A fill that is brand new (not on disk yet) must not appear in reconcile AND candidates."""
        new_fill = _mock_fill(
            exec_id="NEW-FILL-XYZ",
            symbol="AAPL",
            side="BOT",
            shares=100,
            price=200.0,
        )
        upserted_ids: list[str] = []

        def fake_upsert(trade_id, entry, filled_at):
            upserted_ids.append(trade_id)

        empty_db = self._fake_db([])

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=empty_db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [new_fill]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()

        # The new fill goes through the normal candidates path, not reconcile.
        # It must be upserted exactly once.
        assert upserted_ids.count("NEW-FILL-XYZ") == 1, (
            f"New fill must be upserted exactly once. Got: {upserted_ids}"
        )

    def test_reconcile_swallowed_upsert_does_not_abort_cycle(self, trade_log_path):
        """If the reconcile upsert fails, the cycle continues — no exception bubbles up."""
        disk_row = {
            "id": 1,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "ticker": "MU",
            "action": "SELL_TO_OPEN",
            "ib_exec_id": "bad.upsert.exec.id",
            "contracts": 1,
        }
        self._make_trade_log(trade_log_path, [disk_row])

        def exploding_upsert(trade_id, entry, filled_at):
            raise RuntimeError("Turso is down again")

        empty_db = self._fake_db([])

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=empty_db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", exploding_upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()  # must not raise

        assert "error" not in result or "fills" in str(result), (
            "Reconcile upsert failure must be swallowed and logged, not bubbled up"
        )


class TestStructureLabel:
    """`_structure_label` must distinguish open-short from close-long sells.

    A SELL_TO_OPEN (writing a new short call) is a *Short* position, NOT a
    *Closed* one — the old two-branch "Long if BUY else Closed" mislabeled
    every sold-to-open short as "Closed Call".
    """

    def test_sell_to_open_is_short(self):
        s = JournalSyncHandler._structure_label("SELL_TO_OPEN", "OPT", 215, "C", "20260717")
        assert s == "Short Call $215 2026-07-17"

    def test_sell_option_close_long_stays_closed(self):
        s = JournalSyncHandler._structure_label("SELL_OPTION", "OPT", 215, "C", "20260717")
        assert s == "Closed Call $215 2026-07-17"

    def test_buy_option_is_long(self):
        s = JournalSyncHandler._structure_label("BUY_OPTION", "OPT", 1000, "C", "20260612")
        assert s == "Long Call $1000 2026-06-12"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
