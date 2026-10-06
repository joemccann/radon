"""`radon unit restart` must wait out the unit's own stop and start.

2026-10-06 20:10Z, page 77becdbdfc506a32c447c10283efe6b4: a transient
unit ran `/usr/local/bin/radon unit restart radon-api.service` and
exited 70 at 20.5s (`Result=exit-code`, `NRestarts=0`). The API
container was still inside its 30s halt grace (inactive at 27s) and
was active again at 63s with `Result=success`. Exit 70 is the operator
refusing a systemctl that outlived `UNIT_ACTION_TIMEOUT_SECS` (20s).
The lock wrapper around the `unit` verb (40s) is the next cliff.

`systemctl restart` blocks for the stop, then the start. The operator
must not report that as a refusal before those systemd budgets elapse.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bash_toolchain import MODERN_BASH, requires_modern_bash  # noqa: E402
from test_app_runtime import APP_UNITS  # noqa: E402
from test_container_stop_delivery import (  # noqa: E402
    SYSTEMD_DEFAULT_STOP_TIMEOUT,
    _unit_stop_timeout,
)

pytestmark = requires_modern_bash

CLOUD = Path(__file__).resolve().parents[1]
OPERATOR = CLOUD / "scripts" / "operator-radon.sh"
SERVICES = CLOUD / "services"

# systemd DefaultTimeoutStartSec when the unit does not set TimeoutStartSec.
SYSTEMD_DEFAULT_START_TIMEOUT = 90
# Let systemd return its own result before the operator's bound fires.
RESTART_SLACK_S = 15


def _operator() -> str:
    return OPERATOR.read_text(encoding="utf-8")


def _default(text: str, var: str) -> int:
    match = re.search(rf'^{var}="\$\{{[A-Z0-9_]+:-(\d+)\}}"', text, re.M)
    assert match, f"{var} default is not set in operator-radon.sh"
    return int(match.group(1))


def _unit_verb_systemctl_timeout(text: str) -> int:
    branch = text.split('if [[ "$requested_action" == "unit" ]]; then', 1)[1]
    branch = branch.split("\nfi", 1)[0]
    match = re.search(
        r'run_systemctl_bounded "\$unit_action" "\$unit_name"(?: "\$([A-Z0-9_]+)")?',
        branch,
    )
    assert match, branch
    var = match.group(1) or "UNIT_ACTION_TIMEOUT_SECS"
    return _default(text, var)


def _unit_verb_lock_timeout(text: str) -> int:
    match = re.search(
        r'^  unit\) ACTION_TIMEOUT_SECS="\$\{RADON_OPERATOR_ACTION_TIMEOUT_SECS:-(\d+)\}" ;;',
        text,
        re.M,
    )
    assert match, "unit verb lock timeout is not set"
    return int(match.group(1))


def _unit_start_timeout(unit: str) -> int:
    text = (SERVICES / unit).read_text(encoding="utf-8")
    drop_in = SERVICES / f"{unit}.d" / "runtime-container.conf"
    if drop_in.is_file():
        text += "\n" + drop_in.read_text(encoding="utf-8")
    values = re.findall(r"^TimeoutStartSec=(\d+)$", text, re.M)
    return int(values[-1]) if values else SYSTEMD_DEFAULT_START_TIMEOUT


def _restart_budget() -> int:
    stop = max(_unit_stop_timeout(unit) for unit in APP_UNITS)
    start = max(_unit_start_timeout(unit) for unit in APP_UNITS)
    return stop + start + RESTART_SLACK_S


def test_unit_verb_budget_covers_stop_then_start() -> None:
    text = _operator()
    need = _restart_budget()
    inner = _unit_verb_systemctl_timeout(text)
    outer = _unit_verb_lock_timeout(text)
    assert inner >= need, (
        f"radon unit restart gives up at {inner}s, before stop+start "
        f"({need}s, longest TimeoutStopSec plus TimeoutStartSec plus slack)"
    )
    assert outer > inner, (
        f"the unit-verb lock wrapper ({outer}s) kills the restart before "
        f"the systemctl bound ({inner}s)"
    )
    # The default stop ceiling used above is the real systemd default, not a guess.
    assert SYSTEMD_DEFAULT_STOP_TIMEOUT == 90


def test_unit_restart_that_outlives_its_bound_exits_70(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text("#!/bin/sh\nsleep 3\nexit 0\n", encoding="utf-8")
    systemctl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
        "RADON_OPERATOR_UNIT_VERB_TIMEOUT_SECS": "1",
        "RADON_OPERATOR_ACTION_TIMEOUT_SECS": "30",
    }
    result = subprocess.run(
        [str(MODERN_BASH), str(OPERATOR), "unit", "restart", "radon-api.service"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 70, result.stderr
    assert "REFUSING unit action: systemctl timed out for radon-api.service" in result.stderr
