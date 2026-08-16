"""A daemon-deadline kill must go through the handler's own failure recorder.

Plan item C2 (docs/cash-flow-sync-overhaul.md).

`daemon.py` bounds every handler at `max_runtime_seconds`
(default `DEFAULT_HANDLER_DEADLINE_SECONDS`) and, on a timeout, writes a
generic `service_health` error row itself:

    handler.record_cycle_health("error", error={"timeout_seconds": deadline})

Three things break when that path preempts a handler that owns richer
failure bookkeeping:

  1. the row carries no top-level `next_attempt_at`, so the 2026-08-04
     embargo-suppression fix cannot see it and the false-P2 page storm
     (20 `/incident` runs on one fingerprint) comes back;
  2. `CashFlowSyncHandler._record_failure` never runs, so the soft-attempt
     counter does not advance and the daily Flex request budget is not
     spent — the next cycle re-probes a token that is already in trouble;
  3. the abandoned thread later writes its own row and mutates in-memory
     state, so a deploy in that window loses the counter.

⛔ No Flex requests: the stub handler sleeps, it does not fetch.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler
from monitor_daemon.handlers.cash_flow_sync import CashFlowSyncHandler


@pytest.fixture(autouse=True)
def _settle_abandoned_threads():
    """An abandoned handler thread finishes AFTER the test body. Let it land
    while the service_health stub is still installed — otherwise the late
    `ok` row escapes to whatever Turso the runner's env points at, which is
    the 2026-05-14 test-pollution incident."""
    yield
    time.sleep(1.0)


@pytest.fixture
def health_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    import db.writer as writer_mod

    rows: list[dict] = []

    def _record(service, state, **kwargs):
        rows.append({"service": service, "state": state, **kwargs})

    monkeypatch.setattr(writer_mod, "record_service_health", _record)
    return rows


class _SleepingCashFlowSync(CashFlowSyncHandler):
    """The real handler, wedged past its deadline."""

    max_runtime_seconds = 0.2

    def is_due(self) -> bool:
        return True

    def _execute_inner(self):  # noqa: D401 — stand-in for the wedged subprocess
        time.sleep(0.6)
        return {"status": "ok"}


class _PlainHandler(BaseHandler):
    name = "plain"
    interval_seconds = 0
    requires_market_hours = False
    service_name = "plain"
    max_runtime_seconds = 0.2

    def execute(self):
        time.sleep(0.6)
        return {"ok": True}


class TestDeadlineRoutesThroughTheHandler:
    def test_timeout_row_carries_next_attempt_at(self, health_rows):
        daemon = MonitorDaemon(state_file=None, respect_market_hours=False)
        handler = _SleepingCashFlowSync()
        daemon.register(handler)

        result = daemon.run_once()

        assert result["cash_flow_sync"]["status"] == "error"
        timeout_rows = [r for r in health_rows if r["service"] == "cash-flow-sync"]
        assert timeout_rows, "the deadline kill must still heartbeat"
        error = timeout_rows[-1]["error"] or {}
        assert error.get("next_attempt_at"), (
            "a deadline kill with no next_attempt_at is invisible to the "
            "incident watchdog's embargo suppression"
        )

    def test_timeout_advances_the_soft_attempt_counter(self, health_rows):
        daemon = MonitorDaemon(state_file=None, respect_market_hours=False)
        handler = _SleepingCashFlowSync()
        daemon.register(handler)

        daemon.run_once()

        assert handler._backoff_state["soft_attempts"] == 1
        assert handler._backoff_state["throttle_count"] == 0

    def test_handlers_without_a_recorder_keep_the_generic_row(self, health_rows):
        daemon = MonitorDaemon(state_file=None, respect_market_hours=False)
        daemon.register(_PlainHandler())

        daemon.run_once()

        plain = [r for r in health_rows if r["service"] == "plain"]
        assert plain
        assert plain[-1]["state"] == "error"
        assert (plain[-1]["error"] or {}).get("timeout_seconds") == pytest.approx(0.2)
