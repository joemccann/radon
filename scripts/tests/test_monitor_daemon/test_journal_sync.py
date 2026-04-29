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

    def test_uses_daemon_range_client_id(self):
        handler = JournalSyncHandler()
        assert 70 <= handler.client_id <= 89


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
