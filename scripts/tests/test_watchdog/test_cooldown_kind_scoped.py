"""Cross-kind regression tests for the watchdog cooldown machines.

A service has two independent failure machines (``stale`` and ``error``) and
one notification gate per severity. A recovery of one kind must only resolve
a gate latched by that same kind:

* A writer heartbeating ``state=error`` is fresh, so the stale bucket records
  a stale SUCCESS every cycle. That success used to resolve the P1 gate the
  error bucket latched, so the still-failing error re-paged P1 every hour and
  defeated the 24h re-arm ceiling.
* The stale bucket's "healthy" observation used to cancel the P1 emergency
  the error bucket raised, while the error was still live.
* The failure counter was a SELECT then an UPSERT with no transaction: two
  buckets incrementing concurrently lost an increment.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

NOW = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)


class TestRecoveryIsKindScoped:
    def test_stale_success_does_not_rearm_an_error_latched_gate(self, db_conn):
        from watchdog import cooldown

        cooldown.mark_notified(service="exit-orders", severity="P1", kind="error", now=NOW)
        cooldown.record_success(service="exit-orders", kind="stale")
        assert cooldown.cooldown_allows_fire(
            service="exit-orders", severity="P1", now=NOW + timedelta(hours=1, minutes=5)
        ) is False

    def test_same_kind_success_rearms_the_gate(self, db_conn):
        from watchdog import cooldown

        cooldown.mark_notified(service="exit-orders", severity="P1", kind="error", now=NOW)
        cooldown.record_success(service="exit-orders", kind="error")
        assert cooldown.cooldown_allows_fire(
            service="exit-orders", severity="P1", now=NOW + timedelta(hours=1, minutes=5)
        ) is True

    def test_latch_without_a_kind_keeps_the_any_kind_recovery(self, db_conn):
        # Rows written before the gate recorded its kind resolve on any success.
        from watchdog import cooldown

        cooldown.mark_notified(service="exit-orders", severity="P1", now=NOW)
        cooldown.record_success(service="exit-orders", kind="stale")
        assert cooldown.cooldown_allows_fire(
            service="exit-orders", severity="P1", now=NOW + timedelta(hours=1, minutes=5)
        ) is True

    def test_heartbeating_error_writer_pages_once_per_24h(self, db_conn, monkeypatch):
        """End to end through check + dispatch: a writer that keeps writing a
        fresh ``state=error`` row pages P1 once, not every hour."""
        from watchdog import check, cooldown, notify

        monkeypatch.setenv("PUSHOVER_USER", "u")
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        pages: list[str] = []

        def fake_post(url, payload, headers=None):
            if "messages.json" in url:
                pages.append(payload.get("title", ""))
            return (200, b'{"status":1}')

        monkeypatch.setattr(check, "_resolve_severity", lambda **_: "P1")
        with patch("watchdog.notify._http_post", side_effect=fake_post), patch(
            "watchdog.notify._enqueue_grok_page"
        ):
            t = NOW
            while t < NOW + timedelta(hours=6):
                health = {
                    "state": "error",
                    "updated_at": t.isoformat().replace("+00:00", "Z"),
                    "last_error": '{"message": "fact conflict"}',
                }
                err = check._check_error(
                    service="exit-orders", health=health, now=t, market_state="open"
                )
                if err.fired and cooldown.cooldown_allows_fire(
                    service="exit-orders", severity="P1", now=t
                ):
                    notify.dispatch(err, record_health=False)
                check._check_stale(
                    service="exit-orders", health=health, now=t, market_state="open"
                )
                t += timedelta(minutes=5)

        assert len(pages) == 1, pages


class TestEmergencyCancelIsKindScoped:
    def _outcome(self, kind: str, status: str):
        from watchdog import check

        return check.CheckOutcome(
            service="exit-orders", kind=kind, status=status, severity=None,
            fired=False, message="", consecutive_failures=0, now=NOW,
        )

    def test_healthy_stale_does_not_cancel_an_error_emergency(self, db_conn):
        from watchdog import __main__ as wd_main
        from watchdog import cooldown

        cooldown.mark_notified(service="exit-orders", severity="P1", kind="error", now=NOW)
        cancelled: list[str] = []
        with patch("watchdog.notify.cancel_emergency", side_effect=lambda t: cancelled.append(t)):
            wd_main._reconcile_recovered_emergencies(
                outcomes=[self._outcome("stale", "healthy")],
                now=NOW + timedelta(minutes=5),
            )
        assert cancelled == []
        assert "exit-orders" in cooldown.active_emergency_services(
            now=NOW + timedelta(minutes=6)
        )

    def test_healthy_error_cancels_the_error_emergency(self, db_conn):
        from watchdog import __main__ as wd_main
        from watchdog import cooldown

        cooldown.mark_notified(service="exit-orders", severity="P1", kind="error", now=NOW)
        cancelled: list[str] = []
        with patch("watchdog.notify.cancel_emergency", side_effect=lambda t: cancelled.append(t)):
            wd_main._reconcile_recovered_emergencies(
                outcomes=[self._outcome("error", "healthy")],
                now=NOW + timedelta(minutes=5),
            )
        assert cancelled == ["exit-orders"]


class TestGateTransition:
    def test_transition_table(self):
        from watchdog.cooldown import gate_after_recovery

        assert gate_after_recovery("notified:error", recovered_kind="error") == "resolved"
        assert gate_after_recovery("notified:error", recovered_kind="stale") == "notified:error"
        assert gate_after_recovery("notified", recovered_kind="stale") == "resolved"
        assert gate_after_recovery("resolved", recovered_kind="error") == "resolved"
        assert gate_after_recovery(None, recovered_kind="error") is None


class TestCounterIsAtomic:
    def test_concurrent_increment_is_not_lost(self, tmp_path, monkeypatch):
        """A second bucket increments the same counter between this call's
        read and write. With the read-modify-write split across two
        unlocked statements the second increment was overwritten."""
        from watchdog import cooldown, state_db

        path = tmp_path / "state.db"
        monkeypatch.setenv("RADON_WATCHDOG_STATE_DB", str(path))
        state_db.get_state_db().close()

        other = sqlite3.connect(path, timeout=0.05)
        deferred: list[str] = []

        def concurrent_increment() -> None:
            other.execute(
                "INSERT INTO watchdog_cooldowns (service, kind, consecutive_failures, last_outcome) "
                "VALUES ('exit-orders', 'error', 1, 'failure') "
                "ON CONFLICT(service, kind) DO UPDATE SET "
                "consecutive_failures = watchdog_cooldowns.consecutive_failures + 1"
            )
            other.commit()

        class Racing:
            """Runs the competing bucket right after this call's first
            statement. If this call holds the write lock the competitor waits
            (deferred until after commit), as a real second process would."""

            def __init__(self, conn):
                self._conn = conn
                self._raced = False

            def execute(self, *args, **kwargs):
                result = self._conn.execute(*args, **kwargs)
                if not self._raced:
                    self._raced = True
                    try:
                        concurrent_increment()
                    except sqlite3.OperationalError:
                        deferred.append("locked")
                return result

            def commit(self):
                self._conn.commit()
                if deferred:
                    concurrent_increment()

            def __getattr__(self, name):
                return getattr(self._conn, name)

        real = state_db.get_state_db()
        monkeypatch.setattr(cooldown, "_get_db", lambda: Racing(real))
        cooldown.record_failure_and_decide(service="exit-orders", kind="error", now=NOW)

        count = other.execute(
            "SELECT consecutive_failures FROM watchdog_cooldowns "
            "WHERE service='exit-orders' AND kind='error'"
        ).fetchone()[0]
        assert count == 2
