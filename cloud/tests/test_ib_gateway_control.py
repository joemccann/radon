from __future__ import annotations

import os
import fcntl
from pathlib import Path
import re
import subprocess
import sys
import time

import pytest


CLOUD_ROOT = Path(__file__).resolve().parents[1]
# Monorepo: cloud/ lives inside radon/. Legacy: cloud repo is a sibling of radon/.
_parent = CLOUD_ROOT.parent
if (_parent / "scripts" / "utils" / "ib_2fa_lock.py").is_file():
    APP_ROOT = _parent
elif (_parent / "radon" / "scripts" / "utils" / "ib_2fa_lock.py").is_file():
    APP_ROOT = _parent / "radon"
else:
    APP_ROOT = _parent
CONTROL = CLOUD_ROOT / "scripts" / "ib-gateway-control.sh"
LOCK_CLI = APP_ROOT / "scripts" / "utils" / "ib_2fa_lock.py"
WATCHDOG_HOLDER = "scripts.ib_watchdog.trigger_restart"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def control_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path]:
    docker = tmp_path / "docker"
    state = tmp_path / "container-state"
    log = tmp_path / "docker.log"
    lock = tmp_path / "push-lock.json"
    consumed = tmp_path / "consumed-lease.json"
    _write_executable(
        docker,
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
if [ "${1:-}" = inspect ]; then
  if [ -n "${FAKE_DOCKER_INSPECT_MARKER:-}" ] && [ ! -e "$FAKE_DOCKER_INSPECT_MARKER" ]; then
    : > "$FAKE_DOCKER_INSPECT_MARKER"
    sleep "${FAKE_DOCKER_INSPECT_DELAY:-0}"
  fi
  # Mirror real docker inspect: exit 0 with true/false for known containers,
  # non-zero + No such object only when the container does not exist.
  if [ ! -f "$FAKE_DOCKER_STATE" ]; then
    printf '%s\\n' "${FAKE_DOCKER_MISSING_MESSAGE:-Error: No such object: ib-gateway}" >&2
    exit 1
  fi
  if [ "$(cat "$FAKE_DOCKER_STATE")" = running ]; then
    printf 'true\\n'
  else
    printf 'false\\n'
  fi
  exit 0
fi
if [ "${1:-}" = compose ]; then
  case " $* " in
    *" up -d "*) printf 'running\\n' > "$FAKE_DOCKER_STATE" ;;
    *" down "*) printf 'stopped\\n' > "$FAKE_DOCKER_STATE" ;;
  esac
  exit 0
