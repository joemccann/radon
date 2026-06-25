#!/usr/bin/env bash
set -euo pipefail
# ---------------------------------------------------------------------------
# setup-beta.sh — ONE-TIME provisioning for the beta.radon.run stack on the VPS.
# Idempotent: safe to re-run.
#
# RUN AS ROOT (the privileged steps — swap, /etc/systemd, /etc/caddy — need it):
#     sudo -u radon -H git clone -b beta git@github.com:joemccann/radon.git /home/radon/radon-beta
#     bash /home/radon/radon-beta/deploy/beta/setup-beta.sh
#
# Everything that touches the repo / npm / venv is run AS THE radon USER (it owns
# /home/radon and holds the GitHub key; root does not). Privileged steps run as
# root directly. This split is deliberate: services run as User=radon and must be
# able to read their own files.
#
# Does NOT: create the Turso DB (turso-seed.sh), change DNS (Vercel), add the
# Clerk satellite (dashboard), or write .env.beta secrets (make-env-beta.sh).
# ---------------------------------------------------------------------------

readonly APP_REPO="git@github.com:joemccann/radon.git"
readonly BETA_DIR="/home/radon/radon-beta"
readonly BRANCH="beta"
readonly SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../deploy/beta
readonly SWAPFILE="/swapfile-beta"
readonly UNITS=(radon-beta-nextjs radon-beta-api radon-beta-health)

B='\033[0;34m'; G='\033[0;32m'; Y='\033[1;33m'; N='\033[0m'
info(){ echo -e "${B}[setup]${N} $*"; }
ok(){ echo -e "${G}[ok]${N} $*"; }
warn(){ echo -e "${Y}[warn]${N} $*"; }

[[ $EUID -eq 0 ]] || { echo "FATAL: run as root (sudo -i / ssh root@...). Repo steps drop to 'radon' automatically." >&2; exit 1; }
id radon >/dev/null 2>&1 || { echo "FATAL: 'radon' user not found." >&2; exit 1; }

# Run a command as the radon user with radon's HOME (so git/ssh find the key).
as_radon(){ sudo -u radon -H "$@"; }

# 1. Swap (0-swap box → a beta build can OOM-kill prod). 4 GB file. (root)
if ! swapon --show=NAME --noheadings | grep -q "$SWAPFILE"; then
  [[ -f "$SWAPFILE" ]] && warn "$SWAPFILE exists but not active"
  info "Creating 4G swapfile at $SWAPFILE"
  fallocate -l 4G "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count=4096
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
  swapon "$SWAPFILE"
  grep -q "$SWAPFILE" /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
  ok "swap active"
else
  ok "swap already active ($SWAPFILE)"
fi

# 2. App checkout at BETA_DIR on branch `beta` — as radon (GitHub key + ownership).
if [[ ! -d "$BETA_DIR/.git" ]]; then
  info "Cloning $APP_REPO -> $BETA_DIR (as radon)"
  as_radon git clone "$APP_REPO" "$BETA_DIR"
fi
# Defensive: if a prior root run left root-owned files, fix ownership.
chown -R radon:radon "$BETA_DIR"
info "Checking out '$BRANCH' (as radon)"
as_radon git -C "$BETA_DIR" fetch origin
as_radon git -C "$BETA_DIR" checkout "$BRANCH" 2>/dev/null || { warn "branch '$BRANCH' missing on origin — push it first"; exit 2; }
as_radon git -C "$BETA_DIR" reset --hard "origin/$BRANCH"
ok "beta checkout at $(as_radon git -C "$BETA_DIR" rev-parse --short HEAD)"

# 3. Python venv + deps — as radon.
if [[ ! -d "$BETA_DIR/.venv" ]]; then
  info "Creating venv (as radon)"
  as_radon python3.13 -m venv "$BETA_DIR/.venv" || as_radon python3 -m venv "$BETA_DIR/.venv"
fi
info "Installing Python deps (as radon)"
as_radon "$BETA_DIR/.venv/bin/pip" install -q -r "$BETA_DIR/requirements.txt"
ok "python deps installed"

# 4. Node deps (npm, matching prod) — as radon. Build happens in deploy-beta.sh.
info "Installing node deps root+web (as radon, npm)"
as_radon bash -c 'cd "$1" && { [ -f package-lock.json ] && npm ci --silent || npm install --silent; }' _ "$BETA_DIR"
as_radon bash -c 'cd "$1/web" && { [ -f package-lock.json ] && npm ci --silent || npm install --silent; }' _ "$BETA_DIR"
ok "node deps installed"

# 5. systemd units (root).
info "Installing systemd units: ${UNITS[*]}"
for u in "${UNITS[@]}"; do
  install -m 0644 "$SRC_DIR/systemd/$u.service" "/etc/systemd/system/$u.service"
done
systemctl daemon-reload
for u in "${UNITS[@]}"; do systemctl enable "$u.service" >/dev/null; done
ok "units installed + enabled (NOT started — start via deploy-beta.sh after .env.beta + DB)"

# 6. Caddy site block (root): append once, validate, reload.
if ! grep -q "beta.radon.run {" /etc/caddy/Caddyfile; then
  info "Appending beta.radon.run block to /etc/caddy/Caddyfile"
  printf '\n' >> /etc/caddy/Caddyfile
  cat "$SRC_DIR/caddy/beta.radon.run.caddy" >> /etc/caddy/Caddyfile
  if caddy validate --config /etc/caddy/Caddyfile; then
    systemctl reload caddy
    ok "caddy validated + reloaded"
  else
    warn "caddy validate FAILED — block appended but NOT reloaded. Fix /etc/caddy/Caddyfile."
  fi
else
  ok "caddy already has a beta.radon.run block"
fi

echo
ok "setup-beta.sh complete."
cat <<NEXT
Remaining (run repo/turso steps AS radon; install sudoers AS root):
  # generate the beta env file (as radon, owned by radon):
  sudo -u radon -H bash $SRC_DIR/make-env-beta.sh
  # create the beta Turso DB + token (as radon, anywhere with turso auth):
  sudo -u radon -H bash $SRC_DIR/turso-seed.sh
  # paste TURSO_DB_URL + TURSO_AUTH_TOKEN into the file (as radon):
  sudo -u radon -H nano /home/radon/radon-cloud/.env.beta
  # let radon restart the beta units without a password:
  install -m0440 $SRC_DIR/sudoers.radon-beta.example /etc/sudoers.d/radon-beta && visudo -cf /etc/sudoers.d/radon-beta
  # first deploy (as radon):
  sudo -u radon -H bash $BETA_DIR/deploy/beta/deploy-beta.sh

Already done: Vercel DNS (beta A 5.78.148.38), Clerk satellite (beta.radon.run).
Still required before deploy-beta.sh yields a working app: the Clerk-satellite
app-code wiring + RADON_BETA_* IB guards (see README).
NEXT
