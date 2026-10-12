"""radon-control.service wiring: the stack operator must never stop it.

The admin panel's full-stack restart runs `sudo -n /usr/local/bin/radon
restart` from INSIDE radon-control's cgroup. If `radon stop|restart` quiesced
radon-control like a timer-owned oneshot, systemd would kill the operator
process halfway through the restart it was running.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _bash_toolchain import MODERN_BASH, requires_modern_bash

pytestmark = requires_modern_bash

CLOUD_ROOT = Path(__file__).resolve().parents[1]
OPERATOR = CLOUD_ROOT / "scripts" / "operator-radon.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.mark.parametrize("role", ["app", "combined"])
@pytest.mark.parametrize("action", ["stop", "restart", "start"])
def test_stack_operator_never_touches_the_control_daemon(tmp_path: Path, role: str, action: str) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "${1:-}" = list-units ]; then
  for unit in radon-ib-gateway radon-api radon-nextjs radon-relay radon-monitor radon-newsfeed radon-health radon-control; do
    printf '%s.service loaded active running persistent\\n' "$unit"
  done
  printf '%s\\n' 'radon-cor.timer loaded active waiting timer'
  exit 0
fi
if [ "${1:-}" = list-unit-files ]; then
  printf '%s\\n' 'radon-cor.timer enabled enabled'
  exit 0
fi
printf 'systemctl %s\\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(gateway_control, "#!/bin/sh\nexit 0\n")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
        "RADON_HOST_ROLE": role,
    }
    result = subprocess.run(
        [str(MODERN_BASH), str(OPERATOR), action],
        env=env, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = events.read_text().splitlines()
    mutations = [line for line in lines if re.match(r"systemctl (?:start|stop|restart) ", line)]
    assert mutations, lines
    for line in mutations:
        assert "radon-control.service" not in line, line
        assert "radon-health.service" not in line, line


def test_operator_still_controls_the_daemon_only_by_explicit_unit_verb() -> None:
    # `radon unit restart radon-control.service` stays available to an
    # operator over ssh; the panel daemon itself refuses that unit.
    source = OPERATOR.read_text()
    assert "radon-control.service" in source
    assert re.search(r"radon-health\.service\|radon-control\.service", source)


def test_setup_inventories_and_enables_the_control_daemon() -> None:
    setup = (CLOUD_ROOT / "scripts" / "setup-vps.sh").read_text()
    inventory = setup[setup.index("readonly SERVICE_FILES=("):]
    inventory = inventory[: inventory.index(")")]
    assert "radon-control.service" in inventory
    enable = setup[setup.index("enable_services() {"):setup.index("start_services() {")]
    # Persistent daemon: enable_services must not skip it.
    assert "radon-control.service" not in enable


def test_control_daemon_is_manifest_pinned_for_install_units() -> None:
    manifest = (CLOUD_ROOT / "config" / "installed-units.sha256").read_text()
    assert re.search(r"^[0-9a-f]{64}  radon-control\.service$", manifest, re.M)


# --- `radon logs radon-NAME.service`: the read-only journal verb ---------------
#
# The admin panel's failed/stale rows disclose the owning unit's journal. The
# containerised API has no journalctl, so radon-control runs
# `sudo -n /usr/local/bin/radon logs <unit>`. The operator validates argc and
# the unit regex itself and builds the journalctl argv; the caller supplies no
# option, so a wildcard sudoers argument cannot widen into journalctl flags.

EXPECTED_JOURNAL_ARGV = [
    "-u", "radon-api.service", "-n", "300", "--no-pager", "-q",
    "-o", "json", "--output-fields=MESSAGE,PRIORITY",
]
SUDOERS = CLOUD_ROOT / "config" / "sudoers.d" / "radon-ops"


def _logs_env(tmp_path: Path, *, allow_root: bool = True) -> tuple[dict, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    events = tmp_path / "events.log"
    _write_executable(
        fake_bin / "journalctl",
        """#!/bin/sh
{ printf 'journalctl'; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'; } >> "$RADON_OPERATOR_EVENTS"
printf '%s\\n' '{"__REALTIME_TIMESTAMP":"1760000000000000","PRIORITY":"3","MESSAGE":"boom"}'
""",
    )
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
printf 'systemctl %s\\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """#!/bin/sh
{ printf 'sudo'; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'; } >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        k: v for k, v in os.environ.items()
        if k not in {"RADON_OPERATOR_ALLOW_ROOT", "RADON_OPERATOR_ALLOW_UNPRIVILEGED"}
    }
    env.update({
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "topology"),
        "RADON_OPERATOR_INSTALLED_PATH": "/usr/local/bin/radon",
    })
    if allow_root:
        env["RADON_OPERATOR_ALLOW_ROOT"] = "1"
    return env, events


