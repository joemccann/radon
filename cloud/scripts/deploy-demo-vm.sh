#!/usr/bin/env bash
# deploy-demo-vm.sh -- ship one exact commit of the FastAPI backend to the demo VM.
#
#   cloud/scripts/deploy-demo-vm.sh [--dry-run] <40-hex-sha>
#
# Run FROM THE OPERATOR HOST (the Mac mini), which has `ssh radon-demo`. GitHub
# runners cannot do this: fw-radon-demo admits SSH only from the recovery /32.
#
# 1. `git archive <sha> scripts requirements.txt pyproject.toml` -- from the
#    object store, so no macOS AppleDouble (._*) files and no local WIP.
# 2. Ships it over ssh and runs cloud/scripts/deploy-demo-vm-remote.sh on the VM,
#    which keeps the VM's app/data/ seed, writes REVISION=<sha>, tags the current
#    image :rollback, rebuilds, and health-gates (internal /health revision,
#    container isolation, edge /health through Caddy with the real Host header,
#    /health/lite 401 without and 200 with the service token). Any gate failure
#    rolls back on the VM.
# 3. From here, an off-box GET of https://demo-api.radon.run/health must report
#    the same revision; otherwise the VM is told to roll back.
#
# Idempotent: re-running a live sha is a no-op on the VM. --dry-run builds and
# inspects the archive and prints the plan without touching any host.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_SCRIPT="$SCRIPT_DIR/deploy-demo-vm-remote.sh"
REPO_ROOT="${DEMO_DEPLOY_REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
HOST="${DEMO_HOST:-radon-demo}"
SSH="${SSH:-ssh}"
CURL="${CURL:-curl}"
PUBLIC_URL="${DEMO_PUBLIC_URL:-https://demo-api.radon.run}"
REMOTE_ROOT="/opt/radon-demo"
REMOTE_INCOMING="$REMOTE_ROOT/.deploy-incoming"
LOCK_DIR="${DEMO_DEPLOY_LOCK_DIR:-${TMPDIR:-/tmp}/radon-deploy-demo-vm.lock}"
EXTERNAL_ATTEMPTS="${DEMO_EXTERNAL_ATTEMPTS:-5}"
EXTERNAL_POLL_SECS="${DEMO_EXTERNAL_POLL_SECS:-5}"
PATHS=(scripts requirements.txt pyproject.toml)

log() { printf '[deploy-demo-vm] %s\n' "$*"; }
die() { printf '[deploy-demo-vm] ERROR: %s\n' "$*" >&2; exit 1; }
usage() { die "usage: $0 [--dry-run] <40-hex-sha>"; }

DRY_RUN=0
SHA=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage ;;
    -*) die "unknown flag: $1" ;;
    *) [[ -z "$SHA" ]] || usage; SHA="$1" ;;
  esac
  shift
done
[[ -n "$SHA" ]] || usage
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "sha must be a full 40-char lowercase hex commit id, got '$SHA'"
[[ -f "$REMOTE_SCRIPT" ]] || die "missing $REMOTE_SCRIPT"
git -C "$REPO_ROOT" cat-file -e "$SHA^{commit}" 2>/dev/null \
  || die "commit $SHA not found in $REPO_ROOT (git fetch origin first)"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/radon-demo-deploy.XXXXXX")"
LOCKED=0
cleanup() {
  rm -rf "$WORK"
  if [[ "$LOCKED" == 1 ]]; then rm -rf "$LOCK_DIR"; fi
}
trap cleanup EXIT

TARBALL="$WORK/$SHA.tar"
git -C "$REPO_ROOT" archive --format=tar -o "$TARBALL" "$SHA" "${PATHS[@]}"
LISTING="$WORK/listing"
tar -tf "$TARBALL" > "$LISTING"

if grep -Eq '(^|/)\._' "$LISTING"; then die "archive carries AppleDouble (._*) entries"; fi
if grep -Eq '^data(/|$)' "$LISTING"; then die "archive carries a top-level data/; the VM seed must never be overwritten"; fi
grep -qx 'scripts/api/server.py' "$LISTING" || die "archive lacks scripts/api/server.py"
grep -qx 'requirements.txt' "$LISTING" || die "archive lacks requirements.txt"
FILE_COUNT="$(grep -vc '/$' "$LISTING" || true)"

log "sha:     $SHA ($(git -C "$REPO_ROOT" log -1 --format='%cs %s' "$SHA"))"
log "archive: $FILE_COUNT files from ${PATHS[*]} (0 AppleDouble, no data/)"
log "host:    $HOST:$REMOTE_ROOT/app (app/data/ preserved, previous tree -> app.bak)"
log "image:   current -> radon-demo-api:rollback, then docker compose build api && up -d api"
log "gates:   internal /health revision, TEST_MODE=1 + radon-demo TURSO_DB_URL in container,"
log "         edge /health via Caddy Host pin, /health/lite 401/200, off-box $PUBLIC_URL/health"
log "failure: VM restores app.bak + radon-demo-api:rollback"

if [[ "$DRY_RUN" == 1 ]]; then
  log "DRY RUN: no host contacted, nothing shipped"
  exit 0
fi

mkdir "$LOCK_DIR" 2>/dev/null || die "another deploy-demo-vm run holds $LOCK_DIR"
LOCKED=1

REMOTE_TAR="$REMOTE_INCOMING/$SHA.tar"
log "shipping archive to $HOST:$REMOTE_TAR"
"$SSH" "$HOST" "mkdir -p '$REMOTE_INCOMING' && cat > '$REMOTE_TAR'" < "$TARBALL"

log "running remote deploy"
REMOTE_LOG="$WORK/remote.log"
if ! "$SSH" "$HOST" "bash -s -- deploy '$SHA' '$REMOTE_TAR'" < "$REMOTE_SCRIPT" | tee "$REMOTE_LOG"; then
  die "remote deploy failed (the VM rolled back if it got past the swap)"
fi
NOOP=0
if grep -q "already live: $SHA" "$REMOTE_LOG"; then NOOP=1; fi

external_ok() {
  local i body
  for (( i = 1; i <= EXTERNAL_ATTEMPTS; i++ )); do
    if body="$("$CURL" -fsS --max-time 10 "$PUBLIC_URL/health" 2>/dev/null)" \
      && [[ "$body" == *"\"revision\":\"$SHA\""* ]]; then
      return 0
    fi
    (( i < EXTERNAL_ATTEMPTS )) && sleep "$EXTERNAL_POLL_SECS"
  done
  return 1
}

if ! external_ok; then
  # A no-op run changed nothing; rolling back would revert a deploy that was
  # already serving, so only report.
  [[ "$NOOP" == 0 ]] || die "off-box $PUBLIC_URL/health did not report $SHA (no-op run, nothing rolled back)"
  log "off-box $PUBLIC_URL/health did not report revision $SHA; rolling back"
  "$SSH" "$HOST" "bash -s -- rollback" < "$REMOTE_SCRIPT" || die "ROLLBACK FAILED; inspect $HOST by hand"
  die "deploy of $SHA rolled back"
fi

log "LIVE $SHA at $PUBLIC_URL"
