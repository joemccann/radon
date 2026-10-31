#!/usr/bin/env bash
# Legacy provisioning entry point. Authentication, discovery and upgrades are
# owned by the standalone model service and its reviewed CI image.
set -Eeuo pipefail
case "${1:-}" in ""|--check) ;; *) echo "usage: agent_cli_bootstrap.sh [--check]" >&2; exit 64 ;; esac
HELPER=/opt/radon-model-clients/current/lib/model_gateway/cli.py
if [[ ! -r "$HELPER" ]]; then
  echo "Model clients are unavailable; deploy the reviewed gateway client release." >&2
  exit 75
fi
exec "${RADON_GATEWAY_PYTHON:-python3}" -I "$HELPER" status
