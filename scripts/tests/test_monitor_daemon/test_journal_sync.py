#!/usr/bin/env python3
"""
Tests for monitor_daemon JournalSyncHandler — Red/Green TDD.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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


class TestTradeLogRecovery:
    """Recover a corrupted disk mirror without losing DB or disk-only rows."""

    def _write_bad_checksum(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            json.dumps({"trades": rows, "_checksum": "definitely-wrong"}, indent=2),
            encoding="utf-8",
        )

    def _fake_db(self, journal_rows: list[tuple[str, dict, str]]):
        recovery_rows = [
            (trade_id, json.dumps(payload), filled_at, filled_at)
            for trade_id, payload, filled_at in journal_rows
        ]
        coverage_rows = [(trade_id,) for trade_id, _payload, _filled_at in journal_rows]

        def execute(sql, *args, **kwargs):
            result = MagicMock(spec=["fetchall"])
            statement = " ".join(str(sql).split())
            if "SELECT trade_id, payload, filled_at, written_at FROM journal" in statement:
                result.fetchall.return_value = recovery_rows
            elif "SELECT trade_id FROM journal" in statement:
                result.fetchall.return_value = coverage_rows
            else:
                result.fetchall.return_value = []
            return result

        db = MagicMock()
        db.execute.side_effect = execute
        return db

    def _run_with_fills(self, trade_log_path: Path, db, fills: list | None = None):
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = fills or []
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            return handler.execute()

    def test_checksum_mismatch_rebuilds_from_journal_and_repairs_mirror(self, trade_log_path):
        self._write_bad_checksum(trade_log_path, [])
        journal_payload = {
            "date": "2026-06-23",
            "ticker": "AAPL",
            "action": "BUY",
            "shares": 10,
        }
        db = self._fake_db([
            ("RECOVER-1", journal_payload, "2026-06-23T15:10:00Z"),
        ])

        result = self._run_with_fills(trade_log_path, db)

        assert "error" not in result
        assert result["recovered_trade_log_from_journal"] == 1
        assert result["salvaged_trade_log_rows"] == 0
        assert list(trade_log_path.parent.glob("trade_log.json.corrupt.*"))

        loaded = verified_load(str(trade_log_path))
        assert loaded["trades"] == [
            {
                "id": 1,
                "date": "2026-06-23",
                "ticker": "AAPL",
                "action": "BUY",
                "shares": 10,
                "ib_exec_id": "RECOVER-1",
                "filled_at": "2026-06-23T15:10:00Z",
            }
        ]

    def test_checksum_mismatch_salvages_disk_only_rows_for_db_reconcile(self, trade_log_path):
        recent_date = datetime.now().strftime("%Y-%m-%d")
        disk_only_row = {
            "id": 99,
            "date": recent_date,
            "ticker": "MU",
            "action": "SELL_TO_OPEN",
            "ib_exec_id": "DISK-ONLY-1",
            "contracts": 1,
        }
        self._write_bad_checksum(trade_log_path, [disk_only_row])

        db = self._fake_db([
            (
                "JOURNAL-1",
                {
                    "date": recent_date,
                    "ticker": "AAPL",
                    "action": "BUY",
                    "shares": 10,
                },
                f"{recent_date}T15:10:00Z",
            ),
        ])
        upserted: list[str] = []

        def fake_upsert(trade_id, entry, filled_at):
            upserted.append(trade_id)

        with patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert):
            result = self._run_with_fills(trade_log_path, db)

        assert "error" not in result
        assert result["recovered_trade_log_from_journal"] == 1
        assert result["salvaged_trade_log_rows"] == 1
        assert result["reconciled"] == 1
        assert upserted == ["DISK-ONLY-1"]

        loaded = verified_load(str(trade_log_path))
        exec_ids = {row.get("ib_exec_id") for row in loaded["trades"]}
        assert exec_ids == {"JOURNAL-1", "DISK-ONLY-1"}

    def test_checksum_mismatch_without_journal_rows_remains_error(self, trade_log_path):
        self._write_bad_checksum(
            trade_log_path,
            [
                {
                    "id": 1,
                    "date": "2026-06-23",
                    "ticker": "AAPL",
                    "action": "BUY",
                    "ib_exec_id": "UNTRUSTED-DISK",
                }
            ],
        )
        db = self._fake_db([])

        result = self._run_with_fills(trade_log_path, db)

        assert result["error"].startswith("trade_log read failed:")
        assert "journal table is empty" in result["error"]
        with pytest.raises(ValueError, match="Checksum mismatch"):
            verified_load(str(trade_log_path))

    def test_recovered_journal_rows_dedupe_live_fills(self, trade_log_path):
        self._write_bad_checksum(trade_log_path, [])
        db = self._fake_db([
            (
                "DUP-RECOVER",
                {
                    "date": "2026-06-23",
                    "ticker": "AAPL",
                    "action": "BUY",
                    "shares": 10,
                },
                "2026-06-23T15:10:00Z",
            ),
        ])
        duplicate_fill = _mock_fill(
            exec_id="DUP-RECOVER",
            symbol="AAPL",
            side="BOT",
            shares=10,
            price=200.0,
        )

        with patch.object(JournalSyncHandler, "_lookup_prior_qty", return_value=0.0):
            result = self._run_with_fills(trade_log_path, db, [duplicate_fill])

        assert result["imported"] == 0
        assert result["skipped"] == 1
        loaded = verified_load(str(trade_log_path))
        assert [row["ib_exec_id"] for row in loaded["trades"]] == ["DUP-RECOVER"]


class TestEtSessionDates:
    """T-023: journal dates derived from a bare strftime on the UTC exec
    time stamp fills after ~20:00 ET onto the NEXT calendar day, shifting
    the journal_basis cutoff (retroactive label flips) and same-day P&L."""

    def _import_one(self, when, trade_log_path):
        fill = _mock_fill(
            exec_id=f"ET-{when.isoformat()}",
            symbol="USAX",
            side="BOT",
            shares=10,
            price=1.0,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
            when=when,
        )
        empty_db = MagicMock()
        empty_result = MagicMock(spec=["fetchall"])
        empty_result.fetchall.return_value = []
        empty_db.execute.return_value = empty_result
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=empty_db):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = [fill]
            mock_cls.return_value = mock_client
            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            handler.execute()
        loaded = verified_load(str(trade_log_path))
        return loaded["trades"][-1]

    def test_evening_utc_fill_lands_on_the_et_session_date(self, trade_log_path):
        # 2026-06-26 00:30 UTC == 2026-06-25 20:30 EDT — the ET session date
        # is the 25th; the bare-strftime bug wrote the 26th.
        when = datetime(2026, 6, 26, 0, 30, tzinfo=timezone.utc)
        row = self._import_one(when, trade_log_path)
        assert row["date"] == "2026-06-25", f"got {row['date']}"

    def test_dst_transition_evening_fill_uses_est(self, trade_log_path):
        # DST ended 2026-11-01 06:00Z; 2026-11-02 00:30 UTC == 19:30 EST on
        # 2026-11-01.
        when = datetime(2026, 11, 2, 0, 30, tzinfo=timezone.utc)
        row = self._import_one(when, trade_log_path)
        assert row["date"] == "2026-11-01", f"got {row['date']}"

    def test_naive_mid_session_time_is_unchanged(self, trade_log_path):
        row = self._import_one(datetime(2026, 4, 25, 10, 30, 0), trade_log_path)
        assert row["date"] == "2026-04-25"


class TestProductionNoMirrorConfig:
    """T-015: production wires JournalSyncHandler() with trade_log_path=None
    (monitor_daemon/run.py) — a configuration no execute()-level test used.
    Dedupe sources from the journal TABLE, the reconcile step is skipped
    (reconciled == 0), and the journal read is the cycle's hard dependency."""

    @staticmethod
    def _journal_db(rows):
        """Fake DB dispatching on the two SELECT shapes the prod path issues:
        the 4-column trade-log-recovery read and the prior-qty lookup."""
        db = MagicMock()

        def execute(sql, args=()):
            result = MagicMock(spec=["fetchall"])
            if "trade_id, payload, filled_at, written_at" in sql:
                result.fetchall.return_value = rows
            else:
                result.fetchall.return_value = []
            return result

        db.execute.side_effect = execute
        return db

    @staticmethod
    def _journal_row(exec_id, ticker="USAX"):
        payload = {
            "ticker": ticker,
            "action": "BUY_OPTION",
            "contracts": 10,
            "total_cost": 1000.0,
            "ib_exec_id": exec_id,
            "date": "2026-04-20",
        }
        return (exec_id, json.dumps(payload), "2026-04-20T14:00:00Z", "2026-04-20T14:00:01Z")

    def _run(self, db, fills, upsert_mock):
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", upsert_mock):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = fills
            mock_cls.return_value = mock_client
            handler = JournalSyncHandler()  # trade_log_path=None — prod wiring
            return handler.execute(), handler

    def test_prod_dedupe_sources_from_journal_table(self):
        db = self._journal_db([self._journal_row("SEEN-1")])
        fills = [
            _mock_fill(exec_id="SEEN-1", symbol="USAX", side="BOT", shares=10, price=1.0),
            _mock_fill(exec_id="NEW-1", symbol="MU", side="BOT", shares=5, price=2.0),
        ]
        upsert = MagicMock()

        result, _ = self._run(db, fills, upsert)

        assert result.get("error") is None
        assert result["imported"] == 1
        assert result["reconciled"] == 0
        upserted = [str(c.args[0]) for c in upsert.call_args_list]
        assert upserted == ["NEW-1"]

    def test_prod_empty_journal_still_imports_first_fill(self):
        """An EMPTY journal is a legitimate state (fresh host), not a read
        failure — the importer must not brick itself on it."""
        db = self._journal_db([])
        fills = [_mock_fill(exec_id="FIRST-1", symbol="USAX", side="BOT", shares=10, price=1.0)]
        upsert = MagicMock()

        result, _ = self._run(db, fills, upsert)

        assert result.get("error") is None, f"cycle errored: {result.get('error')}"
        assert result["imported"] == 1
        assert [str(c.args[0]) for c in upsert.call_args_list] == ["FIRST-1"]

    def test_prod_journal_read_failure_aborts_without_upserts(self):
        db = MagicMock()
        db.execute.side_effect = RuntimeError("hrana: upstream forward failed")
        fills = [_mock_fill(exec_id="X-1", symbol="USAX", side="BOT", shares=10, price=1.0)]
        upsert = MagicMock()

        result, _ = self._run(db, fills, upsert)

        assert str(result.get("error", "")).startswith("journal read failed:")
        upsert.assert_not_called()  # importing without dedupe would double-write

    def test_prod_failed_upsert_is_retried_next_cycle(self):
        db = self._journal_db([])
        fills = [_mock_fill(exec_id="RETRY-1", symbol="USAX", side="BOT", shares=10, price=1.0)]
        upsert = MagicMock(side_effect=[RuntimeError("hrana: stream not found"), None])

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = fills
            mock_cls.return_value = mock_client
            handler = JournalSyncHandler()
            handler.execute()   # upsert raises (swallowed by _dual_write)
            handler.execute()   # fill still in the session window — re-derived

        assert upsert.call_count == 2
        assert str(upsert.call_args_list[1].args[0]) == "RETRY-1"


