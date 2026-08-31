#!/usr/bin/env bash
set -euo pipefail

# Single authoritative IB Gateway container lifecycle path. All callers use
# this helper; Docker and systemd never cycle the container directly.

# Running the helper as root would create root-owned lease guard files and
# break the radon-owned API/watchdog control planes. Self-demote defensively;
# tests may opt out when their harness itself runs as root.
#
# Docker Compose validates against the process cwd (stat "."). Root sessions
# often start in /root, which radon cannot read after demotion — that surfaces
# as: validating .../docker-compose.yml: error in parsing "compose-spec.json":
# stat .: permission denied. Always demote with an explicit home cwd.
if [[ $EUID -eq 0 && "${RADON_GATEWAY_CONTROL_ALLOW_ROOT:-0}" != "1" ]]; then
  exec "${RADON_RUNUSER_BIN:-/usr/sbin/runuser}" -u radon -- \
    /usr/bin/env HOME=/home/radon \
    /bin/bash -c 'cd /home/radon && exec "$0" "$@"' "$0" "$@"
fi

APP_DIR="${RADON_APP_DIR:-/home/radon/radon}"
CLOUD_DIR="${RADON_CLOUD_DIR:-/home/radon/radon/cloud}"
ENV_FILE="${RADON_DEPLOY_ENV_FILE:-${RADON_GATEWAY_ENV_FILE:-/home/radon/radon-cloud/.env}}"

# Non-root callers can also inherit an unreadable cwd (for example a leftover
# root shell). Move to a path the radon user owns before any Compose call.
if ! cd "$CLOUD_DIR" 2>/dev/null; then
  cd /home/radon || cd /tmp || true
fi
# The lease/mutex code is stdlib-only. Use the immutable system interpreter
# provisioned by setup-vps rather than the app venv swapped during deploys.
LOCK_PYTHON="${RADON_LOCK_PYTHON:-/usr/bin/python3.13}"
LOCK_CLI="${APP_DIR}/scripts/utils/ib_2fa_lock.py"
DOCKER_BIN="${RADON_DOCKER_BIN:-/usr/bin/docker}"
CONSUMED_PATH="${RADON_IB_LEASE_CONSUMED_PATH:-/var/lib/radon/ib-2fa-consumed-lease.json}"
CONTROL_GUARD_PATH="${RADON_IB_CONTROL_GUARD_PATH:-/var/lib/radon/ib-gateway-control.guard}"
TRANSITION_PATH="${RADON_IB_TRANSITION_PATH:-/var/lib/radon/ib-gateway-transition.json}"
CONTROL_LOCK_WAIT_SECS="${RADON_IB_CONTROL_LOCK_WAIT_SECS:-20}"
DOCKER_MUTATION_TIMEOUT_SECS="${RADON_IB_DOCKER_MUTATION_TIMEOUT_SECS:-15}"
DOCKER_INSPECT_TIMEOUT_SECS="${RADON_IB_DOCKER_INSPECT_TIMEOUT_SECS:-3}"
TRANSITION_SETTLE_TIMEOUT_SECS="${RADON_IB_TRANSITION_SETTLE_TIMEOUT_SECS:-10}"
DEPLOY_LOCK_PATH="${RADON_DEPLOY_LOCK_FILE:-/home/radon/.radon-deploy.lock}"
LOCK_HOLDER="radon-cloud.ib-gateway-control"
WATCHDOG_HOLDER="scripts.ib_watchdog.trigger_restart"
CONTAINER_NAME="ib-gateway"
LEASE_HELD_RC=75
CONTROL_BUSY_RC=74
for timeout_value in \
  "$CONTROL_LOCK_WAIT_SECS" "$DOCKER_MUTATION_TIMEOUT_SECS" \
  "$DOCKER_INSPECT_TIMEOUT_SECS" "$TRANSITION_SETTLE_TIMEOUT_SECS"; do
  if [[ ! "$timeout_value" =~ ^[1-9][0-9]*$ ]]; then
    echo "REFUSING Gateway mutation: timeout budgets must be positive integers" >&2
    exit 2
  fi
