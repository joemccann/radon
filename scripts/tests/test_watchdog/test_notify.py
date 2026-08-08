"""Notification channel dispatch.

Channels are pluggable; each is disabled when its env vars are unset.
Tests mock urllib so no live HTTP is made — the watchdog must be safe
to import + run in CI without network access.

Discord was removed 2026-05-19 (operator dropped Discord, keeping
Pushover only). Tests now assert (a) Pushover-only happy path,
(b) graceful degradation with no external channel configured.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def sample_alert():
    from watchdog.check import CheckOutcome

    return CheckOutcome(
        service="vcg-scan",
        kind="stale",
        status="stale",
        severity="P1",
        fired=True,
        message="silent for 23m (window 15m) — market open",
        consecutive_failures=2,
        now=datetime(2026, 5, 11, 14, 0, tzinfo=timezone.utc),
    )


class TestChannelGating:
    def test_pushover_disabled_without_creds(self, monkeypatch, sample_alert):
        from watchdog import notify

        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        channels = notify.enabled_channels()
        assert "pushover" not in channels

    def test_resend_disabled_without_keys(self, monkeypatch, sample_alert):
        from watchdog import notify

        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("WATCHDOG_EMAIL_TO", raising=False)
        channels = notify.enabled_channels()
        assert "resend" not in channels

    def test_service_health_log_is_always_enabled(self, monkeypatch):
        from watchdog import notify

        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        channels = notify.enabled_channels()
        assert "service_health" in channels

    def test_pushover_enabled_with_creds(self, monkeypatch):
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u123abc")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t456def")
        assert "pushover" in notify.enabled_channels()

    def test_discord_env_var_is_ignored(self, monkeypatch):
        """Regression: setting the old Discord env var must not enable
        any channel — Discord support was removed 2026-05-19."""
        from watchdog import notify

        monkeypatch.setenv("DISCORD_WATCHDOG_WEBHOOK_URL", "https://discord.example/x")
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        channels = notify.enabled_channels()
        assert "discord" not in channels
        assert channels == {"service_health"}


class TestPushoverPayload:
    def test_pushover_only_for_p1(self, db_conn, monkeypatch, sample_alert):
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("watchdog.notify._http_post") as http_post:
            http_post.return_value = (200, b"")
            notify.dispatch(sample_alert)
            pushover_calls = [c for c in http_post.call_args_list if "pushover" in c[0][0]]
            assert len(pushover_calls) == 1
            url, payload = pushover_calls[0][0][:2]
            assert url == "https://api.pushover.net/1/messages.json"
            assert payload["user"] == "u"
            assert payload["token"] == "t"
            assert "vcg-scan" in payload["title"]
            assert "silent for 23m" in payload["message"]

    def test_pushover_skipped_for_p3(self, db_conn, monkeypatch, sample_alert):
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        sample_alert.severity = "P3"
        with patch("watchdog.notify._http_post") as http_post:
            http_post.return_value = (200, b"")
            notify.dispatch(sample_alert)
            pushover_calls = [c for c in http_post.call_args_list if "pushover" in c[0][0]]
            assert pushover_calls == []


class TestServiceHealthLog:
    def test_writes_watchdog_alerts_row(self, db_conn, monkeypatch, sample_alert):
        """Contract change 2026-05-19: ``watchdog-alerts`` reflects
        DISPATCHER HEALTH, not the dispatched alert. A successful
        dispatch leaves the row at state=ok with no downstream content
        in last_error. See feedback_banner_only_actionable.md and the
        ``test_dispatcher_writer_semantics`` suite for the full
        contract.
        """
        from watchdog import notify

        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        notify.dispatch(sample_alert)
        rows = db_conn.execute(
            "SELECT service, state, last_error FROM service_health WHERE service='watchdog-alerts'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "ok"
        # Downstream alert content must NOT leak into last_error.
        assert rows[0][2] is None or "vcg-scan" not in (rows[0][2] or "")


class TestCooldownDeliverySemantics:
    def test_failed_pushover_does_not_start_cooldown(self, db_conn, sample_alert):
        from watchdog import notify

        with (
            patch(
                "watchdog.notify._emit_pushover",
                return_value=notify.ChannelResult(attempted=True, error="pushover 503"),
            ),
            patch("watchdog.notify.cooldown_mod.mark_notified") as mark_notified,
        ):
            notify.dispatch(sample_alert)

        mark_notified.assert_not_called()

    def test_successful_pushover_starts_cooldown(self, db_conn, sample_alert):
        from watchdog import notify

        with (
            patch(
                "watchdog.notify._emit_pushover",
                return_value=notify.ChannelResult(attempted=True),
            ),
            patch("watchdog.notify.cooldown_mod.mark_notified") as mark_notified,
        ):
            notify.dispatch(sample_alert)

        mark_notified.assert_called_once()


class TestHeartbeatOk:
    """`heartbeat_ok` writes `watchdog-alerts=ok` so a single fired alert
    doesn't latch the row at state=error forever between fires. See
    feedback_service_health_heartbeat.md.
    """

    def test_writes_ok_row_on_clean_cycle(self, db_conn):
        from watchdog import notify

        now = datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc)
        notify.heartbeat_ok(bucket="continuous", now=now)
        rows = db_conn.execute(
            "SELECT service, state, last_error FROM service_health WHERE service='watchdog-alerts'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "ok"
        assert "heartbeat_at" in rows[0][2]
        assert "continuous" in rows[0][2]

    def test_heartbeat_overwrites_prior_dispatcher_error(self, db_conn, sample_alert):
        """When an earlier cycle hit a dispatcher failure (e.g. Pushover
        5xx) and wrote ``state=error``, the next clean heartbeat must
        overwrite the row back to ``state=ok``.

        Note: post-2026-05-19 a successful dispatch does NOT write
        ``state=error`` — only dispatcher failures do. So the test
        simulates a dispatcher failure by patching ``_http_post`` to
        return 500.
        """
        from watchdog import notify
        from unittest.mock import patch

        # Earlier cycle: dispatch hits a Pushover 5xx → row flips to error.
        import os
        os.environ["PUSHOVER_USER"] = "u"
        os.environ["PUSHOVER_TOKEN"] = "t"
        try:
            with patch("watchdog.notify._http_post", return_value=(500, b"oops")):
                notify.dispatch(sample_alert)
            row_after_dispatch = db_conn.execute(
                "SELECT state FROM service_health WHERE service='watchdog-alerts'"
            ).fetchone()
            assert row_after_dispatch[0] == "error"

            # Next clean cycle: heartbeat ok overwrites it.
            now = datetime(2026, 5, 14, 15, 0, tzinfo=timezone.utc)
            notify.heartbeat_ok(bucket="continuous", now=now)
            row_after_heartbeat = db_conn.execute(
                "SELECT state FROM service_health WHERE service='watchdog-alerts'"
            ).fetchone()
            assert row_after_heartbeat[0] == "ok"
        finally:
            del os.environ["PUSHOVER_USER"]
            del os.environ["PUSHOVER_TOKEN"]


class TestStartupWarning:
    def test_warns_when_only_service_health_enabled(self, monkeypatch, capsys):
        """No external channel configured → warn so the operator notices
        alerts will only land in service_health."""
        from watchdog import notify

        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        notify.log_startup_warning()
        captured = capsys.readouterr()
        assert "warning" in captured.err.lower() or "warn" in captured.err.lower()
        assert "PUSHOVER" in captured.err

    def test_no_warning_when_pushover_enabled(self, monkeypatch, capsys):
        """Pushover-only happy path: external channel present, startup
        prints the enabled-channels line, not the warning."""
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        notify.log_startup_warning()
        captured = capsys.readouterr()
        assert "no external channel" not in captured.err.lower()
        assert "pushover" in captured.err
