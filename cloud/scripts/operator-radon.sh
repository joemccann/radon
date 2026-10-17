#!/usr/bin/env bash
set -euo pipefail

# Enumerate every loaded radon-* unit (services + timers) on each run
# so newly added units are picked up automatically. Avoids the previous
# hard-coded list going stale when vcg-refresh / cta-sync /
# portfolio-sync / watchdog-* timers landed.
#
# EXCLUDE radon-health.service: the standalone health daemon is deliberately
# isolated (no cascade dependency) so it keeps REPORTING while the trading
# stack is stopped/restarted. `radon stop|start|restart` must NOT touch it —
# otherwise the watcher goes down exactly when you stop what it watches.
# Manage it explicitly with `systemctl restart radon-health` when needed.
#
# EXCLUDE radon-control.service for the same reason, and one more: the admin
# panel's full-stack restart runs THIS script from inside radon-control's
# cgroup, so quiescing it would kill the restart halfway through.
#
# Privilege: every mutating invocation re-enters this root-owned script through
# one exact passwordless sudo rule. The script validates its arguments before
# running systemctl, so neither the admin API nor a daemon receives a generic
# systemctl capability.
requested_action="${1:-status}"
# `radon ib release|resume|status` (broker, root): the IBKR operator hold. It
# must never wait on the deploy lock below, so it leaves before any of it.
if [[ "$requested_action" == "ib" ]]; then
  shift
  exec "${RADON_IB_HOLD_BIN:-/usr/local/sbin/radon-ib-hold}" "$@"
fi
case "$requested_action" in
  stop|start|restart|status) ;;
  repair-nextjs-replica)
    (( $# == 1 )) || { echo "usage: radon repair-nextjs-replica" >&2; exit 2; }
    ;;
  unit)
    if (( $# != 3 )) || [[ ! "${2:-}" =~ ^(start|stop|restart)$ ]] \
      || [[ ! "${3:-}" =~ ^radon-[a-z0-9-]+\.(service|timer)$ ]] \
      || [[ "${3:-}" == radon-ib-gateway* ]] \
      || [[ "${3:-}" == radon-beta-* ]]; then
      echo "usage: radon unit {start|stop|restart} radon-NAME.{service|timer}" >&2
      exit 2
    fi
    unit_action="$2"
    unit_name="$3"
    ;;
  logs)
    # Read-only journal tail for the /admin log disclosure. Exactly one
    # radon-NAME.service; the journalctl argv below is fixed, so the caller
    # never supplies an option (sudoers grants `logs radon-*.service`).
    if (( $# != 2 )) || [[ ! "${2:-}" =~ ^radon-[a-z0-9-]+\.service$ ]] \
      || [[ "${2:-}" == radon-beta-* ]]; then
      echo "usage: radon logs radon-NAME.service" >&2
      exit 2
    fi
    unit_name="$2"
    ;;
  *) echo "usage: radon {stop|start|restart|status|unit|logs|repair-nextjs-replica|ib}" >&2; exit 2 ;;
esac

if [[ "$requested_action" != "status" && $EUID -ne 0 \
  && "${RADON_OPERATOR_ALLOW_UNPRIVILEGED:-${RADON_OPERATOR_ALLOW_ROOT:-0}}" != "1" ]]; then
  exec sudo -n "${RADON_OPERATOR_INSTALLED_PATH:-/usr/local/bin/radon}" "$@"
fi

DEPLOY_LOCK_PATH="${RADON_DEPLOY_LOCK_FILE:-/home/radon/.radon-deploy.lock}"
OPERATOR_PYTHON="${RADON_OPERATOR_PYTHON:-/usr/bin/python3.13}"
UNIT_ACTION_TIMEOUT_SECS="${RADON_OPERATOR_UNIT_TIMEOUT_SECS:-20}"
# `radon unit restart` blocks in systemctl for the stop and then the start.
# The longest app-unit TimeoutStopSec is research (120). TimeoutStartSec is
# the systemd default (90) where a unit does not set it. A 20s bound exited
# 70 while radon-api was still in its halt grace and later came up healthy
# (page 77becdbdfc506a32c447c10283efe6b4, 2026-10-06). 15s of slack lets
# systemd return its own result before this bound fires.
UNIT_VERB_TIMEOUT_SECS="${RADON_OPERATOR_UNIT_VERB_TIMEOUT_SECS:-225}"
SYSTEMCTL_BATCH_TIMEOUT_SECS="${RADON_OPERATOR_BATCH_TIMEOUT_SECS:-30}"
SYSTEMCTL_QUERY_TIMEOUT_SECS="${RADON_OPERATOR_QUERY_TIMEOUT_SECS:-10}"
# `radon logs`: radon-control waits 15s for the whole reply.
JOURNAL_QUERY_TIMEOUT_SECS="${RADON_OPERATOR_JOURNAL_TIMEOUT_SECS:-10}"
GATEWAY_ACTION_TIMEOUT_SECS="${RADON_OPERATOR_GATEWAY_TIMEOUT_SECS:-80}"
case "$requested_action" in
  # Longer than UNIT_VERB_TIMEOUT_SECS so the lock wrapper does not SIGTERM
  # a restart that systemctl is still allowed to finish.
  unit) ACTION_TIMEOUT_SECS="${RADON_OPERATOR_ACTION_TIMEOUT_SECS:-240}" ;;
  repair-nextjs-replica) ACTION_TIMEOUT_SECS="${RADON_OPERATOR_ACTION_TIMEOUT_SECS:-90}" ;;
  *) ACTION_TIMEOUT_SECS="${RADON_OPERATOR_ACTION_TIMEOUT_SECS:-165}" ;;
