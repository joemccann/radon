#!/usr/bin/env python3
"""Audit 2026-11-03 §3.1 (monitor-daemon handler cycle).

1. A timed-out handler's worker keeps running (Python cannot kill a thread).
   When it finally returned, ``BaseHandler.run`` latched ``last_run`` and
   heartbeated ``ok`` over the timeout error the daemon had just recorded.
   Only cash_flow_sync retired its run; every other handler painted green.
2. ``save_state`` read ``handler.get_state()`` on the loop thread while a
   late worker could still be mutating the same containers. State is now
   copied under the handler's ``state_lock``.
3. ``last_run`` was a naive local datetime, persisted as such to
   daemon_state.json and Turso. New runs latch aware UTC.
"""

import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from monitor_daemon.daemon import MonitorDaemon
from monitor_daemon.handlers.base import BaseHandler


class _SlowHandler(BaseHandler):
    name = "slow"
    interval_seconds = 0
    requires_market_hours = False
    max_runtime_seconds = 0.05
    service_name = "slow-handler"

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.health = []

    def execute(self):
        self.started.set()
        self.release.wait(timeout=2)
        return {"ok": True}

    def record_cycle_health(self, state, **kwargs):
        self._cycle_health_recorded = True
        self.health.append(state)
        return True


def _finish_late_worker(daemon, handler):
    future, _executor = daemon._inflight_handlers[handler.name]
    handler.release.set()
    future.result(timeout=2)


def test_a_timed_out_run_cannot_latch_last_run_or_heartbeat_ok_late():
    daemon = MonitorDaemon(respect_market_hours=False)
    handler = _SlowHandler()

    first = daemon._run_handler_bounded(handler)
    assert handler.started.wait(timeout=1)
    assert "timed out" in first["error"]
    assert handler.health == ["error"]

    _finish_late_worker(daemon, handler)

    assert handler.last_run is None, "a discarded late run must not burn the slot"
    assert handler.health == ["error"], (
        f"the late worker overwrote the timeout row: {handler.health}"
    )


def test_the_next_run_after_a_timeout_latches_normally():
    daemon = MonitorDaemon(respect_market_hours=False)
    handler = _SlowHandler()
    daemon._run_handler_bounded(handler)
    _finish_late_worker(daemon, handler)

    handler.max_runtime_seconds = 5
    result = daemon._run_handler_bounded(handler)

    assert result["status"] == "ok", result
    assert handler.last_run is not None


class _GuardedStateHandler(BaseHandler):
    name = "guarded"
    requires_market_hours = False

    def __init__(self):
        super().__init__()
        self.entries = {}

    def execute(self):
        return {}

    def get_state(self):
        state = super().get_state()
        state["entries"] = self.entries
        return state


def test_save_state_snapshots_under_the_handler_state_lock(tmp_path):
    daemon = MonitorDaemon(state_file=tmp_path / "state.json", respect_market_hours=False)
    handler = _GuardedStateHandler()
    daemon.register(handler)
    saved = threading.Event()

    def save():
        daemon.save_state()
        saved.set()

    with handler.state_lock:
        handler.entries["a"] = 1
        saver = threading.Thread(target=save)
        saver.start()
        time.sleep(0.1)
        assert not saved.is_set(), "save_state read handler state mid-mutation"
        handler.entries["b"] = 2
    saver.join(timeout=2)

    import json
    persisted = json.loads((tmp_path / "state.json").read_text())
    data = persisted.get("data", persisted)
    assert data["handlers"]["guarded"]["entries"] == {"a": 1, "b": 2}


def test_last_run_latches_aware_utc():
    handler = _GuardedStateHandler()
    handler.run()

    assert handler.last_run.tzinfo is not None
    assert handler.last_run.utcoffset() == timedelta(0)
    assert handler.get_state()["last_run"].endswith("+00:00")


def test_a_legacy_naive_last_run_still_schedules():
    handler = _GuardedStateHandler()
    handler.interval_seconds = 60
    handler.set_state({"last_run": (datetime.now() - timedelta(hours=1)).isoformat()})
    assert handler.is_due() is True

    handler.set_state({"last_run": datetime.now(timezone.utc).isoformat()})
    assert handler.is_due() is False
