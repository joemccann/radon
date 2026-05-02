#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# local.sh — Switch from VPS to local development
# Stops IB Gateway on Hetzner, starts local Docker gateway, launches dev.
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[local]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[local]${NC} $*"; }
log_error() { echo -e "${RED}[local]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# -- Step 1: Persist local Docker mode in .env.ib-mode -----------------------

"$SCRIPT_DIR/ib" mode local

# -- Step 1b: Restore laptop schedulers and set RADON_MODE=local ------------
#
# Phase 5: local mode loads the launchd plists so the laptop becomes
# self-sufficient (no Hetzner dependency). Idempotent — load is a no-op
# if the plist is already loaded.
if command -v launchctl >/dev/null 2>&1; then
  for plist in com.radon.cri-scan com.radon.cta-sync com.radon.data-refresh \
               com.radon.exit-order-service com.radon.monitor-daemon; do
    f="$HOME/Library/LaunchAgents/$plist.plist"
    if [[ -f "$f" ]]; then
      launchctl load "$f" 2>/dev/null || true
      log_info "Loaded $plist (local mode)"
    fi
  done
fi
"$SCRIPT_DIR/_set_radon_mode.sh" local

# -- Step 2: Stop VPS gateway ------------------------------------------------

log_info "Stopping IB Gateway on Hetzner..."
if ssh -o ConnectTimeout=5 ib-gateway "cd /home/radon/radon-cloud && docker compose down" 2>/dev/null; then
  log_info "VPS gateway stopped."
else
  log_warn "Could not reach VPS (offline or already stopped). Continuing."
fi

# -- Step 3: Start local Docker gateway --------------------------------------

log_info "Starting local Docker IB Gateway..."
"$SCRIPT_DIR/docker_ib_gateway.sh" start

log_warn "Approve 2FA on IBKR mobile app now."
log_info "Waiting for container to become healthy..."

for i in $(seq 1 24); do
  status=$(docker inspect --format='{{.State.Health.Status}}' ib-gateway-ib-gateway-1 2>/dev/null || echo "unknown")
  if [[ "$status" == "healthy" ]]; then
    log_info "Container is healthy."
    break
  fi
  if [[ $i -eq 24 ]]; then
    log_error "Container did not become healthy after 120s. Check 2FA and logs."
    "$SCRIPT_DIR/docker_ib_gateway.sh" status
    exit 1
  fi
  sleep 5
done

# -- Step 4: Start dev services -----------------------------------------------
#
# Phase 6: legacy `_post_start_*.sh` warmers retired. Local-mode laptop
# loads its launchd plists in Step 1b above; those plists own all
# scheduled refreshes (CRI scan, CTA sync, etc.) on the same cadence
# that the Hetzner systemd timers use in cloud mode.

log_info "Starting dev services (Next.js + FastAPI + WS relay)..."
cd "$PROJECT_ROOT/web"
exec npm run dev