esac
for timeout_value in \
  "$UNIT_ACTION_TIMEOUT_SECS" "$UNIT_VERB_TIMEOUT_SECS" \
  "$SYSTEMCTL_BATCH_TIMEOUT_SECS" \
  "$SYSTEMCTL_QUERY_TIMEOUT_SECS" "$JOURNAL_QUERY_TIMEOUT_SECS" \
  "$GATEWAY_ACTION_TIMEOUT_SECS" \
  "$ACTION_TIMEOUT_SECS"; do
  if [[ ! "$timeout_value" =~ ^[1-9][0-9]*$ ]]; then
    echo "REFUSING operator action: timeout budgets must be positive integers" >&2
    exit 2
  fi
done

inherited_lock_is_valid() {
  local marker="$1"
  local fd_text="$2"
  local lock_path="$3"
  [[ "$marker" == "1" && "$fd_text" =~ ^[0-9]+$ ]] || return 1
  "$OPERATOR_PYTHON" -I - "$fd_text" "$lock_path" <<'PY'
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
    echo "REFUSING stack action: inherited deploy-lock proof is invalid" >&2
    return 74
  fi
  if [[ ! -x "$OPERATOR_PYTHON" ]]; then
    echo "REFUSING stack action: system Python 3.13 unavailable" >&2
    return 1
  fi
  exec "$OPERATOR_PYTHON" -I - \
    "$DEPLOY_LOCK_PATH" "$ACTION_TIMEOUT_SECS" "$0" "$@" <<'PY'
import fcntl
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys

lock_text, timeout_text, script, *args = sys.argv[1:]
lock_path = Path(lock_text)
canonical_lock = Path("/home/radon/.radon-deploy.lock")
if os.geteuid() == 0 and lock_path == canonical_lock:
    try:
        metadata = lock_path.stat()
        radon = pwd.getpwnam("radon")
    except (FileNotFoundError, KeyError) as exc:
        print(f"REFUSING stack action: canonical deploy lock is not provisioned: {exc}", file=sys.stderr)
        raise SystemExit(74)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != radon.pw_uid
        or metadata.st_gid != radon.pw_gid
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        print("REFUSING stack action: canonical deploy lock ownership/mode is unsafe", file=sys.stderr)
        raise SystemExit(74)
    fd = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
else:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(f"REFUSING stack action: deploy/control lock held: {lock_path}", file=sys.stderr)
    raise SystemExit(74)
os.set_inheritable(fd, True)
env = os.environ.copy()
env["RADON_DEPLOY_LOCKED"] = "1"
env["RADON_DEPLOY_LOCK_FD"] = str(fd)
proc = subprocess.Popen(
    [script, *args],
    env=env,
    pass_fds=(fd,),
    start_new_session=True,
)
try:
    raise SystemExit(proc.wait(timeout=float(timeout_text)))
except subprocess.TimeoutExpired:
    print(f"REFUSING operator action: global timeout after {timeout_text}s", file=sys.stderr)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    raise SystemExit(70)
PY
}

