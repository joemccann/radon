"""T-040: a service that joins an IB outage after the grouped page must page.

``_dispatch_grouped`` returned EVERY currently-failing IB service as
``grouped_handled`` even while the grouped key was cooldown-muted. A
service that started failing after the grouped page was armed was
therefore absorbed on every cycle and never paged — up to the 24h
re-arm ceiling of a latched ``awaiting_2fa`` window.

Contract pinned here: a delivered grouped page stamps the cooldown row
of every service it NAMED, and that roster is the armed set. During the
muted window only those services stay absorbed; anything newer falls
through to the per-service path and pages once.

Drop-in for scripts/tests/test_watchdog/. Uses the suite's ``db_conn``
fixture (in-memory sqlite + patched hrana transport).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


T0 = datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)
T40 = T0 + timedelta(minutes=40)

# The four IB-dependent intraday writers that degrade together in a 2FA window.
COHORT = ("vcg-scan", "cri-scan", "orders-sync", "portfolio-sync")
# Continuous-bucket IB writer that joins the outage late.
NEWCOMER = "exit-orders"


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


def _text(push_call) -> str:
    payload = push_call[1]
    return f"{payload.get('title', '')} {payload.get('message', '')}"


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")


@pytest.fixture
def http_calls():
    return []


@pytest.fixture
def awaiting_2fa(http_calls):
    """Pushover captured, /health pinned to awaiting_2fa, no systemd probe."""

    def fake_http_post(url, payload, headers=None):
        http_calls.append((url, payload))
        return (200, b"")

    with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
         patch("watchdog.grouping.fetch_health", return_value={"auth_state": "awaiting_2fa"}), \
         patch("watchdog.grouping._api_recently_restarted", return_value=False):
        yield


class TestLateJoinerDuringMutedGroupedWindow:
    def test_service_failing_after_the_grouped_page_still_pages(
        self, db_conn, creds, http_calls, awaiting_2fa
    ):
        from watchdog import grouping

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
        )
        assert len(_push_calls(http_calls)) == 1, "T0 must send exactly one grouped page"

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T40) for svc in (*COHORT, NEWCOMER)], now=T40
        )

        pushes = _push_calls(http_calls)
        assert len(pushes) == 2, (
            f"the newly-failing {NEWCOMER} must page while the grouped key is "
            f"muted; got {len(pushes)} push(es): {[_text(p) for p in pushes]}"
        )
        assert NEWCOMER in _text(pushes[1]), (
            f"the second page must name {NEWCOMER}; got {_text(pushes[1])!r}"
        )
        assert pushes[1][1].get("priority") == 2, "a P1 late joiner still pages emergency"

    def test_original_cohort_stays_muted(self, db_conn, creds, http_calls, awaiting_2fa):
        from watchdog import grouping

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
        )
        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T40) for svc in (*COHORT, NEWCOMER)], now=T40
        )

        second_page = _text(_push_calls(http_calls)[1])
        for svc in COHORT:
            assert svc not in second_page, (
                f"{svc} was already named in the T0 grouped page and must stay "
                f"muted; got {second_page!r}"
            )

    def test_muted_cycle_without_newcomers_sends_nothing(
        self, db_conn, creds, http_calls, awaiting_2fa
    ):
        """Regression guard for the reason the absorb-everything branch
        existed: the same cohort re-failing must not re-page."""
        from watchdog import grouping

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
        )
        summary = grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T40) for svc in COHORT], now=T40
        )

        assert len(_push_calls(http_calls)) == 1
        assert summary.suppressed_count == len(COHORT)
        assert summary.health_recorded is False, (
            "a fully-suppressed cycle attempts no delivery, so it writes no "
            "dispatcher row — __main__ heartbeats instead"
        )

    def test_late_joiner_is_absorbed_on_the_next_cycle(
        self, db_conn, creds, http_calls, awaiting_2fa
    ):
        """Once the late joiner has paged it owns a cooldown row like any
        other service — the following cycle must be silent."""
        from watchdog import grouping

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
        )
        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T40) for svc in (*COHORT, NEWCOMER)], now=T40
        )
        t45 = T40 + timedelta(minutes=5)
        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=t45) for svc in (*COHORT, NEWCOMER)], now=t45
        )

        assert len(_push_calls(http_calls)) == 2, (
            "the late joiner must page once, not once per cycle"
        )


class TestDeliveredGroupedPageArmsItsRoster:
    def test_named_services_get_their_own_cooldown_rows(
        self, db_conn, creds, http_calls, awaiting_2fa
    ):
        from watchdog import cooldown, grouping

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
        )

        stamped = {
            row[0]
            for row in db_conn.execute(
                "SELECT service FROM watchdog_cooldowns "
                "WHERE kind='severity:P1' AND last_outcome='notified'"
            ).fetchall()
        }
        assert stamped == {*COHORT, grouping.GROUPED_ALERT_KEY}, (
            f"a delivered grouped page arms its whole roster; got {stamped}"
        )
        assert cooldown.cooldown_allows_fire(
            service=NEWCOMER, severity="P1", now=T40
        ) is True, "a service the page never named must stay fireable"

    def test_failed_grouped_page_arms_nothing(self, db_conn, creds, http_calls):
        """A page that never left the box must not mute its roster."""
        from watchdog import cooldown, grouping

        with patch("watchdog.notify._http_post", return_value=(500, b"boom")), \
             patch("watchdog.grouping.fetch_health", return_value={"auth_state": "unreachable"}), \
             patch("watchdog.grouping._api_recently_restarted", return_value=False):
            grouping.dispatch_with_grouping(
                outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
            )

        assert db_conn.execute(
            "SELECT COUNT(*) FROM watchdog_cooldowns WHERE last_outcome='notified'"
        ).fetchone()[0] == 0
        for svc in COHORT:
            assert cooldown.cooldown_allows_fire(
                service=svc, severity="P1", now=T40
            ) is True

    def test_recovered_service_rearms_and_pages_again(
        self, db_conn, creds, http_calls, awaiting_2fa
    ):
        """Recovery is the condition-transition boundary: a cohort member
        that healed and re-failed is a new page, not a muted repeat."""
        from watchdog import cooldown, grouping

        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=T0) for svc in COHORT], now=T0
        )
        cooldown.record_success(service="cri-scan", kind="stale")

        past_floor = T0 + timedelta(hours=1, minutes=5)
        grouping.dispatch_with_grouping(
            outcomes=[_stale(svc, now=past_floor) for svc in COHORT], now=past_floor
        )

        pushes = _push_calls(http_calls)
        assert len(pushes) == 2, f"the recovered-then-failed service must re-page; got {len(pushes)}"
        assert "cri-scan" in _text(pushes[1])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
