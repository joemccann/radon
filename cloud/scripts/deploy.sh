#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# deploy.sh -- Health-gated deployment with automatic rollback
# Pulls latest main, installs deps, builds, restarts services.
# Rolls back to previous commit if the health check fails.
#
# Layout:
#   Monorepo (preferred): <radon>/cloud/scripts/deploy.sh
#   Legacy dual-checkout: /home/radon/radon-cloud/scripts/deploy.sh
# Override with RADON_DIR / RADON_CLOUD_DIR when needed.
# ---------------------------------------------------------------------------

_DEPLOY_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_CLOUD_FROM_SCRIPT="$(cd "${_DEPLOY_SCRIPT_DIR}/.." && pwd)"
_RADON_FROM_CLOUD="$(cd "${_CLOUD_FROM_SCRIPT}/.." && pwd)"

_default_radon_dir() {
  if [[ -d "${_RADON_FROM_CLOUD}/web" && -d "${_RADON_FROM_CLOUD}/scripts/api" ]]; then
    printf '%s\n' "${_RADON_FROM_CLOUD}"
  else
    printf '%s\n' "/home/radon/radon"
  fi
}

_default_cloud_dir() {
  if [[ "$(basename "${_CLOUD_FROM_SCRIPT}")" == "cloud" \
    && -d "${_CLOUD_FROM_SCRIPT}/scripts" \
    && -d "${_CLOUD_FROM_SCRIPT}/services" ]]; then
    printf '%s\n' "${_CLOUD_FROM_SCRIPT}"
  else
    printf '%s\n' "/home/radon/radon-cloud"
  fi
}

_default_env_file() {
  # Keep production secrets at the historical path when present so the
  # monorepo cutover never requires moving host credentials.
  if [[ -n "${RADON_DEPLOY_ENV_FILE:-}" ]]; then
    printf '%s\n' "${RADON_DEPLOY_ENV_FILE}"
  elif [[ -f /home/radon/radon-cloud/.env ]]; then
    printf '%s\n' "/home/radon/radon-cloud/.env"
  else
    printf '%s\n' "${CLOUD_DIR}/.env"
  fi
}

readonly RADON_DIR="${RADON_DIR:-$(_default_radon_dir)}"
readonly CLOUD_DIR="${RADON_CLOUD_DIR:-$(_default_cloud_dir)}"
readonly VENV_DIR="${VENV_DIR:-${RADON_DIR}/.venv}"
# Full /health is used ONLY by the pre-teardown gateway-readiness wait.
# The post-deploy gate must NEVER touch it: it probes IB auth state, which
# the deployed code does not control, and on 2026-06-11 a wedged /health
# made the gate hang until the GitHub Actions step SIGTERMed the deploy
# and rolled back the fix for the very wedge being measured.
readonly HEALTH_URL="http://localhost:8321/health"
# Post-deploy gate sensors. /health/lite answers from process state with no
# IB probing; :8330/status is the isolated stdlib health daemon (advisory
# second opinion only -- never a rollback trigger; its /healthz is a
# vacuous zero-I/O 200 and is deliberately not used).
readonly HEALTH_LITE_URL="http://localhost:8321/health/lite"
readonly NEXTJS_URL="http://localhost:3000/"
readonly RELAY_URL="http://localhost:8765/"
readonly RELAY_HOST="127.0.0.1"
readonly RELAY_PORT=8765
readonly ISOLATED_STATUS_URL="http://localhost:8330/status"
readonly HEALTH_WAIT_SECONDS=15
# Worst-case gate budget: HEALTH_RETRIES * (HEALTH_CURL_TIMEOUT +
# HEALTH_RETRY_WAIT) = 60s for the lite probe, plus instant systemctl
# checks, bounded Next.js/relay probes, and one advisory curl. Keep this under
# the global supervisor deadline and GitHub Actions step budget.
# Actions step budget (the 06-11 failure was a step SIGTERM at ~3min).
readonly HEALTH_RETRIES=6
readonly HEALTH_RETRY_WAIT=5
readonly HEALTH_CURL_TIMEOUT=5
readonly SURFACE_RETRIES=3
readonly SURFACE_RETRY_WAIT=2
readonly SURFACE_TIMEOUT=3
# The slowest managed restart policy is newsfeed's RestartSec=30. A 40-second
# unchanged NRestarts window detects every configured core crash/restart cycle.
readonly UNIT_STABILITY_SECONDS="${RADON_DEPLOY_UNIT_STABILITY_SECONDS:-40}"
# Bounded wait for the IB gateway data plane to be ready (authenticated +
# port listening) before restarting the relay/api. 12*5s = 60s: long enough
# for a coincidental gateway restart to come up, short enough not to hang a
# code deploy on a pending 2FA prompt.
readonly GATEWAY_READY_RETRIES=12
readonly GATEWAY_READY_WAIT=5
readonly DEPLOY_TIMEOUT="${DEPLOY_TIMEOUT:-900}"
readonly DEPLOY_KILL_AFTER="${DEPLOY_KILL_AFTER:-30}"
readonly BUN_VERSION="1.3.14"

readonly SERVICES=(radon-nextjs radon-api radon-relay radon-monitor radon-newsfeed)

# Required env vars that MUST be present before a deploy touches services.
# Prefer ~/radon-cloud/.env when it exists (stable host secrets path).
# Discord vars are intentionally absent — the notification stack uses Pushover.
readonly ENV_FILE_DEFAULT="$(_default_env_file)"
readonly DEPLOY_LOCK_FILE="${RADON_DEPLOY_LOCK_FILE:-/home/radon/.radon-deploy.lock}"
readonly GREEN_MARKER_FILE="${RADON_DEPLOY_GREEN_MARKER:-/home/radon/.radon-last-green-deploy}"
readonly DEPLOY_ROOT_HELPER="${RADON_DEPLOY_ROOT_HELPER:-/usr/local/sbin/radon-deploy-root}"
readonly GATEWAY_CONTROL_HELPER="${RADON_GATEWAY_CONTROL_HELPER:-/usr/local/bin/radon-ib-gateway-control}"
readonly CONTROL_PLANE_MANIFEST="${RADON_CONTROL_PLANE_MANIFEST:-/var/lib/radon/control-plane-manifest.sha256}"
readonly CONTROL_PLANE_READY="${RADON_CONTROL_PLANE_READY:-/var/lib/radon/control-plane-ready}"
readonly SHA256SUM="${RADON_SHA256SUM:-/usr/bin/sha256sum}"
readonly REQUIRED_ENV_FILE="${RADON_REQUIRED_ENV_FILE:-${CLOUD_DIR}/config/required-env.txt}"
readonly ENV_CHECKER="${CLOUD_DIR}/scripts/check-env.py"
readonly ENV_CHECKER_PYTHON="${RADON_ENV_CHECKER_PYTHON:-${VENV_DIR}/bin/python}"
readonly SYSTEM_PYTHON="${RADON_SYSTEM_PYTHON:-/usr/bin/python3.13}"
readonly RELEASES_DIR="${RADON_RELEASES_DIR:-/home/radon/.radon-releases}"
readonly RELEASE_ARTIFACTS=(node_modules web/node_modules web/.next web/.env)
readonly ROLLBACK_ARTIFACTS=(node_modules web/node_modules web/.next web/.env .venv)
readonly TRANSITION_JOURNAL_FILE="${RADON_DEPLOY_TRANSITION_JOURNAL:-/home/radon/.radon-deploy-transition.json}"
readonly DEPLOY_SUPPORT_DIR="${RADON_DEPLOY_SUPPORT_DIR:-${_CLOUD_FROM_SCRIPT}/scripts}"
readonly JOURNAL_HELPER="${RADON_DEPLOY_JOURNAL_HELPER:-${DEPLOY_SUPPORT_DIR}/deploy-journal.py}"
readonly JOURNAL_PYTHON="${RADON_DEPLOY_JOURNAL_PYTHON:-${SYSTEM_PYTHON}}"
# Mutable deploy-process state used only by the TERM/INT/HUP recovery trap.
# An interrupted deploy deliberately leaves Git at the requested SHA with no
# green marker; the next run sees the marker mismatch and resumes the full
# build/restart/gate path instead of returning a false no-op success.
DEPLOY_TEARDOWN_STARTED=0
DEPLOY_SERVICES_RECOVERED=1
DEPLOY_RELEASE_UNVERIFIED=0
PREVIOUS_RELEASE_SHA=""
STAGED_RELEASE_DIR=""
RELEASE_BACKUP_DIR=""
JOURNAL_REQUESTED_SHA=""
JOURNAL_PREVIOUS_SHA=""
JOURNAL_STAGE_DIR=""
JOURNAL_BACKUP_DIR=""
JOURNAL_PHASE=""
WEB_ENV_TMP=""
SUPERVISED_TIMEOUT_PID=""
SUPERVISED_TIMEOUT_PGID=""
SUPERVISOR_RECOVERY_DONE=0
SUPERVISOR_RECOVERY_ACTIVE=0
SUPERVISOR_PENDING_SIGNAL=""

