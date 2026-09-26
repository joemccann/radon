#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# wipe-vps.sh -- Reset VPS to pre-setup state for a clean re-bootstrap
# Keeps: OS, network config, SSH keys, firewall rules, IP address
# Removes: all radon services, repos, packages, user data
#
# Usage: ssh root@<VPS_IP> 'bash -s' < scripts/wipe-vps.sh
# ---------------------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
  echo "Error: wipe-vps.sh must be run as root"
  exit 1
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "========================================"
echo "  RADON VPS WIPE — DESTRUCTIVE RESET"
echo "========================================"
echo ""
echo "This will remove:"
echo "  - All radon systemd services"
echo "  - Docker containers and volumes"
echo "  - Caddy and its config"
echo "  - Python 3.13, Node.js 22, Docker CE"
echo "  - /home/radon/ (repos, venv, data)"
echo "  - /etc/radon/ secrets and the stored secret-store key (shredded)"
echo "  - /var/lib/radon/ host state and the /var/lib/radon-private corpus"
echo "  - Root control-plane helpers (/usr/local/{sbin,bin,lib}/radon*)"
echo "  - radon sudoers and polkit grants"
echo "  - The radon user account"
echo ""
echo "This will KEEP:"
echo "  - SSH keys and config"
echo "  - Firewall rules (ufw)"
echo "  - Network/IP configuration"
echo "  - Root user"
echo ""
if [[ "${1:-}" == "--force" ]]; then
  log_warn "Force mode — skipping confirmation"
else
  read -p "Type YES to confirm: " confirm
  if [[ "$confirm" != "YES" ]]; then
    echo "Aborted."
    exit 0
  fi
fi

echo ""

# -- Stop and disable services -----------------------------------------------

log_info "Stopping radon services..."
systemctl stop radon-refresh.timer 2>/dev/null || true
systemctl stop radon-monitor 2>/dev/null || true
systemctl stop radon-relay 2>/dev/null || true
systemctl stop radon-api 2>/dev/null || true
systemctl stop radon-nextjs 2>/dev/null || true
systemctl stop radon-ib-gateway 2>/dev/null || true

log_info "Disabling radon services..."
systemctl disable radon-refresh.timer 2>/dev/null || true
systemctl disable radon-monitor 2>/dev/null || true
systemctl disable radon-relay 2>/dev/null || true
systemctl disable radon-api 2>/dev/null || true
systemctl disable radon-nextjs 2>/dev/null || true
systemctl disable radon-ib-gateway 2>/dev/null || true
systemctl disable radon-refresh 2>/dev/null || true

log_info "Removing systemd unit files and drop-ins..."
rm -f /etc/systemd/system/radon-*.service
rm -f /etc/systemd/system/radon-*.timer
# `radon-*.service` cannot match the drop-in DIRECTORIES the control plane
# installs (radon-.service.d/ fleet prefix, radon-<unit>.service.d/
# runtime-container.conf). Left behind, a fresh setup-vps.sh silently
# re-provisions in container mode as User=root.
rm -rf /etc/systemd/system/radon-.service.d
rm -rf /etc/systemd/system/radon-*.service.d
rm -rf /etc/systemd/system/radon-*.timer.d
rm -f /etc/systemd/journald.conf.d/radon.conf
systemctl daemon-reload

# -- Docker cleanup -----------------------------------------------------------

log_info "Stopping Docker containers..."
if command -v docker &>/dev/null; then
  docker stop ib-gateway 2>/dev/null || true
  docker rm ib-gateway 2>/dev/null || true
  docker volume rm radon-cloud_ib-config 2>/dev/null || true
  docker system prune -af --volumes 2>/dev/null || true
fi

# -- Caddy cleanup ------------------------------------------------------------

log_info "Stopping and removing Caddy..."
systemctl stop caddy 2>/dev/null || true
systemctl disable caddy 2>/dev/null || true
apt-get remove -y caddy 2>/dev/null || true
rm -f /etc/caddy/Caddyfile
rm -rf /etc/caddy
rm -rf /var/lib/caddy
rm -rf /var/log/caddy
rm -f /usr/share/keyrings/caddy-stable-archive-keyring.gpg
rm -f /etc/apt/sources.list.d/caddy-stable.list

