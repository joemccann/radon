#!/usr/bin/env bash
# Dedicated VPS clone + stripped env for the Grok P1 responder.
# Run as root on the production host. Does not touch /home/radon/radon.
# Run it from a root-only stage of /opt/radon-provision/radon.git, never
# from the deploy checkout: docs/grok-page-responder.md "Install (VPS)".
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

# Root runs this script and its helpers, so a tree radon can write (the
# deploy checkout) would let radon choose what root executes.
is_root_owned_tree() {
  local dir="$1" perms uid
  while :; do
    read -r perms _ uid _ < <(ls -ldn -- "$dir")
    [[ "$uid" == 0 && "${perms:5:1}" != w && "${perms:8:1}" != w ]] || return 1
    [[ "$dir" == / ]] && return 0
    dir="$(dirname -- "$dir")"
  done
}
if ! is_root_owned_tree "$(cd "$SCRIPT_DIR" && pwd -P)"; then
  echo "refusing: $SCRIPT_DIR is not a root-owned tree; stage it from" \
    "/opt/radon-provision/radon.git (docs/grok-page-responder.md)" >&2
  exit 77
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
# Both env files sit in radon's home, so radon reads and publishes them;
# root only builds from private copies and never touches either by name.
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
sudo -u radon cat -- "$PROD_ENV" >"$work/prod.env"
if sudo -u radon test -f "$ENV_FILE"; then
  sudo -u radon cat -- "$ENV_FILE" >"$work/existing.env"
fi
# Secrets from the production env only. Turso uses TURSO_RESPONDER_AUTH_TOKEN
# (never production TURSO_AUTH_TOKEN). Operator GROK_PAGE_* flags
# (RESPONDER/AUTOSHIP/AUTOPUSH/MAX_ACTIONS_PER_DAY) carried over from the
# current file so a rerun never silently disables the responder.
python3.13 "$SCRIPT_DIR/grok_responder_env.py" \
  "$work/prod.env" "$work/existing.env" "$work/out.env"
sudo -u radon bash -c \
  'set -euo pipefail; umask 077; tmp="$(mktemp "$1.XXXXXX")"; cat >"$tmp"; mv -f "$tmp" "$1"' \
  _ "$ENV_FILE" <"$work/out.env"

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
sudo -u radon install -d -m 0750 /var/lib/radon/grok-runtime /var/lib/radon/grok-upgrade
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
