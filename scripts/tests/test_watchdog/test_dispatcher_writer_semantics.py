"""Dispatcher-only semantics for the ``watchdog-alerts`` service_health row.

The row reflects DISPATCHER HEALTH ONLY — "can the notifier reach the
channel and persist its own bookkeeping" — not the severity of the
last alert it forwarded. The dispatched alert event itself is logged
to journalctl/INFO; ``last_error`` on the row is reserved for actual
dispatcher failures (Pushover 500, DB write failure, etc).

Why this exists: prior to 2026-05-19 the row mirrored the dispatched
alert's kind — dispatching an ``error`` alert left ``state=error`` with
the downstream service's failure JSON in ``last_error``. When the
downstream service recovered between watchdog cycles, no ``healed``
event fired, and the row stayed ``error`` indefinitely. The UI banner
read ``last_error`` and surfaced it as a current outage — long after
recovery. See feedback_service_health_heartbeat.md +
feedback_banner_only_actionable.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


@pytest.fixture
def sample_outcome():
    from watchdog.check import CheckOutcome

    return CheckOutcome(
        service="journal-sync",
        kind="error",
        status="error",
        severity="P2",
        fired=True,
        message=(
            "in error state: Failed to connect to IB on 127.0.0.1:4001 "
            "after 1 attempt(s)"
        ),
        consecutive_failures=2,
        now=datetime(2026, 5, 19, 19, 10, 51, tzinfo=timezone.utc),
    )


@pytest.fixture
def p1_outcome():
    from watchdog.check import CheckOutcome

    return CheckOutcome(
        service="vcg-scan",
        kind="stale",
        status="stale",
        severity="P1",
        fired=True,
        message="silent for 23m (window 15m) — market open",
        consecutive_failures=2,
        now=datetime(2026, 5, 19, 14, 0, tzinfo=timezone.utc),
    )


def _read_watchdog_row(db_conn) -> dict | None:
    row = db_conn.execute(
        "SELECT state, last_error FROM service_health WHERE service='watchdog-alerts'"
    ).fetchone()
    if not row:
        return None
    return {"state": row[0], "last_error": row[1]}


class TestRowReflectsDispatcherHealthOnly:
    """The ``watchdog-alerts`` row stays ``state=ok`` so long as the
    dispatcher itself works. The dispatched alert's severity is
    irrelevant to the row state.
    """

    def test_p1_error_dispatch_leaves_row_state_ok(self, db_conn, monkeypatch, sample_outcome):
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with patch("watchdog.notify._http_post", return_value=(200, b"")):
            notify.dispatch(sample_outcome)

        row = _read_watchdog_row(db_conn)
        assert row is not None, "dispatch must still write a heartbeat row"
        assert row["state"] == "ok", (
            f"row state should reflect dispatcher health, not alert severity; "
            f"got state={row['state']!r}"
        )
        assert row["last_error"] is None or row["last_error"] in {"", "null"}, (
            f"last_error must be empty when dispatcher succeeded; got {row['last_error']!r}"
        )

    def test_p1_dispatch_without_external_channel_still_ok(self, db_conn, monkeypatch, p1_outcome):
        """No Pushover creds means service_health is the only channel.
        That's still a dispatcher success — the row stays ok.
        """
        from watchdog import notify

        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        notify.dispatch(p1_outcome)

        row = _read_watchdog_row(db_conn)
        assert row is not None
        assert row["state"] == "ok"
        assert row["last_error"] is None or row["last_error"] in {"", "null"}


class TestRowFlipsErrorOnDispatcherFailure:
    """Real dispatcher failures (Pushover 5xx, DB write exception) do
    flip the row to ``state=error`` so the banner can surface the
    notifier outage. The ``last_error`` carries a dispatcher-specific
    error string — never the downstream alert JSON.
    """

    def test_pushover_5xx_flips_row_to_error(self, db_conn, monkeypatch, p1_outcome):
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        def fake_post(url, payload, headers=None):
            return (500, b"internal server error")

        with patch("watchdog.notify._http_post", side_effect=fake_post):
            notify.dispatch(p1_outcome)

        row = _read_watchdog_row(db_conn)
        assert row is not None
        assert row["state"] == "error", (
            f"Pushover 5xx must flip the row to error; got state={row['state']!r}"
        )
        # Error message must describe the dispatcher failure, NOT the alert.
        err_str = row["last_error"] or ""
        assert "pushover" in err_str.lower() or "5" in err_str, (
            f"last_error must describe dispatcher failure; got {err_str!r}"
        )
        # Critically, downstream service detail must NOT leak in here.
        assert "vcg-scan" not in err_str, (
            f"downstream alert detail must not leak into dispatcher row; got {err_str!r}"
        )


class TestDbWriteFailureIsBestEffort:
    """When ``record_service_health`` itself raises (DB unreachable,
    schema drift, etc.), the dispatcher cannot write its own error row
    — there is no DB to write to. Per
    ``feedback_service_health_heartbeat.md`` the failure is logged and
    swallowed so the bucket cycle completes.
    """

    def test_db_write_failure_is_swallowed(self, db_conn, monkeypatch, p1_outcome, caplog):
        import logging
        from unittest.mock import patch
        from watchdog import notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        def boom(*args, **kwargs):
            raise RuntimeError("db unreachable")

        with caplog.at_level(logging.WARNING, logger="watchdog.notify"):
            with patch("watchdog.notify._http_post", return_value=(200, b"")), \
                 patch("db.writer.record_service_health", side_effect=boom):
                # Must not raise.
                notify.dispatch(p1_outcome)

        # The warning carries the underlying error so journalctl shows it.
        joined = " ".join(r.message for r in caplog.records)
        assert "db unreachable" in joined or "row write failed" in joined.lower()


class TestGroupedDispatchSemantics:
    """The grouping path follows the same rule: the grouped Pushover
    succeeding leaves ``watchdog-alerts`` at state=ok. The grouped
    message content (downstream services + count) goes to journalctl,
    not into ``last_error``.
    """

    def test_grouped_dispatch_leaves_row_ok(self, db_conn, monkeypatch):
        from watchdog import check, grouping

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")

        # 11:00 ET — past the 35m open-bell grace so the seeded scanners are
        # genuinely stale (10:00 ET would be inside cri-scan's grace window).
        fresh_now = datetime(2026, 5, 19, 15, 0, tzinfo=timezone.utc)

        # Seed 4 IB intraday services as past-window so all 4 fire.
        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            db_conn.execute(
                """
                INSERT OR REPLACE INTO service_health
                  (service, state, last_attempt_started_at, last_attempt_finished_at,
                   last_error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    svc,
                    "ok",
                    None,
                    (fresh_now - timedelta(minutes=60)).isoformat().replace("+00:00", "Z"),
                    None,
                    (fresh_now - timedelta(minutes=60)).isoformat().replace("+00:00", "Z"),
                ),
            )
        db_conn.commit()

        # Pre-trip hysteresis.
        for tick in (fresh_now, fresh_now + timedelta(minutes=5)):
            check.check_bucket(bucket="intraday", now=tick)
        report = check.check_bucket(bucket="intraday", now=fresh_now + timedelta(minutes=10))
        fired = [o for o in report.outcomes if o.fired]
        assert len(fired) == 4

        with patch("watchdog.notify._http_post", return_value=(200, b"")), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}):
            grouping.dispatch_with_grouping(
                outcomes=fired, now=fresh_now + timedelta(minutes=10)
            )

        row = _read_watchdog_row(db_conn)
        assert row is not None
        assert row["state"] == "ok", (
            f"grouped dispatch must leave row state=ok; got {row['state']!r}"
        )
        # Downstream service names must NOT live in last_error.
        err_str = row["last_error"] or ""
        for svc in ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync"):
            assert svc not in err_str, (
                f"downstream service {svc} must not appear in watchdog-alerts.last_error; "
                f"got {err_str!r}"
            )


