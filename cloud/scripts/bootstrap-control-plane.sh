#!/usr/bin/env bash
set -Eeuo pipefail

# Installs the privileged Radon control-plane bundle without changing process
# state. The shared deploy lock serializes this transaction with deploys and
# every authoritative Gateway lifecycle mutation.

umask 022

readonly TEST_MODE="${RADON_BOOTSTRAP_TEST_MODE:-0}"
readonly ALLOW_NONROOT="${RADON_BOOTSTRAP_ALLOW_NONROOT:-0}"
[[ "$TEST_MODE" == "0" || "$TEST_MODE" == "1" ]] || {
  printf 'REFUSING control-plane bootstrap: invalid test-mode flag\n' >&2
  exit 2
}
[[ "$ALLOW_NONROOT" == "0" || "$ALLOW_NONROOT" == "1" ]] || {
  printf 'REFUSING control-plane bootstrap: invalid non-root flag\n' >&2
  exit 2
}

die() {
  local message="$1"
  local status="${2:-1}"
  printf 'REFUSING control-plane bootstrap: %s\n' "$message" >&2
  exit "$status"
}

effective_uid="$EUID"
if [[ "$TEST_MODE" == "1" && "$EUID" -eq 0 && -n "${RADON_BOOTSTRAP_TEST_EUID:-}" ]]; then
  [[ "$RADON_BOOTSTRAP_TEST_EUID" =~ ^[0-9]+$ ]] || die "invalid test effective uid"
  effective_uid="$RADON_BOOTSTRAP_TEST_EUID"
fi
if [[ "$effective_uid" -ne 0 ]]; then
  if [[ "$TEST_MODE" != "1" || "$ALLOW_NONROOT" != "1" ]]; then
    die "must run as root" 77
  fi
fi

