"""P1 Pushover emergencies must be CANCELLED on recovery — otherwise they
re-alert every 60s for a full hour even after the condition clears (a transient
blip thus spams for an hour; today's deploy-warmup 2FA blip did exactly that)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

NOW = datetime(2026, 6, 15, 14, 0, tzinfo=timezone.utc)


def test_active_emergency_services_tracks_notified_p1(db_conn):
    from watchdog import cooldown

    cooldown.mark_notified(service="cri-scan", severity="P1", now=NOW)
    assert "cri-scan" in cooldown.active_emergency_services(now=NOW + timedelta(minutes=5))
    # Past the 1h emergency-expire window → no longer worth cancelling.
    assert "cri-scan" not in cooldown.active_emergency_services(now=NOW + timedelta(hours=2))


def test_mark_emergency_resolved_clears_active(db_conn):
    from watchdog import cooldown

    cooldown.mark_notified(service="cri-scan", severity="P1", now=NOW)
    cooldown.mark_emergency_resolved(service="cri-scan")
    assert "cri-scan" not in cooldown.active_emergency_services(now=NOW + timedelta(minutes=5))


def test_cancel_emergency_posts_cancel_by_tag(monkeypatch):
    from watchdog import notify

    monkeypatch.setenv("PUSHOVER_USER", "u")
    monkeypatch.setenv("PUSHOVER_TOKEN", "t")
    calls = []

    def fake_http_post(url, payload, headers=None):
        calls.append((url, payload))
        return (200, b'{"status":1}')

    with patch("watchdog.notify._http_post", side_effect=fake_http_post):
        err = notify.cancel_emergency("ib-gateway-grouped")

    assert err is None
    assert len(calls) == 1
    assert "receipts/cancel_by_tag/ib-gateway-grouped" in calls[0][0]
    assert calls[0][1] == {"token": "t"}


def test_emergency_payload_carries_tag():
    from watchdog import notify

    p = notify.build_pushover_payload(
        user="u", token="t", title="x", message="y", severity="P1", tag="cri-scan"
    )
    assert p["priority"] == 2
    assert p["tag"] == "cri-scan"
    # Non-emergency never carries the tag (nothing to cancel).
    p3 = notify.build_pushover_payload(
        user="u", token="t", title="x", message="y", severity="P3", tag="cri-scan"
    )
    assert "tag" not in p3


def test_reconcile_cancels_recovered_service(db_conn):
    from watchdog import cooldown, check
    from watchdog import __main__ as wd_main

    cooldown.mark_notified(service="cri-scan", severity="P1", now=NOW)
    healthy = check.CheckOutcome(
        service="cri-scan", kind="stale", status="healthy", severity=None,
        fired=False, message="fresh", consecutive_failures=0, now=NOW,
    )
    report = check.BucketReport(bucket="intraday", ran=True, outcomes=[healthy])

    cancelled = []
    with patch("watchdog.notify.cancel_emergency", side_effect=lambda t: cancelled.append(t)):
        wd_main._reconcile_recovered_emergencies(report=report, now=NOW + timedelta(minutes=5))

    assert cancelled == ["cri-scan"]
    # Marked resolved → not cancelled again next cycle.
    assert "cri-scan" not in cooldown.active_emergency_services(now=NOW + timedelta(minutes=6))


def test_reconcile_leaves_still_failing_service_alone(db_conn):
    from watchdog import cooldown, check
    from watchdog import __main__ as wd_main

    cooldown.mark_notified(service="cri-scan", severity="P1", now=NOW)
    still_stale = check.CheckOutcome(
        service="cri-scan", kind="stale", status="stale", severity="P1",
        fired=True, message="silent", consecutive_failures=2, now=NOW,
    )
    report = check.BucketReport(bucket="intraday", ran=True, outcomes=[still_stale])

    cancelled = []
    with patch("watchdog.notify.cancel_emergency", side_effect=lambda t: cancelled.append(t)):
        wd_main._reconcile_recovered_emergencies(report=report, now=NOW + timedelta(minutes=5))

    assert cancelled == [], "must NOT cancel an emergency while the service is still failing"
