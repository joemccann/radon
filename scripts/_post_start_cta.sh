#!/bin/bash
# ---------------------------------------------------------------------------
# _post_start_cta.sh — One-shot CTA cache refresh after dev stack starts.
#
# Spec:
#   1. Run scripts/cta_sync_service.py with `--source startup`.
#   2. The service is idempotent (skips when today's cache exists, holds a
#      lock so concurrent runs no-op).
#   3. All output appended to logs/cta-startup-sync.log so a missed sync is
#      findable.
#
# Why this exists:
#   The launchd job (com.radon.cta-sync) only fires at scheduled post-close
#   windows. If the machine sleeps through a window or the launchd agent
#   never loaded on this box, /cta surfaces "CTA CACHE STALE" with no
#   recovery path. Wiring a refresh into dev/web-server startup means every
#   time the user runs cloud.sh / local.sh today's CTA pull is requested.
#
# Invocation:
#   Backgrounded from cloud.sh / local.sh just before `exec npm run dev`:
#     ( "$SCRIPT_DIR/_post_start_cta.sh" & )
#
# Safe to run by hand for debugging:
#   scripts/_post_start_cta.sh
# ---------------------------------------------------------------------------

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/cta-startup-sync.log"
PYTHON_BIN="${RADON_PYTHON_BIN:-python3.13}"

mkdir -p "$LOG_DIR"

stamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(stamp)" "$*" >>"$LOG_FILE"
}

log "Starting CTA cache refresh via cta_sync_service.py..."

# `--source startup` flags the run in the health ledger so on-call can tell
# scheduled launchd runs apart from dev-startup catch-up runs. The service
# itself owns lock/retry/backoff — we just kick it.
if "$PYTHON_BIN" "$SCRIPT_DIR/cta_sync_service.py" --source startup >>"$LOG_FILE" 2>&1; then
  log "CTA sync exited 0."
else
  rc=$?
  log "CTA sync exited $rc — see preceding lines for traceback."
fi
