from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from scripts.watchdog import __main__ as watchdog_main


NOW = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)


def _row(*, ok: int, age_minutes: int, detail: str = "") -> dict:
    return {
        "ok": ok,
        "checked_at": (NOW - timedelta(minutes=age_minutes)).isoformat().replace("+00:00", "Z"),
        "detail": detail,
    }


def test_fresh_green_external_probe_is_healthy(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    captured = {}

    def fetch(*, timeout, source=None):
        captured.update(timeout=timeout, source=source)
        return _row(ok=1, age_minutes=2)

    monkeypatch.setattr(external_probe.turso_http, "fetch_external_probe", fetch)
    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False
    assert outcome.severity is None
    assert captured["source"] == "github-actions/edge"


def test_stale_external_probe_pages_the_operator(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    # Cadence calibration (T6E): ordinary GitHub dispatch lag stays healthy for
    # up to two hours. Stale must clear that window, not a single missed cycle.
    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=1, age_minutes=121),
    )
    monkeypatch.setattr(external_probe, "_latest_github_run", lambda: None)
    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "stale"
    assert outcome.fired is True
    assert outcome.severity == "P1"
    assert "silent" in outcome.message.lower()


def test_stale_turso_row_uses_recent_green_github_run(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=300, detail="old failure"),
    )
    monkeypatch.setattr(
        external_probe,
        "_latest_github_run",
        lambda: {
            "status": "completed",
            "conclusion": "success",
            "updated_at": (NOW - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        },
    )

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False


def test_fresh_red_external_probe_pages_the_operator(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="edge_http_503"),
    )
    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "error"
    assert outcome.fired is True
    assert outcome.severity == "P1"
    assert "edge_http_503" in outcome.message


def test_continuous_bucket_dispatches_external_probe_deadman() -> None:
    from scripts.watchdog.check import CheckOutcome
    from scripts.watchdog.grouping import DispatchSummary

    outcome = CheckOutcome(
        service="external-health-probe",
        kind="deadman",
        status="stale",
        severity="P1",
        fired=True,
        message="off-box observer silent",
        consecutive_failures=1,
        now=NOW,
    )
    report = SimpleNamespace(ran=True, outcomes=[])
    captured: list[CheckOutcome] = []

    def dispatch(*, outcomes, now):
        captured.extend(outcomes)
        return DispatchSummary(health_recorded=True)

    with (
        patch("scripts.watchdog.check.check_bucket", return_value=report),
        patch("scripts.watchdog.units.check_units", return_value=[]),
        patch("scripts.watchdog.external_probe.check_external_probe", return_value=outcome),
        patch("scripts.watchdog.grouping.dispatch_with_grouping", side_effect=dispatch),
        patch("scripts.watchdog.notify.log_startup_warning"),
        patch.object(watchdog_main, "_reconcile_recovered_emergencies"),
    ):
        assert watchdog_main._cmd_bucket(Namespace(bucket="continuous")) == 0

    assert [item.service for item in captured] == ["external-health-probe"]


def test_continuous_bucket_cancels_external_probe_emergency_on_recovery() -> None:
    from scripts.watchdog.check import CheckOutcome
    from scripts.watchdog.grouping import DispatchSummary

    healthy = CheckOutcome(
        service="external-health-probe",
        kind="deadman",
        status="healthy",
        severity=None,
        fired=False,
        message="off-box observer current",
        consecutive_failures=0,
        now=NOW,
    )
    report = SimpleNamespace(ran=True, outcomes=[])

    with (
        patch("scripts.watchdog.check.check_bucket", return_value=report),
        patch("scripts.watchdog.units.check_units", return_value=[]),
        patch("scripts.watchdog.external_probe.check_external_probe", return_value=healthy),
        patch(
            "scripts.watchdog.grouping.dispatch_with_grouping",
            return_value=DispatchSummary(),
        ),
        patch("scripts.watchdog.notify.log_startup_warning"),
        patch(
            "scripts.watchdog.cooldown.active_emergency_services",
            return_value=["external-health-probe"],
        ),
        patch("scripts.watchdog.notify.cancel_emergency") as cancel,
        patch("scripts.watchdog.cooldown.mark_emergency_resolved") as resolved,
    ):
        assert watchdog_main._cmd_bucket(Namespace(bucket="continuous")) == 0

    cancel.assert_called_once_with("external-health-probe")
    resolved.assert_called_once_with(service="external-health-probe")
