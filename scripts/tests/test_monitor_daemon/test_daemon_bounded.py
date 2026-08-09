#!/usr/bin/env python3
"""REL-008 — bound the monitor daemon (RELIABILITY_AUDIT.md R-013).

Three unbounded surfaces let one hang wedge exit-order management
alive-but-dead: the per-cycle `upsert_daemon_state` ran on native libsql
(no timeout, holds the GIL), `handler.run()` had no deadline (ib_insync
blocks forever on an auth-wedged gateway), and radon-monitor.service had
no WatchdogSec so systemd never noticed.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler

ROOT = Path(__file__).resolve().parents[3]


class _InstantHandler(BaseHandler):
    name = "instant"
    interval_seconds = 0
    requires_market_hours = False

    def __init__(self):
        super().__init__()
        self.ran = 0

    def execute(self):
        self.ran += 1
        return {"ok": True}


class _HangingHandler(BaseHandler):
    name = "hanging"
    interval_seconds = 0
    requires_market_hours = False
    max_runtime_seconds = 0.2

    def execute(self):
        time.sleep(5)
        return {"ok": True}


class TestDaemonStateViaHrana:
    def test_upsert_daemon_state_uses_bounded_transport(self, monkeypatch):
        from db import writer

        hrana = MagicMock()
        monkeypatch.setattr(writer, "_hrana_execute", hrana)
        native = MagicMock(side_effect=AssertionError(
            "upsert_daemon_state must not touch native libsql (no timeout, "
            "holds the GIL — wedges the daemon loop)"
        ))
        monkeypatch.setattr(writer, "get_db", native)

        writer.upsert_daemon_state("fill_monitor", last_run="2026-08-07T15:00:00Z",
                                   last_status="ok")
        hrana.assert_called_once()
        sql = hrana.call_args[0][0]
        assert "daemon_state" in sql


class TestPerHandlerDeadline:
    def test_hung_handler_does_not_block_the_next(self):
        daemon = MonitorDaemon(state_file=None, respect_market_hours=False)
        hanging = _HangingHandler()
        instant = _InstantHandler()
        daemon.register(hanging)
        daemon.register(instant)

        started = time.monotonic()
        results = daemon.run_once()
        elapsed = time.monotonic() - started

        assert elapsed < 3.0  # not the 5s hang
        assert results["hanging"]["status"] == "error"
        assert "timed out" in results["hanging"]["error"]
        assert instant.ran == 1  # the loop moved on


class TestWatchdogNotify:
    def test_loop_pings_watchdog_each_iteration(self, monkeypatch):
        pings = []
        monkeypatch.setattr(
            "monitor_daemon.daemon.sd_notify", lambda state: pings.append(state)
        )
        daemon = MonitorDaemon(state_file=None, respect_market_hours=False)
        daemon.register(_InstantHandler())

        # Run two loop iterations then stop.
        iterations = {"n": 0}
        original_run_once = daemon.run_once

        def counting_run_once(**kwargs):
            iterations["n"] += 1
            if iterations["n"] >= 2:
                daemon._running = False
            return original_run_once(**kwargs)

        daemon.run_once = counting_run_once
        with patch("monitor_daemon.daemon.time.sleep"):
            daemon.run_loop()

        assert "READY=1" in pings
        assert pings.count("WATCHDOG=1") >= 2


class TestUnitFileContract:
    def test_monitor_unit_has_watchdog(self):
        unit = (ROOT / "cloud" / "services" / "radon-monitor.service").read_text()
        assert "Type=notify" in unit
        assert "WatchdogSec=" in unit
