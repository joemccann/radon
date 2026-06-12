"""DUR-14 — ``db.service_cycle`` writer-contract context manager.

The three service_health writer contracts (heartbeat-every-cycle,
writer-state-not-event-content, raise-don't-latch) were conventions each
violated within days of documentation. ``service_cycle`` makes them
structural for STANDALONE writers (cri_scan, ib_sync, ib_orders, …);
mirror-fed scans stay on ``db.scan_mirror`` (see both module docstrings
for the division of labor).

Semantics pinned here:
  - ok heartbeat on EVERY successful exit path, including no-change
    short-circuits (early return inside the with-block);
  - row state derives ONLY from this writer's own outcome (clean exit vs
    exception) — there is no API to set state from observed event content;
  - on exception: error row with a ~5-min retry embargo
    (``next_attempt_at``) is written BEFORE the exception re-raises;
  - heartbeat writes are best-effort — a Turso failure never masks the
    caller's own outcome (success path stays silent, error path still
    re-raises the ORIGINAL exception).

All db.writer surfaces are monkeypatched — no test may touch a real DB
(feedback_test_pollution_to_production).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _resolved_writer():
    """Resolve whatever module ``from db.writer import …`` yields at call
    time — other suites swap stubs into sys.modules (same trick as
    test_scan_service_health)."""
    writer = sys.modules.get("db.writer")
    if writer is None:
        import db.writer as writer
    return writer


@pytest.fixture
def health_calls(monkeypatch):
    writer = _resolved_writer()
    calls: list[tuple[str, str, dict]] = []

    def fake_health(service, state, **kwargs):
        calls.append((service, state, kwargs))

    monkeypatch.setattr(writer, "record_service_health", fake_health, raising=False)
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None, raising=False)
    return calls


class TestSuccessPath:
    def test_ok_row_on_clean_exit(self, health_calls):
        from db.service_cycle import service_cycle

        with service_cycle("cri-scan", market_hours_class="intraday") as cycle:
            cycle.finished_at = "2026-06-12T14:00:00Z"

        [(service, state, kwargs)] = health_calls
        assert service == "cri-scan"
        assert state == "ok"
        assert kwargs["finished_at"] == "2026-06-12T14:00:00Z"
        assert kwargs["started_at"]  # stamped on entry

    def test_ok_row_on_no_change_short_circuit(self, health_calls):
        """An early return inside the with-block is still a successful
        exit — the heartbeat must fire (feedback_service_health_heartbeat)."""
        from db.service_cycle import service_cycle

        def writer_with_short_circuit() -> str:
            with service_cycle("orders-sync", market_hours_class="intraday"):
                return "nochange"  # noqa: B012 — the short-circuit IS the test

        assert writer_with_short_circuit() == "nochange"
        [(service, state, _)] = health_calls
        assert (service, state) == ("orders-sync", "ok")

    def test_finished_at_defaults_to_none_for_coalesce(self, health_calls):
        from db.service_cycle import service_cycle

        with service_cycle("cta-sync", market_hours_class="daily"):
            pass

        [(_, _, kwargs)] = health_calls
        assert kwargs["finished_at"] is None

    def test_calls_ensure_no_replica(self, monkeypatch, health_calls):
        writer = _resolved_writer()
        called = []
        monkeypatch.setattr(
            writer, "ensure_no_replica_for_writers", lambda: called.append(True), raising=False
        )
        from db.service_cycle import service_cycle

        with service_cycle("portfolio-sync", market_hours_class="intraday"):
            pass
        assert called == [True]


class TestErrorPath:
    def test_error_row_and_reraise(self, health_calls):
        from db.service_cycle import service_cycle

        with pytest.raises(RuntimeError, match="turso down"):
            with service_cycle("orders-sync", market_hours_class="intraday"):
                raise RuntimeError("turso down")

        [(service, state, kwargs)] = health_calls
        assert (service, state) == ("orders-sync", "error")
        assert "turso down" in kwargs["error"]["message"]

    def test_error_row_carries_five_minute_embargo(self, health_calls):
        """Same retry-spacing convention as record_soft_failure in
        _throttle_backoff: ~5 min, so the timer/daemon retries on a
        measured cadence instead of burning its slot
        (feedback_dont_latch_last_run_on_soft_failure)."""
        from db.service_cycle import SOFT_RETRY_EMBARGO_SECS, service_cycle

        assert SOFT_RETRY_EMBARGO_SECS == 5 * 60

        before = datetime.now(timezone.utc)
        with pytest.raises(ValueError):
            with service_cycle("llm-token-index", market_hours_class="daily"):
                raise ValueError("AA api 500")
        after = datetime.now(timezone.utc)

        [(_, _, kwargs)] = health_calls
        next_attempt = datetime.fromisoformat(
            kwargs["error"]["next_attempt_at"].replace("Z", "+00:00")
        )
        assert before + timedelta(seconds=290) <= next_attempt
        assert next_attempt <= after + timedelta(seconds=310)

    def test_original_exception_survives_health_write_failure(self, monkeypatch, health_calls):
        writer = _resolved_writer()

        def boom(*args, **kwargs):
            raise OSError("db gone")

        monkeypatch.setattr(writer, "record_service_health", boom, raising=False)
        from db.service_cycle import service_cycle

        with pytest.raises(RuntimeError, match="the real failure"):
            with service_cycle("cri-scan", market_hours_class="intraday"):
                raise RuntimeError("the real failure")

    def test_success_path_swallows_health_write_failure(self, monkeypatch, health_calls):
        writer = _resolved_writer()

        def boom(*args, **kwargs):
            raise OSError("db gone")

        monkeypatch.setattr(writer, "record_service_health", boom, raising=False)
        from db.service_cycle import service_cycle

        with service_cycle("gex-scan", market_hours_class="on-demand"):
            pass  # must not raise — heartbeat is best-effort


class TestRecordFailedCycle:
    """Public helper for writers whose failure is detected OUTSIDE a
    with-block (e.g. gamma_rotation_gap.main catching fetch errors)."""

    def test_writes_error_row_with_embargo(self, health_calls):
        from db.service_cycle import record_failed_cycle

        record_failed_cycle("gamma-rotation-scan", RuntimeError("UW timeout"))

        [(service, state, kwargs)] = health_calls
        assert (service, state) == ("gamma-rotation-scan", "error")
        assert "UW timeout" in kwargs["error"]["message"]
        assert kwargs["error"]["next_attempt_at"]

    def test_never_raises(self, monkeypatch, health_calls):
        writer = _resolved_writer()
        monkeypatch.setattr(
            writer,
            "record_service_health",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("db gone")),
            raising=False,
        )
        from db.service_cycle import record_failed_cycle

        record_failed_cycle("gamma-rotation-scan", RuntimeError("UW timeout"))


class TestApiGuards:
    def test_unknown_market_hours_class_is_programmer_error(self, health_calls):
        from db.service_cycle import service_cycle

        with pytest.raises(ValueError, match="market_hours_class"):
            with service_cycle("cri-scan", market_hours_class="hourly"):
                pass

    def test_state_is_not_caller_settable(self, health_calls):
        """Writer-state-not-event-content is structural: the cycle handle
        exposes no state knob, so a writer cannot mirror observed event
        content into its own row."""
        from db.service_cycle import service_cycle

        with service_cycle("cri-scan", market_hours_class="intraday") as cycle:
            assert not hasattr(cycle, "state")
            with pytest.raises(AttributeError):
                cycle.state = "error"  # type: ignore[attr-defined]

        [(_, state, _)] = health_calls
        assert state == "ok"

    def test_degrades_silently_when_db_layer_missing(self, monkeypatch, capsys):
        """Hosts without libsql run the writer unchanged — no heartbeat,
        no crash, exceptions still propagate."""
        monkeypatch.setitem(sys.modules, "db.writer", None)  # forces ImportError
        from db.service_cycle import service_cycle

        with service_cycle("cri-scan", market_hours_class="intraday"):
            pass

        with pytest.raises(RuntimeError, match="still propagates"):
            with service_cycle("cri-scan", market_hours_class="intraday"):
                raise RuntimeError("still propagates")
