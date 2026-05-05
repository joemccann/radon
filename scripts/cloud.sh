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

# -- Step 0: Refuse to run when a third-party VPN is hijacking traffic -------
#
# Detect the conflict at the routing-table level rather than by app name —
# any third-party VPN (NordVPN, ProtonVPN, WireGuard, Cisco AnyConnect,
# Cloudflare WARP, OpenVPN, IKEv2, …) breaks Tailscale's data plane the
# same way: by installing a default (or split-default) route over its own
# tunnel interface. The control plane stays green so peers look online;
# only TCP to the peer's IP times out.
#
# Algorithm: find Tailscale's tunnel interface via its 100.64/10 IP, then
# look for `default` / `0/1` / `128.0/1` routes owned by any *other*
# tunnel-class interface (utun, ipsec, ppp, tun). Tailscale itself can
# install a default route via its own interface (e.g. with --exit-node);
# excluding ts_iface keeps that case from firing.
tailscale_tun_iface() {
  ifconfig 2>/dev/null | awk '
    /^[a-z]/ { iface=$1; sub(/:$/,"",iface) }
    /^[[:space:]]+inet 100\./ {
      split($2, octets, ".")
      second = octets[2] + 0
      if (second >= 64 && second <= 127) { print iface; exit }
    }
  '
}

detect_hijacking_interfaces() {
  local ts_iface
  ts_iface="$(tailscale_tun_iface)"
  netstat -nr -f inet 2>/dev/null | awk -v ts="$ts_iface" '
    ($1 == "default" || $1 == "0/1" || $1 == "128.0/1") &&
    $NF ~ /^(utun|ipsec|ppp|tun)/ &&
    $NF != ts {
      print $NF
    }
  ' | sort -u | paste -sd ',' -
}

hijacker="$(detect_hijacking_interfaces)"
if [[ -n "$hijacker" ]]; then
  log_error "VPN tunnel ${hijacker} owns the default route — traffic to"
  log_error "ib-gateway:4001 will be routed through it instead of Tailscale,"
  log_error "and the TCP probe will time out even though the tailnet shows"
  log_error "ib-gateway online. Disconnect the active VPN (NordVPN, ProtonVPN,"
  log_error "WireGuard, Cisco AnyConnect, Cloudflare WARP, etc.) and retry."
  exit 1
fi

# -- Step 1: Verify Tailscale connectivity -----------------------------------

log_info "Checking Tailscale connectivity to ib-gateway..."
tcp_probe() {
  python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(3)
try:
    s.connect((sys.argv[1], int(sys.argv[2])))
    s.close()
except Exception:
    sys.exit(1)
" "$1" "$2" 2>/dev/null
}
if ! tcp_probe ib-gateway 22 && ! tcp_probe ib-gateway 4001; then
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

# -- Step 4: Persist cloud mode in .env.ib-mode -----------------------------

"$SCRIPT_DIR/ib" mode cloud

# -- Step 4b: Persist RADON_MODE=hetzner so DB writes pick the right path ---
#
# Phase 5: in Hetzner mode the laptop runs only the newsfeed scraper +
# Next.js. All other schedulers run inside the radon-services container
# on the VPS. We unload the laptop's launchd plists so they don't race.
if command -v launchctl >/dev/null 2>&1; then
  for plist in com.radon.cri-scan com.radon.cta-sync com.radon.data-refresh \
               com.radon.exit-order-service com.radon.monitor-daemon; do
    if launchctl list | grep -q "$plist"; then
      log_info "Unloading $plist (Hetzner mode)..."
      launchctl unload "$HOME/Library/LaunchAgents/$plist.plist" 2>/dev/null || true
    fi
  done
fi
"$SCRIPT_DIR/_set_radon_mode.sh" hetzner

# -- Step 5: Verify port 4001 reachable on VPS ------------------------------

log_info "Verifying IB Gateway port 4001..."
if tcp_probe ib-gateway 4001; then
  log_info "Port 4001 is open."
else
  log_warn "Port 4001 not responding yet. Gateway may still be starting (2FA pending)."
fi

# -- Step 6: Start dev services ----------------------------------------------
#
# Phase 6: the legacy `_post_start_*.sh` warmers (journal rehydrate, CTA
# cache refresh) are no longer invoked here. In Hetzner mode the
# radon-services container's systemd timers do the periodic refresh;
# warming on every laptop boot is redundant.

log_info "Starting dev services (Next.js + FastAPI + WS relay)..."
cd "$PROJECT_ROOT/web"
exec npm run dev
