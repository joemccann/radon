#!/bin/bash
set -euo pipefail

# Installed root-owned at /usr/local/sbin/radon-deploy-root. The deploy user may
# invoke only the fixed actions listed in config/sudoers.d/radon-deploy.
readonly CORE_SERVICES=(
  radon-nextjs.service
  radon-api.service
  radon-relay.service
  radon-monitor.service
  radon-newsfeed.service
)
readonly PREHELD_UNIT=radon-ib-gateway-preheld-restart.service
readonly -a CONTROL_PLANE_SOURCES=(
  scripts/deploy-root-helper.sh
  scripts/ib-gateway-control.sh
  scripts/operator-radon.sh
  config/sudoers.d/radon-deploy
  config/sudoers.d/radon-monitor
  config/sudoers.d/radon-ops
  config/sudoers.d/radon-caddy
  config/polkit/50-radon-services.rules
  services/radon-health.service
  services/radon-ib-gateway-preheld-restart.service
  services/radon-ib-watchdog.service
  services/radon-ib-gateway.service
  services/radon-api.service
  services/radon-monitor.service
  services/radon-relay.service
  services/radon-portfolio-sync.service
  services/radon-refresh.service
  services/radon-db-backup.service
  services/radon-drift-audit.service
  services/radon-nextjs-db-watchdog.service
)
readonly -a CONTROL_PLANE_TARGETS=(
  /usr/local/sbin/radon-deploy-root
  /usr/local/bin/radon-ib-gateway-control
  /usr/local/bin/radon
  /etc/sudoers.d/radon-deploy
  /etc/sudoers.d/radon-monitor
  /etc/sudoers.d/radon-ops
  /etc/sudoers.d/radon-caddy
  /etc/polkit-1/rules.d/50-radon-services.rules
  /etc/systemd/system/radon-health.service
  /etc/systemd/system/radon-ib-gateway-preheld-restart.service
  /etc/systemd/system/radon-ib-watchdog.service
  /etc/systemd/system/radon-ib-gateway.service
  /etc/systemd/system/radon-api.service
  /etc/systemd/system/radon-monitor.service
  /etc/systemd/system/radon-relay.service
  /etc/systemd/system/radon-portfolio-sync.service
  /etc/systemd/system/radon-refresh.service
  /etc/systemd/system/radon-db-backup.service
  /etc/systemd/system/radon-drift-audit.service
  /etc/systemd/system/radon-nextjs-db-watchdog.service
)
readonly -a CONTROL_PLANE_MODES=(
  755 755 755
  440 440 440 440
  644
  644 644 644 644 644 644 644 644 644 644 644 644
)