class TestAlertEventStillLoggedToJournal:
    """When we stop writing alert content to ``service_health``, the
    event must still be visible to operators via journalctl/stdout.
    """

    def test_dispatch_logs_alert_to_journal(self, db_conn, monkeypatch, sample_outcome, caplog):
        from watchdog import notify
        import logging

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        with caplog.at_level(logging.INFO, logger="watchdog.notify"):
            with patch("watchdog.notify._http_post", return_value=(200, b"")):
                notify.dispatch(sample_outcome)

        joined = "\n".join(r.message for r in caplog.records)
        # Some structured key identifying the dispatched alert must be present
        # (e.g. "dispatched" + service name + severity).
        assert "journal-sync" in joined, f"alert event lost: {joined!r}"
        assert "P2" in joined or "error" in joined.lower(), (
            f"severity/kind missing from log: {joined!r}"
        )


class TestMigrationScript:
    """The migration script cleans up legacy rows that carry alert
    payloads in ``last_error``, but leaves real dispatcher errors alone.
    """

    def test_clears_legacy_alert_payload(self, db_conn):
        from db.migrate_watchdog_alerts_row import clean_watchdog_alerts_row

        legacy_payload = {
            "service": "journal-sync",
            "severity": "P2",
            "kind": "error",
            "message": "in error state: Failed to connect to IB on 127.0.0.1:4001",
            "consecutive_failures": 2,
        }
        db_conn.execute(
            """
            INSERT INTO service_health
              (service, state, last_attempt_started_at, last_attempt_finished_at,
               last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "watchdog-alerts",
                "error",
                None,
                "2026-05-19T19:10:51Z",
                json.dumps(legacy_payload),
                "2026-05-19T19:10:51Z",
            ),
        )
        db_conn.commit()

        outcome = clean_watchdog_alerts_row()

        row = db_conn.execute(
            "SELECT state, last_error FROM service_health WHERE service='watchdog-alerts'"
        ).fetchone()
        assert row[0] == "ok", "legacy alert payload row must be reset to ok"
        assert row[1] is None, f"legacy last_error must be cleared; got {row[1]!r}"
        assert outcome["cleared"] is True

    def test_leaves_real_dispatcher_error_alone(self, db_conn):
        from db.migrate_watchdog_alerts_row import clean_watchdog_alerts_row

        # Looks like a dispatcher failure — opaque string, NOT JSON-with-alert-keys.
        dispatcher_err = "pushover 500: internal server error"
        db_conn.execute(
            """
            INSERT INTO service_health
              (service, state, last_attempt_started_at, last_attempt_finished_at,
               last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "watchdog-alerts",
                "error",
                None,
                "2026-05-19T19:10:51Z",
                dispatcher_err,
                "2026-05-19T19:10:51Z",
            ),
        )
        db_conn.commit()

        outcome = clean_watchdog_alerts_row()

        row = db_conn.execute(
            "SELECT state, last_error FROM service_health WHERE service='watchdog-alerts'"
        ).fetchone()
        assert row[0] == "error", "real dispatcher failure must be preserved"
        assert row[1] == dispatcher_err, (
            f"real dispatcher last_error must be preserved; got {row[1]!r}"
        )
        assert outcome["cleared"] is False

    def test_is_idempotent(self, db_conn):
        from db.migrate_watchdog_alerts_row import clean_watchdog_alerts_row

        # No row at all — should be a no-op.
        out1 = clean_watchdog_alerts_row()
        out2 = clean_watchdog_alerts_row()
        assert out1["cleared"] is False
        assert out2["cleared"] is False

        # Legacy row → clean → second pass is a no-op (row is now ok).
        legacy_payload = {
            "service": "journal-sync",
            "severity": "P2",
            "kind": "error",
            "message": "x",
        }
        db_conn.execute(
            """
            INSERT INTO service_health
              (service, state, last_attempt_started_at, last_attempt_finished_at,
               last_error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "watchdog-alerts",
                "error",
                None,
                "2026-05-19T19:10:51Z",
                json.dumps(legacy_payload),
                "2026-05-19T19:10:51Z",
            ),
        )
        db_conn.commit()
        out3 = clean_watchdog_alerts_row()
        out4 = clean_watchdog_alerts_row()
        assert out3["cleared"] is True
        assert out4["cleared"] is False, "second pass must be a no-op once row is ok"