case "$requested_action" in
  start|restart|stop|unit|repair-nextjs-replica) acquire_deploy_lock "$@" ;;
esac

run_bounded() {
  local timeout_secs="$1"
  shift
  set +e
  "$OPERATOR_PYTHON" -I - "$timeout_secs" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout, *command = sys.argv[1:]
pass_fds = ()
fd_text = os.environ.get("RADON_DEPLOY_LOCK_FD", "")
if os.environ.get("RADON_DEPLOY_LOCKED") == "1" and fd_text.isdigit():
    try:
        deploy_lock_fd = int(fd_text)
        os.fstat(deploy_lock_fd)
    except OSError:
        pass
    else:
        pass_fds = (deploy_lock_fd,)
proc = subprocess.Popen(command, start_new_session=True, pass_fds=pass_fds)
try:
    raise SystemExit(proc.wait(timeout=float(timeout)))
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    raise SystemExit(124)
PY
  bounded_rc=$?
  set -e
  return "$bounded_rc"
}

# Read-only and lock-free: it runs before unit enumeration and never mutates.
if [[ "$requested_action" == "logs" ]]; then
  journal_rc=0
  run_bounded "$JOURNAL_QUERY_TIMEOUT_SECS" \
    journalctl -u "$unit_name" -n 300 --no-pager -q -o json --output-fields=MESSAGE,PRIORITY \
    || journal_rc=$?
  exit "$journal_rc"
fi

cancel_unit_jobs() {
  local target_unit
  local jobs
  for target_unit in "$@"; do
    jobs="$(
      run_bounded "$SYSTEMCTL_QUERY_TIMEOUT_SECS" \
        systemctl list-jobs --no-legend --plain --no-pager || true
    )"
    while read -r job_id job_unit _rest; do
      if [[ "$job_id" =~ ^[0-9]+$ && "$job_unit" == "$target_unit" ]]; then
        run_bounded "$SYSTEMCTL_QUERY_TIMEOUT_SECS" systemctl cancel "$job_id" || true
      fi
    done <<< "$jobs"
  done
}

run_systemctl_bounded() {
  local action="$1"
  local unit="$2"
  local timeout_secs="${3:-$UNIT_ACTION_TIMEOUT_SECS}"
  local unit_rc=0
  run_bounded "$timeout_secs" systemctl "$action" "$unit" || unit_rc=$?
  if (( unit_rc == 124 || unit_rc == 137 )); then
    cancel_unit_jobs "$unit"
    echo "REFUSING unit action: systemctl timed out for $unit" >&2
    return 70
  fi
  (( unit_rc == 0 )) || return "$unit_rc"
}

if [[ "$requested_action" == "unit" ]]; then
  run_systemctl_bounded "$unit_action" "$unit_name" "$UNIT_VERB_TIMEOUT_SECS"
  echo "$unit_action completed for $unit_name"
  exit 0
fi

if [[ "$requested_action" == "repair-nextjs-replica" ]]; then
  run_systemctl_bounded stop radon-nextjs.service
  rm -f -- \
    /home/radon/radon/data/replica.db \
    /home/radon/radon/data/replica.db-wal \
    /home/radon/radon/data/replica.db-shm \
    /home/radon/radon/data/replica.db-info
  run_systemctl_bounded start radon-nextjs.service
  echo "repaired Next.js replica under deploy/control lock"
  exit 0