done

inherited_lock_is_valid() {
  local marker="$1"
  local fd_text="$2"
  local lock_path="$3"
  [[ "$marker" == "1" && "$fd_text" =~ ^[0-9]+$ ]] || return 1
  "$LOCK_PYTHON" - "$fd_text" "$lock_path" <<'PY'
import fcntl
import os
import sys

fd = int(sys.argv[1])
path = sys.argv[2]
try:
    descriptor = os.fstat(fd)
    target = os.stat(path)
    if (descriptor.st_dev, descriptor.st_ino) != (target.st_dev, target.st_ino):
        raise OSError("inherited descriptor does not match lock path")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except (OSError, ValueError):
    raise SystemExit(1)
PY
}

acquire_deploy_lock() {
  if [[ "${RADON_DEPLOY_LOCKED:-0}" == "1" ]]; then
    if inherited_lock_is_valid \
      "${RADON_DEPLOY_LOCKED:-0}" "${RADON_DEPLOY_LOCK_FD:-}" "$DEPLOY_LOCK_PATH"; then
      return 0
    fi
    echo "REFUSING Gateway mutation: inherited deploy-lock proof is invalid" >&2
    return "$CONTROL_BUSY_RC"
  fi
  if [[ ! -x "$LOCK_PYTHON" ]]; then
    echo "REFUSING Gateway mutation: deploy/control lock runtime unavailable" >&2
    return 1
  fi
  exec "$LOCK_PYTHON" - \
    "$DEPLOY_LOCK_PATH" "$LOCK_CLI" "$WATCHDOG_HOLDER" "$0" "$@" <<'PY'
import fcntl
import os
from pathlib import Path
import subprocess
import sys

lock_text, lock_cli, watchdog_holder, script, *args = sys.argv[1:]
lock_path = Path(lock_text)
lock_path.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    # The watchdog acquires its 2FA lease before systemd starts the fixed
    # preheld adapter. A deploy-lock refusal occurs before Docker is touched,
    # so release that exact lease instead of blocking all recovery for 10m.
    if args[:2] == ["restart-preheld", watchdog_holder] and Path(lock_cli).is_file():
        subprocess.run(
            [sys.executable, lock_cli, "release", watchdog_holder],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    print(f"REFUSING Gateway mutation: deploy/control lock held: {lock_path}", file=sys.stderr)
    raise SystemExit(74)
os.set_inheritable(fd, True)
env = os.environ.copy()
env["RADON_DEPLOY_LOCKED"] = "1"
env["RADON_DEPLOY_LOCK_FD"] = str(fd)
os.execve(script, [script, *args], env)
PY
}

acquire_lifecycle_mutex() {
  if [[ "${RADON_IB_CONTROL_LOCKED:-0}" == "1" ]]; then
    if inherited_lock_is_valid \
      "${RADON_IB_CONTROL_LOCKED:-0}" "${RADON_IB_CONTROL_LOCK_FD:-}" "$CONTROL_GUARD_PATH"; then
      return 0
    fi
    echo "REFUSING Gateway mutation: inherited lifecycle-lock proof is invalid" >&2
    return "$CONTROL_BUSY_RC"
  fi
  if [[ ! -x "$LOCK_PYTHON" ]]; then
    echo "REFUSING Gateway mutation: lifecycle mutex runtime unavailable" >&2
    return 1
  fi

  # Hold flock across an exec back into this script. The inherited descriptor
  # stays open for the entire Docker mutation, serializing start/stop/restart
  # without extending the 10-minute 2FA push lease.
  exec "$LOCK_PYTHON" - \
    "$CONTROL_GUARD_PATH" "$CONTROL_LOCK_WAIT_SECS" "$LOCK_CLI" \
    "$WATCHDOG_HOLDER" "$0" "$@" <<'PY'
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

guard_text, wait_text, lock_cli, watchdog_holder, script, *args = sys.argv[1:]
guard_path = Path(guard_text)
guard_path.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(guard_path, os.O_CREAT | os.O_RDWR, 0o600)
deadline = time.monotonic() + float(wait_text)
while True:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.monotonic() >= deadline:
            if args[:2] == ["restart-preheld", watchdog_holder] and Path(lock_cli).is_file():
                subprocess.run(
                    [sys.executable, lock_cli, "release", watchdog_holder],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            print("REFUSING Gateway mutation: lifecycle control is busy", file=sys.stderr)
            raise SystemExit(74)
        time.sleep(0.1)

os.set_inheritable(fd, True)
env = os.environ.copy()
env["RADON_IB_CONTROL_LOCKED"] = "1"
env["RADON_IB_CONTROL_LOCK_FD"] = str(fd)
os.execve(script, [script, *args], env)
PY
}

run_bounded() {
  local timeout_secs="$1"
  shift
  "$LOCK_PYTHON" - "$timeout_secs" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout = float(sys.argv[1])
command = sys.argv[2:]
proc = subprocess.Popen(command, start_new_session=True)
try:
    raise SystemExit(proc.wait(timeout=timeout))
except subprocess.TimeoutExpired:
    print(f"command timed out after {timeout:g}s: {command[0]}", file=sys.stderr)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    raise SystemExit(124)
PY
}

compose() {
  RADON_COMPOSE_ENV_FILE="$ENV_FILE" run_bounded \
    "$DOCKER_MUTATION_TIMEOUT_SECS" "$DOCKER_BIN" compose \
    --env-file "$ENV_FILE" \
    --project-directory "$CLOUD_DIR" \
    -f "$CLOUD_DIR/docker-compose.yml" \
    "$@"
}

gateway_state() {
  local output rc=0
  output="$(
    run_bounded "$DOCKER_INSPECT_TIMEOUT_SECS" \
      "$DOCKER_BIN" inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>&1
  )" || rc=$?
  if (( rc == 0 )); then
    case "$output" in
      true) echo running ;;
      false) echo stopped ;;
      *) echo unknown ;;
    esac
  elif [[ "$output" == *"No such object"* || "$output" == *"No such container"* \
      || "$output" == *"no such object"* || "$output" == *"no such container"* ]]; then
    echo missing
  else
    echo unknown
  fi
}

