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
  scripts/drift_audit.py
  scripts/disk_cleanup.py
  scripts/radon-app-runtime.sh
  scripts/radon-docker-gw.sh
  docker-compose.yml
  config/sudoers.d/radon-deploy
  config/sudoers.d/radon-monitor
  config/sudoers.d/radon-ops
  config/sudoers.d/radon-caddy
  config/polkit/50-radon-services.rules
  services/radon-health.service
  services/radon-ib-gateway-preheld-restart.service
  services/radon-ib-watchdog.service
  services/radon-ib-watchdog.timer
  services/radon-ib-gateway.service
  services/radon-ib-gateway-remote.service
  services/radon-api.service
  services/radon-monitor.service
  services/radon-relay.service
  services/radon-portfolio-sync.service
  services/radon-portfolio-sync.timer
  services/radon-refresh.service
  services/radon-refresh.timer
  services/radon-db-backup.service
  services/radon-db-backup.timer
  services/radon-disk-cleanup.service
  services/radon-disk-cleanup.timer
  services/radon-drift-audit.service
  services/radon-drift-audit.timer
  services/radon-nextjs-db-watchdog.service
  services/radon-nextjs-db-watchdog.timer
  services/radon-api.service.d/runtime-container.conf
  services/radon-nextjs.service.d/runtime-container.conf
  services/radon-relay.service.d/runtime-container.conf
  services/radon-monitor.service.d/runtime-container.conf
  services/radon-newsfeed.service.d/runtime-container.conf
)
readonly -a CONTROL_PLANE_TARGETS=(
  /usr/local/sbin/radon-deploy-root
  /usr/local/bin/radon-ib-gateway-control
  /usr/local/bin/radon
  /usr/local/lib/radon/drift_audit.py
  /usr/local/lib/radon/disk_cleanup.py
  /usr/local/sbin/radon-app-runtime
  /usr/local/sbin/radon-docker-gw
  /etc/radon/ib-gateway-compose.yml
  /etc/sudoers.d/radon-deploy
  /etc/sudoers.d/radon-monitor
  /etc/sudoers.d/radon-ops
  /etc/sudoers.d/radon-caddy
  /etc/polkit-1/rules.d/50-radon-services.rules
  /etc/systemd/system/radon-health.service
  /etc/systemd/system/radon-ib-gateway-preheld-restart.service
  /etc/systemd/system/radon-ib-watchdog.service
  /etc/systemd/system/radon-ib-watchdog.timer
  /etc/systemd/system/radon-ib-gateway.service
  /etc/systemd/system/radon-ib-gateway-remote.service
  /etc/systemd/system/radon-api.service
  /etc/systemd/system/radon-monitor.service
  /etc/systemd/system/radon-relay.service
  /etc/systemd/system/radon-portfolio-sync.service
  /etc/systemd/system/radon-portfolio-sync.timer
  /etc/systemd/system/radon-refresh.service
  /etc/systemd/system/radon-refresh.timer
  /etc/systemd/system/radon-db-backup.service
  /etc/systemd/system/radon-db-backup.timer
  /etc/systemd/system/radon-disk-cleanup.service
  /etc/systemd/system/radon-disk-cleanup.timer
  /etc/systemd/system/radon-drift-audit.service
  /etc/systemd/system/radon-drift-audit.timer
  /etc/systemd/system/radon-nextjs-db-watchdog.service
  /etc/systemd/system/radon-nextjs-db-watchdog.timer
  /etc/systemd/system/radon-api.service.d/runtime-container.conf
  /etc/systemd/system/radon-nextjs.service.d/runtime-container.conf
  /etc/systemd/system/radon-relay.service.d/runtime-container.conf
  /etc/systemd/system/radon-monitor.service.d/runtime-container.conf
  /etc/systemd/system/radon-newsfeed.service.d/runtime-container.conf
)
readonly -a CONTROL_PLANE_MODES=(
  755 755 755 644 644 755 755 644
  440 440 440 440
  644
  644 644 644 644 644 644 644 644 644 644 644 644 644 644 644 644 644 644 644 644 644
  644 644 644 644 644
)

if [[ "${RADON_DEPLOY_HELPER_TEST_MODE:-0}" == "1" ]]; then
  readonly HELPER_TEST_MODE=1
  readonly STATE_DIR="$(dirname "${RADON_TEST_ACTIVE_STATE_FILE:?test active-state file is required}")"
  readonly SYSTEMCTL="${RADON_TEST_SYSTEMCTL:?test systemctl is required}"
  readonly SYSTEMD_ANALYZE="${RADON_TEST_SYSTEMD_ANALYZE:-}"
  readonly RM="${RADON_TEST_RM:?test rm is required}"
  readonly SYNC="${RADON_TEST_SYNC:?test sync is required}"
  readonly ACTIVE_STATE_FILE="${RADON_TEST_ACTIVE_STATE_FILE}"
  readonly FLOCK="${RADON_TEST_FLOCK:-/usr/bin/true}"
  readonly ROOT_LOCK_FILE="${RADON_TEST_ROOT_LOCK_FILE:-${ACTIVE_STATE_FILE}.lock}"
  readonly TIMEOUT="${RADON_TEST_TIMEOUT:-}"
  readonly ROOT_MUTATION_ACTION_TIMEOUT="${RADON_TEST_ROOT_MUTATION_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-180}}"
  readonly ROOT_VERIFY_ACTION_TIMEOUT="${RADON_TEST_ROOT_VERIFY_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-30}}"
  readonly ROOT_COMMIT_ACTION_TIMEOUT="${RADON_TEST_ROOT_COMMIT_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-30}}"
  readonly ROOT_SYNC_ACTION_TIMEOUT="${RADON_TEST_ROOT_SYNC_ACTION_TIMEOUT:-${RADON_TEST_ROOT_ACTION_TIMEOUT:-300}}"
  readonly ROOT_KILL_AFTER="${RADON_TEST_ROOT_KILL_AFTER:-5}"
  readonly ROOT_LOCK_WAIT="${RADON_TEST_ROOT_LOCK_WAIT:-190}"
  readonly SESSION_PYTHON="${RADON_TEST_SESSION_PYTHON:-$(command -v python3)}"
  readonly INSTALL="${RADON_TEST_INSTALL:-$(command -v install)}"
  readonly CADDY_SOURCE="${RADON_TEST_CADDY_SOURCE:-}"
  readonly CADDY_CONFIG="${RADON_TEST_CADDY_CONFIG:-}"
  readonly CADDY_BIN="${RADON_TEST_CADDY_BIN:-}"
  readonly SYSTEMD_UNIT_DIR="${RADON_TEST_SYSTEMD_UNIT_DIR:-}"
  readonly GIT="${RADON_TEST_GIT:-$(command -v git)}"
  readonly TAR="${RADON_TEST_TAR:-$(command -v tar)}"
  readonly BOOTSTRAP_RUNNER="${RADON_TEST_BOOTSTRAP_RUNNER:-/bin/bash}"
  readonly RADON_GIT_DIR="${RADON_TEST_GIT_DIR:-}"
  readonly UNIT_REMOTE="${RADON_TEST_UNIT_REMOTE:-}"
  readonly SYSTEMD_DIR="${RADON_TEST_SYSTEMD_DIR:-}"
  readonly FORCE_GITHUB_REMOTE_CHECK="${RADON_TEST_FORCE_GITHUB_REMOTE_CHECK:-0}"
  readonly TEST_SYSTEMCTL_TIMEOUT="${RADON_TEST_SYSTEMCTL_TIMEOUT:-1}"
  readonly STATE_WAIT_SECONDS="${RADON_TEST_STATE_WAIT_SECONDS:-0}"
  readonly PREHELD_WAIT_SECONDS="${RADON_TEST_PREHELD_WAIT_SECONDS:-0}"
  readonly SLEEP="${RADON_TEST_SLEEP:-/bin/sleep}"
  readonly CONTROL_PLANE_ROOT="${RADON_TEST_CONTROL_PLANE_ROOT:-}"
  readonly CLOUD_SOURCE="${RADON_TEST_CLOUD_SOURCE:-${RADON_TEST_CLOUD_ROOT:-}}"
  readonly SHA256SUM="${RADON_TEST_SHA256SUM:-$(command -v sha256sum)}"
  readonly VISUDO="${RADON_TEST_VISUDO:-$(command -v visudo)}"
  readonly NODE="${RADON_TEST_NODE:-$(command -v node || true)}"
  readonly DOCKER="${RADON_TEST_DOCKER:-}"
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
  readonly SYSTEMD_ANALYZE=/usr/bin/systemd-analyze
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
  readonly CLOUD_SOURCE=/home/radon/radon/cloud
  readonly SHA256SUM=/usr/bin/sha256sum
  readonly VISUDO=/usr/sbin/visudo
  readonly NODE=/usr/bin/node
  readonly DOCKER=/usr/bin/docker
  readonly ROOT_MUTATION_ACTION_TIMEOUT=180
  readonly ROOT_VERIFY_ACTION_TIMEOUT=30
  readonly ROOT_COMMIT_ACTION_TIMEOUT=30
  readonly ROOT_SYNC_ACTION_TIMEOUT=300
  readonly ROOT_KILL_AFTER=5
  readonly ROOT_LOCK_WAIT=190
  readonly SESSION_PYTHON=/usr/bin/python3.13
  readonly CADDY_SOURCE=/home/radon/radon/cloud/caddy/Caddyfile
  readonly CADDY_CONFIG=/etc/caddy/Caddyfile
  readonly CADDY_BIN=/usr/bin/caddy
  readonly SYSTEMD_UNIT_DIR=/etc/systemd/system
  readonly GIT=/usr/bin/git
  readonly TAR=/usr/bin/tar
  readonly BOOTSTRAP_RUNNER=/bin/bash
  readonly RADON_GIT_DIR=/home/radon/radon/.git
  readonly UNIT_REMOTE=https://github.com/joemccann/radon.git
  readonly SYSTEMD_DIR=/etc/systemd/system
  readonly FORCE_GITHUB_REMOTE_CHECK=1
  readonly REPLICA_FILES=(
    /home/radon/radon/data/replica.db
    /home/radon/radon/data/replica.db-wal
    /home/radon/radon/data/replica.db-shm
    /home/radon/radon/data/replica.db-info
  )