fi
exit 2
""",
    )
    env = {
        **os.environ,
        "RADON_APP_DIR": str(APP_ROOT),
        "RADON_CLOUD_DIR": str(tmp_path / "cloud"),
        "RADON_LOCK_PYTHON": sys.executable,
        "RADON_DOCKER_BIN": str(docker),
        "RADON_IB_LEASE_CONSUMED_PATH": str(consumed),
        "RADON_IB_CONTROL_GUARD_PATH": str(tmp_path / "control.guard"),
        "RADON_IB_TRANSITION_PATH": str(tmp_path / "transition.json"),
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
        "RADON_GATEWAY_CONTROL_ALLOW_ROOT": "1",
        "IB_2FA_LOCK_PATH": str(lock),
        "FAKE_DOCKER_STATE": str(state),
        "FAKE_DOCKER_LOG": str(log),
    }
    (tmp_path / "cloud").mkdir()
    return env, state, log, lock


def _run_control(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CONTROL), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _acquire(env: dict[str, str], holder: str) -> None:
    result = subprocess.run(
        [sys.executable, str(LOCK_CLI), "acquire", holder],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_healthy_start_does_not_take_lease(control_env):
    env, state, log, lock = control_env
    state.write_text("running\n")

    result = _run_control(env, "start")

    assert result.returncode == 0, result.stderr
    assert not lock.exists()
    assert "compose up" not in log.read_text()


def test_dead_container_start_acquires_lease_before_compose(control_env):
    env, state, log, lock = control_env
    state.write_text("stopped\n")

    result = _run_control(env, "start")

    assert result.returncode == 0, result.stderr
    assert state.read_text().strip() == "running"
    assert re.search(r"compose .* up -d", log.read_text())
    assert lock.exists()
    assert "radon-cloud.ib-gateway-control" in lock.read_text()


def test_lowercase_missing_container_reconciles_stopped_transition(control_env):
    env, state, log, _lock = control_env
    transition = Path(env["RADON_IB_TRANSITION_PATH"])
    transition.write_text('{"desired":"stopped","action":"preheld-down"}\n')
    env["FAKE_DOCKER_STATE"] = str(state)
    env["FAKE_DOCKER_MISSING_MESSAGE"] = "error: no such object: ib-gateway"

    result = _run_control(env, "start")

    assert result.returncode == 0, result.stderr
    assert "Reconciled prior Docker transition at desired=stopped" in result.stdout
    assert state.read_text().strip() == "running"
    assert not transition.exists()


def test_held_lease_refuses_dead_container_start(control_env):
    env, state, log, _lock = control_env
    state.write_text("stopped\n")
    _acquire(env, "another-control-plane")
    log.write_text("")

    result = _run_control(env, "start")

    assert result.returncode != 0
    assert "compose up" not in log.read_text()


def test_same_holder_restart_reentry_is_refused_before_second_cycle(control_env):
    env, state, log, _lock = control_env
    state.write_text("running\n")

    first = _run_control(env, "restart")
    second = _run_control(env, "restart")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 75
    assert sum(line.endswith(" down") for line in log.read_text().splitlines()) == 1


def test_watchdog_preheld_lease_is_consumed_exactly_once(control_env):
    env, state, log, _lock = control_env
    state.write_text("running\n")
    _acquire(env, WATCHDOG_HOLDER)
    log.write_text("")

    first = _run_control(env, "restart-preheld", WATCHDOG_HOLDER)
    second = _run_control(env, "restart-preheld", WATCHDOG_HOLDER)

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert sum(line.endswith(" down") for line in log.read_text().splitlines()) == 1


def _wait_for_path(path: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), f"timed out waiting for {path}"


def _inherit_deploy_lock(env: dict[str, str]) -> int:
    """Hold the production deploy lock and expose it for helper inheritance.

    Production callers re-exec the helper under an already-held flock. Both
    concurrent helpers must share that open file description so lifecycle
    serialization is measured without a nonblocking deploy-lock collision.
    """
    lock_path = Path(env["RADON_DEPLOY_LOCK_FILE"])
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.set_inheritable(fd, True)
    env["RADON_DEPLOY_LOCKED"] = "1"
    env["RADON_DEPLOY_LOCK_FD"] = str(fd)
    return fd


def test_healthy_start_and_concurrent_stop_are_serialized(control_env, tmp_path):
    env, state, _log, lock = control_env
    state.write_text("running\n")
    marker = tmp_path / "inspect-started"
    env["FAKE_DOCKER_INSPECT_MARKER"] = str(marker)
    env["FAKE_DOCKER_INSPECT_DELAY"] = "0.3"
    deploy_fd = _inherit_deploy_lock(env)

    try:
        start = subprocess.Popen(
            [str(CONTROL), "start"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(deploy_fd,),
        )
        _wait_for_path(marker)
        stop = subprocess.run(
            [str(CONTROL), "stop"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            pass_fds=(deploy_fd,),
        )
        start_stdout, start_stderr = start.communicate(timeout=3)
    finally:
        os.close(deploy_fd)

    assert start.returncode == 0, start_stderr
    assert stop.returncode == 0, stop.stderr
    assert "already running" in start_stdout
    assert state.read_text().strip() == "stopped"
    assert not lock.exists()


def test_restart_and_concurrent_stop_are_serialized(control_env, tmp_path):
    env, state, log, _lock = control_env
    state.write_text("running\n")
    marker = tmp_path / "restart-inspect-started"
    env["FAKE_DOCKER_INSPECT_MARKER"] = str(marker)
    env["FAKE_DOCKER_INSPECT_DELAY"] = "0.3"
    deploy_fd = _inherit_deploy_lock(env)

    try:
        restart = subprocess.Popen(
            [str(CONTROL), "restart"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(deploy_fd,),
        )
        _wait_for_path(marker, timeout=5.0)
        stop = subprocess.run(
            [str(CONTROL), "stop"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            pass_fds=(deploy_fd,),
        )
        _stdout, restart_stderr = restart.communicate(timeout=5)
    finally:
        os.close(deploy_fd)

    assert restart.returncode == 0, restart_stderr
    assert stop.returncode == 0, stop.stderr
    assert state.read_text().strip() == "stopped"
    compose_actions = [
        line.rsplit(" ", 1)[-1]
        for line in log.read_text().splitlines()
        if line.startswith("compose ") and line.rsplit(" ", 1)[-1] in {"down", "-d"}
    ]
    assert compose_actions == ["down", "-d", "down"]


def test_boot_and_watchdog_units_use_authoritative_control_path():
    gateway = (CLOUD_ROOT / "services" / "radon-ib-gateway.service").read_text()
    watchdog = (CLOUD_ROOT / "services" / "radon-ib-watchdog.service").read_text()
    preheld = (
        CLOUD_ROOT / "services" / "radon-ib-gateway-preheld-restart.service"
    ).read_text()

    assert "ExecStart=/usr/local/bin/radon-ib-gateway-control start" in gateway
    assert "docker compose up -d" not in gateway
    assert "User=radon" in watchdog
    assert "--restart-unit radon-ib-gateway-preheld-restart.service" in watchdog
    assert f"restart-preheld {WATCHDOG_HOLDER}" in preheld
    assert "User=radon" in preheld
    for service in (gateway, watchdog, preheld):
        assert "StateDirectory=radon" in service

    setup = (CLOUD_ROOT / "scripts" / "setup-vps.sh").read_text()
    assert 'install -d -m 0750 -o radon -g radon "$state_dir"' in setup
    control = CONTROL.read_text()
    assert 'exec "${RADON_RUNUSER_BIN:-/usr/sbin/runuser}" -u radon' in control
    # Root demotion must land in a radon-readable cwd; Compose stats "." and
    # fails with "stat .: permission denied" when cwd remains /root.
    assert "cd /home/radon && exec" in control
    assert 'LOCK_PYTHON="${RADON_LOCK_PYTHON:-/usr/bin/python3.13}"' in control
    assert "${APP_DIR}/.venv/bin/python" not in control
    assert "System Python 3.13 is required by the Gateway control plane" in setup


def test_control_does_not_depend_on_live_app_venv(control_env, tmp_path):
    env, state, _log, _lock = control_env
    app_dir = tmp_path / "app-with-broken-venv"
    lock_cli = app_dir / "scripts" / "utils" / "ib_2fa_lock.py"
    lock_cli.parent.mkdir(parents=True)
    lock_cli.write_text(LOCK_CLI.read_text())
    broken_python = app_dir / ".venv" / "bin" / "python"
    broken_python.parent.mkdir(parents=True)
    broken_python.write_text("broken deploy artifact\n")
    env["RADON_APP_DIR"] = str(app_dir)
    env["RADON_LOCK_PYTHON"] = sys.executable
    state.write_text("stopped\n")

    result = _run_control(env, "start")

    assert result.returncode == 0, result.stderr
    assert state.read_text().strip() == "running"


def test_operator_and_setup_route_gateway_through_control_helper():
    operator = (CLOUD_ROOT / "scripts" / "operator-radon.sh").read_text()
    start_services = (CLOUD_ROOT / "scripts" / "start-services.sh").read_text()
    post_setup = (CLOUD_ROOT / "scripts" / "post-setup.sh").read_text()
    setup = (CLOUD_ROOT / "scripts" / "setup-vps.sh").read_text()

    assert "radon-ib-gateway-control" in operator
    # The root-only stack controller deliberately preserves its inherited
    # deploy-lock FD while demoting to radon. It must reset HOME too: Docker
    # otherwise reads /root/.docker as radon and status falsely becomes unknown.
    assert "runuser -u radon --preserve-environment" in operator
    assert "env HOME=/home/radon USER=radon LOGNAME=radon" in operator
    assert "PERSISTENT_UNITS=(" in operator
    assert "require_2fa_push_lock" not in operator
    assert not re.search(r"systemctl\s+(?:start|restart)\s+radon-ib-gateway", operator)
    assert "docker compose up -d" not in start_services
    assert "/usr/local/bin/radon" in start_services
    assert "systemctl start radon-nextjs" not in start_services
    assert post_setup.count("/usr/local/bin/radon start") == 1
    assert "systemctl start radon-nextjs" not in post_setup
    assert not re.search(r"sudo\s+systemctl\s+restart", start_services)
    start_fn = setup[setup.index("start_services() {"):setup.index("# -- Firewall")]
    # setup starts the stack only through the locked operator, which owns the
    # Gateway control helper and deploy lock. Direct helper/systemctl is banned.
    assert "/usr/local/bin/radon start" in start_fn
    assert not re.search(r"systemctl\s+start\s+radon-ib-gateway", start_fn)
    assert "radon-ib-gateway-control start" not in start_fn


@pytest.mark.parametrize("action", ["start", "restart", "stop"])
def test_operator_never_mutates_gateway_units_through_generic_systemctl(
    tmp_path: Path, action: str
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    systemctl = fake_bin / "systemctl"
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        systemctl,
        """#!/bin/sh
