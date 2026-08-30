from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def test_local_aggregate_recovery_requires_healthy_schema_v2() -> None:
    from scripts.watchdog import external_probe

    response = MagicMock()
    response.status = 200
    # `generated_at` and the two sections are now REQUIRED evidence: an
    # aggregate produced before the off-box sample cannot describe its recovery,
    # and `degraded` only clears when the serving path is green. R-399.
    response.read.return_value = (
        b'{"schema_version":2,"ok":true,"overall_state":"up",'
        b'"generated_at":"2026-08-29T12:00:00Z",'
        b'"probes":{"api":{"state":"up"}},"units":{"radon-api.service":{"state":"up"}}}'
    )
    sampled_at = datetime(2026, 8, 29, 11, 0, 0, tzinfo=timezone.utc)
    with patch.object(external_probe.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert external_probe._local_aggregate_is_healthy() is True
        assert external_probe._local_aggregate_clears_offbox_down(sampled_at) is True


def test_local_starting_aggregate_does_not_clear_offbox_down() -> None:
    from scripts.watchdog import external_probe

    response = MagicMock()
    response.status = 200
    response.read.return_value = (
        b'{"schema_version":2,"ok":false,"overall_state":"starting"}'
    )
    with patch.object(external_probe.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert external_probe._local_aggregate_clears_offbox_down() is False
        assert external_probe._local_aggregate_is_healthy() is False


def test_local_aggregate_recovery_fails_closed_on_malformed_payload() -> None:
    from scripts.watchdog import external_probe

    response = MagicMock()
    response.status = 200
    response.read.return_value = b'{}'
    with patch.object(external_probe.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert external_probe._local_aggregate_is_healthy() is False


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
    # A silent off-box observer is a MONITORING gap, not a validated edge
    # outage — P2 digest, never a P1 emergency (2026-07-28 storm: the probe's
    # hardcoded P1 drove hours of retry-until-ack pages).
    assert outcome.severity == "P2"
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
    # Local health can only clear aggregate-state recovery. It must never mask
    # a public perimeter failure that requires off-box evidence.
    monkeypatch.setattr(external_probe, "_local_aggregate_is_healthy", lambda: True)
    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "error"
    assert outcome.fired is True
    assert outcome.severity == "P1"
    assert "edge_http_503" in outcome.message


def test_recovered_local_aggregate_clears_fresh_aggregate_failure(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(
            ok=0,
            age_minutes=32,
            detail="aggregate_down",
        ),
    )
    monkeypatch.setattr(
        external_probe, "_local_aggregate_clears_offbox_down", lambda *_a, **_k: True
    )

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False
    assert outcome.severity is None
    assert outcome.message == "off-box aggregate recovered locally"


def test_still_unhealthy_local_aggregate_keeps_fresh_failure_active(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(
            ok=0,
            age_minutes=32,
            detail="aggregate_down",
        ),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_is_healthy", lambda: False)
    monkeypatch.setattr(
        external_probe, "_local_aggregate_clears_offbox_down", lambda *_a, **_k: False
    )

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "error"
    assert outcome.fired is True
    assert outcome.severity == "P1"


def test_local_degraded_aggregate_clears_fresh_aggregate_down() -> None:
    # 2026-08-29 page 344f0592: sidecar flap makes the on-box aggregate
    # degraded (ok=false) while the 5-min off-box row is still aggregate_down.
    # Serving path is up, so local degraded is recovery evidence.
    from scripts.watchdog import external_probe

    response = MagicMock()
    response.status = 200
    response.read.return_value = (
        b'{"schema_version":2,"ok":false,"overall_state":"degraded",'
        b'"generated_at":"2026-08-29T12:00:00Z",'
        b'"probes":{"api":{"state":"up"},"ib-gateway":{"state":"down"}},'
        b'"units":{"radon-api.service":{"state":"up"},'
        b'"radon-newsfeed.service":{"state":"down"}}}'
    )
    sampled_at = datetime(2026, 8, 29, 11, 0, 0, tzinfo=timezone.utc)
    with patch.object(external_probe.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        assert external_probe._local_aggregate_clears_offbox_down(sampled_at) is True
        assert external_probe._local_aggregate_is_healthy() is False

    from scripts.watchdog import external_probe as ep

    with (
        patch.object(
            ep.turso_http,
            "fetch_external_probe",
            lambda timeout, source=None: _row(
                ok=0,
                age_minutes=5,
                detail="aggregate_down",
            ),
        ),
        patch.object(ep, "_local_aggregate_clears_offbox_down", lambda *_a, **_k: True),
    ):
        outcome = ep.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False
    assert outcome.severity is None
    assert outcome.message == "off-box aggregate recovered locally"


def test_legacy_aggregate_unhealthy_stays_fail_closed(monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(
            ok=0,
            age_minutes=32,
            detail="aggregate_unhealthy",
        ),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_is_healthy", lambda: True)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "error"
    assert outcome.fired is True


def _healthy_disk_outcome():
    """Keep continuous-bucket tests hermetic — the real check_disk reads the
    HOST's root fs and would fire on a genuinely full laptop (R-069)."""
    from scripts.watchdog.check import CheckOutcome

    return CheckOutcome(
        service="root-disk-usage",
        kind="disk",
        status="healthy",
        severity=None,
        fired=False,
        message="root fs 10% used",
        consecutive_failures=0,
        now=NOW,
    )


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
        patch("scripts.watchdog.disk.check_disk", return_value=_healthy_disk_outcome()),
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
        patch("scripts.watchdog.disk.check_disk", return_value=_healthy_disk_outcome()),
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


def _deploy_marker(tmp_path, monkeypatch, *, marker_age_minutes=None, journal_exists=False):
    from scripts.watchdog import external_probe

    marker = tmp_path / "last-green-deploy"
    journal = tmp_path / "deploy-transition.json"
    monkeypatch.setattr(external_probe, "DEPLOY_GREEN_MARKER_FILE", str(marker))
    monkeypatch.setattr(external_probe, "DEPLOY_TRANSITION_JOURNAL_FILE", str(journal))
    if marker_age_minutes is not None:
        marker.write_text("deadbeef\n")
        stamp = (NOW - timedelta(minutes=marker_age_minutes)).timestamp()
        import os

        os.utime(marker, (stamp, stamp))
    if journal_exists:
        journal.write_text("{}\n")


def test_deploy_window_edge_5xx_with_healthy_aggregate_is_suppressed(tmp_path, monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=1)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False
    assert "deploy window" in outcome.message


def test_deploy_window_5xx_with_dependency_only_degraded_is_suppressed(
    tmp_path, monkeypatch
) -> None:
    # 2026-08-29 page d98c3364: Saturday deploy while IB weekend clean-exit
    # left the aggregate degraded. Off-box sampled status_http_502 (healthd
    # restart) with user_path+freshness still green. Requiring overall_state
    # == "up" re-paged P1 on every weekend deploy.
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
    )
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=1)

    response = MagicMock()
    response.status = 200
    response.read.return_value = (
        b'{"schema_version":2,"ok":false,"overall_state":"degraded",'
        b'"generated_at":"2026-08-29T16:50:00Z",'
        b'"probes":{"radon-api":{"state":"up"},"radon-relay":{"state":"up"},'
        b'"radon-nextjs":{"state":"up"},"ib-gateway":{"state":"down"}},'
        b'"units":{"radon-api.service":{"state":"up"},'
        b'"radon-relay.service":{"state":"up"},'
        b'"radon-nextjs.service":{"state":"up"},'
        b'"radon-ib-gateway.service":{"state":"up"}}}'
    )
    with patch.object(external_probe.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value = response
        outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False
    assert "deploy window" in outcome.message


def test_deploy_window_5xx_with_sick_aggregate_still_pages(tmp_path, monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: False)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=1)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.fired is True
    assert outcome.severity == "P1"


def test_edge_5xx_outside_deploy_window_pages(tmp_path, monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=120)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.fired is True
    assert outcome.severity == "P1"


def test_non_5xx_reason_inside_deploy_window_pages(tmp_path, monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="ping_unreachable:timeout"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=1)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.fired is True
    assert outcome.severity == "P1"


def test_deploy_window_caddy_status_unreachable_is_suppressed(
    tmp_path, monkeypatch
) -> None:
    # 2026-08-29 23:05Z page 1b0b049c: after the Caddy never-502 floor shipped,
    # healthd-down during deploy is HTTP 200 status_unreachable:caddy, not
    # status_http_502. Same collateral as 5xx; suppress it the same way.
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(
            ok=0, age_minutes=2, detail="status_unreachable:caddy"
        ),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=1)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False
    assert "deploy window" in outcome.message


def test_caddy_status_unreachable_outside_deploy_window_pages(
    tmp_path, monkeypatch
) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(
            ok=0, age_minutes=2, detail="status_unreachable:caddy"
        ),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=120)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.fired is True
    assert outcome.severity == "P1"


def test_aggregate_invalid_inside_deploy_window_still_pages(
    tmp_path, monkeypatch
) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="aggregate_invalid"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, marker_age_minutes=1)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.fired is True
    assert outcome.severity == "P1"


def test_inflight_transition_journal_suppresses_fresh_edge_5xx(tmp_path, monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="ping_http_503"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch, journal_exists=True)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.status == "healthy"
    assert outcome.fired is False


def test_missing_deploy_markers_never_suppress(tmp_path, monkeypatch) -> None:
    from scripts.watchdog import external_probe

    monkeypatch.setattr(
        external_probe.turso_http,
        "fetch_external_probe",
        lambda timeout, source=None: _row(ok=0, age_minutes=2, detail="status_http_502"),
    )
    monkeypatch.setattr(external_probe, "_local_aggregate_serving_path_ok", lambda: True)
    _deploy_marker(tmp_path, monkeypatch)

    outcome = external_probe.check_external_probe(now=NOW)

    assert outcome.fired is True
    assert outcome.severity == "P1"
