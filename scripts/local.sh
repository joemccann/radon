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
  # FU4 — F3/F4/F5 fetcher timers (catalysts / informed-flow sweep / event-odds).
  # systemd equivalents on radon-cloud (separate repo) mirror these cadences:
  #   radon-catalysts.timer       OnCalendar=Mon..Fri 06:30  -> run_catalysts.sh
  #   radon-informed-flow.timer   OnCalendar=Mon..Fri 07:00  -> run_informed_flow.sh (watchlist sweep)
  #   radon-event-odds.timer      OnCalendar=Mon..Fri 07,11,15:00 -> run_event_odds.sh
  # Each wrapper is holiday-aware (skips non-trading days via market_calendar).
  # com.radon.data-refresh is intentionally omitted: the VPS
  # radon-flow-refresh.timer owns the hourly (Mon-Fri 09:00-16:00 ET)
  # scanner / discover / flow-analysis refresh. Loading the laptop agent
  # too would double-spend the UW daily quota. Keep it off.
  for plist in com.radon.cri-scan com.radon.cta-sync \
               com.radon.exit-order-service com.radon.monitor-daemon \
               com.radon.vcg-refresh com.radon.catalysts \
               com.radon.informed-flow com.radon.event-odds; do
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

# Preflight: see scripts/cloud.sh for the full rationale. A duplicate
# launch leaves uvicorn dead and a second newsfeed scraper racing the
# original on the Turso replica.
busy=""
for port in 3000 8321 8765; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    busy="${busy:+$busy }$port"
  fi
done
if [[ -n "$busy" ]]; then
  log_warn "Dev stack already running on port(s): $busy"
  log_warn "Mode persisted to .env.ib-mode. Existing services keep their"
  log_warn "current connection; restart dev manually to apply (Ctrl-C the"
  log_warn "running 'npm run dev' and re-run scripts/local.sh)."
  exit 0
fi

log_info "Starting dev services (Next.js + FastAPI + WS relay)..."
cd "$PROJECT_ROOT/web"
exec npm run dev
