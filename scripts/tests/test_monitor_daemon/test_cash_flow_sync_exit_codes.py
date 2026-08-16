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
        with the code in stderr. Must still reach the ladder."""
        handler = CashFlowSyncHandler()
        legacy = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERR: cash flow fetch failed: Flex throttle (code 1001): busy\n",
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
