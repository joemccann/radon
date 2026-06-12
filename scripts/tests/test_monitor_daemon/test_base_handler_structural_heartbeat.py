"""DUR-14 — heartbeat-on-every-cycle is structural in ``BaseHandler.run()``.

Before this change every handler hand-rolled the same execute() wrapper
(lazy db.writer import, started_at, ok/error mapping) — four near-identical
copies, and any NEW handler could silently ship without one (preset_rebalance
did exactly that). Now any subclass that declares ``service_name`` gets the
heartbeat from ``run()`` itself:

  - ok row after a successful execute (including skip/no-change results);
  - error row when execute raises, returns ``{"status": "error"}`` (the
    soft-failure contract), or returns a dict with a truthy ``"error"`` key
    (the swallowed-IB-failure convention from fill_monitor et al);
  - handlers that record richer rows themselves call
    ``self.record_cycle_health(...)`` which marks the cycle recorded so the
    base never clobbers their state (writer-state-not-event-content);
  - ``service_name = None`` (e.g. replica_watchdog's bespoke event-driven
    writer) opts out entirely.

The directory conftest mocks db.writer so nothing touches a real DB.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def health_calls(monkeypatch):
    import db.writer as writer_mod

    calls: list[tuple[str, str, dict]] = []

    def fake_health(service, state, **kwargs):
        calls.append((service, state, kwargs))

    monkeypatch.setattr(writer_mod, "record_service_health", fake_health, raising=False)
    return calls


def _make_handler(execute_fn, *, declared_service_name="dummy-handler"):
    from monitor_daemon.handlers.base import BaseHandler

    class _Dummy(BaseHandler):
        name = "dummy_handler"
        interval_seconds = 1
        requires_market_hours = False
        service_name = declared_service_name

        def execute(self) -> Dict[str, Any]:
            return execute_fn(self)

    return _Dummy()


class TestStructuralOk:
    def test_ok_heartbeat_after_successful_execute(self, health_calls):
        handler = _make_handler(lambda self: {"status": "ok"})
        result = handler.run()

        assert result["status"] == "ok"
        [(service, state, kwargs)] = health_calls
        assert (service, state) == ("dummy-handler", "ok")
        assert kwargs["started_at"] and kwargs["finished_at"]

    def test_ok_heartbeat_on_skip_result(self, health_calls):
        """Skips/no-change cycles still heartbeat — the writer ran and is
        healthy (feedback_service_health_heartbeat)."""
        handler = _make_handler(lambda self: {"status": "skip", "reason": "off-hours"})
        handler.run()
        [(_, state, _)] = health_calls
        assert state == "ok"

    def test_no_heartbeat_without_service_name(self, health_calls):
        handler = _make_handler(lambda self: {}, declared_service_name=None)
        handler.run()
        assert health_calls == []


class TestStructuralError:
    def test_error_heartbeat_when_execute_raises(self, health_calls):
        def boom(self):
            raise ConnectionError("Gateway down")

        handler = _make_handler(boom)
        result = handler.run()

        assert result["status"] == "error"
        assert handler.last_run is None  # raise-don't-latch preserved
        [(service, state, kwargs)] = health_calls
        assert (service, state) == ("dummy-handler", "error")
        assert "Gateway down" in kwargs["error"]["message"]

    def test_error_heartbeat_on_soft_failure_dict(self, health_calls):
        handler = _make_handler(lambda self: {"status": "error", "error": "flex not ready"})
        result = handler.run()

        assert result["status"] == "error"
        assert handler.last_run is None
        [(_, state, kwargs)] = health_calls
        assert state == "error"
        assert "flex not ready" in kwargs["error"]["message"]

    def test_error_heartbeat_on_swallowed_error_key(self, health_calls):
        """fill_monitor-style handlers swallow IB failures into
        ``result["error"]`` — that is the writer's OWN outcome and must
        surface as state=error."""
        handler = _make_handler(lambda self: {"orders": [], "error": "Failed to connect"})
        result = handler.run()

        assert result["status"] == "ok"  # run() return shape unchanged
        assert handler.last_run is not None  # swallowed errors latch (existing behavior)
        [(_, state, kwargs)] = health_calls
        assert state == "error"
        assert "Failed to connect" in kwargs["error"]["message"]


class TestSelfRecordedCycles:
    def test_base_does_not_clobber_handler_recorded_state(self, health_calls):
        """A handler that records its own richer row (cash_flow_sync's
        throttle metadata) marks the cycle recorded; run() must not
        overwrite it with a structural ok."""

        def record_and_succeed(self):
            self.record_cycle_health(
                "error", error={"message": "throttled", "next_attempt_at": "T"}
            )
            return {"status": "throttled"}

        handler = _make_handler(record_and_succeed)
        handler.run()

        [(_, state, kwargs)] = health_calls
        assert state == "error"
        assert kwargs["error"]["message"] == "throttled"

    def test_flag_resets_between_cycles(self, health_calls):
        def record_first_cycle_only(self):
            if not getattr(self, "_did_first", False):
                self._did_first = True
                self.record_cycle_health("ok")
            return {}

        handler = _make_handler(record_first_cycle_only)
        handler.run()
        handler.last_run = None
        handler.run()

        assert [state for _, state, _ in health_calls] == ["ok", "ok"]
        assert len(health_calls) == 2  # one per cycle, never zero, never double

    def test_record_cycle_health_is_best_effort(self, monkeypatch):
        import db.writer as writer_mod

        monkeypatch.setattr(
            writer_mod,
            "record_service_health",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("db gone")),
            raising=False,
        )
        handler = _make_handler(lambda self: {})
        result = handler.run()  # must not raise
        assert result["status"] == "ok"


class TestExistingHandlersDeclareServiceNames:
    """Every daemon-registered handler that owns a service_health row must
    declare it via the structural attribute (the registration-completeness
    CI test collects these names)."""

    @pytest.mark.parametrize(
        ("module_name", "class_name", "expected"),
        [
            ("monitor_daemon.handlers.fill_monitor", "FillMonitorHandler", "fill-monitor"),
            ("monitor_daemon.handlers.exit_orders", "ExitOrdersHandler", "exit-orders"),
            ("monitor_daemon.handlers.journal_sync", "JournalSyncHandler", "journal-sync"),
            ("monitor_daemon.handlers.flex_token_check", "FlexTokenCheck", "flex-token-check"),
            ("monitor_daemon.handlers.cash_flow_sync", "CashFlowSyncHandler", "cash-flow-sync"),
            ("monitor_daemon.handlers.preset_rebalance_handler", "PresetRebalanceHandler", "preset-rebalance"),
        ],
    )
    def test_handler_declares_service_name(self, module_name, class_name, expected):
        import importlib

        cls = getattr(importlib.import_module(module_name), class_name)
        assert cls.service_name == expected

    def test_replica_watchdog_opts_out(self):
        """Event-driven writer with bespoke ok/syncing/error semantics —
        a structural ok after execute would clobber its own rows."""
        from monitor_daemon.handlers.replica_watchdog import ReplicaWatchdogHandler

        assert ReplicaWatchdogHandler.service_name is None
