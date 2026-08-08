"""T-039: an unpaged P1 must not arm the cooldown.

``_emit_pushover`` returned ``None`` both when Pushover accepted the push
and when there were no credentials to push with. ``dispatch`` read that
``None`` as delivery and stamped ``watchdog_cooldowns.last_outcome =
'notified'``, so a P1 that fired during a credentials gap was muted for
the full 24h re-arm ceiling (``cooldown.REARM_CEILING``) — the operator
never got the page and never got a retry either.

Contract pinned here:

  * P1 + no credentials  → no cooldown stamp, condition stays fireable,
    ``watchdog-alerts`` row stays ``ok`` (documented degraded mode).
  * P1 + HALF credentials → no cooldown stamp AND ``state=error`` on the
    dispatcher row, because half-configured is env drift, not a mode.
  * P1 + credentials + 2xx → cooldown armed (control).
  * P1 + credentials + 5xx → no cooldown stamp (pre-existing behaviour,
    guarded here so the refactor cannot regress it).
  * P2/P3 → cooldown armed with or without credentials; the daily digest
    is their channel and it always accepts the hand-off.

Drop-in for scripts/tests/test_watchdog/. Uses the suite's ``db_conn``
fixture (in-memory sqlite + patched hrana transport).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


NOW = datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc)
FIVE_MIN_LATER = NOW + timedelta(minutes=5)


def _outcome(*, service: str = "portfolio-sync", severity: str = "P1"):
    from watchdog.check import CheckOutcome

    return CheckOutcome(
        service=service,
        kind="stale",
        status="stale",
        severity=severity,
        fired=True,
        message="silent for 23m (window 10m) — market open",
        consecutive_failures=2,
        now=NOW,
    )


def _cooldown_rows(db_conn, service: str) -> list[tuple]:
    return db_conn.execute(
        "SELECT kind, last_notified_at, last_outcome FROM watchdog_cooldowns "
        "WHERE service=?",
        (service,),
    ).fetchall()


def _watchdog_alerts_row(db_conn) -> dict | None:
    row = db_conn.execute(
        "SELECT state, last_error FROM service_health WHERE service='watchdog-alerts'"
    ).fetchone()
    return None if not row else {"state": row[0], "last_error": row[1]}


def _no_creds(monkeypatch) -> None:
    monkeypatch.delenv("PUSHOVER_USER", raising=False)
    monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)


def _with_creds(monkeypatch) -> None:
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")


class TestUnpagedP1StaysFireable:
    def test_no_creds_p1_leaves_cooldown_rearmable(self, db_conn, monkeypatch):
        """The bug: a P1 that never reached a channel muted the next 24h."""
        from watchdog import cooldown, notify

        _no_creds(monkeypatch)
        outcome = _outcome()

        notify.dispatch(outcome)

        assert cooldown.cooldown_allows_fire(
            service="portfolio-sync", severity="P1", now=FIVE_MIN_LATER
        ) is True, (
            "an unpaged P1 must stay fireable — stamping the cooldown here "
            "mutes the condition for up to REARM_CEILING (24h)"
        )
        assert _cooldown_rows(db_conn, "portfolio-sync") == [], (
            "no cooldown row should exist for a P1 that was never delivered"
        )

    def test_no_creds_p1_keeps_dispatcher_row_ok(self, db_conn, monkeypatch):
        """Both creds absent is the documented service_health-only mode
        (``log_startup_warning``): the row stays ok and carries no
        downstream alert content."""
        from watchdog import notify

        _no_creds(monkeypatch)

        notify.dispatch(_outcome())

        row = _watchdog_alerts_row(db_conn)
        assert row is not None, "dispatch must still write the heartbeat row"
        assert row["state"] == "ok"
        assert "portfolio-sync" not in (row["last_error"] or "")

    def test_no_creds_p1_logs_the_unpaged_warning(self, db_conn, monkeypatch, caplog):
        from watchdog import notify

        _no_creds(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="watchdog.notify"):
            notify.dispatch(_outcome())

        joined = " ".join(record.getMessage() for record in caplog.records)
        assert "portfolio-sync" in joined and "NOT paged" in joined, (
            f"an unpaged P1 must be visible in journalctl; got {joined!r}"
        )

    def test_no_creds_p1_is_not_a_dispatcher_error(self, db_conn, monkeypatch):
        """``dispatch`` returns the dispatcher-error string that grouping
        aggregates onto the row. An unconfigured channel is not one."""
        from watchdog import notify

        _no_creds(monkeypatch)
        assert notify.dispatch(_outcome(), record_health=False) is None

    def test_repeat_cycle_pages_once_creds_return(self, db_conn, monkeypatch):
        """End-to-end of the incident: cycle 1 cannot page, cycle 2 can —
        and does, because cycle 1 did not mute it."""
        from watchdog import cooldown, grouping, notify

        _no_creds(monkeypatch)
        first = _outcome()
        assert cooldown.cooldown_allows_fire(
            service="portfolio-sync", severity="P1", now=first.now
        ) is True
        notify.dispatch(first)

        _with_creds(monkeypatch)
        second = _outcome()
        second.now = FIVE_MIN_LATER
        pushes: list[tuple] = []

        def fake_post(url, payload, headers=None):
            pushes.append((url, payload))
            return (200, b"")

        allowed = grouping._cooldown_allows_fire(
            service="portfolio-sync", severity="P1", now=second.now
        )
        assert allowed is True, "the un-paged P1 must still be allowed to fire"
        with patch("watchdog.notify._http_post", side_effect=fake_post):
            notify.dispatch(second)

        assert len(pushes) == 1, f"expected one page on the recovered cycle, got {pushes}"
        assert pushes[0][1]["priority"] == 2


class TestHalfConfiguredCredsSurfaceOnTheRow:
    @pytest.mark.parametrize("present,missing", [
        ("PUSHOVER_USER", "PUSHOVER_TOKEN"),
        ("PUSHOVER_TOKEN", "PUSHOVER_USER"),
    ])
    def test_half_creds_flip_row_to_error_and_skip_cooldown(
        self, db_conn, monkeypatch, present, missing
    ):
        from watchdog import cooldown, notify

        _no_creds(monkeypatch)
        monkeypatch.setenv(present, "set")

        dispatcher_error = notify.dispatch(_outcome())

        assert dispatcher_error and missing in dispatcher_error, (
            f"half-configured creds must name the missing var; got {dispatcher_error!r}"
        )
        row = _watchdog_alerts_row(db_conn)
        assert row is not None and row["state"] == "error", (
            f"env drift on the only external channel is actionable; got {row!r}"
        )
        assert "portfolio-sync" not in (row["last_error"] or ""), (
            "dispatcher row must not carry downstream alert content"
        )
        assert cooldown.cooldown_allows_fire(
            service="portfolio-sync", severity="P1", now=FIVE_MIN_LATER
        ) is True


class TestDeliveredAlertsStillArmTheCooldown:
    def test_creds_present_and_2xx_arms_cooldown(self, db_conn, monkeypatch):
        """Control: the happy path must still suppress the repeat."""
        from watchdog import cooldown, notify

        _with_creds(monkeypatch)
        with patch("watchdog.notify._http_post", return_value=(200, b"")):
            assert notify.dispatch(_outcome()) is None

        assert cooldown.cooldown_allows_fire(
            service="portfolio-sync", severity="P1", now=FIVE_MIN_LATER
        ) is False
        rows = _cooldown_rows(db_conn, "portfolio-sync")
        assert rows and rows[0][2] == "notified", f"cooldown must be armed; got {rows!r}"

    def test_pushover_5xx_leaves_cooldown_rearmable(self, db_conn, monkeypatch):
        from watchdog import cooldown, notify

        _with_creds(monkeypatch)
        with patch("watchdog.notify._http_post", return_value=(500, b"boom")):
            assert notify.dispatch(_outcome())

        assert cooldown.cooldown_allows_fire(
            service="portfolio-sync", severity="P1", now=FIVE_MIN_LATER
        ) is True

    def test_p2_arms_cooldown_without_creds(self, db_conn, monkeypatch):
        """Scope guard: P2/P3 never used Pushover — the daily digest is
        their channel, so their cooldown behaviour is unchanged."""
        from watchdog import cooldown, notify

        _no_creds(monkeypatch)
        notify.dispatch(_outcome(service="cash-flow-sync", severity="P2"))

        assert cooldown.cooldown_allows_fire(
            service="cash-flow-sync", severity="P2", now=FIVE_MIN_LATER
        ) is False


class TestChannelResultSeam:
    def test_emit_pushover_distinguishes_sent_from_unconfigured(self, db_conn, monkeypatch):
        from watchdog import notify

        _no_creds(monkeypatch)
        skipped = notify._emit_pushover(_outcome())
        assert skipped.delivered is False
        assert skipped.skip_reason == notify.PUSHOVER_UNCONFIGURED
        assert skipped.error is None

        _with_creds(monkeypatch)
        with patch("watchdog.notify._http_post", return_value=(200, b"")):
            sent = notify._emit_pushover(_outcome())
        assert sent.delivered is True
        assert sent.skip_reason is None

    def test_emit_pushover_not_applicable_below_p1(self, db_conn, monkeypatch):
        from watchdog import notify

        _with_creds(monkeypatch)
        result = notify._emit_pushover(_outcome(severity="P3"))
        assert result.attempted is False
        assert result.error is None
        assert result.skip_reason is None, (
            "a P3 is not an unpaged P1 — it must not log the unpaged warning"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