set -eu
if [ "${1:-}" = list-units ]; then
  cat <<'EOF'
radon-ib-gateway.service loaded active exited IB Gateway
radon-ib-gateway-preheld-restart.service loaded inactive dead Preheld
radon-api.service loaded active running API
radon-health.service loaded active running Health
radon-nextjs.service loaded active running Next.js
radon-relay.service loaded active running Relay
radon-monitor.service loaded active running Monitor
radon-newsfeed.service loaded active running Newsfeed
radon-beta-nextjs.service loaded active running Beta
radon-portfolio-sync.timer loaded active waiting Portfolio timer
radon-db-backup.timer loaded inactive dead Backup timer
radon-portfolio-sync.service loaded inactive dead Portfolio sync
radon-db-backup.service loaded inactive dead Backup
radon-forecast-nightly.service loaded inactive dead Forecast
EOF
  exit 0
fi
if [ "${1:-}" = list-unit-files ]; then
  printf '%s\n' 'radon-portfolio-sync.timer enabled enabled'
  printf '%s\n' 'radon-db-backup.timer enabled enabled'
  exit 0
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    _write_executable(
        gateway_control,
        """#!/bin/sh
set -eu
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "operator-topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }

    result = subprocess.run(
        [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), action],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    event_lines = events.read_text().splitlines()
    assert f"gateway {action}" in event_lines
    systemctl_actions = [
        line for line in event_lines
        if re.match(r"systemctl (?:start|restart|stop) ", line)
    ]
    assert len(systemctl_actions) == 1
    assert "radon-api.service" in systemctl_actions[0]
    assert "radon-ib-gateway.service" not in systemctl_actions[0]
    assert "radon-ib-gateway-preheld-restart.service" not in systemctl_actions[0]
    assert "radon-health.service" not in systemctl_actions[0]
    assert "radon-beta-nextjs.service" not in systemctl_actions[0]
    assert "radon-portfolio-sync.timer" in systemctl_actions[0]
    if action == "start":
        assert "radon-db-backup.timer" in systemctl_actions[0]
    else:
        assert "radon-db-backup.timer" not in systemctl_actions[0]
    assert "radon-portfolio-sync.service" not in systemctl_actions[0]
    assert "radon-db-backup.service" not in systemctl_actions[0]
    assert "radon-forecast-nightly.service" not in systemctl_actions[0]


def test_operator_preserves_its_inherited_deploy_lock_fd_for_gateway_control(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "${1:-}" = list-units ]; then
  for unit in radon-ib-gateway radon-api radon-nextjs radon-relay radon-monitor radon-newsfeed radon-health; do
    printf '%s.service loaded active running persistent\\n' "$unit"
  done
  exit 0
fi
printf 'systemctl %s\\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
set -eu
fd="${RADON_DEPLOY_LOCK_FD:?missing deploy lock fd}"
"$RADON_OPERATOR_PYTHON" - "$fd" <<'PY'
import os
import sys

os.fstat(int(sys.argv[1]))
PY
printf 'gateway %s\\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "operator-topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }

    result = subprocess.run(
        [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), "restart"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "gateway restart" in events.read_text().splitlines()


def test_operator_quiesces_active_oneshot_without_restarting_it(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "${1:-}" = list-units ]; then
  for unit in radon-ib-gateway radon-api radon-nextjs radon-relay radon-monitor radon-newsfeed radon-health; do
    printf '%s.service loaded active running persistent\n' "$unit"
  done
  printf '%s\n' 'radon-portfolio-sync.timer loaded active waiting timer'
  printf '%s\n' 'radon-portfolio-sync.service loaded activating start scheduled-job'
  exit 0
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }

    result = subprocess.run(
        [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), "restart"],
        env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = events.read_text().splitlines()
    stop_index = lines.index("systemctl stop radon-portfolio-sync.service")
    gateway_index = lines.index("gateway restart")
    restart_line = next(line for line in lines if line.startswith("systemctl restart "))
    assert stop_index < gateway_index
    assert "radon-portfolio-sync.service" not in restart_line


def test_gateway_helper_refuses_while_deploy_lock_is_held(control_env):
    env, state, log, lock = control_env
    state.write_text("stopped\n")
    deploy_lock = Path(env["RADON_DEPLOY_LOCK_FILE"])
    with deploy_lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run_control(env, "start")

    assert result.returncode == 74
    assert "deploy/control lock held" in result.stderr
    assert "compose" not in log.read_text()
    assert not lock.exists()


def test_healthy_start_is_noop_even_when_operator_holds_deploy_lock(control_env):
    env, state, log, lock = control_env
    state.write_text("running\n")
    deploy_lock = Path(env["RADON_DEPLOY_LOCK_FILE"])
    with deploy_lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run_control(env, "start")

    assert result.returncode == 0, result.stderr
    assert "already running" in result.stdout
    assert not lock.exists()
    assert all("compose" not in line for line in log.read_text().splitlines())


def test_deploy_lock_refusal_releases_unused_watchdog_preheld_lease(control_env):
    env, state, log, lock = control_env
    state.write_text("running\n")
    _acquire(env, WATCHDOG_HOLDER)
    deploy_lock = Path(env["RADON_DEPLOY_LOCK_FILE"])
    with deploy_lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run_control(env, "restart-preheld", WATCHDOG_HOLDER)

    assert result.returncode == 74
    assert "deploy/control lock held" in result.stderr
    assert not lock.exists(), "unused watchdog lease remained after pre-Docker refusal"
    assert not log.exists()


def test_operator_refuses_while_deploy_lock_is_held(tmp_path: Path):
    deploy_lock = tmp_path / "deploy.lock"
    events = tmp_path / "events.log"
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(deploy_lock),
    }
    with deploy_lock.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), "restart"],
            env=env, text=True, capture_output=True, check=False,
        )

    assert result.returncode == 74
    assert "deploy/control lock held" in result.stderr
    assert not events.exists()


def test_concurrent_operator_mutation_cannot_interleave_stack_transaction(
    tmp_path: Path,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    marker = tmp_path / "gateway-restart-started"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "${1:-}" = list-units ]; then
  for unit in radon-ib-gateway radon-api radon-nextjs radon-relay radon-monitor radon-newsfeed radon-health; do
    printf '%s.service loaded active running persistent\n' "$unit"
  done
  exit 0
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
if [ "${1:-}" = restart ]; then
  : > "$RADON_GATEWAY_MARKER"
  sleep 0.3
fi
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_GATEWAY_MARKER": str(marker),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }
    operator = str(CLOUD_ROOT / "scripts" / "operator-radon.sh")
    restart = subprocess.Popen(
        [operator, "restart"], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _wait_for_path(marker)

    stop = subprocess.run(
        [operator, "stop"], env=env, text=True, capture_output=True, check=False,
    )
    _stdout, restart_stderr = restart.communicate(timeout=3)

    assert restart.returncode == 0, restart_stderr
    assert stop.returncode == 74
    lines = events.read_text().splitlines()
    assert "gateway restart" in lines
    assert "gateway stop" not in lines
    assert not any(line.startswith("systemctl stop ") for line in lines)


def test_operator_stop_then_start_restores_exact_timer_topology(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    topology = tmp_path / "operator-topology"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
if [ "${1:-}" = list-units ]; then
  for unit in radon-ib-gateway radon-api radon-nextjs radon-relay radon-monitor radon-newsfeed radon-health; do
    printf '%s.service loaded active running persistent\n' "$unit"
  done
  printf '%s\n' 'radon-portfolio-sync.timer loaded active waiting active-timer'
  printf '%s\n' 'radon-db-backup.timer loaded inactive dead inactive-timer'
  printf '%s\n' 'radon-portfolio-sync.service loaded inactive dead scheduled-job'
  printf '%s\n' 'radon-db-backup.service loaded inactive dead scheduled-job'
  exit 0
fi
if [ "${1:-}" = list-unit-files ]; then
  printf '%s\n' 'radon-portfolio-sync.timer enabled enabled'
  printf '%s\n' 'radon-db-backup.timer enabled enabled'
  exit 0
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(topology),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }
    operator = str(CLOUD_ROOT / "scripts" / "operator-radon.sh")

    stopped = subprocess.run(
        [operator, "stop"], env=env, text=True, capture_output=True, check=False,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert topology.exists()
    events.write_text("")

    started = subprocess.run(
        [operator, "start"], env=env, text=True, capture_output=True, check=False,
    )

    assert started.returncode == 0, started.stderr
    start_call = next(
        line for line in events.read_text().splitlines()
        if line.startswith("systemctl start ")
    )
    assert "radon-portfolio-sync.timer" in start_call
    assert "radon-db-backup.timer" not in start_call
    assert "radon-portfolio-sync.service" not in start_call
    assert "radon-db-backup.service" not in start_call
    assert not topology.exists()


@pytest.mark.parametrize("list_mode", ["fail", "empty", "missing-critical"])
def test_operator_fails_closed_before_gateway_when_unit_discovery_is_bad(
    tmp_path: Path, list_mode: str
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
set -eu
if [ "${1:-}" = list-units ]; then
  case "$RADON_LIST_MODE" in
    fail) exit 1 ;;
    empty) exit 0 ;;
    missing-critical)
      printf '%s\n' 'radon-api.service loaded active running API'
      exit 0
      ;;
  esac
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_LIST_MODE": list_mode,
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "operator-topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }

    result = subprocess.run(
        [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), "restart"],
        env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert "REFUSING stack action" in result.stderr
    assert not events.exists(), "gateway or systemd mutation ran after bad discovery"


@pytest.mark.parametrize(
    "missing_unit",
    [
        "radon-ib-gateway.service",
        "radon-api.service",
        "radon-nextjs.service",
        "radon-relay.service",
        "radon-monitor.service",
        "radon-newsfeed.service",
        "radon-health.service",
    ],
)
def test_operator_restart_requires_each_persistent_unit(
    tmp_path: Path, missing_unit: str
):
    required = [
        "radon-ib-gateway.service",
        "radon-api.service",
        "radon-nextjs.service",
        "radon-relay.service",
        "radon-monitor.service",
        "radon-newsfeed.service",
        "radon-health.service",
    ]
    inventory = "\n".join(
        f"{unit} loaded active running test" for unit in required if unit != missing_unit
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    _write_executable(
        fake_bin / "systemctl",
        f"""#!/bin/sh
if [ "${{1:-}}" = list-units ]; then
  cat <<'EOF'
{inventory}
EOF
  exit 0
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "operator-topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }

    result = subprocess.run(
        [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), "restart"],
        env=env, text=True, capture_output=True, check=False,
    )

    assert result.returncode != 0
    assert missing_unit in result.stderr
    assert not events.exists()


