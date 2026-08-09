#!/usr/bin/env python3
"""REL-021 (partial) — P2 batch items with behavior changes
(RELIABILITY_AUDIT.md R-031, R-033, R-038, R-045, R-029-partial).

Each class pins one item; see RELIABILITY_LOG.md for the batch map.
"""

import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAtomicSaveFsync:
    """R-031: atomic_save must fsync file and directory so a power loss
    cannot persist the rename with unwritten content."""

    def test_fsync_called_on_file_and_directory(self, tmp_path, monkeypatch):
        from utils import atomic_io

        synced_fds = []
        real_fsync = os.fsync
        monkeypatch.setattr(atomic_io.os, "fsync", lambda fd: synced_fds.append(fd) or real_fsync(fd))

        atomic_io.atomic_save(str(tmp_path / "state.json"), {"a": 1})
        # At least two fsyncs: the temp file's fd and the directory fd.
        assert len(synced_fds) >= 2


class TestLegacyExitServiceDisabled:
    """R-038: the legacy standalone exit-order placer must refuse to start
    its daemon mode — two concurrent placers can double-place exits."""

    def test_run_daemon_refuses(self):
        """Bounded subprocess so the RED state (current code: infinite
        daemon loop) fails fast instead of hanging pytest."""
        import subprocess as sp

        proc = sp.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'scripts'); "
             "import exit_order_service; exit_order_service.run_daemon()"],
            cwd=str(Path(__file__).parent.parent.parent),
            capture_output=True, timeout=10, text=True,
        )
        assert proc.returncode != 0
        assert "monitor daemon" in (proc.stdout + proc.stderr).lower()


class TestDaemonStateCorruptionBackup:
    """R-045: a corrupt daemon_state.json must be preserved as .corrupt
    (forensics + fill-dedupe recovery), not silently blanked."""

    def test_corrupt_state_backed_up(self, tmp_path):
        from monitor_daemon.daemon import MonitorDaemon

        state_file = tmp_path / "daemon_state.json"
        state_file.write_text("{corrupt json")

        daemon = MonitorDaemon(state_file=state_file, respect_market_hours=False)
        daemon.load_state()

        backups = list(tmp_path.glob("daemon_state.json.corrupt*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "{corrupt json"

    def test_sigterm_saves_state(self, tmp_path):
        """Every deploy SIGTERMs the daemon; only KeyboardInterrupt used
        to reach the final save."""
        from monitor_daemon import daemon as daemon_mod

        daemon = daemon_mod.MonitorDaemon(
            state_file=tmp_path / "daemon_state.json", respect_market_hours=False
        )
        saved = []
        daemon.save_state = lambda: saved.append(True)

        daemon.install_signal_handlers()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(SystemExit):
            handler(signal.SIGTERM, None)
        assert saved == [True]


class TestPriceCachePruneOnWrite:
    """R-033: the 500-file cap is only enforced by the shelved performance
    rebuild — the write path itself must prune."""

    def test_write_path_prunes(self, tmp_path, monkeypatch):
        from utils import price_cache

        stocks = tmp_path / "stocks"
        stocks.mkdir(parents=True)
        monkeypatch.setattr(price_cache, "STOCKS_DIR", stocks)
        prune = MagicMock()
        monkeypatch.setattr(price_cache, "prune_cache", prune)

        price_cache.write_cache(stocks, "AAPL_2026-01-01_2026-02-01",
                                {"2026-01-02": 100.0}, source="ib", ttl=900)
        prune.assert_called_once()


class TestExitOrderTimestampsUtc:
    """R-029 (partial): exit_orders wrote naive local timestamps into
    placed_at/written_at, breaking lexicographic-chronological ordering
    against the UTC-Z rows every other writer produces."""

    def test_update_journal_trade_writes_utc_z(self):
        sys.path.insert(0, str(Path(__file__).parent / "test_monitor_daemon"))
        from monitor_daemon.handlers.exit_orders import ExitOrdersHandler
        from test_exit_orders import FakeJournalDb

        db = FakeJournalDb([
            {
                "id": 8,
                "ticker": "GOOG",
                "exit_orders": {
                    "target": {"price": 15.0, "status": "PENDING", "order_id": None}
                },
            }
        ])
        handler = ExitOrdersHandler(db=db)
        ok = handler._update_journal_trade(8, "target", 99, "trade-8")
        assert ok
        placed_at = db.trades["trade-8"]["exit_orders"]["target"]["placed_at"]
        assert placed_at.endswith("Z") or "+00:00" in placed_at