fi
readonly RESTORED_STATE_FILE="${ACTIVE_STATE_FILE}.restored"
readonly INVENTORY_FILE="${ACTIVE_STATE_FILE}.inventory"
readonly UNITS_TRANSACTION_FILE="${STATE_DIR}/installed-units.transaction"
readonly UNIT_BACKUP_PREFIX="${STATE_DIR}/unit-backup."
readonly CONTROL_PLANE_MANIFEST="${CONTROL_PLANE_ROOT}/var/lib/radon/control-plane-manifest.sha256"
readonly CONTROL_PLANE_READY="${CONTROL_PLANE_ROOT}/var/lib/radon/control-plane-ready"
readonly GATEWAY_TRANSITION_FILE="${CONTROL_PLANE_ROOT}/var/lib/radon/ib-gateway-transition.json"
readonly LOGICAL_CONTROL_PLANE_MANIFEST="/var/lib/radon/control-plane-manifest.sha256"

# Where control-plane bytes are read from. refresh_control_plane repoints this
# at a root-owned staging copy of the git blobs for the deployed commit, so the
# radon-writable checkout is never the source of a root install.
CONTROL_PLANE_SOURCE_ROOT="$CLOUD_SOURCE"

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
  echo "usage: radon-deploy-root {stop-clean|restart-managed|recover|verify-restored|verify-control-plane|commit-transition|publish-caddy|install-units|revert-units|sync-scheduled-units|sync-control-plane|refresh-control-plane|refresh-control-plane-privileged}" >&2
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

read_host_role() {
  local role="${RADON_HOST_ROLE:-}"
  local envf="${RADON_DEPLOY_ENV_FILE:-${CONTROL_PLANE_ROOT}/etc/radon/env}"
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
    *)
      # REL-221 (R-590): fail-closed but never silent — an app host with a
      # corrupt env file stops skipping gateway units and the later
      # verify_control_plane failure reads as "target unavailable".
      if [[ -f "$envf" ]]; then
        echo "read_host_role: ${envf} exists but RADON_HOST_ROLE parsed to '${role}'; falling back to combined" >&2
      fi
      printf 'combined\n'
      ;;
  esac
}

app_skips_control_plane_source() {
  role_skips_control_plane_source "$1"
}

role_skips_control_plane_source() {
  local role
  role="$(read_host_role)"
  case "$1" in
    scripts/ib-gateway-control.sh|\
    services/radon-ib-gateway.service|\
    services/radon-ib-gateway-preheld-restart.service|\
    services/radon-ib-watchdog.service|\
    services/radon-ib-watchdog.timer)
      [[ "$role" == "app" ]] && return 0
      return 1 ;;
    services/radon-ib-gateway-remote.service)
      [[ "$role" == "app" ]] && return 0
      return 1 ;;
    *) return 1 ;;
  esac
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
    if app_skips_control_plane_source "$source_rel"; then
      if [[ ! -f "$installed_target" || -L "$installed_target" ]]; then
        index=$((index + 1))
        continue
      fi
    fi
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

# nextjs and newsfeed are CORE_SERVICES (stopped during install-units) but
# they are not control-plane and not the Gateway. Skipping them left
# EnvironmentFile/ExecStart edits as a root hand-copy. install-units now
# copies them while they are stopped; restart-managed starts the new file.
# api/relay/monitor remain bootstrap/refresh-owned via control_plane_unit_name.
unit_is_release_managed() {
  return 1
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
  close_unit_transaction
  "$RM" -f "$RESTORED_STATE_FILE" "$ACTIVE_STATE_FILE" "$INVENTORY_FILE"
  "$SYNC" -f "$(dirname "$ACTIVE_STATE_FILE")"
}

# Sized above the Caddyfile's grace_period (10s), which is what bounds how long
# the old servers take to drain. 15s left no headroom: the reload was still in
# flight when the timeout fired, the publish read that as a failure, and the
# rollback's second reload hit the same wall and left the admin endpoint wedged
# (2026-08-11). The ceiling still exists so a genuinely hung reload cannot stall
# the deploy lock forever.
reload_caddy() {
  if (( HELPER_TEST_MODE == 1 )); then
    "$SYSTEMCTL" reload caddy
  else
    "$TIMEOUT" --signal=TERM --kill-after=2s 45s "$SYSTEMCTL" reload caddy
  fi
}

# Staged 0600, never 0644. radon owns the source directory, so it can race the
# regular-file check below by flipping the source to `ln -sf /etc/shadow`
# between the test and this copy: install dereferences the symlink, and a
# world-readable candidate inside the radon-traversable /etc/caddy would then be
# readable during the validate window. 0600 makes the race unprofitable even
# when it is won.
stage_caddy_candidate() {
  local candidate="$1"
  if (( HELPER_TEST_MODE == 1 )); then
    "$INSTALL" -m 0600 "$CADDY_SOURCE" "$candidate"
  else
    "$INSTALL" -m 0600 -o root -g root "$CADDY_SOURCE" "$candidate"
  fi
}

