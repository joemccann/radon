#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# start-services.sh -- Start all Radon services on the VPS
# Run as root or radon user with appropriate sudo access.
#
# Usage: bash ~/radon-cloud/scripts/start-services.sh
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

OPERATOR="${RADON_OPERATOR_CLI:-/usr/local/bin/radon}"

# The operator owns the shared deploy lock, Gateway ordering, persistent
# daemons, and timer topology. This wrapper is diagnostics only around it.
log_info "Starting the Radon stack through the locked operator..."
if [[ $EUID -eq 0 ]]; then
  runuser -u radon -- "$OPERATOR" start
else
  "$OPERATOR" start
fi

log_info "Waiting 15s for IB Gateway to initialize..."
sleep 15

# Check if port 4001 is listening
if bash -c 'echo > /dev/tcp/127.0.0.1/4001' 2>/dev/null; then
  log_info "IB Gateway port 4001 is open"
else
  log_warn "IB Gateway port 4001 not yet open — services may retry connections"
fi

log_info "All services started"

# -- Status -------------------------------------------------------------------
sleep 3
echo ""
echo "Service status:"
for svc in radon-ib-gateway radon-nextjs radon-api radon-relay radon-monitor radon-refresh.timer; do
  status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
  case "$status" in
    active)     echo -e "  ${GREEN}●${NC} ${svc}: ${status}" ;;
    activating) echo -e "  ${YELLOW}●${NC} ${svc}: ${status}" ;;
    *)          echo -e "  ${RED}●${NC} ${svc}: ${status}" ;;
  esac
done

echo ""
log_info "Waiting 30s for API to bind (IB pool startup)..."
sleep 30

health=$(curl -s http://localhost:8321/health 2>/dev/null || echo "")
if echo "$health" | grep -q '"status":"ok"'; then
  log_info "API health: OK"
else
  log_warn "API not responding yet — may still be connecting to IB (normal on weekends)"
fi

echo ""
log_info "Done. Visit https://app.radon.run"
