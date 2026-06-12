"""DUR-14 — alert escalation in the watchdog dispatcher.

  (a) P1 alerts use Pushover EMERGENCY priority (2) with retry/expire so
      the push repeats until acknowledged — both the per-service path and
      the grouped IB-outage path.
  (b) P2/P3 outcomes batch into a once-daily digest push carried by the
      daily watchdog bucket instead of vanishing into journalctl.
  (c) The Resend channel registration was dead code: enabled_channels()
      advertised a channel with NO emitter, and no RESEND_API_KEY exists
      in any environment (VPS + laptop checked 2026-06-12). It is deleted;
      setting the env vars must no longer register a phantom channel.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


def _outcome(service="vcg-scan", severity="P1", message="silent for 23m", kind="stale",
             now=None):
    from watchdog.check import CheckOutcome

    return CheckOutcome(
        service=service,
        kind=kind,
        status="stale" if kind == "stale" else "error",
        severity=severity,
        fired=True,
        message=message,
        consecutive_failures=2,
        now=now or datetime(2026, 6, 12, 14, 0, tzinfo=timezone.utc),
    )


@pytest.fixture(autouse=True)
def digest_state_path(tmp_path, monkeypatch):
    from watchdog import notify

    path = tmp_path / "watchdog_digest_state.json"
    monkeypatch.setattr(notify, "DIGEST_STATE_PATH", path)
    return path


@pytest.fixture
def pushover_env(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")


class TestP1EmergencyPriority:
    def test_p1_push_is_emergency_with_retry_and_expire(self, db_conn, pushover_env):
        from watchdog import notify

        with patch("watchdog.notify._http_post", return_value=(200, b"")) as http_post:
            notify.dispatch(_outcome(severity="P1"))

        [(args, _)] = [c for c in http_post.call_args_list]
        url, payload = args[0], args[1]
        assert "pushover" in url
        assert payload["priority"] == 2
        # Pushover API contract: retry >= 30s, expire <= 10800s.
        assert payload["retry"] >= 30
        assert 0 < payload["expire"] <= 10800

    def test_grouped_ib_push_is_emergency_too(self, db_conn, pushover_env):
        from watchdog import grouping

        with patch("watchdog.notify._http_post", return_value=(200, b"")) as http_post:
            error = grouping._emit_grouped_pushover(
                title="radon watchdog: IB Gateway awaiting_2fa",
                message="2 services degraded",
            )

        assert error is None
        [(args, _)] = http_post.call_args_list
        payload = args[1]
        assert payload["priority"] == 2
        assert payload["retry"] >= 30
        assert 0 < payload["expire"] <= 10800


class TestDigestEnqueue:
    def test_p2_dispatch_enqueues_instead_of_pushing(self, db_conn, pushover_env, digest_state_path):
        from watchdog import notify

        with patch("watchdog.notify._http_post", return_value=(200, b"")) as http_post:
            notify.dispatch(_outcome(service="cta-sync", severity="P2", kind="error",
                                     message="in error state: playwright timeout"))

        assert http_post.call_args_list == []  # no immediate push for P2
        state = json.loads(digest_state_path.read_text())
        [entry] = state["pending"]
        assert entry["service"] == "cta-sync"
        assert entry["severity"] == "P2"
        assert "playwright timeout" in entry["message"]

    def test_p1_dispatch_does_not_enqueue(self, db_conn, pushover_env, digest_state_path):
        from watchdog import notify

        with patch("watchdog.notify._http_post", return_value=(200, b"")):
            notify.dispatch(_outcome(severity="P1"))

        assert not digest_state_path.exists() or not (
            json.loads(digest_state_path.read_text()).get("pending")
        )


class TestDigestFlush:
    def _enqueue(self, *outcomes):
        from watchdog import notify

        with patch("watchdog.notify._http_post", return_value=(200, b"")):
            for o in outcomes:
                notify.dispatch(o)

    def test_flush_sends_one_batched_push(self, db_conn, pushover_env, digest_state_path):
        from watchdog import notify

        self._enqueue(
            _outcome(service="cta-sync", severity="P2", kind="error", message="boom"),
            _outcome(service="leap-scan", severity="P3", message="silent for 2d"),
        )
        now = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
        with patch("watchdog.notify._http_post", return_value=(200, b"")) as http_post:
            error = notify.flush_daily_digest(now=now)

        assert error is None
        [(args, _)] = http_post.call_args_list
        payload = args[1]
        assert "digest" in payload["title"].lower()
        assert "cta-sync" in payload["message"]
        assert "leap-scan" in payload["message"]
        assert payload.get("priority", 0) < 2  # never emergency

        state = json.loads(digest_state_path.read_text())
        assert state["pending"] == []
        assert state["last_sent_at"].startswith("2026-06-12")

    def test_flush_is_once_per_utc_day(self, db_conn, pushover_env, digest_state_path):
        from watchdog import notify

        self._enqueue(_outcome(service="cta-sync", severity="P2", kind="error", message="boom"))
        first = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
        with patch("watchdog.notify._http_post", return_value=(200, b"")) as first_post:
            assert notify.flush_daily_digest(now=first) is None
        assert len(first_post.call_args_list) == 1

        # New P3 arrives the same day — must wait for the next UTC day.
        self._enqueue(_outcome(service="leap-scan", severity="P3", message="late"))
        with patch("watchdog.notify._http_post", return_value=(200, b"")) as second_post:
            assert notify.flush_daily_digest(now=first + timedelta(hours=3)) is None
            assert second_post.call_args_list == []
            assert notify.flush_daily_digest(now=first + timedelta(days=1)) is None
            assert len(second_post.call_args_list) == 1

    def test_flush_with_empty_pending_is_noop(self, db_conn, pushover_env):
        from watchdog import notify

        with patch("watchdog.notify._http_post", return_value=(200, b"")) as http_post:
            assert notify.flush_daily_digest(now=datetime.now(timezone.utc)) is None
        assert http_post.call_args_list == []

    def test_flush_failure_keeps_pending_and_flags_dispatcher(
        self, db_conn, pushover_env, digest_state_path
    ):
        from watchdog import notify

        self._enqueue(_outcome(service="cta-sync", severity="P2", kind="error", message="boom"))
        now = datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc)
        with patch("watchdog.notify._http_post", return_value=(500, b"oops")):
            error = notify.flush_daily_digest(now=now)

        assert error and "500" in error
        state = json.loads(digest_state_path.read_text())
        assert len(state["pending"]) == 1  # retried on the next hourly daily-bucket run
        assert not state.get("last_sent_at")
        row = db_conn.execute(
            "SELECT state FROM service_health WHERE service='watchdog-alerts'"
        ).fetchone()
        assert row[0] == "error"

    def test_pending_is_capped(self, db_conn, pushover_env, digest_state_path):
        from watchdog import notify

        for i in range(notify.DIGEST_MAX_PENDING + 25):
            self._enqueue(_outcome(service="cta-sync", severity="P2", kind="error",
                                   message=f"boom {i}"))
        state = json.loads(digest_state_path.read_text())
        assert len(state["pending"]) == notify.DIGEST_MAX_PENDING


class TestResendDeleted:
    def test_resend_never_registers_even_with_env(self, monkeypatch):
        """The registration advertised a channel with no emitter — the
        startup log claimed coverage that did not exist. Deleted after
        verifying no RESEND_API_KEY exists in any env (VPS radon-cloud/.env,
        VPS unit files, laptop .env/web/.env/.zshrc — all zero matches)."""
        from watchdog import notify

        monkeypatch.setenv("RESEND_API_KEY", "re_123")
        monkeypatch.setenv("WATCHDOG_EMAIL_TO", "ops@example.com")
        channels = notify.enabled_channels()
        assert "resend" not in channels

    def test_no_resend_emitter_symbol(self):
        from watchdog import notify

        assert not hasattr(notify, "_resend_creds")
        assert not hasattr(notify, "_emit_resend")
