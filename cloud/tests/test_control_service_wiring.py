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
