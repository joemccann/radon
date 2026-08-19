"""MenthorQ dashboard LIVE login probe.

The metadata-only `menthorq-session` check cannot see a broken re-login
chain: the 2026-08-07 incident (WAF blocking Playwright's HeadlessChrome
UA) left the jar's durable authjs cookies looking healthy while every
actual re-login silently failed. This probe closes that blind spot by
exercising the real serving path once a day: GET /options/exposure on
the local FastAPI, whose provider client owns the credential re-login.

Contract:
- HTTP 200 with a payload -> healthy (DUR-14 auto-ok row).
- HTTP 503 whose detail names authentication -> the login chain is
  broken: a PERSISTENT condition -> error row, last_run latches (the
  CHECK-handler exception; never retry a broken chain every 30s).
- Anything else (connection refused, timeout, 5xx) -> transient soft
  failure: last_run does NOT latch, retries are spaced by a 5-min
  embargo, and after MAX_TRANSIENT_ATTEMPTS_PER_DAY the handler writes
  the error row and burns the daily slot so it can never spin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import requests


NOW = datetime.now(timezone.utc)


def _handler():
    from monitor_daemon.handlers.menthorq_login_probe import MenthorQLoginProbe

    return MenthorQLoginProbe()


def _response(status_code: int, payload: object) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _patch_get(response=None, exc=None):
    target = "monitor_daemon.handlers.menthorq_login_probe.requests.get"
    if exc is not None:
        return patch(target, side_effect=exc)
    return patch(target, return_value=response)


class TestLoginProbeVerdicts:
    def test_successful_probe_reports_ok_and_latches(self):
        handler = _handler()
        ok = _response(200, {"symbol": "SPX", "cells": [{"net_gex": 1.0}]})
        with _patch_get(response=ok), \
                patch.object(handler, "record_cycle_health") as health:
            result = handler.run()
        assert result["status"] == "ok"
        assert handler.last_run is not None
        assert health.call_args[0][0] == "ok"

    def test_auth_503_writes_error_row_and_latches(self):
        """The reason this probe exists: a broken re-login chain."""
        handler = _handler()
        broken = _response(
            503, {"detail": "Options exposure authentication is unavailable"}
        )
        with _patch_get(response=broken), \
                patch.object(
                    handler, "record_cycle_health",
                    wraps=handler.record_cycle_health,
                ) as health:
            result = handler.run()
        assert result["status"] == "ok"  # latched: persistent condition
        assert handler.last_run is not None
        assert health.call_args[0][0] == "error"
        message = health.call_args[1]["error"]["message"]
        assert "authentication" in message

    def test_connection_error_is_soft_failure_with_embargo(self):
        handler = _handler()
        with _patch_get(exc=requests.ConnectionError("refused")):
            result = handler.run()
        assert result["status"] == "error"
        assert handler.last_run is None  # not latched -> retried
        assert handler.is_due() is False  # embargoed, not every 30s

    def test_embargo_expires_and_probe_retries(self):
        from monitor_daemon.handlers import menthorq_login_probe as mod

        handler = _handler()
        with _patch_get(exc=requests.Timeout("slow")):
            handler.run()
        later = handler._retry_after + timedelta(seconds=30)
        with patch.object(mod, "_now_utc", return_value=later):
            assert handler.is_due() is True

    def test_transient_budget_exhaustion_errors_row_and_latches(self):
        from monitor_daemon.handlers import menthorq_login_probe as mod

        handler = _handler()
        # Midday anchor: the loop advances ~16.5 minutes and the handler keys
        # its attempt budget to the UTC DATE — a wall-clock NOW within 16.5
        # minutes of UTC midnight crossed the boundary, reset the counter, and
        # turned the final attempt into a fresh day's soft failure (CI ran at
        # 23:50 UTC and failed exactly this way, 2026-08-19).
        moment = NOW.replace(hour=12, minute=0, second=0, microsecond=0)
        for attempt in range(mod.MAX_TRANSIENT_ATTEMPTS_PER_DAY):
            moment += timedelta(seconds=mod.RETRY_EMBARGO_SECONDS + 30)
            with _patch_get(exc=requests.ConnectionError("refused")), \
                    patch.object(mod, "_now_utc", return_value=moment), \
                    patch.object(
                        handler, "record_cycle_health",
                        wraps=handler.record_cycle_health,
                    ) as health:
                result = handler.run()
        assert result["status"] == "ok"  # final attempt burns the daily slot
        assert handler.last_run is not None
        assert health.call_args[0][0] == "error"

    def test_upstream_503_is_transient_not_auth(self):
        """503 'data is unavailable' = provider flake, not a broken login."""
        handler = _handler()
        flake = _response(503, {"detail": "Options exposure data is unavailable"})
        with _patch_get(response=flake):
            result = handler.run()
        assert result["status"] == "error"
        assert handler.last_run is None


class TestLoginProbeStatePersistence:
    def test_transient_bookkeeping_survives_restart(self):
        handler = _handler()
        with _patch_get(exc=requests.ConnectionError("refused")):
            handler.run()
        restored = _handler()
        restored.set_state(handler.get_state())
        assert restored.is_due() is False  # embargo survives the restart
