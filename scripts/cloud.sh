#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# cloud.sh — Run local dev services against remote IB Gateway on Hetzner
# Stops the local Docker session, admits the broker, then launches Next.js.
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

# Preflight: if any dev port is already bound, the user has a stale stack
# from a prior session. Bail with guidance instead of stomping on it.
busy=""
for port in 3000 8321 8765; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    busy="${busy:+$busy }$port"
  fi
done
if [[ -n "$busy" ]]; then
  log_warn "Dev stack already running on port(s): $busy"
  log_warn "Existing services keep their"
  log_warn "current connection; restart dev manually to apply (Ctrl-C the"
  log_warn "running 'npm run dev' and re-run scripts/cloud.sh)."
  exit 1
fi


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
  log_error "radon-app:8321 will be routed through it instead of Tailscale,"
  log_error "and the TCP probe will time out even though the tailnet shows"
  log_error "radon-app online. Disconnect the active VPN (NordVPN, ProtonVPN,"
  log_error "WireGuard, Cisco AnyConnect, Cloudflare WARP, etc.) and retry."
  exit 1
fi

# -- Step 1: Verify Tailscale connectivity -----------------------------------

log_info "Checking Tailscale connectivity to radon-app..."
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
if ! tcp_probe radon-app 8321; then
  log_error "Cannot reach radon-app via Tailscale. Is Tailscale running?"
  exit 1
fi
log_info "VPS reachable."

# -- Step 2: Stop the local session before broker admission -----------------
# R-729 / REL-313: the app host owns no Gateway. A remote login must not
# precede local logout, and an SSH timeout never authorizes the mode switch.
if ! python3.13 - "$SCRIPT_DIR/docker_ib_gateway.sh" <<'LOCAL'
import subprocess
import sys

try:
    inventory = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                               capture_output=True, text=True, timeout=10)
    if inventory.returncode != 0:
        print("Local Gateway inventory unavailable; no remote start.", file=sys.stderr)
        raise SystemExit(1)
    if any("ib-gateway" in name for name in inventory.stdout.splitlines()):
        result = subprocess.run([sys.argv[1], "stop"], timeout=60)
        if result.returncode != 0:
            raise SystemExit(1)
except (subprocess.TimeoutExpired, OSError):
    print("Local Gateway logout unknown; no remote start.", file=sys.stderr)
    raise SystemExit(1)
LOCAL
then
  log_error "Local Gateway logout not confirmed. Cloud mode was not started."
  exit 1
fi

# -- Step 3: Admit through the broker's installed lifecycle owner ------------
log_info "Ensuring the broker IB Gateway is running through the lease-aware helper..."
if ! python3.13 - <<'PYTHON'
import subprocess
import sys

try:
    result = subprocess.run([
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "root@radon-broker",
        "/usr/local/bin/radon-ib-gateway-control start",
    ], timeout=180)
except (subprocess.TimeoutExpired, OSError):
    print("Broker start outcome unknown; inspect broker status before retrying.", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(result.returncode)
PYTHON
then
  log_error "Broker start not confirmed. Cloud mode was not started."
  log_error "Inspect radon ib status on the broker; an operator hold requires explicit resume after local logout."
  exit 1
fi

# Authentication, rather than an inaccessible tailnet 4001, proves readiness.
if ! python3.13 - <<'HEALTH'
import json
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen("http://radon-app:8321/health/lite", timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError("health response too large")
    state = json.loads(raw)
except (OSError, ValueError, urllib.error.URLError):
    print("Cloud readiness unavailable; no mode change.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(state, dict) or not (
    state.get("auth_state") == "authenticated"
    and state.get("port_listening") is True
    and state.get("operator_hold") is False
):
    print("Cloud broker is not ready; resolve authentication or hold before retrying.", file=sys.stderr)
    raise SystemExit(1)
HEALTH
then
  log_error "Cloud broker readiness not confirmed. Cloud mode was not started."
  exit 1
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
               com.radon.exit-order-service com.radon.monitor-daemon \
               com.radon.vcg-refresh; do
    if launchctl list | grep -q "$plist"; then
      log_info "Unloading $plist (Hetzner mode)..."
      launchctl unload "$HOME/Library/LaunchAgents/$plist.plist" 2>/dev/null || true
    fi
  done
fi
"$SCRIPT_DIR/_set_radon_mode.sh" hetzner

# -- Step 6: Start cloud-thin dev (Next.js only) -----------------------------
#
# Post Phase 5 (cloud-services migration, 2026-05-03) the Hetzner VPS owns
# the full stack: radon-api (clientIds 3/4/5), radon-relay (10/11/12),
# radon-newsfeed (Turso writer), plus radon-monitor and radon-nextjs. If the
# laptop also launched FastAPI / IB relay / scraper, every shared resource
# would double-book — IB Gateway returns Error 326 (clientId in use) and
# Turso emits WalConflict on every dual-write.
#
# So in cloud mode the laptop runs only Next.js, pointing radonFetch at
# Hetzner FastAPI over Tailscale and the ticket-authenticated Caddy relay. The
# tailnet bypass added to scripts/api/auth.py + server.py treats laptop's
# 100.64/10 IP as 'local' for server-to-server calls.

log_info "Starting cloud-thin dev (laptop = Next.js only)..."
log_info "  → FastAPI:  http://radon-app:8321 (Tailscale)"
log_info "  → WS relay: wss://app.radon.run/ws  (Tailscale)"

export RADON_API_URL="http://radon-app:8321"
export IB_REALTIME_WS_URL="wss://app.radon.run/ws"
export NEXT_PUBLIC_IB_REALTIME_WS_URL="wss://app.radon.run/ws"
export RADON_DEV_PROFILE="cloud-thin"

cd "$PROJECT_ROOT/web"
exec npm run dev