def _run_operator(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(MODERN_BASH), str(OPERATOR), *args],
        env=env, text=True, capture_output=True, check=False, timeout=60,
    )


def _events(events: Path) -> list[str]:
    return events.read_text().splitlines() if events.exists() else []


def test_logs_verb_runs_the_fixed_journalctl_argv_without_the_deploy_lock(tmp_path: Path) -> None:
    env, events = _logs_env(tmp_path)
    result = _run_operator(env, "logs", "radon-api.service")
    assert result.returncode == 0, result.stderr
    assert '"MESSAGE":"boom"' in result.stdout
    lines = _events(events)
    assert lines == ["\t".join(["journalctl", *EXPECTED_JOURNAL_ARGV])], lines
    # Read-only: no deploy lock taken, no systemctl, no unit enumeration.
    assert not (tmp_path / "deploy.lock").exists()


def test_logs_verb_reenters_through_the_exact_sudoers_grant(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("sudo re-entry is only taken by a non-root caller")
    env, events = _logs_env(tmp_path, allow_root=False)
    result = _run_operator(env, "logs", "radon-relay.service")
    assert result.returncode == 0, result.stderr
    assert _events(events) == [
        "\t".join(["sudo", "-n", "/usr/local/bin/radon", "logs", "radon-relay.service"])
    ]


@pytest.mark.parametrize(
    "args",
    [
        ("logs",),
        ("logs", "radon-api.service", "extra"),
        ("logs", "radon-api.service", "-n", "5000"),
        ("logs", "radon-cor.timer"),
        ("logs", "radon-beta-api.service"),
        ("logs", "radon-api"),
        ("logs", "--since=yesterday"),
        ("logs", "-u"),
        ("logs", "-oradon-api.service"),
        ("logs", "radon-API.service"),
        ("logs", "radon-api.service;id"),
        ("logs", "ssh.service"),
        ("logs", ""),
    ],
)
def test_logs_verb_refuses_anything_but_one_radon_service(tmp_path: Path, args: tuple) -> None:
    env, events = _logs_env(tmp_path)
    result = _run_operator(env, *args)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "usage: radon logs radon-NAME.service" in result.stderr
    assert _events(events) == []


def test_logs_verb_refuses_before_sudo_reentry(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("sudo re-entry is only taken by a non-root caller")
    env, events = _logs_env(tmp_path, allow_root=False)
    result = _run_operator(env, "logs", "radon-cor.timer")
    assert result.returncode == 2
    assert _events(events) == []


def test_logs_verb_is_bounded() -> None:
    source = OPERATOR.read_text()
    branch = source.split('if [[ "$requested_action" == "logs" ]]; then', 1)[1]
    branch = branch.split("\nfi", 1)[0]
    assert re.search(r'run_bounded "\$JOURNAL_QUERY_TIMEOUT_SECS" \\?\s*journalctl', branch), branch
    assert '-u "$unit_name" -n 300 --no-pager -q -o json --output-fields=MESSAGE,PRIORITY' in branch


def test_sudoers_grants_exactly_the_logs_verb_and_no_journalctl() -> None:
    text = SUDOERS.read_text(encoding="utf-8")
    rules = [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "radon ALL=(root) NOPASSWD: /usr/local/bin/radon logs radon-*.service" in rules
    assert not any("journalctl" in rule for rule in rules), rules
    assert not any(" logs radon-*.timer" in rule for rule in rules), rules
    comments = "\n".join(line for line in text.splitlines() if line.lstrip().startswith("#"))
    assert "No journalctl grant" in comments
    assert "radon logs" in comments and "fixed" in comments
