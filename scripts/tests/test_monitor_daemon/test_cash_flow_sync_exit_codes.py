"""Handler failure classification by exit code, not by stderr substring.

Plan items (docs/cash-flow-sync-overhaul.md):

  C3 — `_record_failure` currently decides "is this a throttle?" by
       searching the last three lines of the subprocess's stderr for
       `code 1001`. An undocumented code, or a stderr tail long enough to
       push the code out of the window, routes a hard failure into the
       soft lane and spends three more SendRequests that day. Worse, a
       PERMANENT error (revoked token, unknown query id) burns 3
       SendRequests every trading day forever and never escalates.

  C4 — missing `IB_FLEX_TOKEN` returns `{"status": "skip"}` today, which
       is not `error`, so `execute()` falls through to `_mark_success()`
       and RESETS the circuit breaker while syncing nothing. A dropped
       env var reports healthy forever. This is the worst hole in the
       file: it would mask every future failure.

⛔ No Flex requests. `subprocess.run` is stubbed on every path.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from monitor_daemon.handlers import cash_flow_sync as handler_mod
from monitor_daemon.handlers.cash_flow_sync import (
    MAX_SOFT_ATTEMPTS_PER_ET_DAY,
    CashFlowSyncHandler,
)


def _completed(returncode: int, status: dict | None = None) -> SimpleNamespace:
    stdout = ""
    if status is not None:
        stdout = f"Synced 0 cash flows.\n{json.dumps(status)}\n"
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def health_rows(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture every service_health row this handler writes."""
    import db.writer as writer_mod

    rows: list[dict] = []

    def _record(service, state, **kwargs):
        rows.append({"service": service, "state": state, **kwargs})

    monkeypatch.setattr(writer_mod, "record_service_health", _record)
    return rows


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_FLEX_TOKEN", "test-token")
    monkeypatch.setenv("IB_FLEX_NAV_QUERY_ID", "1442520")


def _run_with_exit(handler: CashFlowSyncHandler, returncode: int, status: dict | None):
    with patch.object(handler_mod.subprocess, "run",
                      return_value=_completed(returncode, status)):
        return handler.run()


class TestExitCodeClassification:
    def test_zero_is_success_and_resets_the_breaker(self, credentials, health_rows):
        handler = CashFlowSyncHandler()
        handler._backoff_state["throttle_count"] = 3

        result = _run_with_exit(handler, 0, {"status": "ok", "class": "ok", "rows": 16})

        assert result["status"] == "ok"
        assert handler._backoff_state["throttle_count"] == 0
        assert health_rows[-1]["state"] == "ok"

    def test_exit_ten_advances_the_throttle_ladder(self, credentials, health_rows):
        handler = CashFlowSyncHandler()

        _run_with_exit(handler, 10, {"status": "error", "class": "throttle", "code": "1001"})

        assert handler._backoff_state["throttle_count"] == 1
        assert handler._backoff_state["blocked_until"] is not None
        assert health_rows[-1]["state"] == "error"
        assert health_rows[-1]["error"]["next_attempt_at"]

    def test_exit_ten_is_classified_even_with_an_undocumented_code(
        self, credentials, health_rows
    ):
        """The substring matcher only knows 1001/1018/1019. The exit code
        carries the class regardless of which code IBKR invented."""
        handler = CashFlowSyncHandler()

        _run_with_exit(handler, 10, {"status": "error", "class": "throttle", "code": "1099"})

        assert handler._backoff_state["throttle_count"] == 1

    def test_exit_eleven_stops_retrying_for_the_rest_of_the_day(
        self, credentials, health_rows
    ):
        """A revoked token is not retryable. Today it burns 3 SendRequests
        every trading day, forever."""
        handler = CashFlowSyncHandler()

        _run_with_exit(
            handler, 11,
            {"status": "error", "class": "flex_app_error", "code": "1012"},
        )

        assert handler._backoff_state["throttle_count"] == 0
        assert handler._backoff_state["soft_attempts"] >= MAX_SOFT_ATTEMPTS_PER_ET_DAY
        now = datetime.now(timezone.utc)
        with patch.object(handler_mod, "_now_utc", return_value=now):
            assert handler._soft_budget_exhausted(now) is True

    def test_exit_fifteen_1025_embargoes_past_monday_0800(
        self, credentials, health_rows
    ):
        """Live 2026-08-21: class=permanent, next_attempt_at Monday 08:00 ET.
        That Monday SendRequest is what keeps the undocumented 1025 lockout
        alive. A lockout must outlast the next daily window."""
        from monitor_daemon.handlers import _throttle_backoff
        from monitor_daemon.handlers.cash_flow_sync import EXIT_FLEX_LOCKOUT

        # Friday 2026-08-21 09:58 ET = 13:58 UTC (the production attempt).
        friday = datetime(2026, 8, 21, 13, 58, 26, tzinfo=timezone.utc)
        monday_0800_et = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        handler = CashFlowSyncHandler()
        with patch.object(handler_mod, "_now_utc", return_value=friday), \
             patch("utils.flex_embargo.record_lockout"):
            _run_with_exit(
                handler, EXIT_FLEX_LOCKOUT,
                {"status": "error", "class": "lockout", "code": "1025"},
            )

        blocked = _throttle_backoff.blocked_until(handler._backoff_state)
        assert blocked is not None
        assert blocked >= monday_0800_et + timedelta(days=1)
        next_attempt = health_rows[-1]["error"]["next_attempt_at"]
        assert datetime.fromisoformat(next_attempt) >= monday_0800_et + timedelta(days=1)

        with patch.object(handler_mod, "_now_utc", return_value=monday_0800_et):
            assert handler.is_due() is False

    @pytest.mark.parametrize(
        "returncode,flex_class",
        [(12, "not_ready"), (13, "parse_error"), (14, "write_error")],
    )
    def test_soft_lane_exit_codes_do_not_escalate_the_ladder(
        self, credentials, health_rows, returncode, flex_class
    ):
        handler = CashFlowSyncHandler()

        _run_with_exit(handler, returncode, {"status": "error", "class": flex_class})

        assert handler._backoff_state["throttle_count"] == 0
        assert handler._backoff_state["soft_attempts"] == 1
        assert health_rows[-1]["state"] == "error"

    def test_a_soft_failure_never_latches_last_run(self, credentials, health_rows):
        handler = CashFlowSyncHandler()
        _run_with_exit(handler, 12, {"status": "error", "class": "not_ready"})
        assert handler.last_run is None

    def test_legacy_stderr_substring_still_classifies_a_throttle(
        self, credentials, health_rows
    ):
        """Deprecated fallback: a subprocess from an older deploy exits 1
        with the code in stderr. Must still reach the ladder.

        1018 only. The fallback used to match 1001 and 1019 too, so a legacy
        subprocess reporting "still generating" reached the breaker lane."""
        handler = CashFlowSyncHandler()
        legacy = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERR: cash flow fetch failed: Flex throttle (code 1018): "
                   "Too many requests have been made from this token\n",
        )
        with patch.object(handler_mod.subprocess, "run", return_value=legacy):
            handler.run()

        assert handler._backoff_state["throttle_count"] == 1


