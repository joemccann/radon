"""setup-vps.sh ingress hardening: ufw actually enabled, sshd keys-only.

Provisioning previously added ufw allow rules for 80/443 but never set a
default-deny inbound policy or enabled ufw, and never turned off password
SSH login. These tests run the two provisioning functions against stub
`ufw` / `sshd` / `systemctl` binaries on PATH and assert the argv order,
plus a doc contract so cloud/README.md stops claiming ufw blocks public SSH.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "setup-vps.sh"
README = ROOT / "README.md"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_setup_function(
    function: str, fake_bin: Path, extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    shell = f"""
set -euo pipefail
source {SETUP!s}
{function}
"""
    return subprocess.run(
        ["bash", "-c", shell],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RADON_SETUP_SOURCE_ONLY": "1",
            **extra_env,
        },
        capture_output=True,
        text=True,
    )


# ── open_firewall ─────────────────────────────────────────────────────


@pytest.fixture
def fake_ufw(tmp_path: Path) -> tuple[Path, Path]:
    """Stateful ufw stub: records argv, answers `status` from recorded state."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "ufw.log"
    state = tmp_path / "ufw-state"
    state.mkdir()
    _write_executable(
        fake_bin / "ufw",
        f"""#!/bin/sh
log={log!s}
state={state!s}
printf '%s\\n' "$*" >> "$log"
case "$1" in
  status)
    if [ -f "$state/enabled" ]; then
      echo "Status: active"
      cat "$state/rules" 2>/dev/null || true
    else
      echo "Status: inactive"
    fi
    ;;
  allow)
    case "$*" in
      "allow in on tailscale0") echo "Anywhere on tailscale0   ALLOW IN   Anywhere" >> "$state/rules" ;;
      "allow from 10.0.0.0/16 to any port 8321 proto tcp comment radon-broker health")
        echo "8321/tcp   ALLOW IN   10.0.0.0/16" >> "$state/rules" ;;
      *) echo "$2   ALLOW IN   Anywhere" >> "$state/rules" ;;
    esac
    ;;
  --force)
    [ "$2" = "enable" ] && touch "$state/enabled"
    ;;
esac
exit 0
""",
    )
    return fake_bin, log


class TestOpenFirewall:
    def test_enables_ufw_default_deny_with_ssh_allowed_before_enable(
        self, fake_ufw: tuple[Path, Path]
    ) -> None:
        fake_bin, log = fake_ufw
        result = _run_setup_function("open_firewall", fake_bin, {})
        assert result.returncode == 0, result.stderr
        calls = log.read_text(encoding="utf-8").splitlines()

        assert "default deny incoming" in calls
        assert "default allow outgoing" in calls
        assert "allow 22/tcp" in calls
        assert "allow 80/tcp" in calls
        assert "allow 443/tcp" in calls
        assert "allow in on tailscale0" in calls
        assert (
            "allow from 10.0.0.0/16 to any port 8321 proto tcp comment radon-broker health"
            in calls
        )
        assert "--force enable" in calls
        # Lockout guard: SSH must be allowed before a default-deny firewall
        # is switched on.
        assert calls.index("allow 22/tcp") < calls.index("--force enable")
        assert calls.index("default deny incoming") < calls.index("--force enable")
        assert calls.index("--force enable") == len(calls) - 1 or all(
            c.startswith("status") for c in calls[calls.index("--force enable") + 1 :]
        )

    def test_second_run_adds_no_rules_and_does_not_reenable(
        self, fake_ufw: tuple[Path, Path]
    ) -> None:
        fake_bin, log = fake_ufw
        first = _run_setup_function("open_firewall", fake_bin, {})
        assert first.returncode == 0, first.stderr
        first_count = len(log.read_text(encoding="utf-8").splitlines())

        second = _run_setup_function("open_firewall", fake_bin, {})
        assert second.returncode == 0, second.stderr
        second_calls = log.read_text(encoding="utf-8").splitlines()[first_count:]

        assert second_calls, "second run made no ufw calls at all"
        assert "--force enable" not in second_calls
        assert not [c for c in second_calls if c.startswith("allow ")], (
            f"second run duplicated allow rules: {second_calls}"
        )

    def test_main_calls_open_firewall(self) -> None:
        text = SETUP.read_text(encoding="utf-8")
        assert "\n  open_firewall\n" in text