fi

if ! loaded_units="$(
  run_bounded "$SYSTEMCTL_QUERY_TIMEOUT_SECS" \
    systemctl list-units 'radon-*' --all --no-legend --plain
)"; then
  echo "REFUSING stack action: failed to enumerate radon systemd units" >&2
  exit 1
fi
mapfile -t LOADED_UNITS < <(printf '%s\n' "$loaded_units" | awk '{print $1}')
mapfile -t ACTIVE_UNITS < <(
  printf '%s\n' "$loaded_units" | awk '$3 == "active" || $3 == "activating" {print $1}'
)
if (( ${#LOADED_UNITS[@]} == 0 )); then
  echo "REFUSING stack action: no loaded radon systemd units found" >&2
  exit 1
fi

read_host_role() {
  local role="${RADON_HOST_ROLE:-}"
  local envf="${RADON_ENV_FILE:-/etc/radon/env}"
  if [[ -z "$role" && -f "$envf" ]]; then
    role="$(awk -F= '/^RADON_HOST_ROLE=/{v=$2} END{print v}' "$envf" 2>/dev/null || true)"
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

HOST_ROLE="$(read_host_role)"
case "$HOST_ROLE" in
  app)
    REQUIRED_UNITS=(
      radon-api.service
      radon-nextjs.service
      radon-relay.service
      radon-monitor.service
      radon-newsfeed.service
      radon-health.service
    )
    ;;
  broker)
    REQUIRED_UNITS=(
      radon-ib-gateway.service
      radon-ib-gateway-remote.service
    )
    ;;
  *)
    REQUIRED_UNITS=(
      radon-ib-gateway.service
      radon-api.service
      radon-nextjs.service
      radon-relay.service
      radon-monitor.service
      radon-newsfeed.service
      radon-health.service
    )
    ;;
esac
# Exact array membership avoids an early-exit grep closing a printf pipe.
# Under pipefail, that SIGPIPE falsely classifies a present unit as missing.
contains_unit() {
  local expected="$1" candidate
  shift
  for candidate in "$@"; do
    [[ "$candidate" == "$expected" ]] && return 0
  done
  return 1
}

MISSING_UNITS=()
for required_unit in "${REQUIRED_UNITS[@]}"; do
  if ! contains_unit "$required_unit" "${LOADED_UNITS[@]}"; then
    MISSING_UNITS+=("$required_unit")
  fi
done
if (( ${#MISSING_UNITS[@]} > 0 )); then
  if [[ "$requested_action" == "start" || "$requested_action" == "restart" ]]; then
    echo "REFUSING stack action: required units not loaded: ${MISSING_UNITS[*]}" >&2
    exit 1
  elif [[ "$requested_action" == "status" ]]; then
    echo "WARNING: required units not loaded: ${MISSING_UNITS[*]}" >&2
  fi
fi

# Mutate only persistent daemons and timers. Timer-triggered Type=oneshot units
# remain scheduler-owned; starting every loaded unit causes scans, backups, and
# forecasts to stampede immediately outside their calendars.
GATEWAY_UNIT=radon-ib-gateway.service
GATEWAY_CONTROL="${RADON_IB_GATEWAY_CONTROL:-/usr/local/bin/radon-ib-gateway-control}"
TOPOLOGY_PATH="${RADON_OPERATOR_TOPOLOGY_PATH:-/var/lib/radon/operator-topology}"
case "$HOST_ROLE" in
  broker)
    PERSISTENT_UNITS=(
      radon-ib-gateway-remote.service
    )
    ;;
  *)
    PERSISTENT_UNITS=(
      radon-api.service
      radon-nextjs.service
      radon-relay.service
      radon-monitor.service
      radon-newsfeed.service
    )
    ;;
esac
mapfile -t ACTIVE_TIMERS < <(
  printf '%s\n' "${ACTIVE_UNITS[@]}" \
    | grep -E '^radon-.+\.timer$' | grep -v '^radon-beta-' || true
)

ENABLED_TIMERS=()
if [[ "$requested_action" == "start" ]]; then
  if ! enabled_timer_units="$(
    run_bounded "$SYSTEMCTL_QUERY_TIMEOUT_SECS" \
      systemctl list-unit-files 'radon-*.timer' --state=enabled --no-legend --plain
  )"; then
    echo "REFUSING stack action: failed to enumerate enabled radon timers" >&2
    exit 1
  fi
  mapfile -t ENABLED_TIMERS < <(
    printf '%s\n' "$enabled_timer_units" | awk '$1 ~ /^radon-/ && $1 !~ /^radon-beta-/ {print $1}'
  )
fi

ACTIVE_SCHEDULED_SERVICES=()
for unit in "${ACTIVE_UNITS[@]}"; do
  case "$unit" in
    radon-beta-*|radon-health.service|radon-control.service|radon-ib-gateway.service|radon-ib-gateway-preheld-restart.service|radon-ib-gateway-remote.service) ;;
    radon-api.service|radon-nextjs.service|radon-relay.service|radon-monitor.service|radon-newsfeed.service) ;;
    radon-*.service) ACTIVE_SCHEDULED_SERVICES+=("$unit") ;;
  esac