if [[ "${RADON_DEPLOY_HELPER_TEST_MODE:-0}" == "1" ]]; then
  readonly HELPER_TEST_MODE=1
  readonly STATE_DIR="$(dirname "${RADON_TEST_ACTIVE_STATE_FILE:?test active-state file is required}")"
  readonly SYSTEMCTL="${RADON_TEST_SYSTEMCTL:?test systemctl is required}"
  readonly RM="${RADON_TEST_RM:?test rm is required}"
  readonly SYNC="${RADON_TEST_SYNC:?test sync is required}"
  readonly ACTIVE_STATE_FILE="${RADON_TEST_ACTIVE_STATE_FILE}"
  readonly FLOCK="${RADON_TEST_FLOCK:-/usr/bin/true}"
  readonly ROOT_LOCK_FILE="${RADON_TEST_ROOT_LOCK_FILE:-${ACTIVE_STATE_FILE}.lock}"
  readonly TIMEOUT="${RADON_TEST_TIMEOUT:-}"
  readonly ROOT_MUTATION_ACTION_TIMEOUT="${RADON_TEST_ROOT_MUTATION_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-180}}"
  readonly ROOT_VERIFY_ACTION_TIMEOUT="${RADON_TEST_ROOT_VERIFY_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-30}}"
  readonly ROOT_COMMIT_ACTION_TIMEOUT="${RADON_TEST_ROOT_COMMIT_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-30}}"
  readonly ROOT_KILL_AFTER="${RADON_TEST_ROOT_KILL_AFTER:-5}"
  readonly ROOT_LOCK_WAIT="${RADON_TEST_ROOT_LOCK_WAIT:-190}"
  readonly SESSION_PYTHON="${RADON_TEST_SESSION_PYTHON:-$(command -v python3)}"
  readonly TEST_SYSTEMCTL_TIMEOUT="${RADON_TEST_SYSTEMCTL_TIMEOUT:-1}"
  readonly STATE_WAIT_SECONDS="${RADON_TEST_STATE_WAIT_SECONDS:-0}"
  readonly PREHELD_WAIT_SECONDS="${RADON_TEST_PREHELD_WAIT_SECONDS:-0}"
  readonly SLEEP="${RADON_TEST_SLEEP:-/bin/sleep}"
  readonly CONTROL_PLANE_ROOT="${RADON_TEST_CONTROL_PLANE_ROOT:-}"
  readonly SHA256SUM="${RADON_TEST_SHA256SUM:-$(command -v sha256sum)}"
  test_replica_prefix="${RADON_TEST_REPLICA_PREFIX:?test replica prefix is required}"
  readonly REPLICA_FILES=(
    "$test_replica_prefix"
    "${test_replica_prefix}-wal"
    "${test_replica_prefix}-shm"
    "${test_replica_prefix}-info"
  )
else
  readonly HELPER_TEST_MODE=0
  if (( EUID != 0 )); then
    echo "radon-deploy-root must run as root" >&2
    exit 77
  fi
  readonly SYSTEMCTL=/usr/bin/systemctl
  readonly RM=/usr/bin/rm
  readonly SYNC=/usr/bin/sync
  readonly INSTALL=/usr/bin/install
  readonly STATE_DIR=/var/lib/radon/deploy
  readonly ACTIVE_STATE_FILE="${STATE_DIR}/active-units"
  readonly FLOCK=/usr/bin/flock
  readonly TIMEOUT=/usr/bin/timeout
  readonly ROOT_LOCK_FILE=/run/radon-deploy-root.lock
  readonly STATE_WAIT_SECONDS=60
  readonly PREHELD_WAIT_SECONDS=120
  readonly SLEEP=/usr/bin/sleep
  readonly CONTROL_PLANE_ROOT=""
  readonly SHA256SUM=/usr/bin/sha256sum
  readonly ROOT_MUTATION_ACTION_TIMEOUT=180
  readonly ROOT_VERIFY_ACTION_TIMEOUT=30
  readonly ROOT_COMMIT_ACTION_TIMEOUT=30
  readonly ROOT_KILL_AFTER=5
  readonly ROOT_LOCK_WAIT=190
  readonly SESSION_PYTHON=/usr/bin/python3.13
  readonly REPLICA_FILES=(
    /home/radon/radon/data/replica.db
    /home/radon/radon/data/replica.db-wal
    /home/radon/radon/data/replica.db-shm
    /home/radon/radon/data/replica.db-info
  )
fi
readonly RESTORED_STATE_FILE="${ACTIVE_STATE_FILE}.restored"
readonly INVENTORY_FILE="${ACTIVE_STATE_FILE}.inventory"
readonly CONTROL_PLANE_MANIFEST="${CONTROL_PLANE_ROOT}/var/lib/radon/control-plane-manifest.sha256"

[[ "${#CONTROL_PLANE_SOURCES[@]}" -eq "${#CONTROL_PLANE_TARGETS[@]}" && \
   "${#CONTROL_PLANE_SOURCES[@]}" -eq "${#CONTROL_PLANE_MODES[@]}" ]] || {
  echo "internal control-plane contract is inconsistent" >&2
  exit 70
}