class TestOutOfOrderFillLabeling:
    """T-014: fills labelled in DELIVERY order, not execution-time order.

    IB can replay executions out of chronological order (corrections after
    a reconnect, cross-session replay). Processing a closing SELL before
    its own opening BUY sees prior_qty=0 and writes SELL_TO_OPEN — the
    phantom-short mislabel. Both sibling paths sort by execution time
    (journal_rehydrate.py, backfill_journal_from_executed_orders.py); the
    real-time handler must too.
    """

    def test_reverse_chronological_delivery_still_labels_close_correctly(self, trade_log_path):
        buy_open = _mock_fill(
            exec_id="OOO-BUY",
            symbol="USAX",
            side="BOT",
            shares=65,
            price=1.02,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
            when=datetime(2026, 5, 22, 14, 0, 0),
        )
        sell_close = _mock_fill(
            exec_id="OOO-SELL",
            symbol="USAX",
            side="SLD",
            shares=65,
            price=4.0,
            sec_type="OPT",
            strike=45.0,
            right="C",
            expiry="20260619",
            when=datetime(2026, 5, 22, 14, 5, 0),
        )

        empty_db = MagicMock()
        empty_result = MagicMock(spec=["fetchall"])
        empty_result.fetchall.return_value = []
        empty_db.execute.return_value = empty_result

        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=empty_db):
            mock_client = MagicMock()
            # DELIVERY order is reversed: the 14:05 close arrives first.
            mock_client.get_fills.return_value = [sell_close, buy_open]
            mock_cls.return_value = mock_client

            handler = JournalSyncHandler(trade_log_path=trade_log_path)
            result = handler.execute()

        assert result["imported"] == 2
        loaded = verified_load(str(trade_log_path))
        by_exec = {t["ib_exec_id"]: t for t in loaded["trades"] if t["ib_exec_id"] in ("OOO-BUY", "OOO-SELL")}
        assert by_exec["OOO-BUY"]["action"] == "BUY_OPTION"
        assert by_exec["OOO-SELL"]["action"] == "SELL_OPTION", (
            f"got {by_exec['OOO-SELL']['action']} — the 14:05 SELL was labelled "
            "against prior_qty=0 because the 14:00 BUY had not been walked yet "
            "(delivery-order labelling; fills must sort by execution time)"
        )


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

    def test_short_cover_buy_labels_as_buy_to_close(self, trade_log_path):
        """Covering a short with a BUY must label BUY_TO_CLOSE so the blotter
        marks the cover as closed (not a phantom open long)."""
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
        assert new_row["action"] == "BUY_TO_CLOSE"
        assert new_row["structure"].startswith("Closed")

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


