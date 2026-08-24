"""REL-047 / R-100, R-101, R-102, R-104, R-108, R-109 (P1) + R-129, R-130 (P2).

The Flex token is ONE resource shared by cash-flow-sync, radon-perf-twr,
POST /performance/background and the blotter. The embargo that protects it
is neither durable, monotonic, nor shared:

  R-100  record_lockout() unconditionally writes now+7d. A FlexTokenLocked
         raised by the PRE-FLIGHT check (no HTTP performed) exits 15,
         indistinguishable from a fresh IBKR 1025, and the handler maps 15
         back to record_lockout — so every independent arming path EXTENDS a
         token-wide outage without a single Flex request being made.
  R-104  CashFlowSyncHandler.is_due gates only on its private _backoff_state
         and never consults the shared sidecar, which is what lets the daily
         window fire into a live lockout and trigger R-100.
  R-129  The sidecar is written with truncate-then-write while the repo ships
         atomic_save; a reader in the microseconds after truncation sees
         empty JSON and returns "no lockout".
  R-130  clear() only unlinks the sidecar, so the flex-web-service row stays
         error for ~24h after a lockout lapses; and the service_health
         dual-write the module header advertises is write-only, so deleting
         the gitignored sidecar silently drops a live 7-day lockout.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from utils import flex_embargo as fe


T0 = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(fe, "SIDECAR", tmp_path / "flex_token_embargo.json")
    monkeypatch.setattr(fe, "_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(fe, "_rehydrate_from_health", lambda: None)
    yield


class TestRecordLockoutIsExtendOnly:
    def test_a_later_arming_never_shortens_a_live_deadline(self):
        first = fe.record_lockout("1025", now=T0)
        second = fe.record_lockout("1025", now=T0 + timedelta(hours=1))
        assert second == first, (
            "re-arming moved the deadline; every independent caller would "
            "extend a token-wide outage by another full week"
        )

    def test_a_later_arming_never_extends_a_live_deadline(self):
        """R-100: the sliding window is exactly how a 7-day lockout becomes
        14. The deadline belongs to the IBKR event, not to the caller."""
        first = fe.record_lockout("1025", now=T0)
        later = fe.record_lockout("1025", now=T0 + timedelta(hours=18))
        assert later == first

    def test_a_fresh_lockout_after_a_lapse_arms_normally(self):
        fe.record_lockout("1025", now=T0)
        after = T0 + timedelta(days=8)
        assert fe.is_blocked(now=after) is False
        again = fe.record_lockout("1025", now=after)
        assert fe._parse_iso(again) > after


class TestPreflightNeverRearms:
    def test_raise_if_blocked_does_not_touch_the_deadline(self):
        first = fe.record_lockout("1025", now=T0)
        with pytest.raises(fe.FlexTokenLocked):
            fe.raise_if_blocked(now=T0 + timedelta(hours=6))
        assert fe.active_until(now=T0 + timedelta(hours=6)) == first

    def test_the_preflight_exit_code_is_distinct_from_a_fresh_1025(self):
        import cash_flow_sync as cfs
        from monitor_daemon.handlers import cash_flow_sync as handler

        assert cfs.EXIT_FLEX_PREFLIGHT_EMBARGO != cfs.EXIT_FLEX_LOCKOUT
        assert handler.EXIT_FLEX_PREFLIGHT_EMBARGO == cfs.EXIT_FLEX_PREFLIGHT_EMBARGO


class TestSidecarIsAtomic:
    def test_a_truncated_sidecar_is_never_observed(self, tmp_path, monkeypatch):
        """R-129: truncate-then-write let a concurrent reader see empty JSON
        and SendRequest into a live lockout."""
        writes: list[str] = []
        real_atomic = fe.atomic_save

        def _spy(path, payload):
            writes.append(str(path))
            return real_atomic(path, payload)

        monkeypatch.setattr(fe, "atomic_save", _spy)
        fe.record_lockout("1025", now=T0)
        assert writes, "the sidecar is still written with a plain write_text"


class TestServiceHealthRehydration:
    def test_a_deleted_sidecar_is_rebuilt_from_service_health(self, monkeypatch):
        """R-130: the dual-write was write-only, so anything that removed the
        gitignored sidecar silently dropped a live 7-day lockout."""
        until = fe._utc_iso(T0 + timedelta(days=5))
        monkeypatch.setattr(fe, "_rehydrate_from_health", lambda: fe._parse_iso(until))
        assert fe.SIDECAR.exists() is False
        assert fe.is_blocked(now=T0) is True
        assert fe.active_until(now=T0) == until

    def test_a_lapse_clears_the_flex_web_service_row(self, monkeypatch):
        cleared: list[str] = []
        monkeypatch.setattr(fe, "_heartbeat_ok", lambda: cleared.append("ok"))
        fe.record_lockout("1025", now=T0)
        assert fe.is_blocked(now=T0 + timedelta(days=8)) is False
        assert cleared == ["ok"], (
            "the flex-web-service row stays error after the lockout lapses"
        )


class TestHandlerConsultsTheSharedSidecar:
    def test_is_due_is_false_while_the_shared_sidecar_is_armed(self, monkeypatch):
        """R-104: the daily window fired into a live lockout, the subprocess
        died on the embargo, and R-100 extended the outage another week."""
        from monitor_daemon.handlers import cash_flow_sync as handler_mod

        handler = handler_mod.CashFlowSyncHandler()
        handler._enabled = True
        handler._backoff_state = {}
        monkeypatch.setattr(handler_mod, "_now_utc", lambda: T0)
        monkeypatch.setattr(
            handler_mod, "_in_daily_window", lambda *a, **k: True, raising=False
        )
        fe.record_lockout("1025", now=T0 - timedelta(days=1))
        assert handler.is_due() is False


class TestDaemonStateSeeding:
    def test_a_missing_state_file_still_seeds_handlers(self, tmp_path):
        """R-108: load_state returned before the seeding hook when the file
        was MISSING — the hook only fired on a checksum failure. A fresh
        host or a restored VM forgot a live 24h-168h Flex embargo."""
        from monitor_daemon.daemon import MonitorDaemon
        from monitor_daemon.handlers.base import BaseHandler

        seeded: list[str] = []

        class _Seeding(BaseHandler):
            name = "seeder"

            def execute(self):
                return {"status": "ok"}

            def seed_state_after_corrupt_load(self):
                seeded.append("yes")

        daemon = MonitorDaemon(state_file=tmp_path / "never-written.json")
        daemon.register(_Seeding())
        daemon.load_state()

        assert seeded == ["yes"]

    def test_one_handlers_bad_state_does_not_reseed_the_others(self, tmp_path):
        """R-108: set_state ran inside the try, so one handler raising
        aborted the loop, seeded ALL handlers including correctly restored
        ones, and wrote a spurious .corrupt backup of a VALID file."""
        from utils.atomic_io import atomic_save
        from monitor_daemon.daemon import MonitorDaemon
        from monitor_daemon.handlers.base import BaseHandler

        seeded: list[str] = []
        restored: list[str] = []

        class _Good(BaseHandler):
            name = "good"

            def execute(self):
                return {"status": "ok"}

            def set_state(self, state):
                restored.append("good")

            def seed_state_after_corrupt_load(self):
                seeded.append("good")

        class _Bad(BaseHandler):
            name = "bad"

            def execute(self):
                return {"status": "ok"}

            def set_state(self, state):
                raise ValueError("malformed throttle_count")

            def seed_state_after_corrupt_load(self):
                seeded.append("bad")

        state_file = tmp_path / "daemon_state.json"
        atomic_save(str(state_file), {"handlers": {"good": {}, "bad": {}}})

        daemon = MonitorDaemon(state_file=state_file)
        daemon.register(_Good())
        daemon.register(_Bad())
        daemon.load_state()

        assert restored == ["good"]
        assert seeded == ["bad"], "a valid handler's state was thrown away"
        backups = list(tmp_path.glob("daemon_state.json.corrupt-*"))
        assert backups == [], "a VALID state file was backed up as corrupt"


class TestDeadlineKillWins:
    def test_a_late_success_cannot_erase_the_recorded_embargo(self):
        """R-109: the timed-out worker thread kept running; its
        _mark_success cleared blocked_until and wrote an ok row over the
        failure the deadline path had just recorded."""
        from monitor_daemon.handlers import cash_flow_sync as mod

        handler = mod.CashFlowSyncHandler()
        handler._backoff_state = {}
        rows: list[str] = []
        handler.record_cycle_health = lambda *a, **k: rows.append(a[0] if a else "?")
        handler._execute_inner = lambda: {"status": "ok"}

        # The daemon's deadline fires while _execute_inner is still running.
        handler.retire_current_run()
        handler._backoff_state = {"blocked_until": "2026-08-30T12:00:00Z"}

        handler._run_generation = handler._retired_generation - 1
        handler.execute()

        assert handler._backoff_state.get("blocked_until") == "2026-08-30T12:00:00Z"
        assert rows == [], "the late thread wrote an ok row over the embargo"