container_running() {
  [[ "$(gateway_state)" == "running" ]]
}

write_transition() {
  local desired="$1"
  local action="$2"
  "$LOCK_PYTHON" - "$TRANSITION_PATH" "$desired" "$action" <<'PY'
import json
import os
from pathlib import Path
import tempfile
import time
import sys

path = Path(sys.argv[1])
payload = {"desired": sys.argv[2], "action": sys.argv[3], "started_at": time.time()}
path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
try:
    with os.fdopen(fd, "w") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
PY
}

clear_transition() {
  "$LOCK_PYTHON" - "$TRANSITION_PATH" <<'PY'
import os
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    path.unlink()
except FileNotFoundError:
    raise SystemExit(0)
directory = os.open(path.parent, os.O_RDONLY)
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}

transition_desired() {
  "$LOCK_PYTHON" - "$TRANSITION_PATH" <<'PY'
import json
from pathlib import Path
import sys

try:
    value = json.loads(Path(sys.argv[1]).read_text()).get("desired")
except (OSError, ValueError, TypeError):
    raise SystemExit(1)
if value not in {"running", "stopped"}:
    raise SystemExit(1)
print(value)
PY
}

wait_for_desired_state() {
  local desired="$1"
  local deadline state consecutive=0
  deadline=$((SECONDS + TRANSITION_SETTLE_TIMEOUT_SECS))
  while (( SECONDS <= deadline )); do
    state="$(gateway_state)"
    if [[ "$desired" == "running" && "$state" == "running" ]] \
      || [[ "$desired" == "stopped" && ( "$state" == "stopped" || "$state" == "missing" ) ]]; then
      consecutive=$((consecutive + 1))
      if (( consecutive >= 2 )); then
        return 0
      fi
    else
      consecutive=0
    fi
    sleep 0.2
  done
  return 1
}

