"""IBC must yield the shared IBKR login to the operator, not fight for it.

2026-09-25: with ExistingSessionDetectedAction=primary, IBC reclaimed the
session ("scenario 5") every time the operator logged in to the IBKR web
portal and kicked them out. In an emergency the operator flattens from IBKR
Mobile while the app host is down; the Gateway must give the session up
(primaryoverride: "scenario 6", IBC exits) and the watchdog's auto-hold keeps
it down. A fresh Gateway login still takes the session (scenario 3), so the
normal 2FA recovery path is unchanged.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _env_list(compose_path: Path) -> list[str]:
    compose = yaml.safe_load(compose_path.read_text())
    return compose["services"]["ib-gateway"]["environment"]


def test_production_compose_pins_primaryoverride():
    env = _env_list(ROOT / "cloud" / "docker-compose.yml")
    # Pinned, not ${...:-default}: /etc/radon/env still says `primary` on a
    # broker that predates this change, and `environment:` beats `env_file:`.
    assert "EXISTING_SESSION_DETECTED_ACTION=primaryoverride" in env
    assert not any(e.startswith("EXISTING_SESSION_DETECTED_ACTION=$") for e in env)


def test_local_docker_compose_yields_too():
    compose = yaml.safe_load((ROOT / "docker" / "ib-gateway" / "docker-compose.yml").read_text())
    env = compose["services"]["ib-gateway"]["environment"]
    assert env["EXISTING_SESSION_DETECTED_ACTION"] == "primaryoverride"


def test_launchd_ibc_setup_yields_too():
    setup = (ROOT / "scripts" / "setup_ibc.sh").read_text()
    assert 'patch_config_setting "ExistingSessionDetectedAction" "primaryoverride"' in setup
    assert '"ExistingSessionDetectedAction" "primary"' not in setup


def test_2fa_recovery_settings_are_unchanged():
    env = _env_list(ROOT / "cloud" / "docker-compose.yml")
    assert "TWOFA_TIMEOUT_ACTION=exit" in env
    assert "RELOGIN_AFTER_TWOFA_TIMEOUT=no" in env
    assert "AUTO_RESTART_TIME=11:45 PM" in env


def test_watchdog_recognises_the_yield_line_ibc_prints():
    """The auto-hold keys on IBC's exact scenario-6 text. If the pinned IBC
    ever rewords it, the hold silently stops engaging, so pin it here."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from ib_watchdog import parse_session_yield

    line = (
        "2026-10-01 14:02:11:101 IBC: Other session may be primary, so end this "
        "session and let the other one proceed (scenario 6)"
    )
    assert parse_session_yield(line) == line


def test_launchd_ibc_setup_does_not_claim_blank_restart_disables_cycle():
    """DOC-159: a blank AutoRestartTime/ColdRestartTime leaves the Gateway's
    stored daily-cycle setting in force; the installer must not print
    "disabled" for it."""
    setup = (ROOT / "scripts" / "setup_ibc.sh").read_text()
    assert 'patch_config_setting "AutoRestartTime" ""' in setup
    assert 'patch_config_setting "ColdRestartTime" ""' in setup
    assert "RestartTime=disabled" not in setup
    assert setup.count("Gateway's stored daily-cycle setting applies") == 2
