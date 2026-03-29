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

# -- Step 1: Stop VPS gateway ------------------------------------------------

log_info "Stopping IB Gateway on Hetzner..."
if ssh -o ConnectTimeout=5 ib-gateway "cd /home/radon/radon-cloud && docker compose down" 2>/dev/null; then
  log_info "VPS gateway stopped."
else
  log_warn "Could not reach VPS (offline or already stopped). Continuing."
fi

# -- Step 2: Start local Docker gateway --------------------------------------

log_info "Starting local Docker IB Gateway..."
"$SCRIPT_DIR/scripts/docker_ib_gateway.sh" start

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
    "$SCRIPT_DIR/scripts/docker_ib_gateway.sh" status
    exit 1
  fi
  sleep 5
done

# -- Step 3: Start dev services -----------------------------------------------

log_info "Starting dev services (Next.js + FastAPI + WS relay)..."
cd "$SCRIPT_DIR/web"
exec npm run dev