reconcile_pending_transition() {
  local desired
  [[ -e "$TRANSITION_PATH" ]] || return 0
  if ! desired="$(transition_desired)"; then
    echo "REFUSING Gateway mutation: transition journal is unreadable" >&2
    return "$CONTROL_BUSY_RC"
  fi
  if ! wait_for_desired_state "$desired"; then
    echo "REFUSING Gateway mutation: prior Docker transition remains unresolved (desired=$desired)" >&2
    return "$CONTROL_BUSY_RC"
  fi
  clear_transition
  echo "Reconciled prior Docker transition at desired=$desired"
}

compose_to_state() {
  local desired="$1"
  local action="$2"
  shift 2
  local compose_rc=0
  write_transition "$desired" "$action"
  compose "$@" || compose_rc=$?
  if wait_for_desired_state "$desired"; then
    clear_transition
    if (( compose_rc != 0 )); then
      echo "Docker client exited rc=$compose_rc but daemon converged to $desired" >&2
    fi
    return 0
  fi
  echo "Gateway transition did not converge; durable journal blocks later mutations" >&2
  (( compose_rc != 0 )) && return "$compose_rc"
  return 1
}

require_new_lease() {
  local output
  if [[ ! -x "$LOCK_PYTHON" || ! -f "$LOCK_CLI" ]]; then
    echo "REFUSING Gateway cycle: atomic lease CLI unavailable" >&2
    return 1
  fi
  # Match the marker anywhere: the lease CLI also logs diagnostics to stderr
  # (an ignored orphan lease), and stdout/stderr interleave unpredictably once
  # both are captured through one pipe.
  if ! output=$("$LOCK_PYTHON" "$LOCK_CLI" acquire "$LOCK_HOLDER" 2>&1); then
    echo "REFUSING Gateway cycle: ${output:-atomic lease acquisition failed}" >&2
    case "$output" in
      *"held holder="*) return "$LEASE_HELD_RC" ;;
      *) return 1 ;;
    esac
  fi
  case "$output" in
    *"acquired holder="*) printf '2FA push lease: %s\n' "$output" ;;
    *)
      echo "REFUSING Gateway cycle: unrecognized lease response: $output" >&2
      return 1
      ;;
  esac
}

consume_watchdog_lease_once() {
  local expected_holder="$1"
  if [[ "$expected_holder" != "$WATCHDOG_HOLDER" ]]; then
    echo "REFUSING preheld restart: unsupported holder $expected_holder" >&2
    return 1
  fi
  if [[ ! -x "$LOCK_PYTHON" || ! -f "$LOCK_CLI" ]]; then
    echo "REFUSING preheld restart: atomic lease module unavailable" >&2
    return 1
  fi

  if ! "$LOCK_PYTHON" "$LOCK_CLI" consume "$expected_holder" "$CONSUMED_PATH"; then
    echo "REFUSING preheld restart: exact watchdog lease CAS failed" >&2
    return 1
  fi
}

read_host_role() {
  local role="${RADON_HOST_ROLE:-}"
  if [[ -z "$role" && -n "${ENV_FILE:-}" && -f "$ENV_FILE" ]]; then
    role="$(awk -F= '/^RADON_HOST_ROLE=/{v=$2} END{print v}' "$ENV_FILE" 2>/dev/null || true)"
    role="${role%\"}"
    role="${role#\"}"
    role="${role%\'}"
    role="${role#\'}"
    role="${role//$'\r'/}"
  fi
  case "$role" in
    app|broker|combined) printf '%s\n' "$role" ;;
    *) printf 'combined\n' ;;
  esac
}

