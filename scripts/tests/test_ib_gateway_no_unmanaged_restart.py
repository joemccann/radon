"""Gateway supervisors must not restart outside Radon's 2FA push lease."""

import os
from pathlib import Path
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_IB_GATEWAY_IMAGE = (
    "ghcr.io/gnzsnz/ib-gateway@sha256:"
    "1e2236804ac949b789197c225a048c0c891c9958f948c87166226e5b647ff581"
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def test_in_tree_compose_disables_docker_auto_restart():
    compose = yaml.safe_load(
        (ROOT / "docker" / "ib-gateway" / "docker-compose.yml").read_text()
    )
    assert compose["services"]["ib-gateway"]["restart"] == "no"


def test_in_tree_compose_matches_production_gateway_image_and_ports():
    compose = yaml.safe_load(
        (ROOT / "docker" / "ib-gateway" / "docker-compose.yml").read_text()
    )
    gateway = compose["services"]["ib-gateway"]
    assert gateway["image"] == PRODUCTION_IB_GATEWAY_IMAGE
    assert "127.0.0.1:${IB_LIVE_PORT:-4001}:4003" in gateway["ports"]
    assert "127.0.0.1:${IB_PAPER_PORT:-4002}:4004" in gateway["ports"]


def test_in_tree_compose_pins_the_daily_restart_inside_the_quiet_window():
    compose = yaml.safe_load(
        (ROOT / "docker" / "ib-gateway" / "docker-compose.yml").read_text()
    )
    env = compose["services"]["ib-gateway"]["environment"]
    assert env["AUTO_RESTART_TIME"] == "11:45 PM"
    assert env["TWS_COLD_RESTART"] == ""


def test_auto_restart_time_is_valid_ibc_format_and_quiet():
    """IBC silently ignores a malformed value (DUR-08: "23:58 ET") and falls
    back to the stored default. It must be `HH:MM AM/PM`, in the container's
    UTC, inside the watchdog's default quiet window."""
    import re
    from datetime import datetime, timezone

    sys.path.insert(0, str(ROOT / "scripts"))
    import ib_watchdog

    values = {
        yaml.safe_load((ROOT / "docker" / "ib-gateway" / "docker-compose.yml").read_text())[
            "services"]["ib-gateway"]["environment"]["AUTO_RESTART_TIME"],
    }
    for item in yaml.safe_load((ROOT / "cloud" / "docker-compose.yml").read_text())[
            "services"]["ib-gateway"]["environment"]:
        if item.startswith("AUTO_RESTART_TIME="):
            values.add(item.split("=", 1)[1])
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


def test_in_tree_compose_healthcheck_selects_api_port_from_trading_mode():
    compose = yaml.safe_load(
        (ROOT / "docker" / "ib-gateway" / "docker-compose.yml").read_text()
    )
    command = compose["services"]["ib-gateway"]["healthcheck"]["test"]

    assert command[0] == "CMD-SHELL"
    assert "$${TRADING_MODE:-live}" in command[1]
    assert "live) port=4001" in command[1]
    assert "paper) port=4002" in command[1]
    assert "*) exit 1" in command[1]


def test_launchd_setup_exits_instead_of_restarting_after_2fa_timeout():
    setup = (ROOT / "scripts" / "setup_ibc.sh").read_text()
    tracked_plist = (ROOT / "config" / "com.radon.ibc-gateway.plist").read_text()

    assert "TWOFA_TIMEOUT_ACTION=restart" not in setup
    assert "<string>restart</string>" not in setup
    assert "<string>restart</string>" not in tracked_plist
    assert "TWOFA_TIMEOUT_ACTION=exit" in setup
    assert "<string>exit</string>" in setup
    assert 'patch_config_setting "AutoRestartTime" ""' in setup
    assert 'patch_config_setting "ColdRestartTime" ""' in setup
    assert 'patch_config_setting "ReloginAfterSecondFactorAuthenticationTimeout" "no"' in setup
    assert "<key>StartCalendarInterval</key>" not in setup
    assert "<key>StartCalendarInterval</key>" not in tracked_plist
    assert "<key>RunAtLoad</key>\n    <false/>" in setup
    assert "<key>RunAtLoad</key>\n    <false/>" in tracked_plist

    install_block = setup[setup.index("install() {"):setup.index("uninstall() {")]
    assert install_block.index('acquire_push_lease "$INSTALL_LOCK_HOLDER"') < install_block.index(
        "launchctl unload"
    )
    assert install_block.index('acquire_push_lease "$INSTALL_LOCK_HOLDER"') < install_block.index(
        'launchctl start "$LABEL"'
    )

    start_block = setup[setup.index("start_gateway() {"):setup.index("stop_gateway() {")]
    assert start_block.index('acquire_push_lease "$MANUAL_LOCK_HOLDER"') < start_block.index(
        'exec "$IBC_VENDOR/scripts/displaybannerandlaunch.sh"'
    )


def test_local_docker_wrapper_refuses_same_holder_reentry(tmp_path):
    compose_dir = tmp_path / "compose"
    (compose_dir / "secrets").mkdir(parents=True)
    (compose_dir / ".env").write_text("TRADING_MODE=paper\n")
    (compose_dir / "docker-compose.yml").write_text("services: {}\n")
    password = compose_dir / "secrets" / "ib_password.txt"
    password.write_text("test-only\n")
    password.chmod(0o600)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    _write_executable(
        fake_bin / "docker",
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\nexit 0\n',
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
        "IB_GATEWAY_COMPOSE_DIR": str(compose_dir),
        "IB_2FA_LOCK_PATH": str(tmp_path / "push-lock.json"),
        "IB_2FA_LOCK_PYTHON": sys.executable,
    }
    command = [str(ROOT / "scripts" / "docker_ib_gateway.sh"), "restart"]

    first = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    second = subprocess.run(command, env=env, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "shared 2FA push lease" in second.stderr
    restart_calls = [line for line in docker_log.read_text().splitlines() if " restart ib-gateway" in line]
    assert len(restart_calls) == 1


def test_ibc_remote_wrapper_refuses_same_holder_reentry(tmp_path):
    ibc_bin = tmp_path / "ibc-bin"
    ibc_bin.mkdir()
    wrapper_log = tmp_path / "wrapper.log"
    for name in ("start", "stop", "restart", "status"):
        _write_executable(
            ibc_bin / f"{name}-secure-ibc-service.sh",
            f'#!/bin/sh\nprintf "{name}\\n" >> "$WRAPPER_LOG"\n',
        )
    env = {
        **os.environ,
        "IBC_BIN_DIR": str(ibc_bin),
        "WRAPPER_LOG": str(wrapper_log),
        "IB_2FA_LOCK_PATH": str(tmp_path / "push-lock.json"),
        "IB_2FA_LOCK_PYTHON": sys.executable,
    }
    script = str(ROOT / "scripts" / "ibc_remote_control.sh")

    first = subprocess.run([script, "ibc-start"], env=env, text=True, capture_output=True, check=False)
    second = subprocess.run([script, "ibc-restart"], env=env, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert wrapper_log.read_text().splitlines() == ["start"]


def test_cloud_start_uses_authoritative_remote_control_helper():
    cloud = (ROOT / "scripts" / "cloud.sh").read_text()
    assert "/usr/local/bin/radon-ib-gateway-control start" in cloud
    assert "ib_2fa_lock.py acquire scripts.cloud" not in cloud
    assert "docker compose up -d" not in cloud


def test_ib_docs_do_not_instruct_unmanaged_cycles_or_relogin():
    docs = "\n".join(
        (ROOT / path).read_text()
        for path in (
            "docs/ib-gateway-docker.md",
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
