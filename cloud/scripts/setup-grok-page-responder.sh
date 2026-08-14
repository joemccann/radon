#!/usr/bin/env bash
# Dedicated VPS clone + stripped env for the Grok P1 responder.
# Run as root on the production host. Does not touch /home/radon/radon.
#
#   bash cloud/scripts/setup-grok-page-responder.sh
set -euo pipefail

CLONE="${RADON_PAGE_RESPONDER_DIR:-/home/radon/radon-page-responder}"
ENV_FILE="${RADON_PAGE_RESPONDER_ENV:-/home/radon/radon-page-responder.env}"
PROD_ENV="${RADON_DEPLOY_ENV_FILE:-/home/radon/radon-cloud/.env}"
ORIGIN_URL="${RADON_PAGE_RESPONDER_ORIGIN:-git@github.com:joemccann/radon.git}"
MARKER="$CLONE/.radon-page-responder"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 2
fi

echo "[1/5] dedicated clone $CLONE"
if [[ ! -d "$CLONE/.git" ]]; then
  sudo -u radon git clone "$ORIGIN_URL" "$CLONE"
fi
sudo -u radon bash -c "touch '$MARKER'"
sudo -u radon git -C "$CLONE" config user.name "radon-grok-responder"
sudo -u radon git -C "$CLONE" config user.email "ops@radon.run"

echo "[2/5] stripped env $ENV_FILE"
umask 077
tmp="$(mktemp)"
python3.13 - "$PROD_ENV" "$tmp" <<'PY'
import sys
from pathlib import Path
src, dest = Path(sys.argv[1]), Path(sys.argv[2])
keep = ("TURSO_DB_URL", "TURSO_AUTH_TOKEN", "PUSHOVER_USER", "PUSHOVER_TOKEN")
wanted = {k: None for k in keep}
for raw in src.read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    if key in wanted:
        wanted[key] = value
missing = [k for k, v in wanted.items() if not v]
if missing:
    raise SystemExit("missing in production env: " + ", ".join(missing))
dest.write_text(
    "\n".join(f"{k}={wanted[k]}" for k in keep)
    + "\nGROK_PAGE_NO_DOTENV=1\nGROK_PAGE_SYNC_REMOTE=1\n"
    + "GROK_BIN=/home/radon/.local/bin/grok\n"
)
PY
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

echo "[4/5] grok CLI as radon"
if [[ ! -x /home/radon/.local/bin/grok && ! -x /home/radon/.grok/bin/grok ]]; then
  sudo -u radon bash -lc 'curl -fsSL https://x.ai/cli/install.sh | bash'
fi
sudo -u radon mkdir -p /home/radon/.local/bin
if [[ -x /home/radon/.grok/bin/grok && ! -e /home/radon/.local/bin/grok ]]; then
  sudo -u radon ln -sf /home/radon/.grok/bin/grok /home/radon/.local/bin/grok
fi

echo "[5/5] device-code login required next"
echo "  sudo -u radon -H /home/radon/.local/bin/grok login --device-auth"
echo "  then: systemctl enable --now radon-grok-page-responder.timer"
echo "  after control-plane bootstrap has installed the unit files"