# -- Colors ----------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# -- Helpers ----------------------------------------------------------------

# Validates names and raw quoting without sourcing or interpolating credentials.
# Override the env file path with the first arg (used by tests).
# RADON_DEPLOY_SKIP_PREFLIGHT=1 skips only required-key shape checks for an
# emergency deploy. Private permissions, literal-dollar safety, and Compose
# rendering remain mandatory because bypassing those can expose credentials.
preflight_env() {
  local env_file="${1:-${ENV_FILE_DEFAULT}}"
  local skip_shape="${RADON_DEPLOY_SKIP_PREFLIGHT:-0}"
  local checker_args=("$env_file" "$REQUIRED_ENV_FILE")

  if [[ "$skip_shape" == "1" ]]; then
    log_warn "[preflight] required-key checks skipped; permissions, literal values, and Compose validation remain mandatory"
    checker_args+=(--skip-required)
  fi

  if ! "$ENV_CHECKER_PYTHON" "$ENV_CHECKER" "${checker_args[@]}"; then
    return 1
  fi

  # Do not let Compose print expanded values or secret fragments. A generic
  # failure is enough; operators can inspect the named-key checks above.
  if ! (
    cd "$CLOUD_DIR"
    RADON_COMPOSE_ENV_FILE="$env_file" \
      docker compose --env-file "$env_file" config --quiet >/dev/null 2>&1
  ); then
    log_error "[preflight] FAIL: docker compose config validation failed"
    return 1
  fi

  echo "[preflight] ok (shared required-env.txt contract passed)"
  return 0
}

