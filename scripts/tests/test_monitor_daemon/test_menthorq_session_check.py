"""MenthorQ dashboard session liveness check.

2026-08-07: the dashboard cookie jar's `cognito` cookie expired 2026-07-27
and NOTHING noticed for 11 days — /options/net-gex was dark for every
ticker until a human opened the page. Unit tests could not catch it (they
mock the provider), and the exposure path had no service_health row, no
freshness window, and no watchdog entry.

This is the monitoring twin of `flex-token-check`: a cheap daily probe of
the jar's own expiry metadata (no browser, no network) that publishes a
`menthorq-session` row so the existing watchdog pages on it.

The handler reports its finding through record_cycle_health (the
explicitly-allowed CHECK-handler exception to the writer-state rule) and
still latches last_run — a dead session is a persistent condition, not a
transient failure to retry every 30s.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Window-relative, not a pinned calendar date (hardcoded dates rot in CI).
# The handler reads the real clock, so fixtures must too; the +12h offset
# keeps the floor-division day count deterministic regardless of run time.
NOW = datetime.now(timezone.utc)


def _in_days(days: float) -> datetime:
    return NOW + timedelta(days=days, hours=12)


def _jar(path: Path, cookies: list[dict]) -> Path:
    path.write_text(json.dumps({"cookies": cookies, "origins": []}))
    return path


def _cookie(name: str, *, expires_at: datetime | None, domain: str = ".menthorq.io") -> dict:
    return {
        "name": name,
        "value": "x",
        "domain": domain,
        "path": "/",
        "expires": -1 if expires_at is None else expires_at.timestamp(),
    }


def _handler(jar_path: Path):
    from monitor_daemon.handlers.menthorq_session_check import MenthorQSessionCheck

    handler = MenthorQSessionCheck()
    handler._storage_state_path = jar_path
    return handler


class TestSessionExpiryEvaluation:
    def test_live_session_reports_ok_with_days_remaining(self, tmp_path: Path):
        jar = _jar(tmp_path / "jar.json", [
            _cookie("cognito", expires_at=_in_days(9)),
            _cookie("__Secure-authjs.session-token.0", expires_at=_in_days(19)),
        ])
        handler = _handler(jar)
        with patch.object(handler, "record_cycle_health") as health:
            result = handler.execute()
        assert result["expired"] is False
        assert result["days_remaining"] == 9  # the EARLIEST auth cookie governs
        assert health.call_args is None or health.call_args[0][0] != "error"

    def test_expired_auth_cookie_reports_error(self, tmp_path: Path):
        jar = _jar(tmp_path / "jar.json", [
            # The exact production shape: session tokens still valid, the
            # cognito auth cookie dead — the jar LOOKS healthy at a glance.
            _cookie("cognito", expires_at=NOW - timedelta(days=11)),
            _cookie("__Secure-authjs.session-token.0", expires_at=_in_days(19)),
        ])
        handler = _handler(jar)
        with patch.object(handler, "record_cycle_health") as health:
            result = handler.execute()
        assert result["expired"] is True
        assert health.called
        assert health.call_args[0][0] == "error"
        assert "menthorq" in json.dumps(health.call_args[1]["error"]).lower()

    def test_missing_jar_reports_error_not_silence(self, tmp_path: Path):
        handler = _handler(tmp_path / "absent.json")
        with patch.object(handler, "record_cycle_health") as health:
            result = handler.execute()
        assert result["expired"] is True
        assert health.called and health.call_args[0][0] == "error"

    def test_session_nearing_expiry_warns_before_it_dies(self, tmp_path: Path):
        jar = _jar(tmp_path / "jar.json", [
            _cookie("cognito", expires_at=_in_days(2)),
        ])
        handler = _handler(jar)
        with patch.object(handler, "record_cycle_health") as health:
            result = handler.execute()
        assert result["expired"] is False
        assert result["should_warn"] is True
        assert health.called and health.call_args[0][0] == "error"

    def test_non_auth_cookies_do_not_govern_expiry(self, tmp_path: Path):
        """Analytics cookies expire constantly and mean nothing — only the
        auth cookies decide whether the dashboard still authenticates."""
        jar = _jar(tmp_path / "jar.json", [
            _cookie("_hjSession_5063650", expires_at=NOW - timedelta(hours=1)),
            _cookie("_uetsid", expires_at=NOW - timedelta(hours=2)),
            _cookie("cognito", expires_at=_in_days(12)),
        ])
        handler = _handler(jar)
        with patch.object(handler, "record_cycle_health"):
            result = handler.execute()
        assert result["expired"] is False
        assert result["days_remaining"] == 12

    def test_session_cookies_alone_are_sufficient_when_no_cognito(self, tmp_path: Path):
        jar = _jar(tmp_path / "jar.json", [
            _cookie("__Secure-authjs.session-token.0", expires_at=_in_days(5)),
        ])
        handler = _handler(jar)
        with patch.object(handler, "record_cycle_health"):
            result = handler.execute()
        assert result["expired"] is False
        assert result["days_remaining"] == 5

    def test_jar_without_any_auth_cookie_is_an_error(self, tmp_path: Path):
        jar = _jar(tmp_path / "jar.json", [
            _cookie("_uetsid", expires_at=_in_days(30)),
        ])
        handler = _handler(jar)
        with patch.object(handler, "record_cycle_health") as health:
            result = handler.execute()
        assert result["expired"] is True
        assert health.called and health.call_args[0][0] == "error"

    def test_malformed_jar_is_an_error_not_a_crash(self, tmp_path: Path):
        bad = tmp_path / "jar.json"
        bad.write_text("{not json")
        handler = _handler(bad)
        with patch.object(handler, "record_cycle_health") as health:
            result = handler.execute()
        assert result["expired"] is True
        assert health.called and health.call_args[0][0] == "error"


class TestHandlerContract:
    def test_daily_cadence_and_service_name(self, tmp_path: Path):
        handler = _handler(tmp_path / "jar.json")
        assert handler.service_name == "menthorq-session"
        assert handler.interval_seconds == 86400
        assert handler.requires_market_hours is False

    def test_dead_session_still_latches_last_run(self, tmp_path: Path):
        """A dead session is persistent, not transient: returning
        status='error' would trip HandlerSoftFailure and re-run every 30s,
        rewriting the row forever. The finding rides record_cycle_health."""
        handler = _handler(tmp_path / "absent.json")
        with patch.object(handler, "record_cycle_health"):
            result = handler.execute()
        assert result.get("status") != "error"


class TestRegistrationContract:
    """A new scheduled writer means FOUR places, same change."""

    def test_registered_in_python_scheduled_services(self):
        from watchdog import services

        assert "menthorq-session" in services.SCHEDULED_SERVICES

    def test_registered_in_the_daily_bucket(self):
        from watchdog import services

        assert "menthorq-session" in services.BUCKETS["daily"]

    def test_typescript_window_mirrors_python(self):
        import re

        ts = Path(__file__).resolve().parents[3] / "web" / "lib" / "serviceHealthWindows.ts"
        source = ts.read_text()
        assert '"menthorq-session"' in source
        entry = re.search(r'"menthorq-session":\s*\{[^}]*\}', source)
        assert entry and "scheduled" in entry.group(0)
        assert "requires_ib: false" in entry.group(0)


@pytest.mark.parametrize("stamp", ["expired", "live"])
def test_check_never_launches_a_browser(tmp_path: Path, stamp: str):
    """The probe must stay cheap: reading the jar's own metadata, never a
    Playwright launch (the failing exposure path costs ~28s precisely
    because it launches chromium twice before giving up)."""
    delta = timedelta(days=-1) if stamp == "expired" else timedelta(days=5)
    jar = _jar(tmp_path / "jar.json", [_cookie("cognito", expires_at=NOW + delta)])
    handler = _handler(jar)
    with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
        with patch.object(handler, "record_cycle_health"):
            handler.execute()
