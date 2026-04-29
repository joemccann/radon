#!/bin/bash
# ---------------------------------------------------------------------------
# _post_start_journal.sh — One-shot journal rehydrate after dev stack starts.
#
# Spec:
#   1. Wait until FastAPI's /health reports ib_gateway.port_listening: true
#      (or 60s timeout).
#   2. POST /journal/rehydrate exactly once.
#   3. Append both phases to logs/journal-rehydrate.log so failures surface.
#
# Invocation:
#   Backgrounded from cloud.sh / local.sh just before `exec npm run dev`:
#     ( "$SCRIPT_DIR/_post_start_journal.sh" & )
#
# Safe to run by hand for debugging:
#   scripts/_post_start_journal.sh
# ---------------------------------------------------------------------------

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/journal-rehydrate.log"
HEALTH_URL="http://127.0.0.1:8321/health"
REHYDRATE_URL="http://127.0.0.1:8321/journal/rehydrate"
TIMEOUT_SECS=60

mkdir -p "$LOG_DIR"

stamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(stamp)" "$*" >>"$LOG_FILE"
}

log "Waiting for IB Gateway to be reachable via FastAPI /health..."

elapsed=0
ready=0
while (( elapsed < TIMEOUT_SECS )); do
  body="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null || true)"
  if printf '%s' "$body" | grep -q '"port_listening":true'; then
    ready=1
    break
  fi
  sleep 2
  elapsed=$(( elapsed + 2 ))
done

if (( ready == 0 )); then
  log "Timed out after ${TIMEOUT_SECS}s waiting for IB Gateway. Skipping rehydrate."
  exit 0
fi

log "Gateway up. Calling POST $REHYDRATE_URL..."

response="$(curl -fsS --max-time 320 -X POST "$REHYDRATE_URL" 2>>"$LOG_FILE" || true)"
if [[ -z "$response" ]]; then
  log "Rehydrate returned no body (request failed). See errors above."
  exit 0
fi

log "Rehydrate response: $response"
