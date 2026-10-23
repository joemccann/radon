"""Gateway supervisors must not restart outside Radon's 2FA push lease."""

import os
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_IB_GATEWAY_IMAGE = (
    "ghcr.io/gnzsnz/ib-gateway@sha256:"
    "1e2236804ac949b789197c225a048c0c891c9958f948c87166226e5b647ff581"
)


def _production_gateway_env() -> list[str]:
    compose = yaml.safe_load((ROOT / "cloud" / "docker-compose.yml").read_text())
    return compose["services"]["ib-gateway"]["environment"]


def test_production_compose_pins_gateway_image():
    assert PRODUCTION_IB_GATEWAY_IMAGE in (ROOT / "cloud" / "docker-compose.yml").read_text()


def test_auto_restart_time_is_valid_ibc_format_and_quiet():
    """IBC silently ignores a malformed value (DUR-08: "23:58 ET") and falls
    back to the stored default. It must be `HH:MM AM/PM`, in the container's
    UTC, inside the watchdog's default quiet window."""
    import re
    from datetime import datetime, timezone

    sys.path.insert(0, str(ROOT / "scripts"))
    import ib_watchdog

    values = {
        item.split("=", 1)[1]
        for item in _production_gateway_env()
        if item.startswith("AUTO_RESTART_TIME=")
    }
    assert len(values) == 1, values
    value = values.pop()
    assert re.fullmatch(r"(0[1-9]|1[0-2]):[0-5]\d (AM|PM)", value), value
    at = datetime.strptime(value, "%I:%M %p")
    os.environ[ib_watchdog.QUIET_WINDOWS_ENV] = ib_watchdog.DEFAULT_QUIET_WINDOWS_UTC
    try:
        assert ib_watchdog.quiet_window_active(
            datetime(2026, 10, 5, at.hour, at.minute, tzinfo=timezone.utc)
        )
    finally:
        del os.environ[ib_watchdog.QUIET_WINDOWS_ENV]


def test_cloud_launcher_never_touches_gateway_lifecycle():
    cloud = (ROOT / "scripts" / "cloud.sh").read_text()
    for forbidden in (
        "ssh ",
        "docker",
        "launchctl",
        ".env.ib-mode",
        "/usr/local/bin/radon-ib-gateway-control",
        "ib_2fa_lock.py",
        "docker compose up -d",
    ):
        assert forbidden not in cloud, forbidden


def test_ib_docs_do_not_instruct_unmanaged_cycles_or_relogin():
    docs = "\n".join(
        (ROOT / path).read_text()
        for path in (
            "docs/ib-connection-troubleshooting.md",
            "docs/ib_tws_api.md",
            "docs/implement.md",
        )
    )
    for forbidden in (
        "restart: unless-stopped",
        "AutoRestartTime=11:58 PM",
        "ColdRestartTime=07:05",
        "TWOFA_TIMEOUT_ACTION=restart",
        "ReloginAfterSecondFactorAuthenticationTimeout=yes",
        "~/ibc/bin/start-secure-ibc-service.sh",
        "~/ibc/bin/restart-secure-ibc-service.sh",
        "local.ibc-gateway.plist",
        "apps/finance/convex-scavenger",
    ):
        assert forbidden not in docs
