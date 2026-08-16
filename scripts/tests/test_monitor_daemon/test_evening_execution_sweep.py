#!/usr/bin/env python3
"""REL-012 — Evening execution sweep (R-011). Red/Green TDD.

Fills outside RTH + journal_sync's 15-min post-close grace (outsideRth
GTC fills, manual TWS trades, late busts/corrections) reach neither
journal_sync (next morning's fresh IB session no longer returns them)
nor executed_orders (orders-sync is market-hours-gated). The sweep runs
once per ET trading day ~20:30 ET, pulls the evening session's fills
and imports any exec_id not yet in the journal via JournalSyncHandler's
machinery, mirroring executions into executed_orders.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.base import et_session_date  # noqa: E402
from monitor_daemon.handlers.evening_execution_sweep import (  # noqa: E402
    EveningExecutionSweepHandler,
)

ET = ZoneInfo("America/New_York")


def _et_to_utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def _recent_after_hours_fill_time() -> datetime:
    """Window-relative: an after-hours execution a few hours ago (never a
    hardcoded date — house rule, feedback_window_relative_test_dates)."""
    return datetime.now(timezone.utc) - timedelta(hours=3)


def _fill(*, exec_id: str, symbol: str = "URTY", side: str = "SLD", shares: int = 10,
          price: float = 42.5, when: datetime | None = None) -> SimpleNamespace:
    """Plain-attribute fake fill: journal import AND the executed_orders
    serializer (ib_orders.fetch_executed_orders) both walk real values."""
    execution = SimpleNamespace(
        execId=exec_id,
        acctNumber="U000TEST",
        permId=900100,
        orderId=77,
        clientId=31,
        orderRef="",
        side=side,
        shares=shares,
        price=price,
        avgPrice=price,
        cumQty=shares,
        time=when or _recent_after_hours_fill_time(),
        exchange="ISLAND",
    )
    contract = SimpleNamespace(
        symbol=symbol,
        secType="STK",
        strike=None,
        right=None,
        lastTradeDateOrContractMonth="",
        conId=123456,
        currency="USD",
        multiplier=None,
        localSymbol=symbol,
        tradingClass=symbol,
        comboLegs=None,
    )
    commission = SimpleNamespace(commission=1.0, currency="USD", realizedPNL=None)
    return SimpleNamespace(execution=execution, contract=contract, commissionReport=commission)


class _FakeJournalDb:
    """Journal + executed_orders stand-in for the sweep's DB reads.

    Dispatches on SQL shape: the sweep walks two different journal
    projections — the 4-column loader (_load_existing_from_journal) and the
    single-column coverage scan the carried-over gap recovery uses.

    ``rows`` are (trade_id, payload, filled_at, written_at) tuples.
    """

    def __init__(
        self,
        rows: list[tuple] | None = None,
        *,
        executed_rows: list[tuple] | None = None,
    ):
        self.rows = rows or []
        self.executed_rows = executed_rows or []

    def execute(self, sql, params=None):
        cursor = MagicMock()
        if "FROM executed_orders" in sql:
            cursor.fetchall.return_value = self.executed_rows
        elif "SELECT payload FROM journal" in sql:
            cursor.fetchall.return_value = [(r[1],) for r in self.rows]
        elif "FROM journal" in sql:
            cursor.fetchall.return_value = self.rows
        else:
            cursor.fetchall.return_value = []
        return cursor


def _connected_client(fills):
    client = MagicMock()
    client.get_fills.return_value = fills
    return client


@pytest.fixture
def captured_journal_upserts(monkeypatch):
    """Capture journal writes through journal_sync's import machinery."""
    import monitor_daemon.handlers.journal_sync as js_mod

    upserts = MagicMock(name="upsert_journal_entry")
    monkeypatch.setattr(js_mod, "upsert_journal_entry", upserts)
    return upserts


class TestIdentity:
    def test_wiring(self):
        h = EveningExecutionSweepHandler()
        assert h.name == "evening_execution_sweep"
        assert h.service_name == "execution-sweep"
        assert h.requires_market_hours is False
        assert h.client_id == "auto"


class TestDailyFireWindow:
    """Once per ET trading day, at/after 20:30 ET (post IB evening processing)."""

    def _at(self, handler, now_utc):
        return patch(
            "monitor_daemon.handlers.evening_execution_sweep._now_utc",
            return_value=now_utc,
        )

    def test_fires_at_20_45_et_on_a_trading_day(self):
        h = EveningExecutionSweepHandler()
        with self._at(h, _et_to_utc(2026, 5, 11, 20, 45)):  # Monday
            assert h.is_due() is True

    def test_does_not_fire_before_20_30_et(self):
        h = EveningExecutionSweepHandler()
        with self._at(h, _et_to_utc(2026, 5, 11, 20, 15)):
            assert h.is_due() is False

    def test_does_not_fire_on_saturday(self):
        h = EveningExecutionSweepHandler()
        with self._at(h, _et_to_utc(2026, 5, 9, 20, 45)):
            assert h.is_due() is False

    def test_does_not_refire_same_et_day(self):
        h = EveningExecutionSweepHandler()
        h.last_run = _et_to_utc(2026, 5, 11, 20, 35)
        with self._at(h, _et_to_utc(2026, 5, 11, 21, 30)):
            assert h.is_due() is False

    def test_late_fire_when_yesterday_missed(self):
        h = EveningExecutionSweepHandler()
        h.last_run = _et_to_utc(2026, 5, 8, 20, 35)  # Friday
        with self._at(h, _et_to_utc(2026, 5, 11, 23, 30)):  # Monday late
            assert h.is_due() is True