# ── install_sshd_keys_only ────────────────────────────────────────────


@pytest.fixture
def fake_sshd_tools(tmp_path: Path) -> tuple[Path, Path, Path]:
    """sshd + systemctl stubs sharing one ordered log."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "sshd-tools.log"
    for tool in ("sshd", "systemctl"):
        _write_executable(
            fake_bin / tool,
            f"#!/bin/sh\nprintf '%s\\n' \"{tool} $*\" >> {log!s}\nexit 0\n",
        )
    dropin = tmp_path / "etc" / "ssh" / "sshd_config.d" / "10-radon-keys-only.conf"
    return fake_bin, log, dropin


class TestInstallSshdKeysOnly:
    def test_writes_two_directives_validates_then_reloads(
        self, fake_sshd_tools: tuple[Path, Path, Path]
    ) -> None:
        fake_bin, log, dropin = fake_sshd_tools
        result = _run_setup_function(
            "install_sshd_keys_only",
            fake_bin,
            {"RADON_SSHD_KEYS_ONLY_DROPIN": str(dropin)},
        )
        assert result.returncode == 0, result.stderr
        assert dropin.read_text(encoding="utf-8") == (
            "PasswordAuthentication no\nKbdInteractiveAuthentication no\n"
        )
        assert oct(dropin.stat().st_mode & 0o777) == "0o644"

        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls == ["sshd -t", "systemctl reload ssh"]

    def test_second_run_is_a_no_op(
        self, fake_sshd_tools: tuple[Path, Path, Path]
    ) -> None:
        fake_bin, log, dropin = fake_sshd_tools
        env = {"RADON_SSHD_KEYS_ONLY_DROPIN": str(dropin)}
        first = _run_setup_function("install_sshd_keys_only", fake_bin, env)
        assert first.returncode == 0, first.stderr
        second = _run_setup_function("install_sshd_keys_only", fake_bin, env)
        assert second.returncode == 0, second.stderr

        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls == ["sshd -t", "systemctl reload ssh"], (
            f"second run must not re-validate or reload: {calls}"
        )

    def test_failed_validation_removes_dropin_and_skips_reload(
        self, fake_sshd_tools: tuple[Path, Path, Path]
    ) -> None:
        fake_bin, log, dropin = fake_sshd_tools
        _write_executable(
            fake_bin / "sshd",
            f"#!/bin/sh\nprintf '%s\\n' \"sshd $*\" >> {log!s}\nexit 255\n",
        )
        result = _run_setup_function(
            "install_sshd_keys_only",
            fake_bin,
            {"RADON_SSHD_KEYS_ONLY_DROPIN": str(dropin)},
        )
        assert result.returncode != 0
        assert not dropin.exists()
        assert log.read_text(encoding="utf-8").splitlines() == ["sshd -t"]

    def test_main_calls_install_sshd_keys_only_with_open_firewall(self) -> None:
        text = SETUP.read_text(encoding="utf-8")
        assert "\n  install_sshd_keys_only\n" in text
        assert text.index("\n  open_firewall\n") < text.index(
            "\n  install_sshd_keys_only\n"
        )

    def test_default_dropin_path(self) -> None:
        text = SETUP.read_text(encoding="utf-8")
        assert "/etc/ssh/sshd_config.d/10-radon-keys-only.conf" in text


# ── README doc contract ───────────────────────────────────────────────


class TestReadmeSshRouteClaim:
    def test_readme_does_not_claim_ufw_blocks_public_ssh(self) -> None:
        text = README.read_text(encoding="utf-8")
        assert "ufw blocks public SSH" not in text
        assert "SSH via Tailscale only" not in text

    def test_readme_states_keys_only_public_ssh(self) -> None:
        text = README.read_text(encoding="utf-8")
        security = text.split("## Security", 1)[1].split("### DNS / TLS", 1)[0]
        assert "22/tcp" in security
        assert "keys-only" in security
        assert "sshd drop-in" in security
        assert "Tailscale SSH" in security
