"""`radon stop|start|restart` topology file as a two-state machine.

States: running (no saved topology) and stopped (topology file lists what
the stop quiesced). Audit 2026-11-03 §3.2:

- A second `radon stop` saw nothing active and overwrote the saved list with
  an empty one, so the next `radon start` restored no timers (watchdog,
  backups stayed off).
- `set -e` aborted `start` / `restart` on a Gateway helper refusal before the
  app units were started, leaving the whole app plane down.

The fake systemctl here is stateful: stop/start/restart mutate the active
set that the next `list-units` reports, so repeated verbs see real state.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bash_toolchain import MODERN_BASH, requires_modern_bash  # noqa: E402

pytestmark = requires_modern_bash

CLOUD_ROOT = Path(__file__).resolve().parents[1]
OPERATOR = CLOUD_ROOT / "scripts" / "operator-radon.sh"

PERSISTENT = [
    "radon-api.service",
    "radon-nextjs.service",
    "radon-relay.service",
    "radon-monitor.service",
    "radon-newsfeed.service",
]
ALWAYS = ["radon-ib-gateway.service", "radon-health.service"]
TIMERS = ["radon-watchdog-services.timer", "radon-db-backup.timer"]

FAKE_SYSTEMCTL = """#!/bin/sh
state="$RADON_FAKE_ACTIVE"
case "${1:-}" in
  list-units)
    for unit in $RADON_FAKE_KNOWN; do
      if grep -qx "$unit" "$state"; then
        printf '%s loaded active running fake\\n' "$unit"
      else
        printf '%s loaded inactive dead fake\\n' "$unit"
      fi
    done
    exit 0 ;;
  list-unit-files)
    # Nothing enabled: a start must restore timers from the saved topology.
    exit 0 ;;
esac
printf 'systemctl %s\\n' "$*" >> "$RADON_OPERATOR_EVENTS"
verb="$1"; shift
for unit in "$@"; do
  case "$verb" in
    stop) grep -vx "$unit" "$state" > "$state.tmp" || true; mv "$state.tmp" "$state" ;;
    start|restart) grep -qx "$unit" "$state" || printf '%s\\n' "$unit" >> "$state" ;;
  esac
done
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def stack(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "systemctl", FAKE_SYSTEMCTL)
    # A root test runner demotes the helper through runuser; no radon account
    # exists here, so pass the command through unchanged.
    _write_executable(
        fake_bin / "runuser",
        '#!/bin/sh\nwhile [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n',
    )
    gateway = tmp_path / "gateway-control"
    _write_executable(
        gateway,
        """#!/bin/sh
printf 'gateway %s\\n' "$*" >> "$RADON_OPERATOR_EVENTS"
if [ -n "${RADON_FAKE_GATEWAY_RC:-}" ]; then
  echo "REFUSING gateway $1: 2FA push lease held" >&2
  exit "$RADON_FAKE_GATEWAY_RC"
fi
exit 0
""",
    )
    active = tmp_path / "active"
    active.write_text("\n".join([*ALWAYS, *PERSISTENT, *TIMERS]) + "\n")
    topology = tmp_path / "operator-topology"
    events = tmp_path / "events.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_FAKE_ACTIVE": str(active),
        "RADON_FAKE_KNOWN": " ".join([*ALWAYS, *PERSISTENT, *TIMERS]),
        "RADON_IB_GATEWAY_CONTROL": str(gateway),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(topology),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
        "RADON_HOST_ROLE": "combined",
    }
    return env, active, topology, events


def _run(env: dict, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MODERN_BASH), str(OPERATOR), *args],
        env=env, text=True, capture_output=True, check=False, timeout=60,
    )


def _active(active: Path) -> set[str]:
    return set(active.read_text().split())


def test_second_stop_keeps_the_saved_timer_topology(stack):
    env, active, topology, _events = stack
    first = _run(env, "stop")
    assert first.returncode == 0, first.stderr
    saved = set(topology.read_text().split())
    assert set(TIMERS) <= saved

    second = _run(env, "stop")
    assert second.returncode == 0, second.stderr
    assert set(TIMERS) <= set(topology.read_text().split())

    started = _run(env, "start")
    assert started.returncode == 0, started.stderr
    assert set(TIMERS) <= _active(active)
    assert set(PERSISTENT) <= _active(active)
    assert not topology.exists()


def test_stop_after_partial_manual_start_merges_with_saved_topology(stack):
    env, active, topology, _events = stack
    assert _run(env, "stop").returncode == 0
    # An operator hand-starts one app unit, then stops the stack again.
    with active.open("a") as handle:
        handle.write("radon-api.service\n")
    assert _run(env, "stop").returncode == 0
    saved = set(topology.read_text().split())
    assert set(TIMERS) <= saved
    assert "radon-api.service" in saved


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_gateway_refusal_still_starts_app_units_and_exits_nonzero(stack, verb):
    env, active, topology, events = stack
    assert _run(env, "stop").returncode == 0
    env = {**env, "RADON_FAKE_GATEWAY_RC": "75"}

    result = _run(env, verb)

    assert result.returncode == 75, (result.stdout, result.stderr)
    assert "Gateway" in result.stderr and "75" in result.stderr
    lines = events.read_text().splitlines()
    assert f"gateway {verb}" in lines
    assert set(PERSISTENT) <= _active(active)
    if verb == "start":
        assert set(TIMERS) <= _active(active)
        # Units are back; the saved list served its purpose.
        assert not topology.exists()


def test_systemctl_start_failure_keeps_topology_for_retry(stack, tmp_path: Path):
    env, active, topology, _events = stack
    assert _run(env, "stop").returncode == 0
    saved = topology.read_text()
    failing = tmp_path / "bin" / "systemctl"
    failing.write_text(
        FAKE_SYSTEMCTL.replace(
            'verb="$1"; shift',
            'verb="$1"; shift\n[ "$verb" = start ] && exit 1',
        )
    )
    result = _run(env, "start")
    assert result.returncode != 0
    assert topology.read_text() == saved