preflight_control_plane() {
  local line expected_hash source_rel installed_target source_path source_hash installed_hash
  local entries=0
  local deploy_helper_found=0
  local gateway_helper_found=0

  if [[ ! -x "$SHA256SUM" || ! -r "$CONTROL_PLANE_MANIFEST" || ! -r "$CONTROL_PLANE_READY" ]]; then
    log_error "[preflight] control-plane readiness manifest is missing"
    return 1
  fi
  if ! "$SHA256SUM" --check --status "$CONTROL_PLANE_READY"; then
    log_error "[preflight] control-plane readiness marker does not match its manifest"
    return 1
  fi

  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    if [[ ! "$line" =~ ^([0-9a-f]{64})[[:space:]][[:space:]]([^[:space:]]+)[[:space:]]-\>[[:space:]](/.+)$ ]]; then
      log_error "[preflight] malformed control-plane manifest entry"
      return 1
    fi
    expected_hash="${BASH_REMATCH[1]}"
    source_rel="${BASH_REMATCH[2]}"
    installed_target="${BASH_REMATCH[3]}"
    if [[ "$source_rel" == /* || "$source_rel" == *".."* ]]; then
      log_error "[preflight] unsafe control-plane source path"
      return 1
    fi
    source_path="${_CLOUD_FROM_SCRIPT}/${source_rel}"
    [[ -r "$source_path" ]] || {
      log_error "[preflight] target release is missing ${source_rel}"
      return 1
    }
    source_hash="$("$SHA256SUM" "$source_path" | awk '{print $1}')" || return 1
    if [[ "$source_hash" != "$expected_hash" ]]; then
      log_error "[preflight] installed control plane is incompatible with ${source_rel}"
      return 1
    fi
    if [[ ! -f "$installed_target" || -L "$installed_target" ]]; then
      log_error "[preflight] installed control-plane target is unavailable or unsafe: ${installed_target}"
      return 1
    fi
    if [[ -r "$installed_target" ]]; then
      installed_hash="$("$SHA256SUM" "$installed_target" | awk '{print $1}')" || return 1
      if [[ "$installed_hash" != "$expected_hash" ]]; then
        log_error "[preflight] installed control-plane target drifted: ${installed_target}"
        return 1
      fi
    fi
    [[ "$installed_target" == "$DEPLOY_ROOT_HELPER" ]] && deploy_helper_found=1
    [[ "$installed_target" == "$GATEWAY_CONTROL_HELPER" ]] && gateway_helper_found=1
    entries=$((entries + 1))
  done < "$CONTROL_PLANE_MANIFEST"

  if (( entries == 0 || deploy_helper_found == 0 || gateway_helper_found == 0 )); then
    log_error "[preflight] control-plane manifest is incomplete"
    return 1
  fi
  if ! sudo -n "$DEPLOY_ROOT_HELPER" verify-control-plane; then
    log_error "[preflight] installed control-plane target contract failed privileged verification"
    return 1
  fi
  if [[ ! -x "$DEPLOY_ROOT_HELPER" || ! -x "$GATEWAY_CONTROL_HELPER" ]]; then
    log_error "[preflight] required installed control-plane helper is unavailable"
    return 1
  fi
  if ! sudo -n "$DEPLOY_ROOT_HELPER" verify-restored; then
    log_error "[preflight] deploy helper or exact sudo capability is unavailable"
    return 1
  fi
  log_success "[preflight] installed control plane matches the target release"
}

green_marker_matches() {
  local requested_sha="$1"
  [[ -f "$GREEN_MARKER_FILE" ]] || return 1
  [[ "$(head -n 1 "$GREEN_MARKER_FILE" 2>/dev/null)" == "$requested_sha" ]]
}

write_green_marker() {
  local requested_sha="$1"
  local marker_dir marker_tmp
  marker_dir="$(dirname "$GREEN_MARKER_FILE")"
  umask 077
  marker_tmp="$(mktemp "${marker_dir}/.radon-green.XXXXXX")"
  printf '%s\n' "$requested_sha" > "$marker_tmp"
  chmod 0600 "$marker_tmp"
  mv -f "$marker_tmp" "$GREEN_MARKER_FILE"
}

quarantine_green_marker() {
  local requested_sha="$1"
  local quarantine

  green_marker_matches "$requested_sha" || return 0
  quarantine="${GREEN_MARKER_FILE}.invalid.${requested_sha:0:12}.$(date -u +%Y%m%dT%H%M%SZ).$$"
  mv "$GREEN_MARKER_FILE" "$quarantine"
  log_warn "Quarantined stale green marker after the live gate failed"
}

write_web_env() {
  local cloud_env="${1:-$ENV_FILE_DEFAULT}"
  local release_dir="${2:-$RADON_DIR}"
  local web_env="${release_dir}/web/.env"
  umask 077
  WEB_ENV_TMP="$(mktemp "${web_env}.tmp.XXXXXX")"
  grep -E '^NEXT_PUBLIC_[A-Z0-9_]+=' "$cloud_env" > "$WEB_ENV_TMP" || true
  chmod 0600 "$WEB_ENV_TMP"
  mv -f "$WEB_ENV_TMP" "$web_env"
  WEB_ENV_TMP=""
}

run_with_cloud_env() {
  local env_file="$1"
  shift
  "${VENV_DIR}/bin/python" "${CLOUD_DIR}/scripts/run_with_env.py" "$env_file" -- "$@"
}

write_transition_journal() {
  "$JOURNAL_PYTHON" "$JOURNAL_HELPER" write "$TRANSITION_JOURNAL_FILE" "$@"
}

write_transition_phase() {
  "$JOURNAL_PYTHON" "$JOURNAL_HELPER" phase "$TRANSITION_JOURNAL_FILE" "$1"
  JOURNAL_PHASE="$1"
}

load_transition_journal() {
  local values=()
  local value
  [[ -f "$TRANSITION_JOURNAL_FILE" ]] || return 1
  while IFS= read -r -d '' value; do
    values+=("$value")
  done < <("$JOURNAL_PYTHON" "$JOURNAL_HELPER" read "$TRANSITION_JOURNAL_FILE")
  if (( ${#values[@]} != 5 )); then
    log_error "Transition journal has an invalid field count"
    return 1
  fi
  JOURNAL_REQUESTED_SHA="${values[0]}"
  JOURNAL_PREVIOUS_SHA="${values[1]}"
  JOURNAL_STAGE_DIR="${values[2]}"
  JOURNAL_BACKUP_DIR="${values[3]}"
  JOURNAL_PHASE="${values[4]}"
  if [[ ! "$JOURNAL_REQUESTED_SHA" =~ ^[0-9a-f]{40}$ \
      || ! "$JOURNAL_PREVIOUS_SHA" =~ ^[0-9a-f]{40}$ \
      || ! "$JOURNAL_PHASE" =~ ^(prepared|backup_started|backup_complete|promoting|target_unverified|verified)$ \
      ]] || ! release_dir_is_managed "$JOURNAL_STAGE_DIR" \
      || ! release_dir_is_managed "$JOURNAL_BACKUP_DIR"; then
    log_error "Transition journal failed path, SHA, or phase validation"
    return 1
  fi
}

clear_transition_journal() {
  "$JOURNAL_PYTHON" "$JOURNAL_HELPER" remove "$TRANSITION_JOURNAL_FILE"
}

recover_services_after_interrupt() {
  if [[ -f "$TRANSITION_JOURNAL_FILE" ]]; then
    recover_pending_transition || log_error "Interrupted deploy could not complete durable transition recovery"
  elif (( DEPLOY_TEARDOWN_STARTED == 1 && DEPLOY_SERVICES_RECOVERED == 0 )); then
    log_warn "Deploy interrupted during service transition; starting every managed unit"
    sudo "$DEPLOY_ROOT_HELPER" recover || log_error "Interrupted deploy could not recover all services"
    DEPLOY_SERVICES_RECOVERED=1
    DEPLOY_TEARDOWN_STARTED=0
  fi
}

handle_deploy_signal() {
  local signal_name="${1:-TERM}"
  trap - TERM INT HUP
  [[ -z "$WEB_ENV_TMP" ]] || rm -f "$WEB_ENV_TMP" || log_error "Could not remove temporary web env"
  recover_services_after_interrupt
  log_error "Deployment interrupted by ${signal_name}; green marker was not advanced"
  exit 143
}

handle_deploy_exit() {
  local exit_code="${1:-1}"
  trap - EXIT

  if (( exit_code != 0 )); then
    [[ -z "$WEB_ENV_TMP" ]] || rm -f "$WEB_ENV_TMP" || log_error "Could not remove temporary web env"
    # Preserve the triggering command's status even if service recovery fails.
    recover_services_after_interrupt || true
    if [[ ! -f "$TRANSITION_JOURNAL_FILE" && -n "$STAGED_RELEASE_DIR" ]]; then
      cleanup_staged_release || true
    fi
  fi

  exit "$exit_code"
}

current_commit() {
  git -C "$RADON_DIR" rev-parse HEAD
}

verify_tracked_drift_matches_target() {
  local target_sha="$1"
  local path
  local dirty_paths=()

  while IFS= read -r -d '' path; do
    dirty_paths+=("$path")
  done < <(git -C "$RADON_DIR" diff HEAD --name-only -z --)

  for path in "${dirty_paths[@]}"; do
    if ! git -C "$RADON_DIR" diff --quiet "$target_sha" -- "$path"; then
      log_error "Tracked production drift differs from the requested release: ${path}"
      return 1
    fi
  done

  if (( ${#dirty_paths[@]} > 0 )); then
    log_warn "Tracked drift byte-matches the requested release and is safe to reset"
  fi
  return 0
}

short_log() {
  git -C "$RADON_DIR" log --oneline -1
}

prepare_python_wheels() {
  local release_dir="$1"
  local target_wheels="${release_dir}/.deploy-wheels/target"

  rm -rf -- "${release_dir}/.deploy-wheels"
  mkdir -p "$target_wheels"
  "${VENV_DIR}/bin/python" -m pip wheel --wheel-dir "$target_wheels" \
    --requirement "${release_dir}/requirements.txt" \
    --requirement "${release_dir}/scripts/requirements-api.txt"
}

install_target_python_env() {
  local release_dir="$1"
  local wheelhouse="${release_dir}/.deploy-wheels/target"

  if [[ ! -e "${RELEASE_BACKUP_DIR}/.venv" && ! -L "${RELEASE_BACKUP_DIR}/.venv" ]]; then
    log_error "Refusing to replace Python env without an exact previous-env backup"
    return 1
  fi
  "$SYSTEM_PYTHON" -m venv "$VENV_DIR"
  "${VENV_DIR}/bin/python" -m pip install --no-index --find-links "$wheelhouse" \
    --requirement "${release_dir}/requirements.txt" \
    --requirement "${release_dir}/scripts/requirements-api.txt"
  validate_target_python_env "$RADON_DIR"
}

validate_target_python_env() {
  local release_dir="${1:-$RADON_DIR}"
  "${release_dir}/.venv/bin/python" -c \
    'import fastapi, httptools, uvicorn, uvloop, watchfiles, websockets'
}

build_nextjs_at() {
  local release_dir="$1"
  # Persist only browser-safe variables. Server-side build inputs are supplied
  # to the build process from the literal dotenv parser below and never copied
  # into the application tree.
  local cloud_env="$ENV_FILE_DEFAULT"
  write_web_env "$cloud_env" "$release_dir"

  if ! command -v bun >/dev/null 2>&1 || [[ "$(bun --version)" != "$BUN_VERSION" ]]; then
    log_error "Bun ${BUN_VERSION} is required by the production artifact contract"
    return 1
  fi

  if [[ ! -f "${release_dir}/package.json" || ! -f "${release_dir}/bun.lock" ]]; then
    log_error "Root package.json/bun.lock artifact contract is incomplete"
    return 1
  fi
  if [[ ! -f "${release_dir}/web/package.json" || ! -f "${release_dir}/web/bun.lock" ]]; then
    log_error "Web package.json/bun.lock artifact contract is incomplete"
    return 1
  fi

  # CI and production both use Bun's frozen lockfile contract. Browser
  # installation is part of the artifact build and must finish before teardown.
  (
    cd "$release_dir"
    bun install --frozen-lockfile
    ./node_modules/.bin/playwright install chromium chromium-headless-shell >/dev/null
  )

  (
    cd "${release_dir}/web"
    bun install --frozen-lockfile
    ./node_modules/.bin/playwright install chromium chromium-headless-shell >/dev/null
    run_with_cloud_env "$cloud_env" bun run build
  )
}

release_dir_is_managed() {
  [[ -n "${1:-}" && "$1" == "${RELEASES_DIR}/"* ]]
}

validate_release_artifacts() {
  local release_dir="$1"
  local artifact
  for artifact in "${RELEASE_ARTIFACTS[@]}"; do
    if [[ ! -e "${release_dir}/${artifact}" && ! -L "${release_dir}/${artifact}" ]]; then
      log_error "Release artifact missing: ${artifact}"
      return 1
    fi
  done
}

validate_rollback_artifacts() {
  local release_dir="$1"
  local artifact
  for artifact in "${ROLLBACK_ARTIFACTS[@]}"; do
    if [[ ! -e "${release_dir}/${artifact}" && ! -L "${release_dir}/${artifact}" ]]; then
      log_error "Rollback artifact missing: ${artifact}"
      return 1
    fi
  done
  [[ -x "${release_dir}/.venv/bin/python" ]]
}

create_staged_worktree() {
  local requested_sha="$1"
  local candidate

  mkdir -p "$RELEASES_DIR"
  candidate="$(mktemp -d "${RELEASES_DIR}/stage.${requested_sha:0:12}.XXXXXX")"
  rmdir "$candidate"
  STAGED_RELEASE_DIR="$candidate"
  if ! git -C "$RADON_DIR" worktree add --detach "$STAGED_RELEASE_DIR" "$requested_sha"; then
    rm -rf -- "$STAGED_RELEASE_DIR"
    STAGED_RELEASE_DIR=""
    return 1
  fi
}

cleanup_staged_release_path() {
  local staged="$1"
  [[ -n "$staged" ]] || return 0
  if ! release_dir_is_managed "$staged"; then
    log_error "Refusing to clean unmanaged staged release path"
    return 1
  fi
  if [[ -e "$staged/.git" ]]; then
    git -C "$RADON_DIR" worktree remove --force "$staged" >/dev/null 2>&1 \
      || log_warn "Could not remove staged worktree ${staged}"
    git -C "$RADON_DIR" worktree prune >/dev/null 2>&1 || true
  else
    rm -rf -- "$staged"
  fi
}

cleanup_staged_release() {
  cleanup_staged_release_path "$STAGED_RELEASE_DIR"
  STAGED_RELEASE_DIR=""
}

build_staged_release() {
  local requested_sha="$1"
  local status=0

  create_staged_worktree "$requested_sha" || return $?
  build_nextjs_at "$STAGED_RELEASE_DIR" || status=$?
  if (( status == 0 )); then
    validate_release_artifacts "$STAGED_RELEASE_DIR" || status=$?
  fi
  if (( status == 0 )); then
    prepare_python_wheels "$STAGED_RELEASE_DIR" || status=$?
  fi
  if (( status != 0 )); then
    log_error "Staged release build failed; live artifacts were not touched"
    cleanup_staged_release || true
    return "$status"
  fi
  log_success "Validated staged release ${requested_sha:0:7}"
}

prepare_release_transition() {
  local requested_sha="$1"
  local previous_sha="$2"

  if [[ -f "$TRANSITION_JOURNAL_FILE" ]]; then
    log_error "A durable deploy transition is already pending recovery"
    return 1
  fi
  validate_release_artifacts "$STAGED_RELEASE_DIR" || return 1
  validate_rollback_artifacts "$RADON_DIR" || return 1
  RELEASE_BACKUP_DIR="$(mktemp -d "${RELEASES_DIR}/backup.${previous_sha:0:12}.XXXXXX")"
  if ! write_transition_journal "$requested_sha" "$previous_sha" \
    "$STAGED_RELEASE_DIR" "$RELEASE_BACKUP_DIR" prepared; then
    rm -rf -- "$RELEASE_BACKUP_DIR"
    RELEASE_BACKUP_DIR=""
    return 1
  fi
  JOURNAL_REQUESTED_SHA="$requested_sha"
  JOURNAL_PREVIOUS_SHA="$previous_sha"
  JOURNAL_STAGE_DIR="$STAGED_RELEASE_DIR"
  JOURNAL_BACKUP_DIR="$RELEASE_BACKUP_DIR"
  JOURNAL_PHASE=prepared
}

restore_release_backup() {
  local previous_sha="$1"
  local backup_dir="${2:-$RELEASE_BACKUP_DIR}"
  local artifact

  if [[ -z "$backup_dir" ]] || ! release_dir_is_managed "$backup_dir"; then
    log_error "No managed previous-release artifact backup is available"
    return 1
  fi
  git -C "$RADON_DIR" reset --hard "$previous_sha" || return 1
  restore_precloud_control_plane_tree "$previous_sha" || return 1
  for artifact in "${ROLLBACK_ARTIFACTS[@]}"; do
    if [[ -e "${backup_dir}/${artifact}" || -L "${backup_dir}/${artifact}" ]]; then
      rm -rf -- "${RADON_DIR}/${artifact}"
      mkdir -p "$(dirname "${RADON_DIR}/${artifact}")"
      mv "${backup_dir}/${artifact}" "${RADON_DIR}/${artifact}" || return 1
    fi
  done
  validate_rollback_artifacts "$RADON_DIR" || return 1
  DEPLOY_RELEASE_UNVERIFIED=0
}

restore_precloud_control_plane_tree() {
  local release_sha="$1"
  local live_cloud="${RADON_DIR}/cloud"

  if git -C "$RADON_DIR" cat-file -e "${release_sha}:cloud/scripts/deploy.sh" 2>/dev/null; then
    return 0
  fi
  if [[ "${_CLOUD_FROM_SCRIPT}" == "${RADON_DIR}"/* ]]; then
    log_error "Pre-cloud rollback requires immutable deploy support outside the live checkout"
    return 1
  fi
  if [[ ! -r "${_CLOUD_FROM_SCRIPT}/scripts/deploy.sh" ]]; then
    log_error "Immutable control-plane support is unavailable for pre-cloud rollback"
    return 1
  fi

  rm -rf -- "$live_cloud"
  cp -a -- "${_CLOUD_FROM_SCRIPT}" "$live_cloud" || return 1
  chmod -R u+w "$live_cloud" || return 1
  log_warn "Previous app release predates cloud/; retained immutable control-plane compatibility tree"
}

activate_staged_release() {
  local requested_sha="$1"
  local previous_sha="$2"
  local artifact
  local status=0

  if [[ ! -f "$TRANSITION_JOURNAL_FILE" || -z "$RELEASE_BACKUP_DIR" ]]; then
    log_error "Release activation requires a durable prepared transition"
    return 1
  fi
  DEPLOY_RELEASE_UNVERIFIED=1
  write_transition_phase backup_started || return $?

  for artifact in "${ROLLBACK_ARTIFACTS[@]}"; do
    mkdir -p "$(dirname "${RELEASE_BACKUP_DIR}/${artifact}")"
    mv "${RADON_DIR}/${artifact}" "${RELEASE_BACKUP_DIR}/${artifact}" || status=$?
    (( status == 0 )) || break
  done
  if (( status == 0 )); then
    write_transition_phase backup_complete || status=$?
  fi
  if (( status == 0 )); then
    write_transition_phase promoting || status=$?
  fi
  if (( status == 0 )); then
    git -C "$RADON_DIR" reset --hard "$requested_sha" || status=$?
  fi
  if (( status == 0 )); then
    for artifact in "${RELEASE_ARTIFACTS[@]}"; do
      mkdir -p "$(dirname "${RADON_DIR}/${artifact}")"
      mv "${STAGED_RELEASE_DIR}/${artifact}" "${RADON_DIR}/${artifact}" || status=$?
      (( status == 0 )) || break
    done
  fi
  if (( status == 0 )); then
    install_target_python_env "$STAGED_RELEASE_DIR" || status=$?
  fi
  if (( status != 0 )); then
    log_error "Release activation failed; durable recovery is required"
    return "$status"
  fi
  write_transition_phase target_unverified
}

finalize_release_artifacts() {
  local stage_dir="${1:-$STAGED_RELEASE_DIR}"
  local backup_dir="${2:-$RELEASE_BACKUP_DIR}"

  clear_transition_journal || return 1
  if [[ -n "$backup_dir" ]]; then
    if release_dir_is_managed "$backup_dir"; then
      rm -rf -- "$backup_dir"
    else
      log_error "Refusing to clean unmanaged release backup path"
      return 1
    fi
  fi
  cleanup_staged_release_path "$stage_dir" || true
  RELEASE_BACKUP_DIR=""
  STAGED_RELEASE_DIR=""
}

wait_for_recovered_units() {
  local attempt=1
  while (( attempt <= 6 )); do
    if check_units_active; then
      return 0
    fi
    (( attempt++ ))
    sleep 2
  done
  return 1
}

recover_pending_transition() {
  [[ -f "$TRANSITION_JOURNAL_FILE" ]] || return 0
  load_transition_journal || return 1

  STAGED_RELEASE_DIR="$JOURNAL_STAGE_DIR"
  RELEASE_BACKUP_DIR="$JOURNAL_BACKUP_DIR"
  PREVIOUS_RELEASE_SHA="$JOURNAL_PREVIOUS_SHA"

  if [[ "$JOURNAL_PHASE" == "verified" ]]; then
    if [[ "$(current_commit)" == "$JOURNAL_REQUESTED_SHA" ]] \
      && validate_rollback_artifacts "$RADON_DIR" \
      && validate_target_python_env "$RADON_DIR" \
      && deploy_gate; then
      record_deploy_marker "$JOURNAL_REQUESTED_SHA" || return 1
      write_green_marker "$JOURNAL_REQUESTED_SHA" || return 1
      sudo "$DEPLOY_ROOT_HELPER" commit-transition || return 1
      DEPLOY_RELEASE_UNVERIFIED=0
      finalize_release_artifacts "$JOURNAL_STAGE_DIR" "$JOURNAL_BACKUP_DIR"
      log_success "Finalized previously verified release ${JOURNAL_REQUESTED_SHA:0:7}"
      return 0
    fi
    log_error "Verified transition no longer passes its gate; restoring previous release"
  else
    log_warn "Recovering unverified deploy transition at phase ${JOURNAL_PHASE}"
  fi

  DEPLOY_TEARDOWN_STARTED=1
  DEPLOY_SERVICES_RECOVERED=0
  sudo "$DEPLOY_ROOT_HELPER" stop-clean || return 1
  restore_release_backup "$JOURNAL_PREVIOUS_SHA" "$JOURNAL_BACKUP_DIR" || return 1
  sudo "$DEPLOY_ROOT_HELPER" recover || return 1
  DEPLOY_SERVICES_RECOVERED=1
  DEPLOY_TEARDOWN_STARTED=0
  wait_for_recovered_units || return 1
  sudo "$DEPLOY_ROOT_HELPER" verify-restored || return 1
  sudo "$DEPLOY_ROOT_HELPER" commit-transition || return 1
  finalize_release_artifacts "$JOURNAL_STAGE_DIR" "$JOURNAL_BACKUP_DIR"
  log_success "Restored previous release ${JOURNAL_PREVIOUS_SHA:0:7} from durable transition state"
}

# Returns 0 only when the IB gateway socket is listening AND the session is
# authenticated (not mid-restart, not awaiting 2FA). Parses /health with
# python3 (always present on the VPS). Any curl/parse failure is treated as
# "not ready" -- conservative by design.
gateway_data_plane_ready() {
  local body
  body="$(curl -sf --max-time "$HEALTH_CURL_TIMEOUT" "$HEALTH_URL" 2>/dev/null)" || return 1
  printf '%s' "$body" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
g = d.get("ib_gateway", {}) or {}
ok = bool(g.get("port_listening")) and g.get("auth_state") == "authenticated"
sys.exit(0 if ok else 1)
' >/dev/null 2>&1
}

# Bounded wait for the IB gateway data plane before restarting the relay/api.
# A relay restart that lands while the gateway is mid-restart (port refusing)
# or awaiting 2FA reconnects into a dead data plane -- the recurring "no
# realtime data" outage. We wait up to GATEWAY_READY_RETRIES*GATEWAY_READY_WAIT
# for it to settle. If it never does (e.g. an unrelated 2FA prompt is pending)
# we DO NOT block the code deploy forever: warn loudly and proceed. The relay
# now self-heals on gateway recovery and raises a service_health alert if it
# cannot, so a degraded gateway is surfaced rather than silently looped.
wait_for_gateway_ready() {
  local attempt=1
  while (( attempt <= GATEWAY_READY_RETRIES )); do
    if gateway_data_plane_ready; then
      log_success "IB gateway data plane ready (authenticated + listening)"
      return 0
    fi
    log_warn "Gateway not ready (attempt ${attempt}/${GATEWAY_READY_RETRIES}) -- waiting before relay/api restart"
    (( attempt++ ))
    sleep "$GATEWAY_READY_WAIT"
  done
  log_warn "IB gateway still not authenticated after $(( GATEWAY_READY_RETRIES * GATEWAY_READY_WAIT ))s."
  log_warn "Proceeding with restart anyway -- relay self-heals on recovery + alerts via service_health if it cannot."
  return 1
}

stop_services_for_transition() {
  # Gate FIRST, while the CURRENT (not-yet-restarted) radon-api still serves
  # /health, so we read the IB gateway's actual auth state -- not a freshly
  # restarted api's pool warmup (which reads not-authenticated for ~a minute,
  # producing a false 60s wait + bogus warning). This prevents cycling the
  # relay into a mid-restart / awaiting-2FA gateway (the recurring no-realtime
  # outage). Bounded; warns and proceeds if the gateway never settles.
  if ! wait_for_gateway_ready; then
    log_warn "Gateway readiness is advisory for a code-controlled deploy"
  fi

  DEPLOY_TEARDOWN_STARTED=1
  DEPLOY_SERVICES_RECOVERED=0
  # The root-owned helper stops the exact unit set, verifies every unit reached
  # inactive, and only then removes the fixed replica paths. Any failure exits
  # through the armed EXIT recovery path without deleting the replica.
  sudo "$DEPLOY_ROOT_HELPER" stop-clean
}

start_services_after_transition() {
  # Embedded replicas are retired. Start the complete release topology once.
  sudo "$DEPLOY_ROOT_HELPER" restart-managed
  DEPLOY_SERVICES_RECOVERED=1
  DEPLOY_TEARDOWN_STARTED=0
}

restart_services() {
  local requested_sha="${1:-}"
  local previous_sha="${2:-}"
  local status=0

  if [[ -n "$requested_sha" ]]; then
    prepare_release_transition "$requested_sha" "$previous_sha" || return $?
  fi
  stop_services_for_transition
  if [[ -n "$requested_sha" ]]; then
    activate_staged_release "$requested_sha" "$previous_sha" || status=$?
    if (( status != 0 )); then
      log_error "Activation failed; recovering the previous running release"
      recover_pending_transition || true
      return "$status"
    fi
  fi
  start_services_after_transition
}

# -- Post-deploy gate ---------------------------------------------------------
#
# Layered gate that measures ONLY what the deployed code controls. The old
# check_health() curled the FULL /health of the service it had just replaced,
# so an IB-side wedge (or 2FA prompt) could fail -- or hang -- a perfectly
# good code deploy and roll it back (2026-06-11: step SIGTERM at ~3min,
# rollback landed on an equally broken commit, the fix had to ship outside CI).
#
# Layers:
#   1. systemctl is-active on every restarted unit
#   2. :8321/health/lite process probe, with no IB dependency
#   3. Next.js HTTP response
#   4. relay TCP listener and HTTP 426 response
#   5. :8330/status isolated daemon, ADVISORY LOG ONLY
#   6. core units remain active with unchanged NRestarts for 40 seconds

check_units_active() {
  local failed=()
  local unit
  for unit in "${SERVICES[@]}"; do
    if ! systemctl is-active --quiet "${unit}.service"; then
      failed+=("$unit")
    fi
  done
  if (( ${#failed[@]} > 0 )); then
    log_error "[gate] units not active: ${failed[*]}"
    return 1
  fi
  log_success "[gate] all restarted units active: ${SERVICES[*]}"
  return 0
}

check_units_stable() {
  local restart_counts=()
  local unstable=()
  local unit count previous index=0

  for unit in "${SERVICES[@]}"; do
    count="$(systemctl show "${unit}.service" --property=NRestarts --value 2>/dev/null)" || {
      log_error "[gate] could not read restart counter for ${unit}"
      return 1
    }
    [[ "$count" =~ ^[0-9]+$ ]] || {
      log_error "[gate] invalid restart counter for ${unit}"
      return 1
    }
    restart_counts+=("$count")
  done

  sleep "$UNIT_STABILITY_SECONDS"
  for unit in "${SERVICES[@]}"; do
    previous="${restart_counts[$index]}"
    index=$((index + 1))
    if ! systemctl is-active --quiet "${unit}.service"; then
      unstable+=("${unit}:inactive")
      continue
    fi
    count="$(systemctl show "${unit}.service" --property=NRestarts --value 2>/dev/null)" || {
      unstable+=("${unit}:unknown")
      continue
    }
    if [[ ! "$count" =~ ^[0-9]+$ || "$count" != "$previous" ]]; then
      unstable+=("${unit}:restart counter changed")
    fi
  done
  if (( ${#unstable[@]} > 0 )); then
    log_error "[gate] units failed stability window: ${unstable[*]}"
    return 1
  fi
  log_success "[gate] core units stable for ${UNIT_STABILITY_SECONDS}s with no restarts"
  return 0
}

check_release_topology_restored() {
  if sudo "$DEPLOY_ROOT_HELPER" verify-restored; then
    log_success "[gate] predeploy service and timer topology restored"
    return 0
  fi
  log_error "[gate] predeploy service and timer topology was not restored"
  return 1
}

check_health_lite() {
  local attempt=1
  while (( attempt <= HEALTH_RETRIES )); do
    if curl -sf --max-time "$HEALTH_CURL_TIMEOUT" "$HEALTH_LITE_URL" >/dev/null 2>&1; then
      log_success "[gate] /health/lite responding"
      return 0
    fi
    log_warn "[gate] /health/lite attempt ${attempt}/${HEALTH_RETRIES} failed"
    (( attempt++ ))
    sleep "$HEALTH_RETRY_WAIT"
  done
  log_error "[gate] /health/lite never responded (${HEALTH_RETRIES} attempts)"
  return 1
}

check_nextjs_http() {
  local attempt=1
  local status
  while (( attempt <= SURFACE_RETRIES )); do
    status="$(curl -sS --max-time "$SURFACE_TIMEOUT" -o /dev/null -w '%{http_code}' "$NEXTJS_URL" 2>/dev/null)" || status="000"
    if [[ "$status" =~ ^[1-4][0-9][0-9]$ ]]; then
      log_success "[gate] Next.js HTTP responding (${status})"
      return 0
    fi
    log_warn "[gate] Next.js HTTP attempt ${attempt}/${SURFACE_RETRIES} failed (${status})"
    (( attempt++ ))
    sleep "$SURFACE_RETRY_WAIT"
  done
  log_error "[gate] Next.js HTTP never returned a non-5xx response"
  return 1
}

check_relay_listener() {
  local attempt=1
  while (( attempt <= SURFACE_RETRIES )); do
    if python3 - "$RELAY_HOST" "$RELAY_PORT" "$SURFACE_TIMEOUT" <<'PY' >/dev/null 2>&1
import socket
import sys

with socket.create_connection(
    (sys.argv[1], int(sys.argv[2])), timeout=float(sys.argv[3])
):
    pass
PY
    then
      log_success "[gate] relay listener accepting TCP connections"
      return 0
    fi
    log_warn "[gate] relay listener attempt ${attempt}/${SURFACE_RETRIES} failed"
    (( attempt++ ))
    sleep "$SURFACE_RETRY_WAIT"
  done
  log_error "[gate] relay listener never accepted a connection"
  return 1
}

check_relay_http() {
  local attempt=1
  local status
  while (( attempt <= SURFACE_RETRIES )); do
    status="$(curl -sS --max-time "$SURFACE_TIMEOUT" -o /dev/null -w '%{http_code}' "$RELAY_URL" 2>/dev/null)" || status="000"
    if [[ "$status" =~ ^[1-4][0-9][0-9]$ ]]; then
      log_success "[gate] relay HTTP responding (${status})"
      return 0
    fi
    log_warn "[gate] relay HTTP attempt ${attempt}/${SURFACE_RETRIES} failed (${status})"
    (( attempt++ ))
    sleep "$SURFACE_RETRY_WAIT"
  done
  log_error "[gate] relay HTTP never returned a non-5xx response"
  return 1
}

report_isolated_health() {
  # Advisory second sensor. The :8330 daemon is decoupled from the cascade
  # on purpose, so it survives a broken deploy -- but its verdict includes
  # IB-gateway state, so it must never trigger a rollback. Log and move on.
  local body
  if body="$(curl -sf --max-time "$HEALTH_CURL_TIMEOUT" "$ISOLATED_STATUS_URL" 2>/dev/null)"; then
    log_info "[gate] isolated :8330/status (advisory): ${body:0:500}"
  else
    log_warn "[gate] isolated :8330/status unreachable (advisory only -- not a gate failure)"
  fi
  return 0
}

deploy_gate() {
  # Run every layer even after a failure so the deploy logs the complete picture.
  local gate_rc=0
  check_units_active || gate_rc=1
  check_release_topology_restored || gate_rc=1
  check_health_lite || gate_rc=1
  check_nextjs_http || gate_rc=1
  check_relay_listener || gate_rc=1
  check_relay_http || gate_rc=1
  report_isolated_health
  check_units_stable || gate_rc=1
  check_release_topology_restored || gate_rc=1
  return "$gate_rc"
}

# -- Deploy marker (DUR-11) --------------------------------------------------

record_deploy_marker() {
  # Upsert service='deploy' (state=ok, last_error=deployed SHA) into Turso
  # service_health. Migration 0011's UPDATE trigger has an explicit
  # `OR NEW.service = 'deploy'` arm, so every marker (state never changes)
  # lands in the append-only service_health_events history and renders on the
  # /admin Reliability Strip's "Last deploy" tile.
  #
  # Bounded (timeout 15) + non-fatal (|| log_warn): a failed marker insert
  # must NEVER fail or roll back a deploy. Runs only on the green-gate path.
  local sha="$1"
  timeout 15 "${VENV_DIR}/bin/python" - "$sha" "$ENV_FILE_DEFAULT" <<'PYEOF' || log_warn "[marker] deploy marker insert failed (non-fatal)"
import sys
from datetime import datetime, timezone

from dotenv import dotenv_values

import libsql_experimental as libsql

values = dotenv_values(sys.argv[2], interpolate=False)
db = libsql.connect(values["TURSO_DB_URL"], auth_token=values["TURSO_AUTH_TOKEN"])
now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
db.execute(
    """INSERT INTO service_health (service, state, last_attempt_started_at, last_attempt_finished_at, last_error, updated_at)
       VALUES ('deploy', 'ok', NULL, ?, ?, ?)
       ON CONFLICT(service) DO UPDATE SET
         state                    = excluded.state,
         last_attempt_finished_at = excluded.last_attempt_finished_at,
         last_error               = excluded.last_error,
         updated_at               = excluded.updated_at""",
    (now, sys.argv[1], now),
)
db.commit()
print(f"[marker] deploy marker recorded: {sys.argv[1][:7]} @ {now}")
PYEOF
}

# -- Rollback ---------------------------------------------------------------

rollback() {
  local prev_commit="$1"

  log_error "Post-deploy gate failed."
  log_warn "Rolling back to ${prev_commit}..."

  if ! recover_pending_transition; then
    log_error "Rollback: durable recovery failed. Manual intervention required."
    exit 2
  fi

  log_info "Waiting ${HEALTH_WAIT_SECONDS}s before rollback verification..."
  sleep "$HEALTH_WAIT_SECONDS"
  if ! deploy_gate; then
    log_error "Rollback gate failed for ${prev_commit}; manual intervention required."
    exit 3
  fi

  log_error "Rollback complete. Previous release ${prev_commit} passed the deploy gate."
  exit 1
}

# -- Main -------------------------------------------------------------------

main() {
  local requested_sha="${1:-${RADON_DEPLOY_SHA:-}}"
  if [[ ! "$requested_sha" =~ ^[0-9a-f]{40}$ ]]; then
    log_error "Usage: scripts/deploy.sh <40-character commit SHA>"
    return 2
  fi

  trap 'handle_deploy_signal TERM' TERM INT HUP
  log_info "Starting deployment (timeout: ${DEPLOY_TIMEOUT}s)..."

  if ! preflight_control_plane; then
    log_error "Aborting deploy: installed control plane is not ready for this release."
    return 1
  fi

  # Gate the deploy on required env vars BEFORE any service is touched.
  # A missing var here (e.g. PUSHOVER_TOKEN) used to silently degrade the
  # notification stack for a week.
  if ! preflight_env "$ENV_FILE_DEFAULT"; then
    log_error "Aborting deploy: env preflight failed."
    log_error "Fix ${ENV_FILE_DEFAULT}; the escape hatch skips required-key shape only."
    return 1
  fi

  local prev_commit
  prev_commit="$(current_commit)"
  PREVIOUS_RELEASE_SHA="$prev_commit"
  log_info "Current commit: ${prev_commit}"

  log_info "Fetching origin/main to verify requested commit ${requested_sha:0:7}..."
  git -C "$RADON_DIR" fetch origin main
  local origin_tip
  origin_tip="$(git -C "$RADON_DIR" rev-parse origin/main)"
  if [[ "$requested_sha" != "$origin_tip" ]]; then
    log_error "Requested commit is stale; only the fetched origin/main tip may deploy"
    return 1
  fi
  if ! verify_tracked_drift_matches_target "$requested_sha"; then
    log_error "Refusing to overwrite divergent tracked production changes"
    return 1
  fi

  if [[ "$prev_commit" == "$requested_sha" ]] && green_marker_matches "$requested_sha"; then
    log_info "Commit ${requested_sha:0:7} already has a durable green marker; rechecking live gate"
    if deploy_gate; then
      trap - TERM INT HUP
      log_success "Deploy already green: $(short_log)"
      return 0
    else
      log_error "Previously green release no longer passes the live gate; rebuilding and restarting it"
      quarantine_green_marker "$requested_sha"
    fi
  fi

  if [[ "$prev_commit" == "$requested_sha" ]]; then
    log_warn "HEAD matches ${requested_sha:0:7}, but no matching green marker exists; resuming full deploy"
  fi

  log_info "Deploying ${prev_commit:0:7} -> ${requested_sha:0:7}"

  log_info "Building target release in an isolated worktree..."
  build_staged_release "$requested_sha"

  log_info "Promoting staged artifacts and restarting services..."
  restart_services "$requested_sha" "$prev_commit"

  log_info "Waiting ${HEALTH_WAIT_SECONDS}s for services to start..."
  sleep "$HEALTH_WAIT_SECONDS"

  log_info "Running post-deploy gate..."
  if ! deploy_gate; then
    rollback "$prev_commit"
  else
    write_transition_phase verified
    record_deploy_marker "$requested_sha"
    write_green_marker "$requested_sha"
    sudo "$DEPLOY_ROOT_HELPER" commit-transition
    DEPLOY_RELEASE_UNVERIFIED=0
    finalize_release_artifacts
  fi

  echo ""
  trap - TERM INT HUP
  log_success "Deploy successful: $(short_log)"
}

run_deploy_internal() {
  # Ordinary `set -e` failures do not raise TERM. EXIT is therefore the final
  # recovery boundary for any nonzero path after restart_services has stopped
  # units. A successful main() disarms it before returning.
  trap 'handle_deploy_exit "$?"' EXIT
  main "$@"
  trap - EXIT
}

supervisor_signal_status() {
  case "${1:-TERM}" in
    TERM) printf '143\n' ;;
    INT) printf '130\n' ;;
    HUP) printf '129\n' ;;
    *) printf '1\n' ;;
  esac
}

supervisor_recover_once() {
  if (( SUPERVISOR_RECOVERY_DONE == 1 )); then
    return 0
  fi
  SUPERVISOR_RECOVERY_DONE=1
  if [[ -f "$TRANSITION_JOURNAL_FILE" ]]; then
    recover_pending_transition || {
      log_error "Outer deploy recovery could not resolve the durable transition"
      return 1
    }
  else
    log_info "No durable deploy transition exists; no service recovery is required"
  fi
}

supervisor_group_has_running_processes() {
  local pgid="$1"
  ps -axo pgid=,stat= | awk -v target="$pgid" '
    $1 == target && $2 !~ /^Z/ { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

resolve_supervised_process_group() {
  local supervisor_pgid
  local candidate
  local attempt=1

  supervisor_pgid="$(ps -o pgid= -p "$$" 2>/dev/null | tr -d ' ')"
  while (( attempt <= 100 )); do
    candidate="$(ps -o pgid= -p "$SUPERVISED_TIMEOUT_PID" 2>/dev/null | tr -d ' ')"
    if [[ -n "$candidate" && "$candidate" != "$supervisor_pgid" ]]; then
      SUPERVISED_TIMEOUT_PGID="$candidate"
      return 0
    fi
    kill -0 "$SUPERVISED_TIMEOUT_PID" 2>/dev/null || return 1
    (( attempt++ ))
    sleep 0.01
  done
  return 1
}

terminate_supervised_process_group() {
  local deadline

  [[ -n "$SUPERVISED_TIMEOUT_PID" ]] || return 0
  if [[ -z "$SUPERVISED_TIMEOUT_PGID" ]]; then
    resolve_supervised_process_group || true
  fi

  if [[ -n "$SUPERVISED_TIMEOUT_PGID" ]]; then
    kill -TERM -- "-${SUPERVISED_TIMEOUT_PGID}" 2>/dev/null || true
    deadline=$(( SECONDS + DEPLOY_KILL_AFTER ))
    while supervisor_group_has_running_processes "$SUPERVISED_TIMEOUT_PGID" \
      && (( SECONDS < deadline )); do
      sleep 0.1
    done
    if supervisor_group_has_running_processes "$SUPERVISED_TIMEOUT_PGID"; then
      log_warn "Supervised deploy process group ignored TERM; escalating to KILL"
      kill -KILL -- "-${SUPERVISED_TIMEOUT_PGID}" 2>/dev/null || true
    fi
  else
    # GNU timeout normally creates a distinct process group before exec. If it
    # exited during the setup race, a direct signal and wait are still bounded.
    kill -TERM "$SUPERVISED_TIMEOUT_PID" 2>/dev/null || true
  fi

  wait "$SUPERVISED_TIMEOUT_PID" 2>/dev/null || true
  SUPERVISED_TIMEOUT_PID=""
  SUPERVISED_TIMEOUT_PGID=""
}

handle_supervisor_signal() {
  local signal_name="${1:-TERM}"
  local exit_code
  exit_code="$(supervisor_signal_status "$signal_name")"

  # Ignore repeated terminal signals while the first handler owns cleanup.
  trap '' TERM INT HUP
  terminate_supervised_process_group
  supervisor_recover_once
  log_error "Deploy supervisor received ${signal_name}; recovery completed before lock release"
  exec 9>&-
  exit "$exit_code"
}

supervise_deploy_command() {
  # FD 9 and its flock are owned by this outer supervisor. The timeout process
  # closes FD 9 before exec, so neither the inner deploy nor any descendant can
  # keep the production lock alive after the supervisor returns.
  if ! exec 9>"$DEPLOY_LOCK_FILE"; then
    log_error "Cannot open production deploy lock ${DEPLOY_LOCK_FILE}"
    return 1
  fi
  if ! flock -n 9; then
    log_error "Another deployment owns ${DEPLOY_LOCK_FILE}; refusing to overlap"
    exec 9>&-
    return 75
  fi
  log_success "Acquired production deploy lock"

  SUPERVISED_TIMEOUT_PID=""
  SUPERVISED_TIMEOUT_PGID=""
  SUPERVISOR_RECOVERY_DONE=0
  trap 'handle_supervisor_signal TERM' TERM
  trap 'handle_supervisor_signal INT' INT
  trap 'handle_supervisor_signal HUP' HUP

  if [[ -f "$TRANSITION_JOURNAL_FILE" ]]; then
    log_warn "Found an unfinished deploy transition; recovering it before starting new work"
    if ! recover_pending_transition; then
      log_error "Unfinished transition recovery failed; refusing a new deploy"
      trap - TERM INT HUP
      exec 9>&-
      return 76
    fi
  fi

  local exit_code=0
  timeout --signal=TERM --kill-after="${DEPLOY_KILL_AFTER}s" \
    "${DEPLOY_TIMEOUT}s" "$@" 9>&- &
  SUPERVISED_TIMEOUT_PID=$!
  if wait "$SUPERVISED_TIMEOUT_PID"; then
    exit_code=0
  else
    exit_code=$?
  fi
  SUPERVISED_TIMEOUT_PID=""
  SUPERVISED_TIMEOUT_PGID=""

  case "$exit_code" in
    124|137|143)
      log_error "Deploy supervisor terminated the inner process (status ${exit_code}); recovering managed services"
      supervisor_recover_once
      ;;
  esac
  trap - TERM INT HUP
  exec 9>&-
  return "$exit_code"
}

run_bounded_deploy() {
  if [[ ! "$DEPLOY_TIMEOUT" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$DEPLOY_KILL_AFTER" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$UNIT_STABILITY_SECONDS" =~ ^[0-9]+$ ]]; then
    log_error "deploy timeout, kill-after, and stability budgets must be integer seconds"
    return 2
  fi

  export RADON_DEPLOY_INTERNAL=1
  supervise_deploy_command bash "$0" "$@"
}

# Sourcing guard: `source deploy.sh` loads functions without deploying,
# so the gate can be dry-exercised standalone (and tested) safely.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ "${RADON_DEPLOY_INTERNAL:-0}" == "1" ]]; then
    run_deploy_internal "$@"
  else
    run_bounded_deploy "$@"
  fi
fi