# radon publishes Caddy config only through this fixed action. The retired
# `cp <checkout Caddyfile> /etc/caddy/Caddyfile` sudoers rule followed a
# symlinked source, so radon could read any root-only file out of the 0644
# destination, and it installed edge config that no validator ever saw.
publish_caddy() {
  local candidate rollback=""

  [[ -n "$CADDY_SOURCE" && -n "$CADDY_CONFIG" && -n "$CADDY_BIN" ]] || {
    echo "caddy publish paths are not configured" >&2
    return 78
  }
  [[ -f "$CADDY_SOURCE" && ! -L "$CADDY_SOURCE" ]] || {
    echo "caddy source is missing or is not a regular file: ${CADDY_SOURCE}" >&2
    return 66
  }
  if [[ -e "$CADDY_CONFIG" || -L "$CADDY_CONFIG" ]] && \
     [[ ! -f "$CADDY_CONFIG" || -L "$CADDY_CONFIG" ]]; then
    echo "live caddy configuration is not a regular file: ${CADDY_CONFIG}" >&2
    return 74
  fi

  candidate="$(mktemp "${CADDY_CONFIG}.candidate.XXXXXX")"
  if ! stage_caddy_candidate "$candidate"; then
    "$RM" -f "$candidate"
    echo "could not stage the caddy candidate" >&2
    return 73
  fi
  # Both streams are discarded on purpose. The caller is unprivileged and the
  # candidate is radon-writable, so a caddyfile adapter error -- which quotes
  # the offending token -- would relay the contents of anything the candidate
  # imported (`import /etc/shadow`) back to radon. The verdict is the output.
  if ! "$CADDY_BIN" validate --config "$candidate" --adapter caddyfile >/dev/null 2>&1; then
    "$RM" -f "$candidate"
    echo "caddy candidate validation failed; live configuration is unchanged" >&2
    return 65
  fi

  if [[ -f "$CADDY_CONFIG" ]]; then
    rollback="$(mktemp "${CADDY_CONFIG}.rollback.XXXXXX")"
    cp -a -- "$CADDY_CONFIG" "$rollback"
    "$SYNC" -f "$rollback"
  fi
  # Widened to the live config's own mode only now, after validation: caddy runs
  # unprivileged and must read this file. Content that failed validate never
  # reaches this line, so a symlinked-source race cannot publish /etc/shadow.
  chmod 0644 "$candidate"
  mv -f -- "$candidate" "$CADDY_CONFIG"
  "$SYNC" -f "$CADDY_CONFIG"

  if reload_caddy; then
    [[ -z "$rollback" ]] || "$RM" -f "$rollback"
    return 0
  fi
  if [[ -n "$rollback" ]]; then
    mv -f -- "$rollback" "$CADDY_CONFIG"
    "$SYNC" -f "$CADDY_CONFIG"
    reload_caddy || echo "known-good caddy reload reconciliation also failed" >&2
  else
    "$RM" -f "$CADDY_CONFIG"
  fi
  echo "caddy candidate reload failed; restored the known-good configuration" >&2
  return 75
}

# The timer-owned units the deploy is allowed to (re)install. The manifest
# digest is the review gate: a unit whose checkout content does not hash to
# its committed entry is unreviewed and is NOT installed -- that is exactly
# the pending-install window config/drift-allowlist.conf acknowledges.
# Control-plane units stay bootstrap-owned (listed in CONTROL_PLANE_SOURCES);
# gateway and beta units stay excluded. `enable --now` is issued for NEWLY
# installed timers only, never for a timer-owned .service: enabling both made
# every scheduled job fire at once (setup-vps.sh, enable_services).
#
# Same hardening as publish_caddy: root-side constant paths, a symlinked source
# is refused, the candidate is staged 0600 and its hash is checked against the
# manifest digest before promotion, so a radon-side swap of the source between
# the regular-file test and the copy can never land in /etc/systemd/system.
file_sha256() {
  local digest
  digest="$("$SHA256SUM" -- "$1")" || return 1
  printf '%s\n' "${digest%% *}"
}

stage_unit_candidate() {
  local source="$1" candidate="$2"
  if (( HELPER_TEST_MODE == 1 )); then
    "$INSTALL" -m 0600 "$source" "$candidate"
  else
    "$INSTALL" -m 0600 -o root -g root "$source" "$candidate"
  fi
}

# Installs the timer-owned units recorded in installed-units.sha256. Both the
# manifest and every unit body come from git objects at the GitHub main tip --
# never from /home/radon/radon, which anything running as `radon` can write
# (R-084). A unit that is not reachable from that tip is refused even when its
# digest matches; the checkout manifest is not evidence of review.
# `systemd-analyze verify` on the staged body, mirroring publish_caddy's
# `caddy validate` gate. The unit has to carry its real name for the
# verifier to parse it as that unit type.
unit_candidate_verifies() {
  local candidate="$1" unit="$2" scratch rc=0
  # REL-221 (R-589): "verifier unavailable" (no systemd-analyze on this host)
  # is a logged, documented proceed — the manifest-hash gate still stands.
  # A FAILED scratch setup is not: soft-passing it installed the candidate
  # unverified, so it skips the unit instead.
  if [[ -z "$SYSTEMD_ANALYZE" || ! -x "$SYSTEMD_ANALYZE" ]]; then
    echo "unit-verify: systemd-analyze unavailable; installing ${unit} on the manifest gate alone" >&2
    return 0
  fi
  if ! scratch="$(mktemp -d "${SYSTEMD_UNIT_DIR}/.verify.XXXXXX")"; then
    echo "unit-verify: scratch setup failed under ${SYSTEMD_UNIT_DIR}; refusing ${unit}" >&2
    return 1
  fi
  if cat -- "$candidate" > "${scratch}/${unit}"; then
    if [[ -n "${TIMEOUT:-}" ]]; then
      "$TIMEOUT" --signal=TERM --kill-after=2s 10s \
        "$SYSTEMD_ANALYZE" verify "${scratch}/${unit}" || rc=$?
    else
      "$SYSTEMD_ANALYZE" verify "${scratch}/${unit}" || rc=$?
    fi
  else
    rc=1
  fi
  "$RM" -rf "$scratch"
  return "$rc"
}

# T-104: install-units journals every unit it promotes so a rollback can put
# /etc/systemd/system back exactly. install-units cannot re-run against the
# restored SHA (it reads the GitHub main tip and refuses a HEAD behind it),
# and a unit absent from the restored manifest would never be revisited, so a
# Persistent=true timer enabled by a gate-failing deploy fired 203/EXEC
# forever. commit-transition closes the journal; revert-units consumes it.
begin_unit_transaction() {
  "$RM" -f -- "${UNIT_BACKUP_PREFIX}"*
  : > "$UNITS_TRANSACTION_FILE"
  chmod 0600 "$UNITS_TRANSACTION_FILE"
}

record_unit_change() {
  printf '%s\t%s\n' "$1" "$2" >> "$UNITS_TRANSACTION_FILE"
  "$SYNC" -f "$UNITS_TRANSACTION_FILE"
}

close_unit_transaction() {
  "$RM" -f -- "$UNITS_TRANSACTION_FILE" "${UNIT_BACKUP_PREFIX}"*
}

