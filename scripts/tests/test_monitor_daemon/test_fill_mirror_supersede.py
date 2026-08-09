#!/usr/bin/env python3
"""REL-011 / R-010 — fill_monitor mirror rows must be superseded by execId rows.

fill_monitor mirrors a partial fill inline under a synthetic key
(``fill-monitor:con-{conId}:order-{permId}:{date}:filled-{N}``) so a daemon
restart between detection and the next journal_sync cycle cannot drop the
fill. journal_sync (≤300s later) imports the SAME execution keyed by the
real IB execId. The blotter dedupe (``fromJournal.ts:dedupeJournalRows``)
keys on exec-id parts, so both rows survive — doubled qty/cost.

Contract under test: when journal_sync successfully imports execId rows
covering a fill-monitor mirror's fill count for the same contract + order +
session date, it deletes the mirror. A failed import must NOT delete the
mirror (never destroy the only record of a fill).

Also: fill_monitor must price each increment from the execution delta
(new total cost − prior total cost), not the running avgFillPrice.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers.fill_monitor import FillMonitorHandler  # noqa: E402
from monitor_daemon.handlers.journal_sync import JournalSyncHandler  # noqa: E402

MIRROR_TRADE_ID = "fill-monitor:con-123:order-777:2026-08-07:filled-2"
OTHER_MIRROR_TRADE_ID = "fill-monitor:con-999:order-888:2026-08-07:filled-1"
EXEC_ID = "0001.aa"


def _journal_db(*rows: tuple[str, dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE journal (
            trade_id TEXT PRIMARY KEY,
            payload TEXT,
            filled_at TEXT,
            written_at TEXT
        )
        """
    )
    for trade_id, payload in rows:
        conn.execute(
            "INSERT OR REPLACE INTO journal VALUES (?, ?, ?, ?)",
            (trade_id, json.dumps(payload), "2026-08-07", "2026-08-07T14:31:00Z"),
        )
    return conn


def _mirror_payload(trade_id: str, *, ticker: str = "MU", contracts: int = 2) -> dict:
    return {
        "date": "2026-08-07",
        "ticker": ticker,
        "structure": "Long Call $100 2026-09-18",
        "decision": "FILL_MONITOR_AUTO_IMPORT",
        "action": "BUY_OPTION",
        "fill_price": 12.0,
        "total_cost": contracts * 1200.0,
        "ib_exec_id": trade_id,
        "contracts": contracts,
    }


def _execution_fill(
    *,
    exec_id: str = EXEC_ID,
    con_id: int = 123,
    perm_id: int = 777,
    shares: int = 2,
    when: datetime | None = None,
) -> MagicMock:
    fill = MagicMock()
    fill.execution = MagicMock()
    fill.execution.execId = exec_id
    fill.execution.permId = perm_id
    fill.execution.side = "BOT"
    fill.execution.shares = shares
    fill.execution.price = 12.0
    fill.execution.time = when or datetime(2026, 8, 7, 14, 30, 0)
    fill.execution.acctNumber = "U000000"
    fill.execution.orderRef = ""

    fill.contract = MagicMock()
    fill.contract.symbol = "MU"
    fill.contract.secType = "OPT"
    fill.contract.strike = 100.0
    fill.contract.right = "C"
    fill.contract.lastTradeDateOrContractMonth = "20260918"
    fill.contract.conId = con_id
    fill.contract.multiplier = "100"
    fill.contract.currency = "USD"

    fill.commissionReport = MagicMock()
    fill.commissionReport.commission = 1.0
    fill.commissionReport.currency = "USD"
    return fill