class TestMissingConfigIsNeverGreen:
    def test_unset_env_writes_an_error_row(self, monkeypatch, health_rows):
        monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
        monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)
        handler = CashFlowSyncHandler()

        handler.run()

        assert health_rows, "a config error must still heartbeat"
        assert health_rows[-1]["state"] == "error"
        assert "IB_FLEX_TOKEN" in health_rows[-1]["error"]["message"]

    def test_unset_env_does_not_reset_the_circuit_breaker(self, monkeypatch, health_rows):
        monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
        monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)
        handler = CashFlowSyncHandler()
        handler._backoff_state["throttle_count"] = 2

        handler.run()

        assert handler._backoff_state["throttle_count"] == 2

    def test_unset_env_does_not_shorten_an_active_throttle_embargo(
        self, monkeypatch, health_rows
    ):
        """A config error must never buy a shorter re-probe than the
        throttle ladder already imposed."""
        monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
        monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)
        handler = CashFlowSyncHandler()
        embargo = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        handler._backoff_state["throttle_count"] = 3
        handler._backoff_state["blocked_until"] = embargo

        handler.run()

        assert handler._backoff_state["blocked_until"] == embargo

    def test_unset_env_does_not_latch_last_run(self, monkeypatch, health_rows):
        monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
        monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)
        handler = CashFlowSyncHandler()

        handler.run()

        assert handler.last_run is None

    def test_unset_env_makes_no_subprocess_call(self, monkeypatch, health_rows):
        monkeypatch.delenv("IB_FLEX_TOKEN", raising=False)
        monkeypatch.delenv("IB_FLEX_NAV_QUERY_ID", raising=False)
        handler = CashFlowSyncHandler()

        with patch.object(handler_mod.subprocess, "run") as run_mock:
            handler.run()

        run_mock.assert_not_called()


class TestLockoutReconstructedFromTurso:
    """Live 2026-08-21: sidecar gone, daemon_state blocked_until is Monday
    08:00 ET, service_health still holds class=permanent 1025. is_due at
    that window must stay False so the subprocess never starts."""

    def test_is_due_false_at_monday_0800_when_sidecar_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monday = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            "utils.flex_embargo.SIDECAR", tmp_path / "flex_token_embargo.json"
        )
        monkeypatch.setattr("utils.flex_embargo._heartbeat", lambda *a, **k: None)

        last_error = json.dumps({
            "message": (
                "ERR: Flex SendRequest failed (code 1025): "
                "Too many failed attempts. Please review your configuration."
            ),
            "class": "permanent",
            "next_attempt_at": "2026-08-24T12:00:00+00:00",
        })
        row = ("error", "2026-08-21T13:58:28.298780Z", last_error)

        def fake(sql, args=(), timeout=None):  # noqa: ANN001
            key = args[0] if args else None
            if key == "cash-flow-sync" or (not args and "cash-flow-sync" in (sql or "")):
                return [row]
            return []

        import db.hrana_http as hrana_mod

        monkeypatch.setattr(hrana_mod, "hrana_execute", fake)
        monkeypatch.setattr(hrana_mod, "hrana_query", fake)

        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):  # noqa: ANN001
                if tz is None:
                    return monday.replace(tzinfo=None)
                return monday.astimezone(tz)

        monkeypatch.setattr("utils.flex_embargo.datetime", _Frozen)

        handler = CashFlowSyncHandler()
        handler._backoff_state["blocked_until"] = "2026-08-24T12:00:00+00:00"

        with patch.object(handler_mod, "_now_utc", return_value=monday), \
             patch.object(handler_mod.subprocess, "run") as run_mock:
            assert handler.is_due() is False
            run_mock.assert_not_called()