# -- Remove radon user and home -----------------------------------------------

log_info "Removing radon user and home directory..."
if id radon &>/dev/null; then
  killall -u radon 2>/dev/null || true
  sleep 1
  userdel -r radon 2>/dev/null || true
fi
rm -rf /home/radon

# R-618: /home/radon holds the secret-store CIPHERTEXT
# (data/secret_store/secrets.db); its master key lives outside that tree. Left
# behind, a re-provision finds a key that decrypts nothing (the store is
# key-bound by fingerprint and refuses to open, so every /credentials route
# answers 503) — and a decommissioned host is handed back with a live key.
# Shred BEFORE unlink: an rm here would leave the shred below with nothing to
# overwrite and the key bytes recoverable from the block device.
log_info "Shredding secret-store master credential..."
shred -u /etc/credstore.encrypted/radon-secret-store-key 2>/dev/null || true
rm -f /etc/credstore.encrypted/radon-secret-store-key
rmdir /etc/credstore.encrypted 2>/dev/null || true

# -- Remove root control plane --------------------------------------------------

# bootstrap-control-plane.sh installs root-owned helpers outside /home/radon.
# They survive userdel and would execute stale-release root code on a host
# that believes itself clean.
log_info "Removing root control-plane helpers..."
rm -f /usr/local/sbin/radon-deploy-root
rm -f /usr/local/sbin/radon-app-runtime
rm -f /usr/local/sbin/radon-docker-gw
rm -f /usr/local/bin/radon
rm -f /usr/local/bin/radon-ib-gateway-control
rm -rf /usr/local/lib/radon
rm -rf /opt/radon-provision
rm -rf /run/radon-app-runtime
rm -f /run/radon-deploy-root.lock
groupdel radon-secrets 2>/dev/null || true
groupdel radon-media 2>/dev/null || true

# -- Remove sudoers and polkit ------------------------------------------------

log_info "Removing radon sudoers and polkit config..."
rm -f /etc/sudoers.d/radon-deploy
rm -f /etc/sudoers.d/radon-monitor
rm -f /etc/sudoers.d/radon-ops
rm -f /etc/sudoers.d/radon-caddy
rm -f /etc/polkit-1/rules.d/50-radon-services.rules

# -- Remove secrets and host state --------------------------------------------

# setup-vps.sh writes the whole production credential set here (/etc/radon/env
# and /etc/radon/mcp.env), and the API unit loads its secret-store key from the
# systemd credential store. A reset that leaves these behind hands the next
# owner of the machine live credentials.
log_info "Shredding radon secrets and host state..."
find /etc/radon -type f -exec shred -u {} + 2>/dev/null || true
rm -rf /etc/radon
rm -rf /var/lib/radon
# Private research corpus anchored by radon-app-runtime.sh.
rm -rf /var/lib/radon-private

# -- Remove packages ----------------------------------------------------------

log_info "Removing Docker CE..."
apt-get remove -y docker-ce docker-ce-cli containerd.io docker-compose-plugin 2>/dev/null || true
rm -f /usr/share/keyrings/docker.gpg
rm -f /etc/apt/keyrings/docker.gpg
rm -f /etc/apt/sources.list.d/docker.list

log_info "Removing Python 3.13..."
apt-get remove -y python3.13 python3.13-venv python3.13-dev 2>/dev/null || true

log_info "Removing Node.js..."
apt-get remove -y nodejs 2>/dev/null || true
rm -f /etc/apt/sources.list.d/nodesource.list
rm -f /usr/share/keyrings/nodesource.gpg
rm -f /etc/apt/keyrings/nodesource.gpg

log_info "Cleaning up apt..."
apt-get autoremove -y 2>/dev/null || true
apt-get clean

# -- Remove git safe.directory ------------------------------------------------

git config --global --unset-all safe.directory 2>/dev/null || true

# -- Summary ------------------------------------------------------------------

echo ""
log_info "========================================="
log_info "  VPS wipe complete."
log_info "========================================="
echo ""
echo "The machine is ready for a fresh setup."
echo "Next steps:"
echo "  1. From your local machine:"
echo "     ssh root@$(hostname -I | awk '{print $1}') 'bash -s' < scripts/setup-vps.sh"
echo ""