@pytest.mark.parametrize("action", ["stop", "status"])
def test_operator_keeps_emergency_stop_and_status_available_when_unit_is_missing(
    tmp_path: Path, action: str
):
    inventory = "\n".join(
        f"{unit} loaded active running test"
        for unit in (
            "radon-ib-gateway.service",
            "radon-nextjs.service",
            "radon-relay.service",
            "radon-monitor.service",
            "radon-newsfeed.service",
            "radon-health.service",
        )
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    events = tmp_path / "events.log"
    _write_executable(
        fake_bin / "systemctl",
        f"""#!/bin/sh
if [ "${{1:-}}" = list-units ]; then
  cat <<'EOF'
{inventory}
EOF
  exit 0
fi
printf 'systemctl %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
""",
    )
    gateway_control = tmp_path / "gateway-control"
    _write_executable(
        gateway_control,
        """#!/bin/sh
printf 'gateway %s\n' "$*" >> "$RADON_OPERATOR_EVENTS"
[ "${1:-}" != status ] || printf 'running\n'
""",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RADON_IB_GATEWAY_CONTROL": str(gateway_control),
        "RADON_OPERATOR_EVENTS": str(events),
        "RADON_OPERATOR_TOPOLOGY_PATH": str(tmp_path / "operator-topology"),
        "RADON_OPERATOR_ALLOW_ROOT": "1",
        "RADON_OPERATOR_PYTHON": sys.executable,
        "RADON_DEPLOY_LOCK_FILE": str(tmp_path / "deploy.lock"),
    }

    result = subprocess.run(
        [str(CLOUD_ROOT / "scripts" / "operator-radon.sh"), action],
        env=env, text=True, capture_output=True, check=False,
    )

    event_lines = events.read_text().splitlines()
    assert f"gateway {action}" in event_lines
    if action == "stop":
        assert result.returncode == 0, result.stderr
        assert any(line.startswith("systemctl stop ") for line in event_lines)
    else:
        assert result.returncode != 0
        assert "radon-api.service" in result.stderr
        assert "ib-gateway-container: running" in result.stdout


def test_direct_gateway_privileges_are_removed_and_polkit_excludes_gateway():
    sudoers = (CLOUD_ROOT / "config" / "sudoers.d" / "radon-ops").read_text()
    polkit = (
        CLOUD_ROOT / "config" / "polkit" / "50-radon-services.rules"
    ).read_text()
    setup = (CLOUD_ROOT / "scripts" / "setup-vps.sh").read_text()

    assert "systemctl restart radon-ib-gateway" not in sudoers
    assert "docker restart ib-gateway" not in sudoers
    assert 'unit.indexOf("radon-ib-gateway") != 0' in polkit
    assert "radon-ib-gateway-preheld-restart.service" in polkit
    assert '"${CLOUD_DIR}/config/sudoers.d/radon-ops"' in setup
    assert '"${CLOUD_DIR}/config/polkit/50-radon-services.rules"' in setup


def test_setup_upgrade_replaces_legacy_gateway_privileges(tmp_path: Path):
    sudoers_dir = tmp_path / "sudoers.d"
    polkit_dir = tmp_path / "polkit-rules.d"
    fake_bin = tmp_path / "bin"
    sudoers_dir.mkdir()
    polkit_dir.mkdir()
    fake_bin.mkdir()
    (sudoers_dir / "radon-ops").write_text(
        "radon ALL=(root) NOPASSWD: /bin/systemctl restart radon-ib-gateway\n"
        "radon ALL=(root) NOPASSWD: /usr/bin/docker restart ib-gateway\n"
    )
    (polkit_dir / "50-radon-services.rules").write_text(
        'if (unit && unit.indexOf("radon-") == 0) return polkit.Result.YES;\n'
    )
    visudo = fake_bin / "visudo"
    _write_executable(
        visudo,
        """#!/bin/sh
set -eu
[ "$1" = -cf ]
[ -s "$2" ]
""",
    )
    env = {
        **os.environ,
        "RADON_SETUP_SOURCE_ONLY": "1",
        "RADON_CLOUD_DIR": str(CLOUD_ROOT),
        "RADON_APP_DIR": str(APP_ROOT),
        "RADON_SUDOERS_DIR": str(sudoers_dir),
        "RADON_POLKIT_RULES_DIR": str(polkit_dir),
        "RADON_VISUDO_BIN": str(visudo),
        "RADON_POLICY_SKIP_CHOWN": "1",
        "RADON_SKIP_POLKIT_RELOAD": "1",
    }
    command = (
        f'source "{CLOUD_ROOT / "scripts" / "setup-vps.sh"}"; '
        "configure_sudoers; install_admin_polkit_rule"
    )

    result = subprocess.run(
        ["bash", "-c", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    installed_ops = (sudoers_dir / "radon-ops").read_text()
    installed_polkit = (polkit_dir / "50-radon-services.rules").read_text()
    assert installed_ops == (
        CLOUD_ROOT / "config" / "sudoers.d" / "radon-ops"
    ).read_text()
    assert installed_polkit == (
        CLOUD_ROOT / "config" / "polkit" / "50-radon-services.rules"
    ).read_text()
    assert "systemctl restart radon-ib-gateway" not in installed_ops
    assert "docker restart ib-gateway" not in installed_ops
    assert 'unit.indexOf("radon-ib-gateway") != 0' in installed_polkit
    assert (sudoers_dir / "radon-ops").stat().st_mode & 0o777 == 0o440
    assert (polkit_dir / "50-radon-services.rules").stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(
    ("function_name", "source_name", "target_env"),
    [
        (
            "install_gateway_control",
            "ib-gateway-control.sh",
            "RADON_GATEWAY_CONTROL_TARGET",
        ),
        ("install_operator_cli", "operator-radon.sh", "RADON_OPERATOR_CLI_TARGET"),
    ],
)
def test_setup_never_replaces_live_helper_with_invalid_candidate(
    tmp_path: Path,
    function_name: str,
    source_name: str,
    target_env: str,
):
    cloud_dir = tmp_path / "cloud"
    scripts_dir = cloud_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / source_name).write_text("#!/bin/bash\nif then\n")
    target = tmp_path / source_name
    target.write_text("#!/bin/bash\nprintf working\\n\n")
    target.chmod(0o755)
    state_dir = tmp_path / "state"
    env = {
        **os.environ,
        "RADON_SETUP_SOURCE_ONLY": "1",
        "RADON_CLOUD_DIR": str(cloud_dir),
        "RADON_APP_DIR": str(APP_ROOT),
        "RADON_STATE_DIR": str(state_dir),
        "RADON_HELPER_SKIP_CHOWN": "1",
        "RADON_SYSTEM_PYTHON": sys.executable,
        target_env: str(target),
    }
    command = (
        f'source "{CLOUD_ROOT / "scripts" / "setup-vps.sh"}"; '
        f"if {function_name}; then exit 9; fi"
    )

    result = subprocess.run(
        ["bash", "-c", command], env=env, text=True,
        capture_output=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    assert target.read_text() == "#!/bin/bash\nprintf working\\n\n"
    assert list(tmp_path.glob(f"{source_name}.tmp.*")) == []


def test_deployment_guide_only_documents_safe_operator_commands():
    guide = (CLOUD_ROOT / "radon-cloud-deployment-guide.html").read_text()

    assert "docker compose up -d" not in guide
    assert "docker compose down" not in guide
    assert not re.search(r"systemctl\s+(?:start|restart)\s+radon-ib-gateway", guide)
    assert "/usr/local/bin/radon start" in guide
    assert "/usr/local/bin/radon restart" in guide


def test_no_shipped_direct_gateway_cycle_bypasses_helper():
    offenders: list[str] = []
    direct_compose = re.compile(r"docker\s+compose\s+(?:up|start|restart)")
    direct_systemd = re.compile(
        r"systemctl\s+(?:start|restart)\s+radon-ib-gateway(?:\.service)?(?:\s|$)"
    )
    for path in [
        *sorted((CLOUD_ROOT / "scripts").glob("*.sh")),
        *sorted((CLOUD_ROOT / "services").glob("*.service")),
    ]:
        if path == CONTROL:
            continue
        text = path.read_text()
        if direct_compose.search(text) or direct_systemd.search(text):
            offenders.append(str(path.relative_to(CLOUD_ROOT)))

    assert offenders == []
