#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# cloud.sh - attach-only laptop launcher
# The laptop runs Next.js only, pointed at the Hetzner VPS over Tailscale.
# It never starts a backend, a Gateway or a collector here: no ssh, no
# container runtime, no launchd agents, no mode overlay file.
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
# Next.js runs from THIS tree (a worktree runs its own code). web/.env is
# gitignored, so a worktree has none; point at the main checkout's copy.
# Outside a git checkout MAIN_ROOT equals PROJECT_ROOT.
MAIN_ROOT="$PROJECT_ROOT"
common="$(git -C "$PROJECT_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
[[ -n "$common" ]] && MAIN_ROOT="$(cd "$PROJECT_ROOT" && cd "$(dirname "$common")" && pwd)"
if [[ ! -e "$PROJECT_ROOT/web/.env" && -f "$MAIN_ROOT/web/.env" ]]; then
  log_warn "web/.env is missing in this tree; Next.js will start without Clerk keys."
  log_warn "Link the main checkout's copy: ln -s \"$MAIN_ROOT/web/.env\" \"$PROJECT_ROOT/web/.env\""
fi

# Preflight: if any dev port is already bound, the user has a stale stack
# from a prior session. Bail with guidance instead of stomping on it.
busy=""
for port in 3000; do
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

# -- Step 2: Report cloud readiness ------------------------------------------
# Report only. The laptop attaches whatever the broker state is; a hold or a
# pending 2FA is the operator's call on the VPS, not a reason to refuse the
# workspace. Fail only when the API itself is unreachable or untrusted.
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
    print("Cloud readiness unavailable (unreachable, 401 or non-JSON); fix tailnet trust before next dev.", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(state, dict):
    state = {}
print(
    f"auth_state={state.get('auth_state')} "
    f"port_listening={state.get('port_listening')} "
    f"operator_hold={state.get('operator_hold')}"
)
HEALTH
then
  log_error "Cloud readiness report failed. Workspace not started."
  exit 1
fi

# -- Step 3: Start cloud-thin dev (Next.js only) -----------------------------
#
# The Hetzner VPS owns the full stack: radon-api, radon-relay, the newsfeed
# and headlines writers, the schedulers and the broker Gateway. If the laptop
# also launched FastAPI / IB relay / scraper, every shared resource would
# double-book: IB Gateway returns Error 326 (clientId in use) and Turso emits
# WalConflict on every dual-write.
#
# So the laptop runs only Next.js, pointing radonFetch at Hetzner FastAPI over
# Tailscale and the ticket-authenticated Caddy relay. The tailnet bypass in
# scripts/api/auth.py + server.py treats the laptop's 100.64/10 IP as 'local'
# for server-to-server calls.

log_info "Starting cloud-thin dev (laptop = Next.js only)..."
log_info "  → FastAPI:   http://radon-app:8321 (Tailscale)"
log_info "  → WS relay:  wss://app.radon.run/ws"
log_info "  → Headlines: wss://app.radon.run/ws-headlines"

export RADON_API_URL="http://radon-app:8321"
export IB_REALTIME_WS_URL="wss://app.radon.run/ws"
export NEXT_PUBLIC_IB_REALTIME_WS_URL="wss://app.radon.run/ws"
export HEADLINES_WS_URL="wss://app.radon.run/ws-headlines"
export NEXT_PUBLIC_HEADLINES_WS_URL="wss://app.radon.run/ws-headlines"
export RADON_DEV_PROFILE="cloud-thin"

if [[ -z "${ALLOWED_USER_IDS:-}" ]] && ! grep -q '^ALLOWED_USER_IDS=' "$PROJECT_ROOT/web/.env" 2>/dev/null; then
  log_warn "ALLOWED_USER_IDS is not set in the environment or web/.env;"
  log_warn "the operator routes will answer 403 until it is."
fi

cd "$PROJECT_ROOT/web"
exec npm run dev
