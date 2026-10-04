"""IBKR operator hold: IB-dependent failures are HELD, not an outage page.

2026-10-01: in an emergency the operator flattens from IBKR Mobile, which
takes the IBKR username the Gateway shares; the Gateway is down on purpose.
The grouped "IB Gateway unreachable ... Recover with radon restart" page would
tell the operator to log the Gateway back in and kick themselves out mid
flatten. The broker ib-watchdog pages HELD once; this path stays quiet.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

def _fired(service: str, *, now: datetime):
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
        last_error=None,
    )


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 13, 15, 0, tzinfo=timezone.utc)


def _dispatch(outcomes, now, health):
    from watchdog import grouping

    http_calls: list = []

    def fake_http_post(url, payload, headers=None):
        http_calls.append((url, payload))
        return (200, b"")

    with patch("watchdog.notify._http_post", side_effect=fake_http_post), \
         patch("watchdog.grouping.fetch_health", return_value=health), \
         patch("watchdog.grouping._api_recently_restarted", return_value=False):
        grouping.dispatch_with_grouping(outcomes=outcomes, now=now)
    return [c for c in http_calls if "pushover" in c[0]]


@pytest.mark.parametrize("services", [["vcg-scan"], ["vcg-scan", "cri-scan", "orders-sync"]])
def test_ib_failures_during_a_hold_send_no_page(db_conn, monkeypatch, now, services):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    outcomes = [_fired(s, now=now) for s in services]
    pushes = _dispatch(outcomes, now, {"auth_state": "unreachable", "operator_hold": True})
    assert pushes == []


def test_non_ib_failures_still_page_during_a_hold(db_conn, monkeypatch, now):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    outcomes = [_fired("vcg-scan", now=now), _fired("newsfeed-scraper", now=now)]
    pushes = _dispatch(outcomes, now, {"auth_state": "unreachable", "operator_hold": True})
    assert len(pushes) == 1
    assert "newsfeed-scraper" in pushes[0][1]["title"] + pushes[0][1]["message"]


def test_without_a_hold_the_unreachable_page_still_fires(db_conn, monkeypatch, now):
    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    outcomes = [_fired("vcg-scan", now=now), _fired("cri-scan", now=now)]
    pushes = _dispatch(outcomes, now, {"auth_state": "unreachable"})
    assert len(pushes) == 1
    assert "radon restart" in pushes[0][1]["message"]


def test_fetch_health_reports_the_hold_only_when_held():
    from watchdog import grouping

    class Resp:
        def __init__(self, body):
            self._body = json.dumps(body).encode()

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    held = {"ib_gateway": {"auth_state": "unreachable", "operator_hold": {"held": True, "actor": "x"}}}
    with patch("urllib.request.urlopen", return_value=Resp(held)):
        assert grouping.fetch_health() == {"auth_state": "unreachable", "operator_hold": True}
    free = {"ib_gateway": {"auth_state": "unreachable", "operator_hold": {"held": False}}}
    with patch("urllib.request.urlopen", return_value=Resp(free)):
        assert grouping.fetch_health() == {"auth_state": "unreachable"}
