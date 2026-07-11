#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# post-setup.sh -- Local script to complete VPS setup after setup-vps.sh
# Run from your Mac after setup-vps.sh has finished (both passes).
#
# Usage: scripts/post-setup.sh [VPS_HOST]
#   VPS_HOST defaults to 5.78.148.38
# ---------------------------------------------------------------------------

readonly VPS_HOST="${1:-5.78.148.38}"
readonly VPS_ROOT="root@${VPS_HOST}"
readonly VPS_RADON="radon@${VPS_HOST}"
readonly LOCAL_RADON="$HOME/dev/apps/finance/radon"
readonly ENV_FILE="$HOME/dev/apps/finance/radon-cloud/.env.production"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_step()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

# ---------------------------------------------------------------------------
# Step 1: Write .env
# ---------------------------------------------------------------------------
log_step "Step 1: Write .env to VPS"

if [[ -f "$ENV_FILE" ]]; then
  log_info "Using ${ENV_FILE}"
  scp "$ENV_FILE" "${VPS_RADON}:~/radon-cloud/.env"
else
  log_warn "No .env.production found at ${ENV_FILE}"
  echo "  Create it with your production values, or enter them now."
  echo ""
  read -p "  Paste the path to your .env file (or press Enter to write interactively): " env_path

  if [[ -n "$env_path" ]] && [[ -f "$env_path" ]]; then
    scp "$env_path" "${VPS_RADON}:~/radon-cloud/.env"
  else
    echo ""
    echo "  Opening nano on the VPS to edit .env..."
    echo "  Fill in your values, save (Ctrl+O), and exit (Ctrl+X)."
    echo ""
    ssh -t "${VPS_RADON}" "cp ~/radon-cloud/.env.example ~/radon-cloud/.env && chmod 0600 ~/radon-cloud/.env && nano ~/radon-cloud/.env"
  fi
fi

ssh "${VPS_RADON}" 'chmod 0600 ~/radon-cloud/.env'
ssh "${VPS_RADON}" '/home/radon/radon/.venv/bin/python /home/radon/radon-cloud/scripts/check-env.py /home/radon/radon-cloud/.env /home/radon/radon-cloud/config/required-env.txt'
log_info ".env configured"

# ---------------------------------------------------------------------------
# Step 2: Write public web env and rebuild Next.js
# ---------------------------------------------------------------------------
log_step "Step 2: Write public web env and rebuild Next.js"

ssh "${VPS_ROOT}" 'for unit in radon-nextjs.service radon-relay.service radon-newsfeed.service; do if systemctl is-active --quiet "$unit"; then echo "Refusing live rebuild while $unit is active; use scripts/deploy.sh with the exact tested commit SHA" >&2; exit 78; fi; done'
ssh "${VPS_ROOT}" 'set -o pipefail; tmp=$(mktemp); grep -E '\''^NEXT_PUBLIC_[A-Z0-9_]+='\'' /home/radon/radon-cloud/.env > "$tmp" || true; install -m 0600 -o radon -g radon "$tmp" /home/radon/radon/web/.env; rm -f "$tmp"; cd /home/radon/radon/web && sudo -u radon /home/radon/radon/.venv/bin/python /home/radon/radon-cloud/scripts/run_with_env.py /home/radon/radon-cloud/.env -- bun run build 2>&1 | tail -5'

log_info "Next.js rebuilt with production env vars"

# ---------------------------------------------------------------------------
# Step 3: Start IB Gateway and wait for 2FA
# ---------------------------------------------------------------------------
log_step "Step 3: Start IB Gateway"

log_info "Requesting one lock-coordinated stack start..."
ssh "${VPS_ROOT}" '/usr/local/bin/radon start'

echo ""
echo -e "  ${YELLOW}Check your phone for the IB 2FA push notification.${NC}"
echo -e "  Waiting up to 90 seconds for login to complete..."
echo ""

ib_logged_in=false
for i in $(seq 1 18); do
  sleep 5
  login_status=$(ssh "${VPS_ROOT}" 'docker compose -f /home/radon/radon-cloud/docker-compose.yml logs 2>/dev/null | grep -c "Login has completed" || true' 2>/dev/null | tail -1 | tr -d '[:space:]')
  if [[ "${login_status:-0}" -gt 0 ]]; then
    ib_logged_in=true
    break
  fi
  printf "  %ds...\r" $((i * 5))
done

echo ""
if [[ "$ib_logged_in" == "true" ]]; then
  log_info "IB Gateway login confirmed"
else
  log_warn "2FA not confirmed within 90 seconds; no second push was sent"
  log_warn "The lock-aware watchdog owns bounded retry and recovery from here"
fi

# The locked operator in Step 3 already restored persistent daemons and timer
# topology. Do not issue a second, unlocked systemctl start here.
log_step "Step 4: Verify stack start"
ssh "${VPS_ROOT}" '/usr/local/bin/radon status' || true

# ---------------------------------------------------------------------------
# Step 5: Migrate data
# ---------------------------------------------------------------------------
log_step "Step 5: Migrate data"

if [[ -d "${LOCAL_RADON}/data" ]]; then
  log_info "Transferring data/*.json..."
  scp "${LOCAL_RADON}"/data/*.json "${VPS_RADON}:~/radon/data/" 2>/dev/null || true

  for subdir in menthorq_cache service_health cache; do
    if [[ -d "${LOCAL_RADON}/data/${subdir}" ]]; then
      log_info "Transferring data/${subdir}/..."
      scp -r "${LOCAL_RADON}/data/${subdir}" "${VPS_RADON}:~/radon/data/" 2>/dev/null || true
    fi
  done

  log_info "Data migrated"
else
  log_warn "No local data directory found at ${LOCAL_RADON}/data — skipping"
fi

# ---------------------------------------------------------------------------
# Step 6: Verify
# ---------------------------------------------------------------------------
log_step "Step 6: Verify"

echo "  Waiting 30s for API to initialize..."
sleep 30

health=$(ssh "${VPS_ROOT}" 'curl -s http://localhost:8321/health 2>/dev/null' || echo "")
nextjs=$(ssh "${VPS_ROOT}" 'curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null' || echo "000")
caddy=$(curl -s -o /dev/null -w "%{http_code}" "https://app.radon.run" 2>/dev/null || echo "000")

if echo "$health" | grep -q '"status":"ok"'; then
  echo -e "  FastAPI health:  ${GREEN}OK${NC}"
else
  echo -e "  FastAPI health:  ${RED}WAITING (IB pool still connecting — normal on weekends)${NC}"
fi

if [[ "$nextjs" == "200" || "$nextjs" == "302" || "$nextjs" == "307" || "$nextjs" == "404" ]]; then
  echo -e "  Next.js:         ${GREEN}OK (${nextjs})${NC}"
else
  echo -e "  Next.js:         ${RED}${nextjs}${NC}"
fi

if [[ "$caddy" == "200" || "$caddy" == "302" || "$caddy" == "307" ]]; then
  echo -e "  Caddy (HTTPS):   ${GREEN}OK (${caddy})${NC}"
else
  echo -e "  Caddy (HTTPS):   ${YELLOW}${caddy} (TLS cert may still be provisioning)${NC}"
fi

echo ""
log_info "Post-setup complete. Visit https://app.radon.run"
