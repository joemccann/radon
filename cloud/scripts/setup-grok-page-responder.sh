#!/usr/bin/env bash
# Dedicated VPS clone + stripped env for the Grok P1 responder.
# Run as root on the production host. Does not touch /home/radon/radon.
#
#   bash cloud/scripts/setup-grok-page-responder.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLONE="${RADON_PAGE_RESPONDER_DIR:-/home/radon/radon-page-responder}"
ENV_FILE="${RADON_PAGE_RESPONDER_ENV:-/home/radon/radon-page-responder.env}"
PROD_ENV="${RADON_DEPLOY_ENV_FILE:-/home/radon/radon-cloud/.env}"
ORIGIN_URL="${RADON_PAGE_RESPONDER_ORIGIN:-https://github.com/joemccann/radon.git}"
MARKER="$CLONE/.radon-page-responder"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 2
fi

echo "[1/5] dedicated clone $CLONE"
if [[ ! -d "$CLONE/.git" ]]; then
  sudo -u radon git clone "$ORIGIN_URL" "$CLONE"
fi
# Public HTTPS origin: the responder unit hides ~/.ssh from the agent.
sudo -u radon git -C "$CLONE" remote set-url origin "$ORIGIN_URL"
sudo -u radon bash -c "touch '$MARKER'"
sudo -u radon git -C "$CLONE" config user.name "radon-grok-responder"
sudo -u radon git -C "$CLONE" config user.email "ops@radon.run"

echo "[2/5] stripped env $ENV_FILE"
umask 077
tmp="$(mktemp)"
# Secrets from the production env only; operator GROK_PAGE_* flags
# (RESPONDER/AUTOSHIP/AUTOPUSH/MAX_ACTIONS_PER_DAY) carried over from the
# current file so a rerun never silently disables the responder.
python3.13 "$SCRIPT_DIR/grok_responder_env.py" "$PROD_ENV" "$ENV_FILE" "$tmp"
chown radon:radon "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"
chown radon:radon "$ENV_FILE"
chmod 600 "$ENV_FILE"

echo "[3/5] python venv + bun in the clone"
sudo -u radon bash -lc "
  set -euo pipefail
  cd '$CLONE'
  if [[ ! -x .venv/bin/python ]]; then
    /usr/bin/python3.13 -m venv .venv
  fi
  .venv/bin/pip install -q -r requirements.txt
  bun install --frozen-lockfile
  cd web && bun install --frozen-lockfile
"

echo "[4/5] grok CLI as radon (latest stable; LKG is machine-written)"
if [[ ! -x /home/radon/.local/bin/grok && ! -x /home/radon/.grok/bin/grok ]]; then
  sudo -u radon bash -lc 'curl -fsSL https://x.ai/cli/install.sh | bash'
fi
sudo -u radon mkdir -p /home/radon/.local/bin
if [[ -x /home/radon/.grok/bin/grok && ! -e /home/radon/.local/bin/grok ]]; then
  sudo -u radon ln -sf /home/radon/.grok/bin/grok /home/radon/.local/bin/grok
fi
install -d -o radon -g radon -m 0750 /var/lib/radon
# Runtime lock dir (responder-writable) and upgrade scratch (not). The
# responder unit mounts ~/.grok/{bin,hooks,downloads} read-only; create them
# so the read-only mount covers them rather than being skipped.
install -d -o radon -g radon -m 0750 /var/lib/radon/grok-runtime /var/lib/radon/grok-upgrade
sudo -u radon mkdir -p /home/radon/.grok/bin /home/radon/.grok/hooks /home/radon/.grok/downloads
if [[ -x /home/radon/.local/bin/grok && -x "$CLONE/.venv/bin/python" ]]; then
  sudo -u radon -H "$CLONE/.venv/bin/python" "$CLONE/scripts/grok_upgrade.py" \
    --seed-if-missing \
    --grok-bin /home/radon/.local/bin/grok \
    --lkg /var/lib/radon/grok_lkg.json \
    || echo "warn: LKG seed failed; first upgrade cycle will try again" >&2
fi

echo "[5/5] device-code login required next"
echo "  sudo -u radon -H /home/radon/.local/bin/grok login --device-auth"
echo "  then: systemctl enable --now radon-grok-page-responder.timer radon-grok-upgrade.timer"
echo "  after control-plane bootstrap has installed the unit files"