def _journal_trade_ids(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT trade_id FROM journal").fetchall()}


def _run_journal_sync(conn: sqlite3.Connection, fills: list, *, upsert_raises: bool = False):
    """One prod-wiring (trade_log_path=None) execute() cycle against ``conn``."""

    def fake_upsert(trade_id, entry, filled_at=None):
        if upsert_raises:
            raise RuntimeError("simulated Turso outage")
        conn.execute(
            "INSERT OR REPLACE INTO journal VALUES (?, ?, ?, ?)",
            (str(trade_id), json.dumps(entry), filled_at, "2026-08-07T14:35:00Z"),
        )

    def fake_delete(trade_id):
        conn.execute("DELETE FROM journal WHERE trade_id = ?", (str(trade_id),))

    with patch("monitor_daemon.handlers.journal_sync.IBClient") as mock_cls, \
         patch("monitor_daemon.handlers.journal_sync.get_db", return_value=conn), \
         patch("monitor_daemon.handlers.journal_sync.upsert_journal_entry", fake_upsert), \
         patch(
             "monitor_daemon.handlers.journal_sync.delete_journal_entry",
             fake_delete,
             create=True,  # attribute exists only once the fix lands
         ), \
         patch(
             "monitor_daemon.handlers.journal_sync.prior_net_qty_for_contract",
             MagicMock(return_value=0.0),
         ):
        mock_client = MagicMock()
        mock_client.get_fills.return_value = fills
        mock_cls.return_value = mock_client
        handler = JournalSyncHandler()  # trade_log_path=None — prod wiring
        return handler.execute()


class TestFillMonitorMirrorSupersede:
    def test_execid_import_supersedes_the_fill_monitor_mirror(self):
        """RED: both rows survive today. Required: the execId row replaces
        the fill-monitor mirror once the import covers its fill count."""
        conn = _journal_db((MIRROR_TRADE_ID, _mirror_payload(MIRROR_TRADE_ID)))

        result = _run_journal_sync(conn, [_execution_fill()])

        assert result["imported"] == 1
        remaining = _journal_trade_ids(conn)
        assert EXEC_ID in remaining
        assert MIRROR_TRADE_ID not in remaining, (
            "fill-monitor mirror row must be superseded by the execId row — "
            f"journal still holds {sorted(remaining)}"
        )

    def test_unrelated_mirror_row_survives(self):
        """A mirror for a DIFFERENT contract/order is not covered — keep it."""
        conn = _journal_db(
            (MIRROR_TRADE_ID, _mirror_payload(MIRROR_TRADE_ID)),
            (OTHER_MIRROR_TRADE_ID, _mirror_payload(OTHER_MIRROR_TRADE_ID, ticker="AAOI", contracts=1)),
        )

        _run_journal_sync(conn, [_execution_fill()])

        remaining = _journal_trade_ids(conn)
        assert OTHER_MIRROR_TRADE_ID in remaining, (
            "supersede must be scoped to the covered contract+order+date"
        )
        assert MIRROR_TRADE_ID not in remaining

    def test_failed_execid_upsert_preserves_the_mirror(self):
        """If the execId upsert fails, the mirror is the ONLY record of the
        fill — deleting it would destroy the fill entirely."""
        conn = _journal_db((MIRROR_TRADE_ID, _mirror_payload(MIRROR_TRADE_ID)))

        _run_journal_sync(conn, [_execution_fill()], upsert_raises=True)

        remaining = _journal_trade_ids(conn)
        assert MIRROR_TRADE_ID in remaining, (
            "mirror row deleted although the execId row never landed"
        )
        assert EXEC_ID not in remaining

    def test_partial_coverage_preserves_the_mirror(self):
        """An execId row covering only 1 of the mirrored 2 fills is not
        enough — the mirror stays until the full count is covered."""
        conn = _journal_db((MIRROR_TRADE_ID, _mirror_payload(MIRROR_TRADE_ID)))

        _run_journal_sync(conn, [_execution_fill(shares=1)])

        assert MIRROR_TRADE_ID in _journal_trade_ids(conn)


class TestFillMonitorIncrementPricing:
    """fill_monitor prices each increment at the running avgFillPrice —
    the increment's true price is the delta of total cost."""

    @staticmethod
    def _two_price_partial_trade():
        """Order filled 1 @ $10.00 earlier; 1 more fills @ $12.00 → IB
        reports filled=2, avgFillPrice=11.00."""
        mock_trade = MagicMock()
        mock_trade.order.orderId = 5
        mock_trade.order.permId = 777
        mock_trade.order.action = "BUY"
        mock_trade.order.totalQuantity = 2
        mock_trade.order.lmtPrice = 12.00
        mock_trade.orderStatus.status = "Submitted"
        mock_trade.orderStatus.filled = 2
        mock_trade.orderStatus.remaining = 0
        mock_trade.orderStatus.avgFillPrice = 11.00
        mock_trade.contract.symbol = "MU"
        mock_trade.contract.localSymbol = "MU 260918C00100000"
        mock_trade.contract.conId = 123
        mock_trade.contract.secType = "OPT"
        mock_trade.contract.strike = 100.0
        mock_trade.contract.right = "C"
        mock_trade.contract.lastTradeDateOrContractMonth = "20260918"
        return mock_trade

    def test_increment_priced_from_execution_delta(self):
        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.fill_monitor.upsert_journal_entry") as mock_upsert, \
             patch("monitor_daemon.handlers.fill_monitor.get_db", None):
            mock_client = MagicMock()
            mock_client.get_open_orders.return_value = [self._two_price_partial_trade()]
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {5: {"filled": 1, "avg_fill_price": 10.00}}

            result = handler.execute()

            assert result["partial_fills"] == 1
            payload = mock_upsert.call_args.args[1]
            # Increment cost = 2×$11.00 − 1×$10.00 = $12.00/contract.
            assert payload["fill_price"] == 12.00, (
                f"increment priced at running average: {payload['fill_price']}"
            )
            assert payload["total_cost"] == 1200.0

    def test_first_fill_keeps_avg_price(self):
        """No prior fill state → avgFillPrice IS the increment price."""
        trade = self._two_price_partial_trade()
        trade.orderStatus.filled = 1
        trade.orderStatus.avgFillPrice = 10.00
        trade.orderStatus.remaining = 1

        with patch("monitor_daemon.handlers.fill_monitor.IBClient") as mock_cls, \
             patch("monitor_daemon.handlers.fill_monitor.upsert_journal_entry") as mock_upsert, \
             patch("monitor_daemon.handlers.fill_monitor.get_db", None):
            mock_client = MagicMock()
            mock_client.get_open_orders.return_value = [trade]
            mock_cls.return_value = mock_client

            handler = FillMonitorHandler(send_notifications=False)
            handler.known_orders = {5: {"filled": 0}}

            handler.execute()

            payload = mock_upsert.call_args.args[1]
            assert payload["fill_price"] == 10.00
            assert payload["total_cost"] == 1000.0