revert_installed_units() {
  local verb unit target backup
  local -a timers=() services=()
  local restored=0 changed=0
  local unit_re='^radon-[a-zA-Z0-9_.@-]+\.(service|timer)$'

  [[ -f "$UNITS_TRANSACTION_FILE" ]] || {
    printf 'revert-units: nothing to revert\n'
    return 0
  }
  while IFS=$'\t' read -r verb unit; do
    [[ "$unit" =~ $unit_re ]] || continue
    case "$verb" in
      installed)
        if [[ "$unit" == *.timer ]]; then timers+=("$unit"); else services+=("$unit"); fi
        ;;
      updated)
        backup="${UNIT_BACKUP_PREFIX}${unit}"
        [[ -f "$backup" ]] || {
          echo "revert-units: no saved body for ${unit}" >&2
          continue
        }
        target="${SYSTEMD_UNIT_DIR}/${unit}"
        mv -f -- "$backup" "$target" || return 73
        chmod 0644 "$target"
        "$SYNC" -f "$target"
        restored=$((restored + 1))
        changed=1
        ;;
    esac
  done < "$UNITS_TRANSACTION_FILE"

  # Timers first so a newly armed timer cannot re-queue the service it owns
  # while that service is being stopped.
  for unit in ${timers[@]+"${timers[@]}"}; do
    systemctl_bounded disable --now "$unit" || true
  done
  for unit in ${services[@]+"${services[@]}"}; do
    systemctl_bounded stop "$unit" || true
  done
  for unit in ${timers[@]+"${timers[@]}"} ${services[@]+"${services[@]}"}; do
    "$RM" -f -- "${SYSTEMD_UNIT_DIR}/${unit}"
    changed=1
  done
  if (( changed )); then
    systemctl_bounded daemon-reload || return $?
  fi
  if (( ${#timers[@]} + ${#services[@]} > 0 )); then
    # A removed unit that already failed 203/EXEC stays listed as failed
    # after the reload; clearing it is what stops the pages.
    systemctl_bounded reset-failed ${timers[@]+"${timers[@]}"} ${services[@]+"${services[@]}"} || true
  fi
  close_unit_transaction
  printf 'revert-units: removed=%d restored=%d\n' \
    $(( ${#timers[@]} + ${#services[@]} )) "$restored"
}

install_manifest_units() {
  local tip manifest line digest unit target candidate actual was_present backup
  local -a new_timers=() skipped=()
  local installed=0 updated=0 unchanged=0 changed=0 entry
  local manifest_line_re='^([0-9a-f]{64})[[:space:]][[:space:]](radon-[a-zA-Z0-9_.@-]+\.(service|timer))$'

  [[ -n "$SYSTEMD_UNIT_DIR" ]] || {
    echo "unit install paths are not configured" >&2
    return 78
  }
  [[ -d "$SYSTEMD_UNIT_DIR" && ! -L "$SYSTEMD_UNIT_DIR" ]] || {
    echo "systemd unit directory is missing: ${SYSTEMD_UNIT_DIR}" >&2
    return 66
  }

  tip="$(resolve_trusted_main_tip)" || return $?
  manifest="$(git_bounded --git-dir="$RADON_GIT_DIR" cat-file blob \
    "${tip}:cloud/config/installed-units.sha256")" || {
    echo "installed-units manifest is missing at main tip" >&2
    return 66
  }
  begin_unit_transaction

  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    if [[ ! "$line" =~ $manifest_line_re ]]; then
      echo "install-units: ignoring malformed manifest line" >&2
      continue
    fi
    digest="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    if control_plane_unit_name "$unit"; then
      skipped+=("${unit} (control-plane)")
      continue
    fi
    unit_is_excluded "$unit" && continue
    if unit_is_release_managed "$unit"; then
      skipped+=("${unit} (release-managed)")
      continue
    fi

    target="${SYSTEMD_UNIT_DIR}/${unit}"
    if [[ -L "$target" ]]; then
      skipped+=("${unit} (symlinked-target)")
      continue
    fi

    candidate="$(mktemp "${SYSTEMD_UNIT_DIR}/.${unit}.candidate.XXXXXX")"
    chmod 0600 "$candidate"
    if ! git_bounded --git-dir="$RADON_GIT_DIR" cat-file blob \
         "${tip}:cloud/services/${unit}" > "$candidate"; then
      "$RM" -f "$candidate"
      skipped+=("${unit} (not-at-main-tip)")
      continue
    fi
    actual="$(file_sha256 "$candidate")"
    if [[ "$actual" != "$digest" ]]; then
      "$RM" -f "$candidate"
      skipped+=("${unit} (manifest-mismatch)")
      continue
    fi

    was_present=0
    if [[ -f "$target" ]]; then
      was_present=1
      if [[ "$(file_sha256 "$target")" == "$digest" ]]; then
        "$RM" -f "$candidate"
        unchanged=$((unchanged + 1))
        continue
      fi
    fi

    chmod 0644 "$candidate"
    if ! unit_candidate_verifies "$candidate" "$unit"; then
      "$RM" -f "$candidate"
      skipped+=("${unit} (verify-failed)")
      continue
    fi
    # daemon-reload never fails on a malformed unit body, and
    # restore_release_backup does not reinstall unit files, so the only
    # recovery from a bad promotion is a root SSH. Keep the live body and
    # put it back if anything below fails.
    backup=""
    if (( was_present )); then
      backup="$(mktemp "${SYSTEMD_UNIT_DIR}/.${unit}.backup.XXXXXX")"
      if ! cat -- "$target" > "$backup"; then
        "$RM" -f "$backup" "$candidate"
        skipped+=("${unit} (snapshot-failed)")
        continue
      fi
    fi
    if ! mv -f -- "$candidate" "$target"; then
      [[ -n "$backup" ]] && mv -f -- "$backup" "$target"
      "$RM" -f "$candidate"
      skipped+=("${unit} (promote-failed)")
      continue
    fi
    "$SYNC" -f "$target"
    changed=1
    if (( was_present )); then
      mv -f -- "$backup" "${UNIT_BACKUP_PREFIX}${unit}"
      record_unit_change updated "$unit"
      updated=$((updated + 1))
    else
      record_unit_change installed "$unit"
      installed=$((installed + 1))
      [[ "$unit" == *.timer ]] && new_timers+=("$unit")
    fi
  done <<< "$manifest"

  if (( changed )); then
    systemctl_bounded daemon-reload || return $?
  fi
  # ${arr[@]+"${arr[@]}"}: an empty array is an unbound variable under set -u
  # on bash 3.2 (the test host).
  # R-184: `|| return $?` on the first failure silently skipped every LATER
  # new timer, and neither the deploy nor the drift audit noticed the ones
  # that were never attempted. Try them all, name the failures, then fail.
  local enable_failures=0
  for unit in ${new_timers[@]+"${new_timers[@]}"}; do
    if ! systemctl_bounded enable --now "$unit"; then
      printf 'install-units: enable failed %s\n' "$unit" >&2
      enable_failures=$((enable_failures + 1))
      continue
    fi
  done
  printf 'install-units: installed=%d updated=%d unchanged=%d skipped=%d\n' \
    "$installed" "$updated" "$unchanged" "${#skipped[@]}"
  for entry in ${skipped[@]+"${skipped[@]}"}; do
    printf 'install-units: skipped %s\n' "$entry"
  done
  if (( enable_failures )); then
    printf 'install-units: %d timer(s) failed to enable\n' "$enable_failures" >&2
    return 1
  fi
  return 0
}

github_origin_is_allowed() {
  case "$1" in
    git@github.com:joemccann/radon|git@github.com:joemccann/radon.git) return 0 ;;
    ssh://git@github.com/joemccann/radon|ssh://git@github.com/joemccann/radon.git) return 0 ;;
    https://github.com/joemccann/radon|https://github.com/joemccann/radon.git) return 0 ;;
    https://*@github.com/joemccann/radon|https://*@github.com/joemccann/radon.git) return 0 ;;
    *) return 1 ;;
  esac
}

control_plane_unit_name() {
  local name="$1" target base
  for target in "${CONTROL_PLANE_TARGETS[@]}"; do
    base="$(basename -- "$target")"
    [[ "$base" == "$name" ]] && return 0
    # The timer that schedules a control-plane service is control-plane too:
    # rewriting radon-drift-audit.timer hides an installed unit, and the same
    # trick on radon-ib-watchdog.timer disarms gateway supervision.
    [[ "$name" == *.timer && "${name%.timer}.service" == "$base" ]] && return 0
  done
  return 1
}

git_bounded() {
  if [[ -n "${TIMEOUT:-}" ]]; then
    "$TIMEOUT" --signal=TERM --kill-after=2s 20s "$GIT" "$@"
  else
    "$GIT" "$@"
  fi
}

# Allowlisted non-control-plane units only. Content comes from git objects at
# the GitHub main tip -- never from the radon-writable checkout -- and must
# match installed-units.sha256. Install is 0644 root:root plus one
# daemon-reload. No start/stop/enable.
# The one trust anchor for anything that installs a unit file: HEAD must be
# the GitHub main tip, and the tip commit must be in the local object store so
# every subsequent `cat-file blob` reads reviewed content.
resolve_trusted_main_tip() {
  local remote_sha local_sha

  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
        GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_EXEC_PATH GIT_SSH GIT_SSH_COMMAND \
        GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM \
        GIT_SSL_NO_VERIFY GIT_HTTP_USER_AGENT GIT_PROXY_COMMAND || true

  [[ -n "$GIT" && -n "$RADON_GIT_DIR" && -n "$UNIT_REMOTE" ]] || {
    echo "unit trust-source paths are not configured" >&2
    return 78
  }
  if [[ "$FORCE_GITHUB_REMOTE_CHECK" == "1" ]]; then
    github_origin_is_allowed "$UNIT_REMOTE" || {
      echo "unit remote is not the GitHub radon repo" >&2
      return 76
    }
  fi
  # Protocol v1: the refs arrive in the info/refs GET. From this host an
  # unauthenticated v2 ls-refs POST against the public repo answers 401
  # ("could not read Username"), which failed every deploy at the first real
  # sync-control-plane run (2026-09-02).
  remote_sha="$(git_bounded -c protocol.version=1 ls-remote --refs "$UNIT_REMOTE" refs/heads/main | awk '{print $1}')" || {
    echo "could not read the GitHub main tip" >&2
    return 69
  }
  [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "invalid GitHub main tip" >&2
    return 69
  }
  local_sha="$(git_bounded --git-dir="$RADON_GIT_DIR" rev-parse HEAD)" || {
    echo "could not read local HEAD" >&2
    return 66
  }
  [[ "$local_sha" == "$remote_sha" ]] || {
    echo "HEAD is not the GitHub main tip; refusing unit install" >&2
    return 76
  }
  git_bounded --git-dir="$RADON_GIT_DIR" cat-file -e "${remote_sha}^{commit}" || {
    echo "local git store is missing the main tip" >&2
    return 66
  }
  printf '%s\n' "$remote_sha"
}

# The GitHub main tip, required to be present in the local object store (the
# deploy job fetches it as radon before asking). Unlike
# resolve_trusted_main_tip this does NOT require HEAD to be the tip: it runs
# before promote, while HEAD is still the previous release.
resolve_fetched_main_tip() {
  local remote_sha

  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
        GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_EXEC_PATH GIT_SSH GIT_SSH_COMMAND \
        GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM \
        GIT_SSL_NO_VERIFY GIT_HTTP_USER_AGENT GIT_PROXY_COMMAND || true

  [[ -n "$GIT" && -n "$RADON_GIT_DIR" && -n "$UNIT_REMOTE" ]] || {
    echo "control-plane trust-source paths are not configured" >&2
    return 78
  }
  if [[ "$FORCE_GITHUB_REMOTE_CHECK" == "1" ]]; then
    github_origin_is_allowed "$UNIT_REMOTE" || {
      echo "control-plane remote is not the GitHub radon repo" >&2
      return 76
    }
  fi
  # Protocol v1: the refs arrive in the info/refs GET. From this host an
  # unauthenticated v2 ls-refs POST against the public repo answers 401
  # ("could not read Username"), which failed every deploy at the first real
  # sync-control-plane run (2026-09-02).
  remote_sha="$(git_bounded -c protocol.version=1 ls-remote --refs "$UNIT_REMOTE" refs/heads/main | awk '{print $1}')" || {
    echo "could not read the GitHub main tip" >&2
    return 69
  }
  [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "invalid GitHub main tip" >&2
    return 69
  }
  git_bounded --git-dir="$RADON_GIT_DIR" cat-file -e "${remote_sha}^{commit}" || {
    echo "local git store is missing the main tip; fetch origin main first" >&2
    return 66
  }
  printf '%s\n' "$remote_sha"
}

# Installs the control-plane bundle (helpers, sudoers, polkit, control-plane
# units and drop-ins) at the GitHub main tip by running that tip's own
# bootstrap-control-plane.sh from a root-owned extraction. Same trust anchor
# as install-units: content is read through the commit that GitHub reports as
# main, never from the radon-writable working tree. Bootstrap owns the deploy
# lock, refuses pending transitions, validates every artifact, installs
# atomically, and rewrites the readiness manifest; a bundle that is already
# current is a no-op. Before this verb every helper/sudoers/polkit edit, and
# every root hot-patch of a drop-in, blocked all deploys at preflight until
# a human ran bootstrap over SSH (2026-08-21, 2026-08-29). R-430.
sync_control_plane() {
  local tip workdir bootstrap rc

  tip="$(resolve_fetched_main_tip)" || return $?
  workdir="$(mktemp -d "${STATE_DIR}/control-plane-sync.XXXXXX")" || {
    echo "could not create a root-owned control-plane staging tree" >&2
    return 73
  }
  if ! git_bounded --git-dir="$RADON_GIT_DIR" archive --format=tar "$tip" cloud \
      | "$TAR" -x -C "$workdir"; then
    "$RM" -rf "$workdir"
    echo "could not extract cloud/ at the GitHub main tip ${tip}" >&2
    return 66
  fi
  bootstrap="${workdir}/cloud/scripts/bootstrap-control-plane.sh"
  if [[ ! -f "$bootstrap" || -L "$bootstrap" ]]; then
    "$RM" -rf "$workdir"
    echo "GitHub main tip ${tip} ships no control-plane bootstrap" >&2
    return 66
  fi
  printf 'sync-control-plane: reconciling the installed control plane with %s\n' "$tip"
  if RADON_BOOTSTRAP_CLOUD_ROOT="${workdir}/cloud" "$BOOTSTRAP_RUNNER" "$bootstrap"; then
    rc=0
  else
    rc=$?
  fi
  "$RM" -rf "$workdir"
  return "$rc"
}

sync_scheduled_units() {
  local remote_sha allowlist manifest unit expected actual live
  local tmp dest installed=0

  [[ -n "$SYSTEMD_DIR" ]] || {
    echo "scheduled unit sync paths are not configured" >&2
    return 78
  }
  remote_sha="$(resolve_trusted_main_tip)" || return $?

  allowlist="$(git_bounded --git-dir="$RADON_GIT_DIR" cat-file blob \
    "${remote_sha}:cloud/config/auto-sync-units.txt")" || {
    echo "auto-sync unit allowlist is missing at main tip" >&2
    return 66
  }
  manifest="$(git_bounded --git-dir="$RADON_GIT_DIR" cat-file blob \
    "${remote_sha}:cloud/config/installed-units.sha256")" || {
    echo "installed-units manifest is missing at main tip" >&2
    return 66
  }

  mkdir -p "$SYSTEMD_DIR"
  while IFS= read -r unit || [[ -n "$unit" ]]; do
    unit="${unit%%#*}"
    unit="${unit#"${unit%%[![:space:]]*}"}"
    unit="${unit%"${unit##*[![:space:]]}"}"
    [[ -n "$unit" ]] || continue

    [[ "$unit" =~ ^radon-[a-z0-9][a-z0-9.-]*\.(service|timer)$ ]] || {
      echo "refusing unsafe scheduled unit name: ${unit}" >&2
      return 64
    }
    if control_plane_unit_name "$unit"; then
      echo "refusing to sync a control-plane unit: ${unit}" >&2
      return 64
    fi

    expected="$(printf '%s\n' "$manifest" | awk -v n="$unit" '
      $2 == n { print $1; found = 1 }
      END { exit(found ? 0 : 1) }
    ')" || {
      echo "allowlisted unit is missing from the install manifest: ${unit}" >&2
      return 66
    }
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
      echo "invalid manifest digest for ${unit}" >&2
      return 66
    }

    dest="${SYSTEMD_DIR}/${unit}"
    if [[ -e "$dest" || -L "$dest" ]] && [[ ! -f "$dest" || -L "$dest" ]]; then
      echo "live unit path is not a regular file: ${dest}" >&2
      return 74
    fi

    tmp="$(mktemp "${STATE_DIR}/scheduled-unit.XXXXXX")"
    if ! git_bounded --git-dir="$RADON_GIT_DIR" cat-file blob \
         "${remote_sha}:cloud/services/${unit}" > "$tmp"; then
      "$RM" -f "$tmp"
      echo "allowlisted unit blob is missing at main tip: ${unit}" >&2
      return 66
    fi
    chmod 0600 "$tmp"
    actual="$("$SHA256SUM" "$tmp" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
      "$RM" -f "$tmp"
      echo "allowlisted unit does not match the install manifest: ${unit}" >&2
      return 65
    fi

    if [[ -f "$dest" ]]; then
      live="$("$SHA256SUM" "$dest" | awk '{print $1}')"
      if [[ "$live" == "$actual" ]]; then
        "$RM" -f "$tmp"
        continue
      fi
    fi

    if (( HELPER_TEST_MODE == 1 )); then
      "$INSTALL" -m 0644 "$tmp" "$dest" || { "$RM" -f "$tmp"; return 73; }
    else
      "$INSTALL" -m 0644 -o root -g root "$tmp" "$dest" || { "$RM" -f "$tmp"; return 73; }
    fi
    "$RM" -f "$tmp"
    installed=1
  done <<< "$allowlist"

  if (( installed == 1 )); then
    systemctl_bounded daemon-reload || {
      echo "daemon-reload failed after scheduled unit sync" >&2
      return 75
    }
  fi
  return 0
}

# Drop-in content gate. Mirrors the `dropin` validator in
# bootstrap-control-plane.sh; cloud/tests/test_rel133_control_plane_recovery.py
# pins the two against each other so they cannot drift. R-394.
dropin_body_is_valid() {
  local path="$1" label="${2:-$1}"
  grep -qE '^Type=(simple|notify)$' "$path" || {
    echo "drop-in must set Type=simple or Type=notify: ${label}" >&2
    return 1
  }
  grep -q '^ExecStart=/usr/local/sbin/radon-app-runtime run %n$' "$path" || {
    echo "drop-in must ExecStart radon-app-runtime: ${label}" >&2
    return 1
  }
  grep -q '^ExecStartPre=$' "$path" || {
    echo "drop-in must reset ExecStartPre: ${label}" >&2
    return 1
  }
  if grep -q 'radon-ib-gateway' "$path"; then
    echo "drop-in must not mention radon-ib-gateway: ${label}" >&2
    return 1
  fi
  if grep -qE '^Exec[A-Za-z]*=[^ ]*/home/radon' "$path"; then
    echo "drop-in must not execute from /home/radon: ${label}" >&2
    return 1
  fi
  return 0
}

refresh_install_file() {
  local source="$1"
  local dest="$2"
  local mode="$3"
  local dest_dir candidate
  dest_dir="$(dirname -- "$dest")"
  mkdir -p "$dest_dir" || return 73
  if [[ -e "$dest" || -L "$dest" ]] && [[ ! -f "$dest" || -L "$dest" ]]; then
    echo "live control-plane path is not a regular file: ${dest}" >&2
    return 74
  fi
  candidate="$(mktemp "${dest_dir}/.radon-refresh.XXXXXX")"
  if (( HELPER_TEST_MODE == 1 )); then
    if ! "$INSTALL" -m "$mode" "$source" "$candidate"; then
      "$RM" -f "$candidate"
      return 73
    fi
  else
    if ! "$INSTALL" -m "$mode" -o root -g root "$source" "$candidate"; then
      "$RM" -f "$candidate"
      return 73
    fi
  fi
  case "$dest" in
    */sudoers.d/*)
      if [[ -z "$VISUDO" ]] || ! "$VISUDO" -cf "$candidate" >/dev/null; then
        echo "sudoers validation failed: ${dest}" >&2
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
    */radon-deploy-root|*/radon-ib-gateway-control|*/usr/local/bin/radon|*/radon-app-runtime|*/radon-docker-gw)
      if ! bash -n "$candidate"; then
        echo "shell syntax validation failed: ${dest}" >&2
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
    # The arms below mirror bootstrap-control-plane.sh's KINDS table. This
    # path runs on every deploy with the app tier STOPPED and used to install
    # the app runtime, polkit rules, python helpers and control-plane units
    # unvalidated; test_refresh_control_plane pins one arm per target. R-437.
    # The Gateway compose body root acts on. It is a control-plane file for
    # exactly one reason: radon-docker-gw runs it as root, so it must not be
    # readable out of the radon-writable checkout. Same shape as publish_caddy's
    # `caddy validate` gate.
    */ib-gateway-compose.yml)
      # `docker compose config` would have to resolve env_file and the whole
      # variable set, which is not available at install time. Gate on the two
      # things that make this body safe for root to run instead: it declares
      # the pinned container, and it does not ask for privilege or the host
      # filesystem. Provenance (the git blob at the deployed commit) is the
      # real integrity gate.
      if ! compose_body_is_valid "$candidate" "$dest"; then
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
    */polkit-1/rules.d/*.rules)
      if [[ -z "$NODE" || ! -x "$NODE" ]] || ! "$NODE" --check < "$candidate"; then
        echo "polkit syntax validation failed: ${dest}" >&2
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
    */lib/radon/*.py)
      # Parse only; importing would execute candidate code as root.
      if ! "$SESSION_PYTHON" -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])' "$candidate"; then
        echo "python syntax validation failed: ${dest}" >&2
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
    */systemd/system/radon-*.service|*/systemd/system/radon-*.timer)
      if ! unit_candidate_verifies "$candidate" "$(basename -- "$dest")"; then
        echo "systemd unit validation failed: ${dest}" >&2
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
    */radon-*.service.d/*.conf)
      # An operator who followed the (now deleted) .conf.example instructions
      # and hand-copied a drop-in to this exact path has it overwritten with no
      # backup and no journal entry, unlike install_manifest_units'
      # UNIT_BACKUP_PREFIX snapshot. Record what was replaced so the change is
      # at least attributable in the deploy log. R-420.
      if [[ -f "$dest" && ! -L "$dest" ]]; then
        local previous
        previous="$(file_sha256 "$dest" 2>/dev/null || echo unknown)"
        if [[ "$previous" != "$(file_sha256 "$candidate" 2>/dev/null || echo unknown)" ]]; then
          echo "replacing drop-in ${dest} (previous digest ${previous})" >&2
        fi
      fi
      # The same rules bootstrap-control-plane.sh enforces. That path almost
      # never runs; THIS one installs the drop-in on every deploy, out of the
      # radon-writable /home/radon/radon/cloud, and had no arm at all. A drop-in
      # is the root-run unit definition of all five app services. R-394.
      if ! dropin_body_is_valid "$candidate" "$dest"; then
        "$RM" -f "$candidate"
        return 73
      fi
      ;;
  esac
  if ! mv -f -- "$candidate" "$dest"; then
    "$RM" -f "$candidate"
    return 73
  fi
  "$SYNC" -f "$dest"
}

# Root runs this compose body. It must name the Gateway container and must
# not request privilege, a host mount, or a widened runtime. Kept byte-for-
# byte identical in deploy-root-helper.sh, bootstrap-control-plane.sh and
# setup-vps.sh; cloud/tests/test_rel234_compose_gate.py pins the copies
# against each other. Deny-list first (deterministic, no tooling), then a
# render check where docker compose is available. R-635.
compose_body_is_valid() {
  local candidate="$1" dest="$2"
  local body render_env

  # Comments must never satisfy or trip a structural gate.
  body="$(grep -Ev '^[[:space:]]*#' "$candidate")" || body=""

  printf '%s\n' "$body" | grep -Eq '^services:' || {
    echo "compose validation failed: ${dest} declares no services" >&2
    return 1
  }
  printf '%s\n' "$body" | grep -Eq '^[[:space:]]+container_name:[[:space:]]*ib-gateway[[:space:]]*$' || {
    echo "compose validation failed: ${dest} does not pin container_name ib-gateway" >&2
    return 1
  }
  if printf '%s\n' "$body" | grep -Eq '^[[:space:]]*privileged:[[:space:]]*true'; then
    echo "compose validation failed: ${dest} requests privileged" >&2
    return 1
  fi
  # The Gateway body's only volume is the named ib-config volume, so any
  # short-form entry whose source is an absolute host path (quoted or not)
  # is a host mount root must not perform. There is no allowlist.
  if printf '%s\n' "$body" | grep -Eq "^[[:space:]]*-[[:space:]]*[\"']?/"; then
    echo "compose validation failed: ${dest} binds an absolute host path" >&2
    return 1
  fi
  if printf '%s\n' "$body" | grep -Eq "type:[[:space:]]*[\"']?bind"; then
    echo "compose validation failed: ${dest} declares a long-form bind mount" >&2
    return 1
  fi
  if printf '%s\n' "$body" | grep -Eq "source:[[:space:]]*[\"']?/"; then
    echo "compose validation failed: ${dest} declares an absolute long-form source" >&2
    return 1
  fi
  if printf '%s\n' "$body" | grep -q 'docker\.sock'; then
    echo "compose validation failed: ${dest} mounts the docker socket" >&2
    return 1
  fi
  # R-668 (REL-249): every host-namespace join is denied, not only pid — ipc,
  # userns_mode, uts and cgroup widen the container's runtime the same way.
  if printf '%s\n' "$body" | grep -Eq '^[[:space:]]*(pid|ipc|userns_mode|uts|cgroup):'; then
    echo "compose validation failed: ${dest} joins a host namespace (pid/ipc/userns_mode/uts/cgroup)" >&2
    return 1
  fi
  if printf '%s\n' "$body" | grep -Eq "^[[:space:]]*network_mode:[[:space:]]*[\"']?host"; then
    echo "compose validation failed: ${dest} requests host networking" >&2
    return 1
  fi
  if printf '%s\n' "$body" | grep -Eq '^[[:space:]]*(cap_add|devices):'; then
    echo "compose validation failed: ${dest} adds capabilities or devices" >&2
    return 1
  fi
  if printf '%s\n' "$body" | grep -Eq "^[[:space:]]*user:[[:space:]]*[\"']?(root|0)[\"']?[[:space:]]*$"; then
    echo "compose validation failed: ${dest} runs as root in the container" >&2
    return 1
  fi
  # security_opt may only tighten: block form, no-new-privileges:true entries
  # and nothing else. The inline form is refused outright.
  if printf '%s\n' "$body" | grep -Eq '^[[:space:]]*security_opt:[[:space:]]*[^[:space:]]'; then
    echo "compose validation failed: ${dest} uses inline security_opt" >&2
    return 1
  fi
  if ! printf '%s\n' "$body" | awk '
    /^[[:space:]]*security_opt:[[:space:]]*$/ { inso = 1; next }
    inso == 1 && /^[[:space:]]*-[[:space:]]*/ {
      entry = $0
      sub(/^[[:space:]]*-[[:space:]]*/, "", entry)
      gsub(/[" \t]/, "", entry)
      if (entry != "no-new-privileges:true") { bad = 1; exit }
      next
    }
    inso == 1 { inso = 0 }
    END { exit bad }
  '; then
    echo "compose validation failed: ${dest} sets a security_opt beyond no-new-privileges" >&2
    return 1
  fi
  # Render check where the tooling exists (the deploy host has it; a test
  # host may not). RADON_COMPOSE_ENV_FILE is pointed at an empty file so the
  # env_file directive resolves without reading production secrets.
  if command -v docker >/dev/null 2>&1 && \
     docker compose version >/dev/null 2>&1; then
    render_env="$(mktemp)" || {
      echo "compose validation failed: ${dest} render env could not be created" >&2
      return 1
    }
    if ! RADON_COMPOSE_ENV_FILE="$render_env" docker compose \
        -f "$candidate" --project-name radon-compose-validate \
        config --quiet >/dev/null 2>&1; then
      rm -f -- "$render_env"
      echo "compose validation failed: ${dest} does not render with docker compose config" >&2
      return 1
    fi
    rm -f -- "$render_env"
  fi
  return 0
}

write_control_plane_manifest_and_ready() {
  local index source_rel dest digest tmp_manifest tmp_ready state_dir
  state_dir="$(dirname -- "$CONTROL_PLANE_MANIFEST")"
  mkdir -p "$state_dir" || return 73
  tmp_manifest="$(mktemp "${CONTROL_PLANE_MANIFEST}.XXXXXX")"
  for index in "${!CONTROL_PLANE_SOURCES[@]}"; do
    source_rel="${CONTROL_PLANE_SOURCES[$index]}"
    dest="${CONTROL_PLANE_ROOT}${CONTROL_PLANE_TARGETS[$index]}"
    if app_skips_control_plane_source "$source_rel" && \
       [[ ! -f "$dest" || -L "$dest" ]]; then
      digest="$(file_sha256 "${CONTROL_PLANE_SOURCE_ROOT}/${source_rel}")" || {
        "$RM" -f "$tmp_manifest"
        return 73
      }
      printf '%s  %s -> %s\n' "$digest" "$source_rel" "${CONTROL_PLANE_TARGETS[$index]}" \
        >> "$tmp_manifest"
      continue
    fi
    if [[ ! -f "$dest" || -L "$dest" ]]; then
      # Same rollback shape as the skip in refresh_control_plane: a drop-in that
      # this checkout does not ship, and that was never installed, is not a
      # manifest entry. R-380.
      case "$source_rel" in
        services/*.service.d/*.conf)
          [[ -f "${CONTROL_PLANE_SOURCE_ROOT}/${source_rel}" ]] || continue
          ;;
      esac
      "$RM" -f "$tmp_manifest"
      echo "installed control-plane target is unavailable after refresh: ${CONTROL_PLANE_TARGETS[$index]}" >&2
      return 73
    fi
    digest="$(file_sha256 "$dest")" || {
      "$RM" -f "$tmp_manifest"
      return 73
    }
    printf '%s  %s -> %s\n' "$digest" "$source_rel" "${CONTROL_PLANE_TARGETS[$index]}" \
      >> "$tmp_manifest"
  done
  chmod 0644 "$tmp_manifest"
  mv -f -- "$tmp_manifest" "$CONTROL_PLANE_MANIFEST"
  "$SYNC" -f "$CONTROL_PLANE_MANIFEST"
  tmp_ready="$(mktemp "${CONTROL_PLANE_READY}.XXXXXX")"
  printf '%s  %s\n' "$(file_sha256 "$CONTROL_PLANE_MANIFEST")" \
    "$LOGICAL_CONTROL_PLANE_MANIFEST" > "$tmp_ready"
  chmod 0644 "$tmp_ready"
  mv -f -- "$tmp_ready" "$CONTROL_PLANE_READY"
  "$SYNC" -f "$CONTROL_PLANE_READY"
}

# The commit whose control-plane bytes this host may install: the local HEAD,
# required to be reachable from the GitHub main tip. Unlike
# resolve_trusted_main_tip this does NOT demand HEAD == tip, because a rollback
# deliberately runs an older release and must install THAT release's control
# plane, not the newest one. What it refuses is a commit GitHub main has never
# contained -- a local commit, an amended history, a rewritten branch -- which
# is what keeps the radon-writable checkout from being an input to a root
# install.
resolve_deployed_control_plane_commit() {
  local remote_sha local_sha

  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
        GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_EXEC_PATH GIT_SSH GIT_SSH_COMMAND \
        GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM \
        GIT_SSL_NO_VERIFY GIT_HTTP_USER_AGENT GIT_PROXY_COMMAND || true

  [[ -n "$GIT" && -n "$RADON_GIT_DIR" && -n "$UNIT_REMOTE" ]] || {
    echo "control-plane trust-source paths are not configured" >&2
    return 78
  }
  if [[ "$FORCE_GITHUB_REMOTE_CHECK" == "1" ]]; then
    github_origin_is_allowed "$UNIT_REMOTE" || {
      echo "control-plane remote is not the GitHub radon repo" >&2
      return 76
    }
  fi
  remote_sha="$(git_bounded -c protocol.version=1 ls-remote --refs "$UNIT_REMOTE" refs/heads/main | awk '{print $1}')" || {
    echo "could not read the GitHub main tip" >&2
    return 69
  }
  [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "invalid GitHub main tip" >&2
    return 69
  }
  local_sha="$(git_bounded --git-dir="$RADON_GIT_DIR" rev-parse HEAD)" || {
    echo "could not read local HEAD" >&2
    return 66
  }
  [[ "$local_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "invalid local HEAD" >&2
    return 66
  }
  # Ancestry can only be decided against an object this host actually has.
  # The deploy job fetches main as radon before asking; a missing tip is a
  # broken deploy, not a licence to install unreviewed bytes.
  git_bounded --git-dir="$RADON_GIT_DIR" cat-file -e "${remote_sha}^{commit}" || {
    echo "local git store is missing the main tip" >&2
    return 66
  }
  git_bounded --git-dir="$RADON_GIT_DIR" merge-base --is-ancestor \
    "$local_sha" "$remote_sha" || {
    echo "HEAD is not reachable from the GitHub main tip; refusing control-plane refresh" >&2
    return 76
  }
  printf '%s\n' "$local_sha"
}

# Materializes every control-plane source as a root-owned copy of the git blob
# at $commit. The checkout working tree is never read: the radon account can
# write it, and these bytes become /etc/sudoers.d, /usr/local/sbin and unit
# files. R-084, which install_manifest_units has always honoured and this path
# did not.
stage_control_plane_sources() {
  local commit="$1" staging="$2"
  local index source_rel staged

  for index in "${!CONTROL_PLANE_SOURCES[@]}"; do
    source_rel="${CONTROL_PLANE_SOURCES[$index]}"
    staged="${staging}/${source_rel}"
    mkdir -p "$(dirname -- "$staged")" || return 73
    # A source absent at this commit stays absent, so the existing
    # missing-source arms (drop-in rollback, app-role skip) still decide.
    if ! git_bounded --git-dir="$RADON_GIT_DIR" cat-file blob \
      "${commit}:cloud/${source_rel}" > "$staged" 2>/dev/null; then
      /bin/rm -f -- "$staged"
      continue
    fi
    chmod 0600 "$staged" || return 73
  done
}

# Unit-only refresh during deploy. Must not exec bootstrap-control-plane.sh:
# bootstrap refuses the in-flight app transition journal and the deploy lock.
# Pending Gateway transition is the only transition that blocks this path.
refresh_control_plane() {
  local privileged=0
  local commit staging rc=0

  [[ "${1:-}" == "privileged" ]] && privileged=1

  [[ -n "$CLOUD_SOURCE" && -d "$CLOUD_SOURCE" ]] || {
    echo "control-plane source tree is not configured" >&2
    return 78
  }

  if [[ -e "$GATEWAY_TRANSITION_FILE" || -L "$GATEWAY_TRANSITION_FILE" ]]; then
    echo "refusing control-plane refresh while a gateway transition is pending" >&2
    return 75
  fi

  commit="$(resolve_deployed_control_plane_commit)" || return $?
  mkdir -p "$STATE_DIR" || return 73
  staging="$(mktemp -d "${STATE_DIR}/control-plane-src.XXXXXX")" || return 73
  chmod 0700 "$staging"
  CONTROL_PLANE_SOURCE_ROOT="$staging"
  stage_control_plane_sources "$commit" "$staging" || rc=$?
  if (( rc == 0 )); then
    refresh_control_plane_staged "$privileged" || rc=$?
  fi
  /bin/rm -rf -- "$staging"
  CONTROL_PLANE_SOURCE_ROOT="$CLOUD_SOURCE"
  return "$rc"
}

refresh_control_plane_staged() {
  local privileged="$1"
  local index source_rel source dest installed_hash source_hash mode
  local -a unit_indexes=()
  local -a privileged_indexes=()

  for index in "${!CONTROL_PLANE_SOURCES[@]}"; do
    source_rel="${CONTROL_PLANE_SOURCES[$index]}"
    source="${CONTROL_PLANE_SOURCE_ROOT}/${source_rel}"
    dest="${CONTROL_PLANE_ROOT}${CONTROL_PLANE_TARGETS[$index]}"
    if app_skips_control_plane_source "$source_rel"; then
      if [[ -f "$dest" && ! -L "$dest" ]]; then
        "$RM" -f "$dest" || {
          echo "failed to strip app-role gateway artifact: ${dest}" >&2
          return 73
        }
      fi
      continue
    fi
    if [[ ! -f "$source" || -L "$source" ]]; then
      # A drop-in source is absent from every checkout before 702ae26a, and the
      # INSTALLED helper is always the newest one, so a rollback enumerates
      # sources the restored tree does not have. Aborting here left all five app
      # units stopped with no path back; skip and warn instead. R-380.
      case "$source_rel" in
        services/*.service.d/*.conf)
          echo "control-plane source absent, leaving installed copy in place: ${source_rel}" >&2
          continue
          ;;
      esac
      echo "control-plane source is missing or is not a regular file: ${source_rel}" >&2
      return 66
    fi
    source_hash="$(file_sha256 "$source")" || {
      echo "control-plane source is unreadable: ${source_rel}" >&2
      return 66
    }
    installed_hash=""
    if [[ -f "$dest" && ! -L "$dest" ]]; then
      installed_hash="$(file_sha256 "$dest")" || installed_hash=""
    fi
    [[ "$source_hash" == "$installed_hash" ]] && continue
    case "$source_rel" in
      services/*) unit_indexes+=("$index") ;;
      *) privileged_indexes+=("$index") ;;
    esac
  done

  if (( ${#privileged_indexes[@]} > 0 )) && (( privileged == 0 )); then
    echo "refresh-control-plane: privileged control-plane diffs require refresh-control-plane-privileged" >&2
    return 78
  fi

  if (( ${#unit_indexes[@]} == 0 && ${#privileged_indexes[@]} == 0 )); then
    printf 'refresh-control-plane: control-plane is current/unchanged\n'
    return 0
  fi

  for index in ${unit_indexes[@]+"${unit_indexes[@]}"}; do
    source_rel="${CONTROL_PLANE_SOURCES[$index]}"
    source="${CONTROL_PLANE_SOURCE_ROOT}/${source_rel}"
    dest="${CONTROL_PLANE_ROOT}${CONTROL_PLANE_TARGETS[$index]}"
    refresh_install_file "$source" "$dest" 0644 || return $?
    if [[ "$(file_sha256 "$dest")" != "$(file_sha256 "$source")" ]]; then
      echo "control-plane install verification failed: ${source_rel}" >&2
      return 73
    fi
  done

  if (( privileged == 1 )); then
    for index in ${privileged_indexes[@]+"${privileged_indexes[@]}"}; do
      source_rel="${CONTROL_PLANE_SOURCES[$index]}"
      source="${CONTROL_PLANE_SOURCE_ROOT}/${source_rel}"
      dest="${CONTROL_PLANE_ROOT}${CONTROL_PLANE_TARGETS[$index]}"
      mode="${CONTROL_PLANE_MODES[$index]}"
      refresh_install_file "$source" "$dest" "$mode" || return $?
      if [[ "$(file_sha256 "$dest")" != "$(file_sha256 "$source")" ]]; then
        echo "control-plane install verification failed: ${source_rel}" >&2
        return 73
      fi
    done
  fi

  write_control_plane_manifest_and_ready || return $?
  systemctl_bounded daemon-reload || {
    echo "daemon-reload failed after control-plane refresh" >&2
    return 75
  }
  return 0
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

# Only the release-lifecycle actions queue systemd jobs for radon-* units, so
# only their failures leave torn jobs worth cancelling (install-units joins
# them: `enable --now` on a new timer queues a start job). publish-caddy and
# sync-scheduled-units are reachable by the unprivileged account, so cancelling
# on a rejected Caddyfile or allowlist (exit 65) would let radon tear down an
# unrelated deploy's in-flight restarts.
action_queues_radon_jobs() {
  case "${1:-}" in
    stop-clean|restart-managed|recover|install-units|revert-units) return 0 ;;
    *) return 1 ;;
  esac
}

cancel_radon_jobs_for_action() {
  action_queues_radon_jobs "${ROOT_ACTION_NAME:-}" || return 0
  cancel_radon_jobs || true
}

handle_root_supervisor_signal() {
  local signal_name="$1"
  local exit_code=143
  [[ "$signal_name" == INT ]] && exit_code=130
  [[ "$signal_name" == HUP ]] && exit_code=129
  trap '' TERM INT HUP
  terminate_root_action_group
  cancel_radon_jobs_for_action
  exit "$exit_code"
}

root_action_timeout() {
  case "$1" in
    stop-clean|restart-managed|recover)
      printf '%s\n' "$ROOT_MUTATION_ACTION_TIMEOUT"
      ;;
    publish-caddy|sync-scheduled-units)
      # Bounded like a mutation (stage/install/reload) but deliberately
      # outside the release-lifecycle job-cancel class above. A rejected
      # allowlist or hash mismatch must not cancel an in-flight deploy.
      printf '%s\n' "$ROOT_MUTATION_ACTION_TIMEOUT"
      ;;
    install-units|revert-units)
      printf '%s\n' "$ROOT_MUTATION_ACTION_TIMEOUT"
      ;;
    refresh-control-plane|refresh-control-plane-privileged)
      printf '%s\n' "$ROOT_MUTATION_ACTION_TIMEOUT"
      ;;
    sync-control-plane)
      # git archive + bootstrap validation (systemd-analyze, visudo, node,
      # py_compile) for the whole bundle; not in the job-cancel class.
      printf '%s\n' "$ROOT_SYNC_ACTION_TIMEOUT"
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
  ROOT_ACTION_NAME="$1"
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
      cancel_radon_jobs_for_action
      trap - TERM INT HUP
      return 124
    fi
    "$SLEEP" 0.1
  done
  if wait "$ROOT_ACTION_PID"; then exit_code=0; else exit_code=$?; fi
  ROOT_ACTION_PID=""
  ROOT_ACTION_PGID=""
  if (( exit_code != 0 )); then cancel_radon_jobs_for_action; fi
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
  publish-caddy)
    publish_caddy
    ;;
  install-units)
    install_manifest_units
    ;;
  revert-units)
    revert_installed_units
    ;;
  sync-scheduled-units)
    sync_scheduled_units
    ;;
  sync-control-plane)
    sync_control_plane
    ;;
  refresh-control-plane)
    refresh_control_plane
    ;;
  refresh-control-plane-privileged)
    refresh_control_plane privileged
    ;;
  *)
    echo "unknown deploy-root action" >&2
    exit 64
    ;;
esac