done

# `radon stop` is a NOPASSWD root action for the unprivileged `radon` account
# and /var/lib/radon is radon-owned, so a pathname root re-resolves between
# creating the temp entry and writing it can be swapped for a symlink: root
# then truncates and overwrites an attacker-chosen file. Same idiom as the
# root private-directory helper: pin every ancestor with O_NOFOLLOW, create
# the temp entry O_EXCL|O_NOFOLLOW relative to that fd, then write, fchmod
# and rename by fd so no name is resolved twice.
persist_topology() {
  "$OPERATOR_PYTHON" -I - "$TOPOLOGY_PATH" "$@" <<'PY_PERSIST_TOPOLOGY' || exit 74
import os, sys
from pathlib import Path

path, units = sys.argv[1], sys.argv[2:]
parts = Path(path).parts
if not parts or parts[0] != '/' or '..' in parts:
    print('operator-radon: topology path must be absolute without traversal', file=sys.stderr)
    raise SystemExit(74)

dir_flags = os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY
parent_fd = None
try:
    parent_fd = os.open('/', dir_flags)
    for part in parts[1:-1]:
        try:
            os.mkdir(part, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        child_fd = os.open(part, dir_flags, dir_fd=parent_fd)
        os.close(parent_fd)
        parent_fd = child_fd
    name = parts[-1]
    tmp_name = f'{name}.tmp.{os.getpid()}'
    try:
        os.unlink(tmp_name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    fd = os.open(
        tmp_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        os.write(fd, ''.join(f'{unit}\n' for unit in units).encode())
        os.fchmod(fd, 0o600)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
except OSError as error:
    print(f'operator-radon: refusing to persist topology: {error}', file=sys.stderr)
    raise SystemExit(74)
finally:
    if parent_fd is not None:
        os.close(parent_fd)
PY_PERSIST_TOPOLOGY
}

saved_timers() {
  [[ -f "$TOPOLOGY_PATH" ]] || return 0
  while IFS= read -r unit; do
    [[ "$unit" =~ ^radon-[a-z0-9-]+\.timer$ ]] || continue
    [[ "$unit" == radon-beta-* ]] && continue
    printf '%s\n' "$unit"
  done < "$TOPOLOGY_PATH"
}

unique_units() {
  awk 'NF && !seen[$0]++'
}

gateway_control() {
  if [[ $EUID -eq 0 ]]; then
    # Keep the inherited deploy-lock descriptor across the privilege drop, but
    # never keep root's identity environment. Docker reads HOME for its client
    # config; preserving HOME=/root makes an otherwise healthy Gateway appear
    # "unknown" to the root-only operator because radon cannot read it.
    run_bounded "$GATEWAY_ACTION_TIMEOUT_SECS" \
      runuser -u radon --preserve-environment -- \
        env HOME=/home/radon USER=radon LOGNAME=radon "$GATEWAY_CONTROL" "$@"
  else
    run_bounded "$GATEWAY_ACTION_TIMEOUT_SECS" "$GATEWAY_CONTROL" "$@"
  fi
}

systemctl_many() {
  local action="$1"
  shift
  (( $# == 0 )) && return 0
  local rc=0
  run_bounded "$SYSTEMCTL_BATCH_TIMEOUT_SECS" systemctl "$action" "$@" || rc=$?
  if (( rc == 124 || rc == 137 )); then
    cancel_unit_jobs "$@"
    echo "REFUSING stack action: systemctl $action timed out" >&2
    return 70
  fi
  return "$rc"
}

emit_stack_result() {
  local verb="$1"
  local count="$2"
  if [[ "$HOST_ROLE" == "app" ]]; then
    echo "${verb} ${count} app-plane radon units. Gateway stays on the broker."
  else
    echo "${verb} Gateway and ${count} radon units"
  fi
}

case "$requested_action" in
  stop)
    STOP_UNITS=()
    for unit in "${PERSISTENT_UNITS[@]}"; do
      if contains_unit "$unit" "${ACTIVE_UNITS[@]}"; then
        STOP_UNITS+=("$unit")
      fi
    done
    STOP_UNITS+=("${ACTIVE_TIMERS[@]}")
    STOP_UNITS+=("${ACTIVE_SCHEDULED_SERVICES[@]}")
    persist_topology "${STOP_UNITS[@]}"
    systemctl_many stop "${STOP_UNITS[@]}"
    if [[ "$HOST_ROLE" != "app" ]]; then
      gateway_control stop
    fi
    emit_stack_result "stopped" "${#STOP_UNITS[@]}"
    ;;
  start)
    if [[ -f "$TOPOLOGY_PATH" ]]; then
      mapfile -t PRIOR_TIMERS < <(saved_timers)
    else
      PRIOR_TIMERS=("${ENABLED_TIMERS[@]}")
    fi
    # radon-health is required to be loaded, but the isolated health daemon is
    # never started or stopped by the stack operator. Manage it explicitly.
    mapfile -t START_UNITS < <(
      printf '%s\n' "${PERSISTENT_UNITS[@]}" \
        "${PRIOR_TIMERS[@]}" | unique_units
    )
    if [[ "$HOST_ROLE" != "app" ]]; then
      gateway_control start
    fi
    systemctl_many start "${START_UNITS[@]}"
    rm -f "$TOPOLOGY_PATH"
    emit_stack_result "started" "${#START_UNITS[@]}"
    ;;
  restart)
    mapfile -t RESTART_UNITS < <(
      printf '%s\n' "${PERSISTENT_UNITS[@]}" "${ACTIVE_TIMERS[@]}" \
        | unique_units
    )
    # Quiesce timer-owned jobs that are currently running, but never start
    # them directly after the Gateway cycle. Their timers retain ownership.
    systemctl_many stop "${ACTIVE_SCHEDULED_SERVICES[@]}"
    if [[ "$HOST_ROLE" != "app" ]]; then
      gateway_control restart
    fi
    systemctl_many restart "${RESTART_UNITS[@]}"
    emit_stack_result "restarted" "${#RESTART_UNITS[@]}"
    ;;
  status)
    run_bounded "$SYSTEMCTL_QUERY_TIMEOUT_SECS" \
      systemctl list-units 'radon-*' --all --no-pager --no-legend
    gateway_status=0
    if [[ "$HOST_ROLE" == "app" ]]; then
      echo "ib-gateway-container: broker (not this host)"
    else
      printf 'ib-gateway-container: '
      gateway_control status || gateway_status=$?
    fi
    if (( ${#MISSING_UNITS[@]} > 0 || gateway_status != 0 )); then
      exit 1
    fi
    ;;
esac
