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

log_info "Removing systemd unit files..."
rm -f /etc/systemd/system/radon-*.service
rm -f /etc/systemd/system/radon-*.timer
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

# -- Remove sudoers -----------------------------------------------------------

log_info "Removing radon sudoers config..."
rm -f /etc/sudoers.d/radon-deploy

# -- Remove packages ----------------------------------------------------------

log_info "Removing Docker CE..."
apt-get remove -y docker-ce docker-ce-cli containerd.io docker-compose-plugin 2>/dev/null || true
rm -f /usr/share/keyrings/docker.gpg
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