class TestExpiryNormalization:
    """Journal option rows must carry compact YYYYMMDD expiry.

    fromJournal.ts keys lot-matching on the raw expiry string, so an ISO
    "2026-06-26" row silently splits a position from its "20260626"
    counterpart. 22 ISO rows were hand-normalized in prod on 2026-07-02;
    _fill_to_entry is the ingest chokepoint that must compact them.
    """

    def test_fill_to_entry_normalizes_iso_expiry_to_compact(self, trade_log_path):
        fill = _mock_fill(
            exec_id="ISO-EXPIRY",
            symbol="META",
            side="SLD",
            shares=5,
            price=1.74,
            sec_type="OPT",
            strike=625.0,
            right="C",
            expiry="2026-06-26",
            when=datetime(2026, 6, 25, 15, 59, 0),
        )

        handler = JournalSyncHandler(trade_log_path=trade_log_path)
        entry = handler._fill_to_entry(fill, next_id=1, prior_qty=0.0)

        assert entry is not None
        assert entry["expiry"] == "20260626"

    def test_fill_to_entry_keeps_compact_expiry_unchanged(self, trade_log_path):
        fill = _mock_fill(
            exec_id="COMPACT-EXPIRY",
            symbol="META",
            side="SLD",
            shares=5,
            price=1.74,
            sec_type="OPT",
            strike=625.0,
            right="C",
            expiry="20260626",
            when=datetime(2026, 6, 25, 15, 59, 0),
        )

        handler = JournalSyncHandler(trade_log_path=trade_log_path)
        entry = handler._fill_to_entry(fill, next_id=1, prior_qty=0.0)

        assert entry is not None
        assert entry["expiry"] == "20260626"


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


