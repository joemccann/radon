"""GPU bootstrap regression guards; subprocesses never mutate host configuration."""

import os
import subprocess
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[1] / "gpu" / "bootstrap.sh"


def shell(body: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"; shift; ' + body, "test", str(BOOTSTRAP), *args],
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("role", ["", "app", "broker", "combined", "radon-slm; true"])
def test_wrong_role_refuses_before_commands(role: str):
    result = shell(
        'python3() { echo UNEXPECTED; }; validate_config "$@"', role, "8.8.8.8/32", "enp7s0", "/root/operator.pub"
    )
    assert result.returncode == 78
    assert "explicit --role" in result.stderr
    assert "UNEXPECTED" not in result.stdout


@pytest.mark.parametrize("interface", ["", "lo", "tailscale0", "eth0;id", "eth0 extra", "-eth0", "a" * 16])
def test_invalid_interface_refuses_before_commands(interface: str):
    result = shell(
        'python3() { echo UNEXPECTED; }; validate_config "$@"',
        "radon-slm",
        "8.8.8.8/32",
        interface,
        "/root/operator.pub",
    )
    assert result.returncode == 78
    assert "invalid public interface" in result.stderr
    assert "UNEXPECTED" not in result.stdout


@pytest.mark.parametrize(
    "cidr",
    ["", "0.0.0.0/0", "8.8.8.0/24", "127.0.0.1/32", "10.0.0.1/32", "100.64.0.1/32", "::/0", "::1/128", "8.8.8.8;id"],
)
def test_invalid_cidr_refuses_before_key_or_network_changes(cidr: str):
    result = shell(
        'apt-get() { echo UNEXPECTED; }; ufw() { echo UNEXPECTED; }; validate_config "$@"',
        "radon-slm",
        cidr,
        "enp7s0",
        "/nonexistent/operator.pub",
    )
    assert result.returncode == 78
    assert "UNEXPECTED" not in result.stdout
    assert "No such file" not in result.stderr


def test_untrusted_key_file_refused(tmp_path: Path):
    keys = tmp_path / "operator.pub"
    keys.write_text("ssh-ed25519 invalid\n")
    keys.chmod(0o666)
    result = shell('validate_config "$@"', "radon-slm", "8.8.8.8/32", "eth0", str(keys))
    assert result.returncode == 78
    assert "root-owned, regular" in result.stderr


def test_symlink_key_file_refused(tmp_path: Path):
    keys = tmp_path / "operator.pub"
    keys.symlink_to(tmp_path / "missing")
    result = shell('validate_config "$@"', "radon-slm", "8.8.8.8/32", "eth0", str(keys))
    assert result.returncode == 78
    assert "root-owned, regular" in result.stderr


@pytest.mark.parametrize(
    "marker",
    [
        "etc/radon/env",
        "etc/radon/ib-gateway-compose.yml",
        "var/lib/radon/control-plane-ready",
        "home/radon/radon-cloud/.env",
        "etc/systemd/system/radon-api.service",
        "etc/systemd/system/radon-nextjs.service",
        "etc/systemd/system/ib-gateway.service",
    ],
)
@pytest.mark.parametrize("symlink", [False, True])
def test_existing_app_broker_marker_refuses(tmp_path: Path, marker: str, symlink: bool):
    target = tmp_path / marker
    target.parent.mkdir(parents=True)
    if symlink:
        target.symlink_to(tmp_path / "absent")
    else:
        target.write_text("DO NOT READ SECRETS")
    result = shell('check_host_role "$1"', str(tmp_path))
    assert result.returncode == 78
    assert "existing app/broker" in result.stderr
    assert "DO NOT READ SECRETS" not in result.stderr


def test_role_marker_allows_repeat_only_for_same_role(tmp_path: Path):
    marker = tmp_path / "etc/radon/gpu-host"
    marker.parent.mkdir(parents=True)
    marker.write_text("radon-slm\n")
    assert shell('check_host_role "$1"', str(tmp_path)).returncode == 0
    marker.write_text("broker\n")
    assert shell('check_host_role "$1"', str(tmp_path)).returncode == 78


def test_firewall_preserves_recovery_before_enable_and_never_exposes_inference():
    result = shell('ufw() { printf "%s\\n" "$*"; }; configure_firewall "$@"', "8.8.8.8/32", "enp7s0")
    assert result.returncode == 0
    commands = result.stdout.splitlines()
    assert commands[0] == "allow in on enp7s0 from 8.8.8.8/32 to any port 22 proto tcp comment radon-gpu-bootstrap-ssh"
    assert commands[-1] == "--force enable"
    inference_allows = [command for command in commands if command.startswith("allow") and "8350" in command]
    assert inference_allows == ["allow in on tailscale0 to any port 8350 proto tcp comment radon-gpu-tailnet-inference"]
    assert "insert 1 deny in on enp7s0 to any port 8350 proto tcp comment radon-gpu-private-inference" in commands
    assert not any("reset" in command or "flush" in command for command in commands)


def test_source_has_no_side_effects():
    result = shell(":")
    assert result.returncode == 0
    assert not result.stdout and not result.stderr


def test_help_does_not_require_root_or_change_host():
    result = subprocess.run(["bash", str(BOOTSTRAP), "--help"], text=True, capture_output=True, check=False)
    assert result.returncode == 0
    assert "--role radon-slm" in result.stdout


def test_non_root_execution_refused():
    if os.geteuid() == 0:
        pytest.skip("CI normally executes as an unprivileged user")
    result = subprocess.run(["bash", str(BOOTSTRAP)], text=True, capture_output=True, check=False)
    assert result.returncode == 78
    assert "run as root" in result.stderr
