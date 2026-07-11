#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# migrate-data.sh -- Snapshot and transfer data/*.json to VPS
# Creates a tar.gz, transfers via scp, extracts, and verifies file count.
# The snapshot is kept on the VPS for rollback.
# ---------------------------------------------------------------------------

readonly DEFAULT_SOURCE="$HOME/dev/apps/finance/radon"
readonly DEFAULT_TARGET="radon@ib-gateway:~/radon"
readonly SNAPSHOT_NAME="radon-data-snapshot-$(date +%Y%m%d-%H%M%S).tar.gz"
readonly SNAPSHOT_PATH="/tmp/${SNAPSHOT_NAME}"

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

# -- Usage ------------------------------------------------------------------

usage() {
  cat <<HELP
Usage: $(basename "$0") [OPTIONS]

Migrate data/*.json files from local radon to VPS.

Options:
  --source DIR    Local radon directory (default: ${DEFAULT_SOURCE})
  --target HOST   Remote target in scp format (default: ${DEFAULT_TARGET})
  -h, --help      Show this help message

Examples:
  $(basename "$0")
  $(basename "$0") --source /path/to/radon --target user@host:~/radon
HELP
  exit 0
}

# -- Argument parsing -------------------------------------------------------

parse_args() {
  SOURCE="$DEFAULT_SOURCE"
  TARGET="$DEFAULT_TARGET"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source)
        SOURCE="${2:?--source requires a directory}"
        shift 2
        ;;
      --target)
        TARGET="${2:?--target requires a host:path}"
        shift 2
        ;;
      -h|--help)
        usage
        ;;
      *)
        log_error "Unknown option: $1"
        usage
        ;;
    esac
  done
}

# -- Extract host and path from TARGET -------------------------------------

parse_target() {
  TARGET_HOST="${TARGET%%:*}"
  TARGET_PATH="${TARGET#*:}"

  if [[ "$TARGET_PATH" =~ [^a-zA-Z0-9_./~-] ]]; then
    log_error "Target path contains unsafe characters: ${TARGET_PATH}"
    exit 1
  fi
}

# -- Snapshot ---------------------------------------------------------------

create_snapshot() {
  local data_dir="${SOURCE}/data"

  if [[ ! -d "$data_dir" ]]; then
    log_error "Data directory not found: ${data_dir}"
    exit 1
  fi

  local json_count
  json_count=$(find "$data_dir" -maxdepth 1 -name '*.json' | wc -l | tr -d ' ')

  if [[ "$json_count" -eq 0 ]]; then
    log_error "No .json files found in ${data_dir}"
    exit 1
  fi

  log_info "Found ${json_count} JSON files in ${data_dir}"
  log_info "Creating snapshot: ${SNAPSHOT_PATH}"

  find "$data_dir" -maxdepth 1 -name '*.json' -printf 'data/%f\n' \
    | tar czf "$SNAPSHOT_PATH" -C "$SOURCE" -T -

  local size
  size=$(du -h "$SNAPSHOT_PATH" | cut -f1)
  log_success "Snapshot created (${size})"

  echo "$json_count"
}

# -- Transfer ---------------------------------------------------------------

transfer_snapshot() {
  log_info "Transferring snapshot to ${TARGET_HOST}..."
  if ! scp "$SNAPSHOT_PATH" "${TARGET_HOST}:${TARGET_PATH}/"; then
    log_error "scp transfer failed. Snapshot preserved locally at: ${SNAPSHOT_PATH}"
    exit 1
  fi
  log_success "Snapshot transferred"
}

# -- Extract on VPS ---------------------------------------------------------

extract_on_vps() {
  log_info "Extracting snapshot on VPS..."
  ssh "$TARGET_HOST" "cd ${TARGET_PATH} && tar xzf ${SNAPSHOT_NAME}"
  log_success "Snapshot extracted"
}

# -- Verify -----------------------------------------------------------------

verify_file_count() {
  local expected_count="$1"

  log_info "Verifying file count on VPS..."
  local remote_count
  remote_count=$(ssh "$TARGET_HOST" "find ${TARGET_PATH}/data -maxdepth 1 -name '*.json' | wc -l" | tr -d ' ')

  if [[ "$remote_count" -ne "$expected_count" ]]; then
    log_error "File count mismatch: local=${expected_count}, remote=${remote_count}"
    log_warn "Snapshot preserved on VPS at: ${TARGET_PATH}/${SNAPSHOT_NAME}"
    exit 1
  fi

  log_success "File count verified: ${remote_count} files match"
}

# -- Cleanup info -----------------------------------------------------------

print_summary() {
  local count="$1"
  echo ""
  log_success "Migration complete: ${count} files transferred"
  echo -e "  Snapshot kept on VPS: ${GREEN}${TARGET_PATH}/${SNAPSHOT_NAME}${NC}"
  echo -e "  Local snapshot:       ${GREEN}${SNAPSHOT_PATH}${NC}"
  echo ""
  echo -e "  ${YELLOW}Rollback:${NC} ssh ${TARGET_HOST} 'cd ${TARGET_PATH} && tar xzf ${SNAPSHOT_NAME}'"
}

# -- Main -------------------------------------------------------------------

main() {
  parse_args "$@"
  parse_target

  log_info "Source: ${SOURCE}"
  log_info "Target: ${TARGET}"
  echo ""

  local file_count
  file_count=$(create_snapshot)

  transfer_snapshot
  extract_on_vps
  verify_file_count "$file_count"
  print_summary "$file_count"
}

main "$@"