# ---------------------------------------------------------------------------
# T-035 — APPEND this class to
# scripts/tests/test_monitor_daemon/test_journal_sync.py
# (uses the module's existing `_mock_fill` helper + `trade_log_path` fixture)
# ---------------------------------------------------------------------------


class TestCorrectionSuffixSupersede:
    """T-035: a corrected execution REPLACES its original, never appends.

    IB re-delivers a corrected execution under the same exec-id root with the
    trailing two-digit segment incremented (`…01.01` → `…01.02`). Deduping on
    exact string equality imported the correction as an ADDITIONAL row, so the
    contract's quantity and cost were counted twice — the journal showed 3 + 5
    contracts for one 5-contract fill. Distinct executions differ in the
    segments BEFORE the counter (`…69f202e9.02.01` vs `…69f202e9.03.01` are two
    real fills of one order), so only the final segment may be collapsed.
    Same convention as web/lib/journal/realizedPnl.ts:stripCorrectionSuffix.
    """

    ROOT = "0002920b.6a19d5a9.01"
    ORIGINAL = f"{ROOT}.01"
    CORRECTED = f"{ROOT}.02"

    # -- doubles -----------------------------------------------------------

    def _journal_row(self, exec_id: str, contracts: int, **overrides) -> dict:
        row = {
            "id": 1,
            "date": "2026-07-10",
            "ticker": "MU",
            "structure": "Short Call $1050 2026-07-17",
            "decision": "IB_AUTO_IMPORT",
            "action": "SELL_TO_OPEN",
            "contracts": contracts,
            "total_cost": contracts * 11000.0,
            "strike": 1050.0,
            "right": "C",
            "expiry": "20260717",
            "ib_exec_id": exec_id,
        }
        row.update(overrides)
        return row

    def _fake_db(self, rows: list[dict]):
        """Dispatch on the three SELECT shapes journal_sync issues: the
        4-column journal read, the trade_id coverage scan, and the
        prior-qty-per-contract scan."""
        recovery = [
            (r["ib_exec_id"], json.dumps(r), "2026-07-10T18:30:00Z", "2026-07-10T18:30:01Z")
            for r in rows
        ]
        coverage = [(r["ib_exec_id"],) for r in rows]
        prior = [(json.dumps(r), "2026-07-10T18:30:00Z", "2026-07-10T18:30:00Z") for r in rows]

        def execute(sql, *args, **kwargs):
            result = MagicMock(spec=["fetchall"])
            statement = " ".join(str(sql).split())
            if "trade_id, payload, filled_at, written_at" in statement:
                result.fetchall.return_value = recovery
            elif "SELECT trade_id FROM journal" in statement:
                result.fetchall.return_value = coverage
            else:
                result.fetchall.return_value = prior
            return result

        db = MagicMock()
        db.execute.side_effect = execute
        return db

    def _option_fill(self, exec_id: str, shares: int, *, side: str = "SLD", when=None):
        return _mock_fill(
            exec_id=exec_id,
            symbol="MU",
            side=side,
            shares=shares,
            price=110.0,
            sec_type="OPT",
            strike=1050.0,
            right="C",
            expiry="20260717",
            when=when or datetime(2026, 7, 10, 14, 30, 0),
        )

    def _run(self, trade_log_path, journal_rows: list[dict], fills: list, *, mirror=True):
        """One execute() cycle. Returns (result, mirror rows, upsert calls)."""
        upserts: list[tuple] = []

        def fake_upsert(trade_id, entry, filled_at=None):
            upserts.append((str(trade_id), entry))

        if mirror:
            atomic_save(str(trade_log_path), {"trades": [dict(r) for r in journal_rows]})

        db = self._fake_db(journal_rows)
        with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.journal_sync.get_db", return_value=db), \
             patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert):
            mock_client = MagicMock()
            mock_client.get_fills.return_value = fills
            mock_cls.return_value = mock_client
            handler = JournalSyncHandler(trade_log_path=trade_log_path if mirror else None)
            result = handler.execute()

        rows = verified_load(str(trade_log_path))["trades"] if mirror else []
        return result, rows, upserts

    # -- supersede ---------------------------------------------------------

    def test_correction_supersedes_the_imported_original(self, trade_log_path):
        """One row for the root, carrying the CORRECTED contracts."""
        result, rows, upserts = self._run(
            trade_log_path,
            [self._journal_row(self.ORIGINAL, 3)],
            [self._option_fill(self.CORRECTED, 5)],
        )

        for_root = [r for r in rows if str(r["ib_exec_id"]).startswith(self.ROOT)]
        assert len(for_root) == 1, (
            f"correction appended a second row for one execution: {for_root}"
        )
        assert for_root[0]["contracts"] == 5
        assert result["imported"] == 0
        assert result["superseded"] == 1
        assert [trade_id for trade_id, _entry in upserts] == [self.ORIGINAL], (
            "the correction must upsert the SAME trade_id so Turso replaces the row"
        )

    def test_prod_wiring_supersedes_without_a_disk_mirror(self):
        """trade_log_path=None (monitor_daemon/run.py): dedupe + supersede both
        source from the journal TABLE."""
        result, _rows, upserts = self._run(
            None,
            [self._journal_row(self.ORIGINAL, 3)],
            [self._option_fill(self.CORRECTED, 5)],
            mirror=False,
        )

        assert result["superseded"] == 1
        trade_id, entry = upserts[0]
        assert trade_id == self.ORIGINAL
        assert entry["contracts"] == 5
        assert entry["ib_exec_id"] == self.ORIGINAL
        assert entry["ib_exec_id_corrected"] == self.CORRECTED, (
            "the absorbed correction number must be persisted or the next cycle "
            "re-supersedes the same row forever"
        )

    def test_lower_numbered_late_arrival_is_skipped(self, trade_log_path):
        """The pre-correction execution replays after the correction landed."""
        result, rows, upserts = self._run(
            trade_log_path,
            [self._journal_row(self.CORRECTED, 5)],
            [self._option_fill(self.ORIGINAL, 3)],
        )

        assert result["imported"] == 0
        assert result["superseded"] == 0
        assert result["skipped"] == 1
        assert upserts == []
        assert [r["contracts"] for r in rows] == [5], "stale quantity overwrote the correction"

    def test_redelivery_after_a_supersede_is_idempotent(self, trade_log_path):
        """Both ids stay in the session cache all afternoon: the cycle must not
        rewrite the row every 300s."""
        absorbed = self._journal_row(self.ORIGINAL, 5, ib_exec_id_corrected=self.CORRECTED)
        result, rows, upserts = self._run(
            trade_log_path,
            [absorbed],
            [self._option_fill(self.ORIGINAL, 3), self._option_fill(self.CORRECTED, 5)],
        )

        assert (result["imported"], result["superseded"], result["skipped"]) == (0, 0, 2)
        assert upserts == []
        assert [r["contracts"] for r in rows] == [5]

    def test_original_and_correction_in_one_cycle_collapse(self, trade_log_path):
        """Nothing imported yet: the pair must write ONE row, corrected."""
        result, rows, upserts = self._run(
            trade_log_path,
            [],
            [
                self._option_fill(self.ORIGINAL, 3, when=datetime(2026, 7, 10, 14, 30, 0)),
                self._option_fill(self.CORRECTED, 5, when=datetime(2026, 7, 10, 14, 30, 0)),
            ],
        )

        assert len(rows) == 1, f"correction pair wrote {len(rows)} rows"
        assert rows[0]["contracts"] == 5
        assert rows[0]["ib_exec_id"] == self.ORIGINAL
        assert len(upserts) == 1

    def test_corrected_sell_keeps_its_closing_label(self, trade_log_path):
        """A correction is labelled against the position BEFORE the fill it
        replaces. Rebasing wrong reads the original as already-closed and
        relabels the close to SELL_TO_OPEN (the phantom-short class)."""
        opened = {
            "id": 1,
            "date": "2026-07-09",
            "ticker": "MU",
            "structure": "Long Call $1050 2026-07-17",
            "action": "BUY_OPTION",
            "contracts": 25,
            "total_cost": 25000.0,
            "strike": 1050.0,
            "right": "C",
            "expiry": "20260717",
            "ib_exec_id": "OPEN-BUY",
        }
        sold = self._journal_row(self.ORIGINAL, 25, id=2, action="SELL_OPTION")

        _result, rows, _upserts = self._run(
            trade_log_path, [opened, sold], [self._option_fill(self.CORRECTED, 30)]
        )

        corrected = next(r for r in rows if r["ib_exec_id"] == self.ORIGINAL)
        assert corrected["action"] == "SELL_OPTION", (
            f"got {corrected['action']} — the corrected sell was labelled against "
            "a net-flat position instead of the 25-contract long it closes"
        )
        assert corrected["contracts"] == 30

    # -- invariants the root logic must not break --------------------------

    def test_distinct_executions_of_one_order_are_not_collapsed(self, trade_log_path):
        """Real IB shape: the THIRD segment separates executions; only the
        fourth is the correction counter."""
        result, rows, upserts = self._run(
            trade_log_path,
            [],
            [
                self._option_fill("00021ab9.69f202e9.02.01", 2, side="BOT"),
                self._option_fill(
                    "00021ab9.69f202e9.03.01", 4, side="BOT",
                    when=datetime(2026, 7, 10, 14, 31, 0),
                ),
            ],
        )

        assert result["imported"] == 2
        assert sorted(r["contracts"] for r in rows) == [2, 4]
        assert sorted(trade_id for trade_id, _entry in upserts) == [
            "00021ab9.69f202e9.02.01",
            "00021ab9.69f202e9.03.01",
        ]

    def test_composite_exec_id_dedupe_is_unchanged(self, trade_log_path):
        """journal_rehydrate's '+'-joined ids stay opaque: their PARTS are the
        correctable units, so part-wise exact dedupe still governs."""
        composite = {
            "id": 1,
            "date": "2026-07-10",
            "ticker": "WULF",
            "structure": "Long Call $17 2027-01-15",
            "action": "BUY_OPTION",
            "contracts": 77,
            "total_cost": 7700.0,
            "strike": 17.0,
            "right": "C",
            "expiry": "20270115",
            "ib_exec_id": "FILL-A+FILL-B",
        }
        known_part = _mock_fill(
            exec_id="FILL-A", symbol="WULF", side="BOT", shares=8, price=5.2,
            sec_type="OPT", strike=17.0, right="C", expiry="20270115",
        )
        unrelated = _mock_fill(
            exec_id="FILL-C", symbol="WULF", side="BOT", shares=9, price=5.3,
            sec_type="OPT", strike=17.0, right="C", expiry="20270115",
        )

        result, rows, upserts = self._run(trade_log_path, [composite], [known_part])
        assert (result["imported"], result["superseded"]) == (0, 0)
        assert [r["contracts"] for r in rows] == [77]
        assert upserts == []

        result, _rows, _upserts = self._run(trade_log_path, [composite], [unrelated])
        assert result["imported"] == 1