class TestSweepImport:
    def test_imports_missing_after_hours_execution(self, captured_journal_upserts):
        fill_time = _recent_after_hours_fill_time()
        fill = _fill(exec_id="0001.EVE.01", side="BOT", when=fill_time)
        h = EveningExecutionSweepHandler()
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=_connected_client([fill]),
        ), patch.object(
            EveningExecutionSweepHandler, "_open_journal_db",
            return_value=_FakeJournalDb(),
        ), patch.object(
            EveningExecutionSweepHandler, "_fetch_executed_rows", return_value=[],
        ):
            result = h.execute()

        assert result["imported"] == 1
        assert captured_journal_upserts.call_count == 1
        exec_id, entry = captured_journal_upserts.call_args[0][:2]
        assert exec_id == "0001.EVE.01"
        assert entry["ib_exec_id"] == "0001.EVE.01"
        # The handler dates a row by the ET SESSION DATE OF THE FILL
        # (journal_sync -> et_session_date), not by "now". Comparing against
        # now is deterministically wrong whenever ET local time is 00:00-02:59
        # — the fill is 3h back, so it sits on the previous ET day — and the
        # window widens on the spring-forward Sunday.
        assert entry["date"] == et_session_date(fill_time)

    def test_already_imported_exec_ids_are_skipped(self, captured_journal_upserts):
        """Idempotent: a re-run over the same evening's fills writes nothing."""
        fill = _fill(exec_id="0001.EVE.01")
        seeded_row = (
            "0001.EVE.01",
            '{"ib_exec_id": "0001.EVE.01", "ticker": "URTY", "action": "SELL"}',
            _recent_after_hours_fill_time().isoformat(),
            _recent_after_hours_fill_time().isoformat(),
        )
        h = EveningExecutionSweepHandler()
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=_connected_client([fill]),
        ), patch.object(
            EveningExecutionSweepHandler, "_open_journal_db",
            return_value=_FakeJournalDb([seeded_row]),
        ), patch.object(
            EveningExecutionSweepHandler, "_fetch_executed_rows", return_value=[],
        ):
            result = h.execute()

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert captured_journal_upserts.call_count == 0

    def test_mirrors_executions_into_executed_orders(self, captured_journal_upserts, monkeypatch):
        import monitor_daemon.handlers.evening_execution_sweep as sweep_mod

        mirrored = MagicMock(name="upsert_executed_order")
        monkeypatch.setattr(sweep_mod, "upsert_executed_order", mirrored)

        fill = _fill(exec_id="0001.EVE.02", side="SLD")
        h = EveningExecutionSweepHandler()
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=_connected_client([fill]),
        ), patch.object(
            EveningExecutionSweepHandler, "_open_journal_db",
            return_value=_FakeJournalDb(),
        ):
            result = h.execute()

        assert result["executed_mirrored"] == 1
        assert mirrored.call_count == 1
        assert mirrored.call_args[0][0] == "0001.EVE.02"


def _executed_order_row(
    *,
    exec_id: str,
    symbol: str = "IWM",
    side: str = "SLD",
    quantity: float = 500.0,
    price: float = 302.92,
    when: datetime,
) -> tuple:
    """An executed_orders row as orders-sync / the sweep mirror writes it."""
    payload = {
        "execId": exec_id,
        "permId": 900200,
        "orderId": 295,
        "clientId": 29,
        "symbol": symbol,
        "contract": {
            "conId": 9579970,
            "symbol": symbol,
            "secType": "STK",
            "currency": "USD",
            "multiplier": 1,
            "localSymbol": symbol,
            "tradingClass": symbol,
            "strike": 0.0,
            "right": "?",
            "expiry": None,
        },
        "side": side,
        "quantity": quantity,
        "price": price,
        "avgPrice": price,
        "cumQty": quantity,
        "commission": 6.57,
        "time": when.isoformat(),
    }
    fill_time = when.isoformat()
    return (exec_id, json.dumps(payload), fill_time)


