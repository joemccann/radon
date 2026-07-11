from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from scripts.watchdog import __main__ as watchdog_main
from scripts.watchdog.check import CheckOutcome


def test_all_cooldown_suppressed_alerts_still_heartbeat_dispatcher() -> None:
    now = datetime(2026, 7, 10, 20, 0, tzinfo=timezone.utc)
    outcome = CheckOutcome(
        service="portfolio-sync",
        kind="stale",
        status="stale",
        severity="P1",
        fired=True,
        message="stale",
        consecutive_failures=2,
        now=now,
    )
    report = SimpleNamespace(ran=True, outcomes=[outcome])
    summary = SimpleNamespace(health_recorded=False)

    with (
        patch("scripts.watchdog.check.check_bucket", return_value=report),
        patch("scripts.watchdog.notify.log_startup_warning"),
        patch("scripts.watchdog.grouping.dispatch_with_grouping", return_value=summary),
        patch("scripts.watchdog.notify.heartbeat_ok") as heartbeat,
        patch.object(watchdog_main, "_reconcile_recovered_emergencies"),
    ):
        assert watchdog_main._cmd_bucket(Namespace(bucket="intraday")) == 0

    heartbeat.assert_called_once()
