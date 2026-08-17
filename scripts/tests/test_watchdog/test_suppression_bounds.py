"""REL-031: every watchdog suppression must be bounded (R-056/R-057/R-064).

Three suppressions each read a pure timestamp/file predicate with no
ceiling, so a wedged input suppresses forever:

 * R-056 — grouping's deploy-warmup branch drops the grouped IB page
   whenever radon-api entered active <180s ago. A ~120-200s restart
   loop (the pool-recovery ``os._exit(1)`` ladder) keeps that predicate
   true on every 5-min cycle, muting every IB-outage page indefinitely.
 * R-057 — while the deploy transition journal exists, a validated
   off-box edge 5xx inside the 900s lookback is un-fired. An
   interrupted deploy strands the journal (which also blocks later
   deploys, so it cannot self-clear) — the mute became permanent.
 * R-064 — units' ``_is_deploy_collateral`` kill-before-green branch
   compares two frozen timestamps (kill vs green marker) with no
   now-cap, so a unit that never runs again stays P3-digest forever.

Contracts pinned here: N consecutive warmup-suppressed cycles, then the
page fires; a stranded journal stops suppressing AND is itself raised;
a frozen failed unit returns to P1 once past the frozen cap.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts.watchdog import external_probe, units


T0 = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
CYCLE = timedelta(minutes=5)


def _stale(service: str, *, now: datetime):
    from watchdog.check import CheckOutcome

    return CheckOutcome(
        service=service,
        kind="stale",
        status="stale",
        severity="P1",
        fired=True,
        message=f"{service} silent for 23m (window 10m) — market open",
        consecutive_failures=2,
        now=now,
    )


def _push_calls(http_calls: list) -> list:
    return [c for c in http_calls if "pushover" in c[0]]


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")


@pytest.fixture
def http_calls():
    return []


@pytest.fixture
def restart_looping_api(http_calls):
    """Pushover captured; /health stuck awaiting_2fa; radon-api's
    ActiveEnterTimestamp always <180s old (the ~120s restart loop)."""

    def fake_http_post(url, payload, headers=None):
        http_calls.append((url, payload))
        return (200, b"")

    with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
         patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}), \
         patch("watchdog.grouping._api_recently_restarted", return_value=True):
        yield


class TestWarmupSuppressionCeiling:
    """R-056 — the deploy-warmup 2FA suppression has a consecutive ceiling."""

    def test_restart_loop_pages_on_cycle_after_ceiling(
        self, db_conn, creds, http_calls, restart_looping_api
    ):
        from watchdog import grouping

        ceiling = grouping.WARMUP_SUPPRESS_MAX_CONSECUTIVE
        cohort = ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync")

        for cycle in range(ceiling):
            now = T0 + cycle * CYCLE
            grouping.dispatch_with_grouping(
                outcomes=[_stale(svc, now=now) for svc in cohort], now=now
            )
            assert _push_calls(http_calls) == [], (
                f"cycle {cycle + 1} is within the warmup ceiling ({ceiling}) "
                f"and must stay suppressed"
            )

        now = T0 + ceiling * CYCLE
        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=now) for svc in cohort], now=now
        )
        pushes = _push_calls(http_calls)
        assert len(pushes) == 1, (
            f"cycle {ceiling + 1} exceeded the consecutive warmup-suppression "
            f"ceiling — the grouped IB page must fire; got {len(pushes)} push(es)"
        )
        assert "awaiting_2fa" in f"{pushes[0][1].get('title', '')}"

    def test_isolated_deploy_warmups_do_not_accumulate(
        self, db_conn, creds, http_calls, restart_looping_api
    ):
        """Non-consecutive suppressions (ordinary deploys days apart,
        separated by healthy cycles) must never sum to the ceiling."""
        from watchdog import grouping

        cohort = ("vcg-scan", "cri-scan")
        for deploy in range(grouping.WARMUP_SUPPRESS_MAX_CONSECUTIVE + 2):
            now = T0 + timedelta(days=deploy)
            grouping.dispatch_with_grouping(
                outcomes=[_stale(svc, now=now) for svc in cohort], now=now
            )
            # The deploy finishes; the next cycle is healthy (nothing fired).
            grouping.dispatch_with_grouping(outcomes=[], now=now + CYCLE)

        assert _push_calls(http_calls) == [], (
            "isolated one-cycle warmup suppressions must reset the counter, "
            "not accumulate across deploys"
        )


NOW = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)


def _row(*, ok: int, age_minutes: int, detail: str = "") -> dict:
    return {
        "ok": ok,
        "checked_at": (NOW - timedelta(minutes=age_minutes)).isoformat().replace("+00:00", "Z"),
        "detail": detail,
    }


def _journal(tmp_path, monkeypatch, *, age_seconds: int):
    marker = tmp_path / "last-green-deploy"
    journal = tmp_path / "deploy-transition.json"
    monkeypatch.setattr(external_probe, "DEPLOY_GREEN_MARKER_FILE", str(marker))
    monkeypatch.setattr(external_probe, "DEPLOY_TRANSITION_JOURNAL_FILE", str(journal))
    journal.write_text("{}\n")
    stamp = (NOW - timedelta(seconds=age_seconds)).timestamp()
    os.utime(journal, (stamp, stamp))


class TestStrandedTransitionJournal:
    """R-057 — the deploy transition journal has a staleness cap."""

    def test_stranded_journal_does_not_suppress_edge_5xx(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            external_probe.turso_http,
            "fetch_external_probe",
            lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
        )
        monkeypatch.setattr(external_probe, "_local_aggregate_is_healthy", lambda: True)
        _journal(
            tmp_path,
            monkeypatch,
            age_seconds=external_probe.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS + 600,
        )

        outcome = external_probe.check_external_probe(now=NOW)

        assert outcome.fired is True, (
            "a journal aged past the staleness cap is an interrupted deploy, "
            "not deploy evidence — the validated edge 5xx must page"
        )
        assert outcome.severity == "P1"

    def test_fresh_journal_still_suppresses_edge_5xx(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            external_probe.turso_http,
            "fetch_external_probe",
            lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
        )
        monkeypatch.setattr(external_probe, "_local_aggregate_is_healthy", lambda: True)
        _journal(tmp_path, monkeypatch, age_seconds=120)

        outcome = external_probe.check_external_probe(now=NOW)

        assert outcome.fired is False
        assert outcome.status == "healthy"

    def test_stranded_journal_raises_even_when_edge_is_healthy(self, tmp_path, monkeypatch):
        """A stranded journal blocks every later deploy, so it is itself
        alarm-worthy — not only when it happens to co-occur with a 5xx."""
        monkeypatch.setattr(
            external_probe.turso_http,
            "fetch_external_probe",
            lambda timeout, source=None: _row(ok=1, age_minutes=2),
        )
        _journal(
            tmp_path,
            monkeypatch,
            age_seconds=external_probe.TRANSITION_JOURNAL_STRANDED_AFTER_SECONDS + 600,
        )

        outcome = external_probe.check_external_probe(now=NOW)

        assert outcome.fired is True
        assert outcome.severity == "P2"
        assert "journal" in outcome.message.lower()

    def test_healthy_edge_without_journal_stays_quiet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            external_probe.turso_http,
            "fetch_external_probe",
            lambda timeout, source=None: _row(ok=1, age_minutes=2),
        )
        marker = tmp_path / "last-green-deploy"
        journal = tmp_path / "deploy-transition.json"
        monkeypatch.setattr(external_probe, "DEPLOY_GREEN_MARKER_FILE", str(marker))
        monkeypatch.setattr(external_probe, "DEPLOY_TRANSITION_JOURNAL_FILE", str(journal))

        outcome = external_probe.check_external_probe(now=NOW)

        assert outcome.fired is False
        assert outcome.status == "healthy"


def _show_output(body: str) -> str:
    return body + "\n"


def _signal_block(unit_id: str, killed_at: datetime) -> str:
    ts = killed_at.strftime("%a %Y-%m-%d %H:%M:%S UTC")
    return _show_output(
        f"Id={unit_id}\nActiveState=failed\nSubState=failed\n"
        f"Result=signal\nNRestarts=0\nInactiveEnterTimestamp={ts}"
    )


class TestKillBeforeGreenFrozenCap:
    """R-064 — kill-before-green collateral has a now-cap."""

    def test_unit_frozen_past_cap_returns_to_p1(self):
        """2026-08-15 shape (kill 78s before green) but the unit never
        runs again — e.g. the deploy left its timer disabled. Once the
        frozen cap passes, the permanent failure must page P1, not sit
        in the daily digest forever."""
        killed = datetime(2026, 8, 15, 0, 34, 41, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 15, 0, 35, 59, tzinfo=timezone.utc)
        now = killed + timedelta(
            seconds=units.KILL_BEFORE_GREEN_FROZEN_CAP_SECS + 3600
        )
        current = units.parse_show_output(_signal_block("radon-bpi.service", killed))

        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )

        assert [o.severity for o in outcomes] == ["P1"], (
            "a failed-and-frozen unit past the kill-before-green frozen cap "
            "must return to P1"
        )

    def test_oneshot_inside_cap_stays_p3(self):
        """A daily oneshot legitimately stays failed until its next timer
        fire (up to ~24h) — inside the frozen cap it remains digest-tier."""
        killed = datetime(2026, 8, 15, 0, 34, 41, tzinfo=timezone.utc)
        marker = datetime(2026, 8, 15, 0, 35, 59, tzinfo=timezone.utc)
        now = killed + timedelta(hours=10)
        current = units.parse_show_output(_signal_block("radon-bpi.service", killed))

        outcomes = units.evaluate(
            current=current, previous={}, now=now,
            deploy={"marker_mtime": marker, "in_flight": False},
        )

        assert [o.severity for o in outcomes] == ["P3"]
