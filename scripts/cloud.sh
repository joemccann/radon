#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# cloud.sh — Run local dev services against remote IB Gateway on Hetzner
# Ensures VPS gateway is running, stops local Docker gateway, launches dev.
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[cloud]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[cloud]${NC} $*"; }
log_error() { echo -e "${RED}[cloud]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

# -- Step 1: Verify Tailscale connectivity -----------------------------------

log_info "Checking Tailscale connectivity to ib-gateway..."
if ! ping -c 1 -W 3 ib-gateway >/dev/null 2>&1; then
  log_error "Cannot reach ib-gateway via Tailscale. Is Tailscale running?"
  exit 1
fi
log_info "VPS reachable."

# -- Step 2: Ensure VPS gateway is running -----------------------------------

log_info "Checking VPS IB Gateway..."
if ssh -o ConnectTimeout=5 ib-gateway "cd /home/radon/radon-cloud && docker compose ps --format json" 2>/dev/null | grep -q '"running"'; then
  log_info "VPS gateway already running."
else
  log_info "Starting VPS gateway..."
  ssh ib-gateway "cd /home/radon/radon-cloud && docker compose up -d" 2>/dev/null
  log_warn "Approve 2FA on IBKR mobile if this is a cold start."
  log_info "Waiting 30s for gateway to initialize..."
  sleep 30
fi

# -- Step 3: Stop local Docker gateway if running ----------------------------

if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "ib-gateway"; then
  log_info "Stopping local Docker IB Gateway..."
  "$SCRIPT_DIR/docker_ib_gateway.sh" stop
fi

# -- Step 4: Switch .env to cloud mode --------------------------------------

log_info "Switching .env to cloud mode..."
sed -i '' 's/^IB_GATEWAY_HOST=.*/IB_GATEWAY_HOST=ib-gateway/' "$ENV_FILE"
sed -i '' 's/^IB_GATEWAY_MODE=.*/IB_GATEWAY_MODE=cloud/' "$ENV_FILE"
log_info ".env → IB_GATEWAY_HOST=ib-gateway, IB_GATEWAY_MODE=cloud"

# -- Step 5: Verify port 4001 reachable on VPS ------------------------------

log_info "Verifying IB Gateway port 4001..."
if bash -c "echo > /dev/tcp/ib-gateway/4001" 2>/dev/null; then
  log_info "Port 4001 is open."
else
  log_warn "Port 4001 not responding yet. Gateway may still be starting (2FA pending)."
fi

# -- Step 6: Start dev services ----------------------------------------------

log_info "Starting dev services (Next.js + FastAPI + WS relay)..."
cd "$PROJECT_ROOT/web"
exec npm run dev