root_prefix="${RADON_BOOTSTRAP_ROOT:-}"
if [[ -n "$root_prefix" ]]; then
  [[ "$TEST_MODE" == "1" ]] || die "filesystem root override is test-only"
  [[ "$root_prefix" == /* && "$root_prefix" != "/" ]] || \
    die "test filesystem root must be an absolute non-root path"
  mkdir -p "$root_prefix" || die "cannot create isolated filesystem root"
  root_prefix="$(cd "$root_prefix" && pwd -P)"
  [[ "$root_prefix" != "/" ]] || die "test filesystem root resolves to the host root"
fi
readonly ROOT_PREFIX="$root_prefix"
if [[ "$TEST_MODE" == "1" && "$effective_uid" -ne 0 && -z "$ROOT_PREFIX" ]]; then
  die "non-root test mode requires an isolated filesystem root"
fi

root_path() {
  printf '%s%s\n' "$ROOT_PREFIX" "$1"
}

readonly CLOUD_ROOT_INPUT="${RADON_BOOTSTRAP_CLOUD_ROOT:-/home/radon/radon/cloud}"
[[ -d "$CLOUD_ROOT_INPUT" ]] || die "canonical cloud root is missing: $CLOUD_ROOT_INPUT"
readonly CLOUD_ROOT="$(cd "$CLOUD_ROOT_INPUT" && pwd -P)"

readonly FLOCK_BIN="${RADON_BOOTSTRAP_FLOCK_BIN:-flock}"
readonly VISUDO_BIN="${RADON_BOOTSTRAP_VISUDO_BIN:-visudo}"
readonly NODE_BIN="${RADON_BOOTSTRAP_NODE_BIN:-node}"
readonly SYSTEMD_ANALYZE_BIN="${RADON_BOOTSTRAP_SYSTEMD_ANALYZE_BIN:-systemd-analyze}"
readonly SYSTEMCTL_BIN="${RADON_BOOTSTRAP_SYSTEMCTL_BIN:-systemctl}"
readonly PYTHON_BIN="${RADON_BOOTSTRAP_PYTHON_BIN:-python3}"

for required_command in \
  "$FLOCK_BIN" "$VISUDO_BIN" "$NODE_BIN" \
  "$SYSTEMD_ANALYZE_BIN" "$SYSTEMCTL_BIN" "$PYTHON_BIN"; do
  command -v "$required_command" >/dev/null 2>&1 || \
    die "required validator is unavailable: $required_command"
done

readonly DEPLOY_LOCK_PATH="$(root_path /home/radon/.radon-deploy.lock)"
readonly APP_TRANSITION_PATH="$(root_path /home/radon/.radon-deploy-transition.json)"
readonly GATEWAY_TRANSITION_PATH="$(root_path /var/lib/radon/ib-gateway-transition.json)"
readonly ROOT_ACTIVE_UNITS_PATH="$(root_path /var/lib/radon/deploy/active-units)"
readonly ROOT_INVENTORY_PATH="${ROOT_ACTIVE_UNITS_PATH}.inventory"
readonly ROOT_RESTORED_PATH="${ROOT_ACTIVE_UNITS_PATH}.restored"
readonly MANIFEST_PATH="$(root_path /var/lib/radon/control-plane-manifest.sha256)"
readonly READY_PATH="$(root_path /var/lib/radon/control-plane-ready)"
readonly STATE_DIR="$(dirname "$MANIFEST_PATH")"
readonly LOGICAL_MANIFEST_PATH="/var/lib/radon/control-plane-manifest.sha256"

mkdir -p "$(dirname "$DEPLOY_LOCK_PATH")"
if [[ ! -e "$DEPLOY_LOCK_PATH" && ! -L "$DEPLOY_LOCK_PATH" ]]; then
  if [[ "$TEST_MODE" == "1" ]]; then
    install -m 0600 /dev/null "$DEPLOY_LOCK_PATH"
  else
    install -m 0600 -o radon -g radon /dev/null "$DEPLOY_LOCK_PATH"
  fi
fi
[[ -f "$DEPLOY_LOCK_PATH" && ! -L "$DEPLOY_LOCK_PATH" ]] || \
  die "deploy/control lock is not a regular file: $DEPLOY_LOCK_PATH"
exec {DEPLOY_LOCK_FD}<>"$DEPLOY_LOCK_PATH" || \
  die "cannot open deploy/control lock: $DEPLOY_LOCK_PATH"
if ! "$FLOCK_BIN" -n "$DEPLOY_LOCK_FD"; then
  die "deploy/control lock is held: $DEPLOY_LOCK_PATH" 75
fi

for transition_path in \
  "$APP_TRANSITION_PATH" "$GATEWAY_TRANSITION_PATH" \
  "$ROOT_ACTIVE_UNITS_PATH" "$ROOT_INVENTORY_PATH" "$ROOT_RESTORED_PATH"; do
  if [[ -e "$transition_path" || -L "$transition_path" ]]; then
    die "pending transition exists: $transition_path" 75
  fi
done

readonly -a SOURCES=(
  scripts/deploy-root-helper.sh
  scripts/ib-gateway-control.sh
  scripts/operator-radon.sh
  scripts/drift_audit.py
  scripts/radon-app-runtime.sh
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
  services/radon-api.service
  services/radon-monitor.service
  services/radon-relay.service
  services/radon-portfolio-sync.service
  services/radon-portfolio-sync.timer
  services/radon-refresh.service
  services/radon-refresh.timer
  services/radon-db-backup.service
  services/radon-db-backup.timer
  services/radon-drift-audit.service
  services/radon-drift-audit.timer
  services/radon-nextjs-db-watchdog.service
  services/radon-nextjs-db-watchdog.timer
)
readonly -a LOGICAL_TARGETS=(
  /usr/local/sbin/radon-deploy-root
  /usr/local/bin/radon-ib-gateway-control
  /usr/local/bin/radon
  /usr/local/lib/radon/drift_audit.py
  /usr/local/sbin/radon-app-runtime
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
  /etc/systemd/system/radon-api.service
  /etc/systemd/system/radon-monitor.service
  /etc/systemd/system/radon-relay.service
  /etc/systemd/system/radon-portfolio-sync.service
  /etc/systemd/system/radon-portfolio-sync.timer
  /etc/systemd/system/radon-refresh.service
  /etc/systemd/system/radon-refresh.timer
  /etc/systemd/system/radon-db-backup.service
  /etc/systemd/system/radon-db-backup.timer
  /etc/systemd/system/radon-drift-audit.service
  /etc/systemd/system/radon-drift-audit.timer
  /etc/systemd/system/radon-nextjs-db-watchdog.service
  /etc/systemd/system/radon-nextjs-db-watchdog.timer
)
readonly -a MODES=(
  0755 0755 0755 0644 0755
  0440 0440 0440 0440
  0644
  0644 0644 0644 0644 0644 0644 0644 0644 0644 0644 0644 0644 0644 0644 0644
  0644 0644 0644
)
readonly -a KINDS=(
  shell shell shell python shell
  sudoers sudoers sudoers sudoers
  polkit
  systemd systemd systemd systemd systemd systemd systemd systemd systemd
  systemd systemd systemd systemd systemd systemd systemd systemd systemd
)

[[ "${#SOURCES[@]}" -eq "${#LOGICAL_TARGETS[@]}" && \
   "${#SOURCES[@]}" -eq "${#MODES[@]}" && \
   "${#SOURCES[@]}" -eq "${#KINDS[@]}" ]] || \
  die "internal artifact table is inconsistent"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "$1" | awk '{print $1}'
  else
    die "no SHA-256 implementation is available"
  fi
}

file_mode() {
  local mode
  if mode="$(stat -c '%a' "$1" 2>/dev/null)"; then
    printf '%s\n' "$mode"
  else
    stat -f '%Lp' "$1"
  fi
}

mode_matches() {
  local expected="${2#0}"
  [[ "$(file_mode "$1")" == "$expected" ]]
}

ownership_matches() {
  local ownership
  [[ "$TEST_MODE" == "1" ]] && return 0
  if ownership="$(stat -c '%U:%G' "$1" 2>/dev/null)"; then
    [[ "$ownership" == "root:root" ]]
  else
    [[ "$(stat -f '%Su:%Sg' "$1")" == "root:root" ]]
  fi
}

STAGE_DIR=""
BACKUP_DIR=""
TRANSACTION_ACTIVE=0
TRANSACTION_COMMITTED=0
DAEMON_RELOAD_ATTEMPTED=0
declare -a TRANSACTION_TARGETS=()
declare -a BACKUP_EXISTED=()

rollback_bundle() {
  local index target
  for ((index=${#TRANSACTION_TARGETS[@]} - 1; index >= 0; index--)); do
    target="${TRANSACTION_TARGETS[$index]}"
    rm -f -- "$target"
    if [[ "${BACKUP_EXISTED[$index]:-0}" == "1" ]]; then
      mkdir -p "$(dirname "$target")"
      cp -a -- "$BACKUP_DIR/$index" "$target"
    fi
  done
  if [[ "$DAEMON_RELOAD_ATTEMPTED" == "1" ]]; then
    # The in-memory unit graph may reflect the rejected bundle. A second
    # reload is intentionally forbidden, so readiness remains withdrawn.
    rm -f -- "$READY_PATH"
  fi
}

cleanup() {
  local status="$?"
  trap - EXIT INT TERM
  set +e
  if [[ "$TRANSACTION_ACTIVE" == "1" && "$TRANSACTION_COMMITTED" != "1" ]]; then
    rollback_bundle
  fi
  [[ -z "$STAGE_DIR" ]] || rm -rf -- "$STAGE_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/radon-control-plane.XXXXXX")"
mkdir -p "$STAGE_DIR/candidates"

declare -a STAGED=()
declare -a SOURCE_HASHES=()
declare -a INSTALLED_TARGETS=()
declare -a SYSTEMD_CANDIDATES=()

for index in "${!SOURCES[@]}"; do
  relative_source="${SOURCES[$index]}"
  source_path="$CLOUD_ROOT/$relative_source"
  [[ -f "$source_path" && ! -L "$source_path" ]] || \
    die "source artifact is missing or not a regular file: $relative_source"
  source_parent="$(cd "$(dirname "$source_path")" && pwd -P)"
  case "$source_parent/" in
    "$CLOUD_ROOT/"*) ;;
    *) die "source artifact escapes canonical cloud root: $relative_source" ;;
  esac

  staged_path="$STAGE_DIR/candidates/$index/$(basename "$relative_source")"
  mkdir -p "$(dirname "$staged_path")"
  source_hash_before="$(sha256_file "$source_path")"
  install -m "${MODES[$index]}" "$source_path" "$staged_path"
  source_hash_after="$(sha256_file "$source_path")"
  staged_hash="$(sha256_file "$staged_path")"
  if [[ "$source_hash_before" != "$source_hash_after" || \
        "$source_hash_before" != "$staged_hash" ]]; then
    die "source changed while staging: $relative_source"
  fi

  case "${KINDS[$index]}" in
    shell)
      bash -n "$staged_path" || die "shell syntax validation failed: $relative_source"
      ;;
    sudoers)
      "$VISUDO_BIN" -cf "$staged_path" >/dev/null || \
        die "sudoers validation failed: $relative_source"
      ;;
    polkit)
      "$NODE_BIN" --check < "$staged_path" || \
        die "polkit syntax validation failed: $relative_source"
      ;;
    python)
      # Parse only. Importing or compiling to disk would execute or cache
      # candidate code during a privileged transaction.
      "$PYTHON_BIN" -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1])' \
        "$staged_path" || die "python syntax validation failed: $relative_source"
      ;;
    systemd)
      SYSTEMD_CANDIDATES+=("$staged_path")
      ;;
    *)
      die "unknown validator for: $relative_source"
      ;;
  esac

  STAGED+=("$staged_path")
  SOURCE_HASHES+=("$staged_hash")
  INSTALLED_TARGETS+=("$(root_path "${LOGICAL_TARGETS[$index]}")")
done

# systemd-analyze verify resolves absolute ExecStart paths. On the first live
# cutover those helpers are not installed yet, so seed staged shell artifacts
# at their final paths before unit validation. On failure, remove only the
# helpers this run created so a partial seed cannot linger.
declare -a PRESEEDED_HELPERS=()
for index in "${!KINDS[@]}"; do
  if [[ "${KINDS[$index]}" != "shell" ]]; then
    continue
  fi
  helper_target="${INSTALLED_TARGETS[$index]}"
  if [[ -e "$helper_target" || -L "$helper_target" ]]; then
    continue
  fi
  mkdir -p "$(dirname "$helper_target")"
  if [[ "$TEST_MODE" == "1" ]]; then
    install -m "${MODES[$index]}" "${STAGED[$index]}" "$helper_target" || \
      die "failed to preseed helper for unit validation: ${LOGICAL_TARGETS[$index]}"
  else
    install -m "${MODES[$index]}" -o root -g root \
      "${STAGED[$index]}" "$helper_target" || \
      die "failed to preseed helper for unit validation: ${LOGICAL_TARGETS[$index]}"
  fi
  PRESEEDED_HELPERS+=("$helper_target")
done

if ! "$SYSTEMD_ANALYZE_BIN" verify "${SYSTEMD_CANDIDATES[@]}" >/dev/null; then
  for helper_target in "${PRESEEDED_HELPERS[@]}"; do
    rm -f -- "$helper_target"
  done
  die "systemd unit validation failed"
fi

STAGED_MANIFEST="$STAGE_DIR/control-plane-manifest.sha256"
for index in "${!SOURCES[@]}"; do
  printf '%s  %s -> %s\n' \
    "${SOURCE_HASHES[$index]}" "${SOURCES[$index]}" "${LOGICAL_TARGETS[$index]}" \
    >> "$STAGED_MANIFEST"
done
STAGED_READY="$STAGE_DIR/control-plane-ready"
printf '%s  %s\n' "$(sha256_file "$STAGED_MANIFEST")" "$LOGICAL_MANIFEST_PATH" \
  > "$STAGED_READY"

if [[ -L "$STATE_DIR" || ( -e "$STATE_DIR" && ! -d "$STATE_DIR" ) ]]; then
  die "control-plane state path is not a directory: $STATE_DIR"
fi
if [[ "$TEST_MODE" == "1" ]]; then
  install -d -m 0750 "$STATE_DIR"
else
  install -d -m 0750 -o radon -g radon "$STATE_DIR"
fi

bundle_is_current=1
for index in "${!INSTALLED_TARGETS[@]}"; do
  target_path="${INSTALLED_TARGETS[$index]}"
  if [[ ! -f "$target_path" || -L "$target_path" ]] || \
     [[ "$(sha256_file "$target_path" 2>/dev/null || true)" != "${SOURCE_HASHES[$index]}" ]] || \
     ! mode_matches "$target_path" "${MODES[$index]}" || \
     ! ownership_matches "$target_path"; then
    bundle_is_current=0
    break
  fi
done
if [[ "$bundle_is_current" == "1" ]] && \
   cmp -s "$STAGED_MANIFEST" "$MANIFEST_PATH" && \
   mode_matches "$MANIFEST_PATH" 0644 && ownership_matches "$MANIFEST_PATH" && \
   cmp -s "$STAGED_READY" "$READY_PATH" && \
   mode_matches "$READY_PATH" 0644 && ownership_matches "$READY_PATH"; then
  printf 'Control-plane bundle is already current.\n'
  exit 0
fi

atomic_install() {
  local source="$1"
  local target="$2"
  local mode="$3"
  local target_dir temporary
  local -a owner_args=()
  target_dir="$(dirname "$target")"
  mkdir -p "$target_dir"
  temporary="$(mktemp "$target_dir/.radon-bootstrap.$(basename "$target").XXXXXX")"
  if [[ "$TEST_MODE" != "1" ]]; then
    owner_args=(-o root -g root)
  fi
  if ! install -m "$mode" "${owner_args[@]}" "$source" "$temporary"; then
    rm -f -- "$temporary"
    return 1
  fi
  if ! mv -f -- "$temporary" "$target"; then
    rm -f -- "$temporary"
    return 1
  fi
}

TRANSACTION_TARGETS=("${INSTALLED_TARGETS[@]}" "$MANIFEST_PATH" "$READY_PATH")
BACKUP_DIR="$STAGE_DIR/backups"
mkdir -p "$BACKUP_DIR"
for index in "${!TRANSACTION_TARGETS[@]}"; do
  target_path="${TRANSACTION_TARGETS[$index]}"
  if [[ -e "$target_path" || -L "$target_path" ]]; then
    [[ -f "$target_path" || -L "$target_path" ]] || \
      die "installed target is not replaceable: $target_path"
    cp -a -- "$target_path" "$BACKUP_DIR/$index"
    BACKUP_EXISTED+=(1)
  else
    BACKUP_EXISTED+=(0)
  fi
done

TRANSACTION_ACTIVE=1
rm -f -- "$READY_PATH"
for index in "${!INSTALLED_TARGETS[@]}"; do
  atomic_install "${STAGED[$index]}" "${INSTALLED_TARGETS[$index]}" "${MODES[$index]}" || \
    die "failed to install: ${LOGICAL_TARGETS[$index]}"
done

for index in "${!INSTALLED_TARGETS[@]}"; do
  target_path="${INSTALLED_TARGETS[$index]}"
  installed_hash="$(sha256_file "$target_path")"
  if [[ "$installed_hash" != "${SOURCE_HASHES[$index]}" ]] || \
     ! mode_matches "$target_path" "${MODES[$index]}" || \
     ! ownership_matches "$target_path"; then
    die "installed artifact verification failed: ${LOGICAL_TARGETS[$index]}"
  fi
done

atomic_install "$STAGED_MANIFEST" "$MANIFEST_PATH" 0644 || \
  die "failed to install control-plane manifest"
cmp -s "$STAGED_MANIFEST" "$MANIFEST_PATH" || \
  die "installed control-plane manifest verification failed"
mode_matches "$MANIFEST_PATH" 0644 && ownership_matches "$MANIFEST_PATH" || \
  die "installed control-plane manifest metadata verification failed"

DAEMON_RELOAD_ATTEMPTED=1
"$SYSTEMCTL_BIN" daemon-reload || die "systemd daemon reload failed"

atomic_install "$STAGED_READY" "$READY_PATH" 0644 || \
  die "failed to publish control-plane readiness"
cmp -s "$STAGED_READY" "$READY_PATH" || \
  die "control-plane readiness verification failed"
mode_matches "$READY_PATH" 0644 && ownership_matches "$READY_PATH" || \
  die "control-plane readiness metadata verification failed"

TRANSACTION_COMMITTED=1
TRANSACTION_ACTIVE=0
printf 'Installed and verified %d control-plane artifacts.\n' "${#SOURCES[@]}"