refuse_app_role_mutation() {
  if [[ "$(read_host_role)" == "app" ]]; then
    echo "REFUSING Gateway mutation: RADON_HOST_ROLE=app (broker owns the session)" >&2
    return 1
  fi
  return 0
}

start_gateway() {
  refuse_app_role_mutation || return 1
  reconcile_pending_transition
  if container_running; then
    echo "IB Gateway container already running; no lease acquired"
    return 0
  fi
  require_new_lease
  compose_to_state running start up -d
  if ! container_running; then
    echo "Gateway start command completed but container is not running" >&2
    return 1
  fi
}

restart_gateway() {
  refuse_app_role_mutation || return 1
  reconcile_pending_transition
  require_new_lease
  compose_to_state stopped restart-down down
  compose_to_state running restart-up up -d
  if ! container_running; then
    echo "Gateway restart command completed but container is not running" >&2
    return 1
  fi
}

restart_gateway_preheld() {
  local expected_holder="${1:-}"
  refuse_app_role_mutation || return 1
  reconcile_pending_transition
  consume_watchdog_lease_once "$expected_holder"
  compose_to_state stopped preheld-down down
  compose_to_state running preheld-up up -d
  if ! container_running; then
    echo "Preheld Gateway restart completed but container is not running" >&2
    return 1
  fi
}

release_any_lease() {
  local output
  if [[ ! -x "$LOCK_PYTHON" || ! -f "$LOCK_CLI" ]]; then
    echo "WARNING: 2FA lease CLI unavailable; lease not cleared" >&2
    return 0
  fi
  if output=$("$LOCK_PYTHON" "$LOCK_CLI" release-any 2>&1); then
    printf '2FA push lease: %s\n' "$output"
  else
    echo "WARNING: could not clear 2FA push lease: ${output:-unknown error}" >&2
  fi
}

stop_gateway() {
  reconcile_pending_transition
  compose_to_state stopped stop down
  # A stopped container has no login session, so no IBKR push can still be
  # pending against it. Leaving the lease held locks operator start, the
  # watchdog, and deploy out for the rest of its 10m TTL — 2026-08-25, an admin
  # stop 14s after a restart wedged every Gateway control until the TTL ran out.
  # Only reached on a converged stop: compose_to_state returns non-zero under
  # set -e when the container is still up, and a live container still needs the
  # lease. Unconditional by design — whoever took it, their push is now moot.
  release_any_lease
}

case "${1:-}" in
  start)
    # A healthy start is a read-only no-op. Take only the short lifecycle
    # mutex so an overlapping stop cannot invalidate the observation. Acquire
    # the deploy lock only when Docker will actually be mutated.
    acquire_lifecycle_mutex "$@"
    if [[ -e "$TRANSITION_PATH" ]]; then
      acquire_deploy_lock "$@"
      reconcile_pending_transition
    fi
    if container_running; then
      echo "IB Gateway container already running; no lease acquired"
      exit 0
    fi
    acquire_deploy_lock "$@"
    start_gateway
    ;;
  restart)
    acquire_deploy_lock "$@"
    acquire_lifecycle_mutex "$@"
    restart_gateway
    ;;
  restart-preheld)
    acquire_deploy_lock "$@"
    acquire_lifecycle_mutex "$@"
    restart_gateway_preheld "${2:-}"
    ;;
  stop)
    acquire_deploy_lock "$@"
    acquire_lifecycle_mutex "$@"
    stop_gateway
    ;;
  status)
    if [[ -e "$TRANSITION_PATH" ]]; then
      echo "transition-pending"
      exit "$CONTROL_BUSY_RC"
    fi
    if container_running; then
      echo "running"
    else
      state="$(gateway_state)"
      if [[ "$state" == "stopped" || "$state" == "missing" ]]; then
        echo "stopped"
        exit 1
      fi
      echo "unknown"
      exit 2
    fi
    ;;
  *)
    echo "usage: $0 {start|restart|restart-preheld HOLDER|stop|status}" >&2
    exit 2
    ;;
esac
