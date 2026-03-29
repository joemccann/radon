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
ENV_FILE="$PROJECT_ROOT/.env"

# -- Step 1: Switch .env to local Docker mode --------------------------------

log_info "Switching .env to local Docker mode..."
sed -i '' 's/^IB_GATEWAY_HOST=.*/IB_GATEWAY_HOST=127.0.0.1/' "$ENV_FILE"
sed -i '' 's/^IB_GATEWAY_MODE=.*/IB_GATEWAY_MODE=docker/' "$ENV_FILE"
log_info ".env → IB_GATEWAY_HOST=127.0.0.1, IB_GATEWAY_MODE=docker"

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

log_info "Starting dev services (Next.js + FastAPI + WS relay)..."
cd "$PROJECT_ROOT/web"
exec npm run dev
