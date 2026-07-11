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
# Privilege: every mutating invocation re-enters this root-owned script through
# one exact passwordless sudo rule. The script validates its arguments before
# running systemctl, so neither the admin API nor a daemon receives a generic
# systemctl capability.
requested_action="${1:-status}"
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
  *) echo "usage: radon {stop|start|restart|status|unit|repair-nextjs-replica}" >&2; exit 2 ;;
esac

if [[ "$requested_action" != "status" && $EUID -ne 0 \
  && "${RADON_OPERATOR_ALLOW_UNPRIVILEGED:-${RADON_OPERATOR_ALLOW_ROOT:-0}}" != "1" ]]; then
  exec sudo -n "${RADON_OPERATOR_INSTALLED_PATH:-/usr/local/bin/radon}" "$@"
fi

DEPLOY_LOCK_PATH="${RADON_DEPLOY_LOCK_FILE:-/home/radon/.radon-deploy.lock}"
OPERATOR_PYTHON="${RADON_OPERATOR_PYTHON:-/usr/bin/python3.13}"
UNIT_ACTION_TIMEOUT_SECS="${RADON_OPERATOR_UNIT_TIMEOUT_SECS:-20}"
SYSTEMCTL_BATCH_TIMEOUT_SECS="${RADON_OPERATOR_BATCH_TIMEOUT_SECS:-30}"
SYSTEMCTL_QUERY_TIMEOUT_SECS="${RADON_OPERATOR_QUERY_TIMEOUT_SECS:-10}"
GATEWAY_ACTION_TIMEOUT_SECS="${RADON_OPERATOR_GATEWAY_TIMEOUT_SECS:-80}"
case "$requested_action" in
  unit) ACTION_TIMEOUT_SECS="${RADON_OPERATOR_ACTION_TIMEOUT_SECS:-40}" ;;
  repair-nextjs-replica) ACTION_TIMEOUT_SECS="${RADON_OPERATOR_ACTION_TIMEOUT_SECS:-90}" ;;
  *) ACTION_TIMEOUT_SECS="${RADON_OPERATOR_ACTION_TIMEOUT_SECS:-165}" ;;
esac
for timeout_value in \
  "$UNIT_ACTION_TIMEOUT_SECS" "$SYSTEMCTL_BATCH_TIMEOUT_SECS" \
  "$SYSTEMCTL_QUERY_TIMEOUT_SECS" "$GATEWAY_ACTION_TIMEOUT_SECS" \
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
  "$OPERATOR_PYTHON" - "$fd_text" "$lock_path" <<'PY'
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
  exec "$OPERATOR_PYTHON" - \
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
  "$OPERATOR_PYTHON" - "$timeout_secs" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout, *command = sys.argv[1:]
proc = subprocess.Popen(command, start_new_session=True)
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
  run_systemctl_bounded "$unit_action" "$unit_name"
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

REQUIRED_UNITS=(
  radon-ib-gateway.service
  radon-api.service
  radon-nextjs.service
  radon-relay.service
  radon-monitor.service
  radon-newsfeed.service
  radon-health.service
)
MISSING_UNITS=()
for required_unit in "${REQUIRED_UNITS[@]}"; do
  if ! printf '%s\n' "${LOADED_UNITS[@]}" | grep -qx "$required_unit"; then
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
PERSISTENT_UNITS=(
  radon-api.service
  radon-nextjs.service
  radon-relay.service
  radon-monitor.service
  radon-newsfeed.service
)
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
    radon-beta-*|radon-health.service|radon-ib-gateway.service|radon-ib-gateway-preheld-restart.service) ;;
    radon-api.service|radon-nextjs.service|radon-relay.service|radon-monitor.service|radon-newsfeed.service) ;;
    radon-*.service) ACTIVE_SCHEDULED_SERVICES+=("$unit") ;;
  esac
done

persist_topology() {
  local tmp
  mkdir -p "$(dirname "$TOPOLOGY_PATH")"
  tmp="$(mktemp "${TOPOLOGY_PATH}.tmp.XXXXXX")"
  printf '%s\n' "$@" > "$tmp"
  chmod 0600 "$tmp"
  mv -f "$tmp" "$TOPOLOGY_PATH"
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
    run_bounded "$GATEWAY_ACTION_TIMEOUT_SECS" \
      runuser -u radon --preserve-environment -- "$GATEWAY_CONTROL" "$@"
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

case "$requested_action" in
  stop)
    STOP_UNITS=()
    for unit in "${PERSISTENT_UNITS[@]}"; do
      if printf '%s\n' "${ACTIVE_UNITS[@]}" | grep -qx "$unit"; then
        STOP_UNITS+=("$unit")
      fi
    done
    STOP_UNITS+=("${ACTIVE_TIMERS[@]}")
    STOP_UNITS+=("${ACTIVE_SCHEDULED_SERVICES[@]}")
    persist_topology "${STOP_UNITS[@]}"
    systemctl_many stop "${STOP_UNITS[@]}"
    gateway_control stop
    echo "stopped Gateway and ${#STOP_UNITS[@]} active radon units"
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
    gateway_control start
    systemctl_many start "${START_UNITS[@]}"
    rm -f "$TOPOLOGY_PATH"
    echo "started Gateway and ${#START_UNITS[@]} radon units"
    ;;
  restart)
    mapfile -t RESTART_UNITS < <(
      printf '%s\n' "${PERSISTENT_UNITS[@]}" "${ACTIVE_TIMERS[@]}" \
        | unique_units
    )
    # Quiesce timer-owned jobs that are currently running, but never start
    # them directly after the Gateway cycle. Their timers retain ownership.
    systemctl_many stop "${ACTIVE_SCHEDULED_SERVICES[@]}"
    gateway_control restart
    systemctl_many restart "${RESTART_UNITS[@]}"
    echo "restarted Gateway and ${#RESTART_UNITS[@]} radon units"
    ;;
  status)
    run_bounded "$SYSTEMCTL_QUERY_TIMEOUT_SECS" \
      systemctl list-units 'radon-*' --all --no-pager --no-legend
    printf 'ib-gateway-container: '
    gateway_status=0
    gateway_control status || gateway_status=$?
    if (( ${#MISSING_UNITS[@]} > 0 || gateway_status != 0 )); then
      exit 1
    fi
    ;;
esac
