#!/usr/bin/env bash
# Persist RADON_MODE=<local|hetzner> to .env.ib-mode (the same overlay
# that scripts/ib uses for IB_GATEWAY_HOST). Both .env and .env.ib-mode
# load on every Python + Node entry point, with the latter overriding —
# so writing here is the single source of truth for runtime mode.

set -euo pipefail

mode="${1:-local}"
case "$mode" in
  local|hetzner) ;;
  *) echo "Usage: $0 <local|hetzner>" >&2; exit 1 ;;
esac

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env.ib-mode"

# Strip any existing RADON_MODE line, append the new one.
if [[ -f "$ENV_FILE" ]]; then
  grep -v '^RADON_MODE=' "$ENV_FILE" > "$ENV_FILE.tmp" || true
  mv "$ENV_FILE.tmp" "$ENV_FILE"
fi
echo "RADON_MODE=$mode" >> "$ENV_FILE"

echo "[radon-mode] persisted RADON_MODE=$mode to $ENV_FILE"