prepare_state_dir() {
  if (( HELPER_TEST_MODE == 1 )); then
    mkdir -p "$STATE_DIR"
  else
    "$INSTALL" -d -m 0700 -o root -g root "$STATE_DIR"
  fi
}

if (( $# != 1 )); then
  echo "usage: radon-deploy-root {stop-clean|restart-managed|recover|verify-restored|verify-control-plane|commit-transition}" >&2
  exit 64
fi

file_mode() {
  local mode
  if mode="$(stat -c '%a' "$1" 2>/dev/null)"; then
    printf '%s\n' "$mode"
  else
    stat -f '%Lp' "$1"
  fi
}

root_ownership_matches() {
  local ownership
  (( HELPER_TEST_MODE == 1 )) && return 0
  if ownership="$(stat -c '%U:%G' "$1" 2>/dev/null)"; then
    [[ "$ownership" == "root:root" ]]
  else
    [[ "$(stat -f '%Su:%Sg' "$1")" == "root:root" ]]
  fi
}

verify_control_plane() {
  local line expected_hash source_rel logical_target installed_target installed_hash
  local index=0

  [[ -x "$SHA256SUM" && -f "$CONTROL_PLANE_MANIFEST" && \
     ! -L "$CONTROL_PLANE_MANIFEST" ]] || {
    echo "control-plane manifest is unavailable or unsafe" >&2
    return 1
  }
  while IFS= read -r line; do
    if (( index >= ${#CONTROL_PLANE_TARGETS[@]} )) || \
       [[ ! "$line" =~ ^([0-9a-f]{64})[[:space:]][[:space:]]([^[:space:]]+)[[:space:]]-\>[[:space:]](/.+)$ ]]; then
      echo "control-plane manifest contract is malformed" >&2
      return 1
    fi
    expected_hash="${BASH_REMATCH[1]}"
    source_rel="${BASH_REMATCH[2]}"
    logical_target="${BASH_REMATCH[3]}"
    if [[ "$source_rel" != "${CONTROL_PLANE_SOURCES[$index]}" || \
          "$logical_target" != "${CONTROL_PLANE_TARGETS[$index]}" ]]; then
      echo "control-plane manifest does not match the fixed target contract" >&2
      return 1
    fi
    installed_target="${CONTROL_PLANE_ROOT}${logical_target}"
    if [[ ! -f "$installed_target" || -L "$installed_target" ]]; then
      echo "installed control-plane target is unavailable or unsafe: ${logical_target}" >&2
      return 1
    fi
    if ! installed_hash="$("$SHA256SUM" "$installed_target" 2>/dev/null | awk '{print $1}')"; then
      echo "installed control-plane target is unreadable: ${logical_target}" >&2
      return 1
    fi
    if [[ "$installed_hash" != "$expected_hash" || \
          "$(file_mode "$installed_target")" != "${CONTROL_PLANE_MODES[$index]}" ]] || \
       ! root_ownership_matches "$installed_target"; then
      echo "installed control-plane target drifted: ${logical_target}" >&2
      return 1
    fi
    index=$((index + 1))
  done < "$CONTROL_PLANE_MANIFEST"
  if (( index != ${#CONTROL_PLANE_TARGETS[@]} )); then
    echo "control-plane manifest is incomplete" >&2
    return 1
  fi
}

systemctl_bounded() {
  if (( HELPER_TEST_MODE == 1 )); then
    if [[ -n "$TIMEOUT" ]]; then
      "$TIMEOUT" --signal=TERM --kill-after=1s "${TEST_SYSTEMCTL_TIMEOUT}s" \
        "$SYSTEMCTL" "$@"
    else
      "$SYSTEMCTL" "$@"
    fi
  else
    "$TIMEOUT" --signal=TERM --kill-after=2s 5s "$SYSTEMCTL" "$@"
  fi
}

wait_for_unit_state() {
  local unit="$1"
  local desired="$2"
  local deadline=$((SECONDS + STATE_WAIT_SECONDS))
  local state
  while :; do
    state="$(active_state "$unit")" || return 69
    [[ "$state" == "$desired" ]] && return 0
    if [[ "$desired" == inactive && "$state" == failed ]]; then
      return 0
    fi
    (( SECONDS >= deadline )) && break
    "$SLEEP" 1
  done
  echo "timed out waiting for ${unit} to become ${desired}" >&2
  return 71
}

unit_is_excluded() {
  local unit="$1"
  [[ "$unit" == radon-beta-* ]] && return 0
  [[ "$unit" == radon-ib-gateway.service || "$unit" == "$PREHELD_UNIT" ]]
}

list_transition_units() {
  local unit file_units loaded_units
  file_units="$(systemctl_bounded list-unit-files 'radon-*.service' 'radon-*.timer' --no-legend --no-pager)" \
    || return 1
  loaded_units="$(systemctl_bounded list-units --all 'radon-*.service' 'radon-*.timer' --no-legend --no-pager --plain)" \
    || return 1
  printf '%s\n%s\n' "$file_units" "$loaded_units" \
    | awk '{print $1}' \
    | while IFS= read -r unit; do
        [[ "$unit" =~ ^radon-[a-zA-Z0-9_.@-]+\.(service|timer)$ ]] || continue
        unit_is_excluded "$unit" && continue
        printf '%s\n' "$unit"
      done \
    | sort -u
}

active_state() {
  systemctl_bounded show "$1" --property=ActiveState --value 2>/dev/null
}

unit_type() {
  if [[ "$1" == *.timer ]]; then
    printf 'timer\n'
  else
    systemctl_bounded show "$1" --property=Type --value 2>/dev/null
  fi
}

snapshot_line_is_valid() {
  local unit="$1"
  local type="$2"
  [[ "$unit" =~ ^radon-[a-zA-Z0-9_.@-]+\.(service|timer)$ ]] || return 1
  unit_is_excluded "$unit" && return 1
  [[ "$type" =~ ^(timer|simple|notify|forking|exec|dbus|idle|oneshot)$ ]]
}

inventory_unit_is_valid() {
  [[ "$1" =~ ^radon-[a-zA-Z0-9_.@-]+\.(service|timer)$ ]] || return 1
  ! unit_is_excluded "$1"
}

validate_inventory() {
  local unit core found count=0
  [[ -f "$INVENTORY_FILE" ]] || return 1
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    inventory_unit_is_valid "$unit" || return 1
    count=$((count + 1))
  done < "$INVENTORY_FILE"
  (( count > 0 )) || return 1
  for core in "${CORE_SERVICES[@]}"; do
    found=0
    while IFS= read -r unit; do
      [[ "$unit" == "$core" ]] && found=1
    done < "$INVENTORY_FILE"
    (( found == 1 )) || return 1
  done
}

ensure_inventory() {
  local units temporary
  if [[ -f "$INVENTORY_FILE" ]]; then
    validate_inventory
    return
  fi
  units="$(list_transition_units)" || {
    echo "could not enumerate release consumer units" >&2
    return 68
  }
  temporary="$(mktemp "${INVENTORY_FILE}.tmp.XXXXXX")"
  printf '%s\n' "$units" > "$temporary"
  chmod 0600 "$temporary"
  "$SYNC" -f "$temporary"
  mv -f "$temporary" "$INVENTORY_FILE"
  if ! validate_inventory; then
    "$RM" -f "$INVENTORY_FILE"
    echo "release consumer inventory is empty or missing a core service" >&2
    return 68
  fi
  "$SYNC" -f "$INVENTORY_FILE"
  "$SYNC" -f "$(dirname "$INVENTORY_FILE")"
}

validate_active_snapshot() {
  local unit type extra
  [[ -f "$ACTIVE_STATE_FILE" ]] || return 0
  while IFS=$'\t' read -r unit type extra; do
    [[ -n "$unit" && -z "$extra" ]] || return 1
    snapshot_line_is_valid "$unit" "$type" || return 1
  done < "$ACTIVE_STATE_FILE"
}

wait_for_preheld_restart() {
  local deadline=$((SECONDS + PREHELD_WAIT_SECONDS))
  local state
  while :; do
    if ! state="$(active_state "$PREHELD_UNIT")"; then
      echo "could not verify ${PREHELD_UNIT} state" >&2
      return 69
    fi
    case "$state" in
      ""|inactive|failed) return 0 ;;
    esac
    (( SECONDS >= deadline )) && break
    "$SLEEP" 1
  done
  echo "refusing release transition while ${PREHELD_UNIT} is ${state:-unknown}" >&2
  return 66
}

snapshot_active_units() {
  local unit state type temporary
  if [[ -f "$ACTIVE_STATE_FILE" && ! -f "$INVENTORY_FILE" ]]; then
    echo "active-unit snapshot exists without its inventory" >&2
    return 68
  fi
  ensure_inventory || return $?
  if [[ -f "$ACTIVE_STATE_FILE" ]]; then
    validate_active_snapshot
    return
  fi
  temporary="$(mktemp "${ACTIVE_STATE_FILE}.tmp.XXXXXX")"
  while IFS= read -r unit; do
    if ! state="$(active_state "$unit")"; then
      "$RM" -f "$temporary"
      echo "could not read active state for ${unit}" >&2
      return 69
    fi
    case "$state" in
      active|activating|reloading)
        if ! type="$(unit_type "$unit")"; then
          "$RM" -f "$temporary"
          echo "could not read service type for ${unit}" >&2
          return 69
        fi
        snapshot_line_is_valid "$unit" "$type" || {
          "$RM" -f "$temporary"
          return 1
        }
        printf '%s\t%s\n' "$unit" "$type" >> "$temporary"
        ;;
    esac
  done < "$INVENTORY_FILE"
  chmod 0600 "$temporary"
  "$SYNC" -f "$temporary"
  mv -f "$temporary" "$ACTIVE_STATE_FILE"
  "$SYNC" -f "$(dirname "$ACTIVE_STATE_FILE")"
  "$RM" -f "$RESTORED_STATE_FILE"
}

stop_release_consumers() {
  local unit state
  local timers=()
  local services=()
  validate_inventory || return 68
  while IFS= read -r unit; do
    [[ -n "$unit" ]] || continue
    if [[ "$unit" == *.timer ]]; then timers+=("$unit"); else services+=("$unit"); fi
  done < "$INVENTORY_FILE"
  (( ${#timers[@]} == 0 )) || systemctl_bounded --no-block stop "${timers[@]}"
  for unit in "${timers[@]}"; do
    wait_for_unit_state "$unit" inactive || return $?
  done
  wait_for_preheld_restart
  (( ${#services[@]} == 0 )) || systemctl_bounded --no-block stop "${services[@]}"
  for unit in "${services[@]}"; do
    wait_for_unit_state "$unit" inactive || return $?
  done
  wait_for_preheld_restart
}

is_core_service() {
  local candidate="$1"
  local core
  for core in "${CORE_SERVICES[@]}"; do
    [[ "$candidate" == "$core" ]] && return 0
  done
  return 1
}

reset_core_failures() {
  systemctl_bounded reset-failed "${CORE_SERVICES[@]}"
}

verify_restored_state() {
  local unit type extra state
  [[ -f "$ACTIVE_STATE_FILE" ]] || return 0
  validate_active_snapshot || return 1
  [[ -f "$RESTORED_STATE_FILE" ]] || return 1
  while IFS=$'\t' read -r unit type extra; do
    [[ "$type" == oneshot ]] && continue
    state="$(active_state "$unit")" || return 69
    [[ "$state" == active ]] || {
      echo "snapshotted unit is not restored: ${unit} (${state:-unknown})" >&2
      return 67
    }
  done < "$ACTIVE_STATE_FILE"
}

resume_active_snapshot() {
  local unit type extra state
  local services=()
  local timers=()
  local already_resumed=0
  [[ -f "$ACTIVE_STATE_FILE" ]] || return 0
  validate_active_snapshot || return 1
  [[ -f "$RESTORED_STATE_FILE" ]] && already_resumed=1
  while IFS=$'\t' read -r unit type extra; do
    is_core_service "$unit" && continue
    if [[ "$type" == oneshot ]]; then
      continue
    fi
    state="$(active_state "$unit")" || return 69
    [[ "$state" == active ]] && continue
    if [[ "$type" == timer ]]; then timers+=("$unit"); else services+=("$unit"); fi
  done < "$ACTIVE_STATE_FILE"
  (( ${#services[@]} == 0 )) || systemctl_bounded --no-block start "${services[@]}"
  for unit in "${services[@]}"; do
    wait_for_unit_state "$unit" active || return $?
  done
  (( ${#timers[@]} == 0 )) || systemctl_bounded --no-block start "${timers[@]}"
  for unit in "${timers[@]}"; do
    wait_for_unit_state "$unit" active || return $?
  done
  : > "$RESTORED_STATE_FILE"
  chmod 0600 "$RESTORED_STATE_FILE"
  "$SYNC" -f "$RESTORED_STATE_FILE"
  verify_restored_state
}

commit_transition() {
  verify_restored_state
  "$RM" -f "$RESTORED_STATE_FILE" "$ACTIVE_STATE_FILE" "$INVENTORY_FILE"
  "$SYNC" -f "$(dirname "$ACTIVE_STATE_FILE")"
}

cancel_radon_jobs() {
  local jobs id unit
  local job_ids=()
  jobs="$(systemctl_bounded list-jobs --all --no-legend --no-pager --plain)" || return 1
  while read -r id unit _rest; do
    [[ "$id" =~ ^[0-9]+$ ]] || continue
    inventory_unit_is_valid "$unit" || continue
    job_ids+=("$id")
  done <<< "$jobs"
  (( ${#job_ids[@]} == 0 )) || systemctl_bounded cancel "${job_ids[@]}"
}

root_group_has_running_processes() {
  local pgid="$1"
  ps -axo pgid=,stat= | awk -v target="$pgid" '
    $1 == target && $2 !~ /^Z/ { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

resolve_root_action_group() {
  local candidate
  local attempt=1
  while (( attempt <= 100 )); do
    candidate="$(ps -o pgid= -p "$ROOT_ACTION_PID" 2>/dev/null | tr -d ' ')"
    if [[ "$candidate" == "$ROOT_ACTION_PID" ]]; then
      ROOT_ACTION_PGID="$candidate"
      return 0
    fi
    kill -0 "$ROOT_ACTION_PID" 2>/dev/null || return 1
    attempt=$((attempt + 1))
    "$SLEEP" 0.01
  done
  return 1
}

terminate_root_action_group() {
  local deadline
  [[ -n "${ROOT_ACTION_PID:-}" ]] || return 0
  [[ -n "${ROOT_ACTION_PGID:-}" ]] || resolve_root_action_group || true
  if [[ -n "${ROOT_ACTION_PGID:-}" ]]; then
    kill -TERM -- "-${ROOT_ACTION_PGID}" 2>/dev/null || true
    deadline=$((SECONDS + ROOT_KILL_AFTER))
    while root_group_has_running_processes "$ROOT_ACTION_PGID" \
      && (( SECONDS < deadline )); do
      "$SLEEP" 0.1
    done
    if root_group_has_running_processes "$ROOT_ACTION_PGID"; then
      kill -KILL -- "-${ROOT_ACTION_PGID}" 2>/dev/null || true
    fi
  else
    kill -KILL "$ROOT_ACTION_PID" 2>/dev/null || true
  fi
  wait "$ROOT_ACTION_PID" 2>/dev/null || true
  ROOT_ACTION_PID=""
  ROOT_ACTION_PGID=""
}

handle_root_supervisor_signal() {
  local signal_name="$1"
  local exit_code=143
  [[ "$signal_name" == INT ]] && exit_code=130
  [[ "$signal_name" == HUP ]] && exit_code=129
  trap '' TERM INT HUP
  terminate_root_action_group
  cancel_radon_jobs || true
  exit "$exit_code"
}

root_action_timeout() {
  case "$1" in
    stop-clean|restart-managed|recover)
      printf '%s\n' "$ROOT_MUTATION_ACTION_TIMEOUT"
      ;;
    verify-restored|verify-control-plane)
      printf '%s\n' "$ROOT_VERIFY_ACTION_TIMEOUT"
      ;;
    commit-transition)
      printf '%s\n' "$ROOT_COMMIT_ACTION_TIMEOUT"
      ;;
    *)
      return 64
      ;;
  esac
}

supervise_root_action() {
  local action_timeout
  action_timeout="$(root_action_timeout "$1")" || return $?
  [[ "$action_timeout" =~ ^[1-9][0-9]*$ ]] || return 64
  local deadline=$((SECONDS + action_timeout))
  local exit_code=0

  ROOT_ACTION_PID=""
  ROOT_ACTION_PGID=""
  trap 'handle_root_supervisor_signal TERM' TERM
  trap 'handle_root_supervisor_signal INT' INT
  trap 'handle_root_supervisor_signal HUP' HUP
  "$SESSION_PYTHON" -c '
import os
import sys
os.setsid()
os.environ["RADON_DEPLOY_ROOT_INTERNAL"] = "1"
os.execv(sys.argv[2], sys.argv[2:])
' -- "$0" "$@" &
  ROOT_ACTION_PID=$!
  resolve_root_action_group || true
  while kill -0 "$ROOT_ACTION_PID" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      terminate_root_action_group
      cancel_radon_jobs || true
      trap - TERM INT HUP
      return 124
    fi
    "$SLEEP" 0.1
  done
  if wait "$ROOT_ACTION_PID"; then exit_code=0; else exit_code=$?; fi
  ROOT_ACTION_PID=""
  ROOT_ACTION_PGID=""
  if (( exit_code != 0 )); then cancel_radon_jobs || true; fi
  trap - TERM INT HUP
  return "$exit_code"
}

if [[ "${RADON_DEPLOY_ROOT_INTERNAL:-0}" != "1" ]]; then
  prepare_state_dir
  exec 8>"$ROOT_LOCK_FILE"
  if ! "$FLOCK" -w "$ROOT_LOCK_WAIT" 8; then
    echo "timed out waiting for the root deploy lifecycle lock" >&2
    exit 70
  fi
  supervise_root_action "$@"
  exit $?
fi

case "$1" in
  stop-clean)
    snapshot_active_units
    stop_release_consumers
    "$RM" -f "${REPLICA_FILES[@]}"
    ;;
  restart-managed)
    reset_core_failures
    systemctl_bounded --no-block restart "${CORE_SERVICES[@]}"
    for unit in "${CORE_SERVICES[@]}"; do
      wait_for_unit_state "$unit" active
    done
    resume_active_snapshot
    ;;
  recover)
    reset_core_failures
    systemctl_bounded --no-block start "${CORE_SERVICES[@]}"
    for unit in "${CORE_SERVICES[@]}"; do
      wait_for_unit_state "$unit" active
    done
    resume_active_snapshot
    ;;
  verify-restored)
    verify_restored_state
    ;;
  verify-control-plane)
    verify_control_plane
    ;;
  commit-transition)
    commit_transition
    ;;
  *)
    echo "unknown deploy-root action" >&2
    exit 64
    ;;
esac