class TestCarriedOverGapRecovery:
    """A day whose sweep never succeeded leaves after-hours fills orphaned.

    The live evening session only exposes the CURRENT day's executions, so a
    later sweep can never re-pull them from IB. executed_orders holds the
    durable payload, so the sweep must close residual gaps from Turso —
    otherwise journal-gap-sli pages forever until a human runs
    scripts/backfill_journal_from_executed_orders.py by hand.

    Incident 20260813T140500Z: IWM perm 1805918121 filled 16:24:50 ET on
    2026-08-12 (past journal_sync's 15-min post-close grace); that evening's
    sweep burned its 3-attempt budget on an unreachable gateway.
    """

    @pytest.fixture
    def captured_backfill_upserts(self, monkeypatch):
        """The recovery path writes through db.writer, not journal_sync."""
        import db.writer as writer_mod

        upserts = MagicMock(name="upsert_journal_entry")
        monkeypatch.setattr(writer_mod, "upsert_journal_entry", upserts)
        return upserts

    def test_recovers_prior_day_gap_the_session_no_longer_returns(
        self, captured_journal_upserts, captured_backfill_upserts
    ):
        """Yesterday's orphaned fill is journaled from executed_orders."""
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        orphan = _executed_order_row(exec_id="00010129.6a7d0497.01.01", when=yesterday)

        h = EveningExecutionSweepHandler()
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            # Today's evening session: yesterday's execution is NOT in it.
            return_value=_connected_client([]),
        ), patch.object(
            EveningExecutionSweepHandler,
            "_open_journal_db",
            return_value=_FakeJournalDb(executed_rows=[orphan]),
        ), patch.object(
            EveningExecutionSweepHandler, "_fetch_executed_rows", return_value=[],
        ):
            result = h.execute()

        assert result["gaps_recovered"] == 1
        assert captured_backfill_upserts.call_count == 1
        trade_id = captured_backfill_upserts.call_args[0][0]
        assert trade_id == "00010129.6a7d0497.01.01"

    def test_already_journaled_gap_is_not_rewritten(
        self, captured_journal_upserts, captured_backfill_upserts
    ):
        """Idempotent: an exec_id already covered in journal is left alone."""
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        covered = _executed_order_row(exec_id="00010129.6a7d0497.01.01", when=yesterday)

        h = EveningExecutionSweepHandler()
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=_connected_client([]),
        ), patch.object(
            EveningExecutionSweepHandler,
            "_open_journal_db",
            return_value=_FakeJournalDb(
                [(
                    "00010129.6a7d0497.01.01",
                    '{"ib_exec_id": "00010129.6a7d0497.01.01"}',
                    None,
                    None,
                )],
                executed_rows=[covered],
            ),
        ), patch.object(
            EveningExecutionSweepHandler, "_fetch_executed_rows", return_value=[],
        ):
            result = h.execute()

        assert result["gaps_recovered"] == 0
        assert captured_backfill_upserts.call_count == 0


class TestSoftFailure:
    """IB/DB unavailability raises so BaseHandler never latches last_run
    (feedback_dont_latch_last_run_on_soft_failure)."""

    def test_ib_connect_failure_raises(self):
        h = EveningExecutionSweepHandler()
        broken = MagicMock()
        broken.connect.side_effect = ConnectionRefusedError("gateway down")
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=broken,
        ):
            with pytest.raises(Exception, match="gateway down"):
                h.execute()

    def test_journal_db_unavailable_raises(self):
        h = EveningExecutionSweepHandler()
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=_connected_client([_fill(exec_id="0001.EVE.03")]),
        ), patch.object(
            EveningExecutionSweepHandler, "_open_journal_db", return_value=None,
        ), patch.object(
            EveningExecutionSweepHandler, "_fetch_executed_rows", return_value=[],
        ):
            with pytest.raises(Exception):
                h.execute()

    def test_failure_sets_short_embargo_not_daily_burn(self):
        """A failed attempt embargoes ~5 min (record_soft_failure), and the
        handler stays due again afterwards the same ET day."""
        h = EveningExecutionSweepHandler()
        now = _et_to_utc(2026, 5, 11, 20, 45)
        broken = MagicMock()
        broken.connect.side_effect = ConnectionRefusedError("gateway down")
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep._now_utc",
            return_value=now,
        ), patch(
            "monitor_daemon.handlers.evening_execution_sweep.IBClient",
            return_value=broken,
        ):
            with pytest.raises(Exception):
                h.execute()
            assert h.is_due() is False  # inside the 5-min embargo

        later = now + timedelta(minutes=6)
        with patch(
            "monitor_daemon.handlers.evening_execution_sweep._now_utc",
            return_value=later,
        ):
            assert h.is_due() is True


class TestRegistration:
    def test_registered_in_create_daemon(self, tmp_path: Path):
        with patch("monitor_daemon.run.STATE_FILE", tmp_path / "daemon_state.json"):
            from monitor_daemon.run import create_daemon

            daemon = create_daemon()
        names = {h.name for h in daemon.handlers}
        assert "evening_execution_sweep" in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
