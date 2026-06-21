#!/usr/bin/env bash
#
# provision_venv.sh — provision the dedicated Chronos-2 forecasting host ONLY.
#
# This creates an isolated virtualenv that carries torch + chronos-forecasting.
# Those heavy deps must NEVER land on the standard radon-* fleet units; they
# live only on the dedicated forecasting host that runs the nightly timer
# (see docs/forecasting-deploy.md). Run this once per host, then re-run with
# --upgrade to refresh dependencies. The script is idempotent: it reuses an
# existing venv rather than recreating it.
#
# Env:
#   RADON_FC_VENV   venv path (default /home/radon/forecasting-venv)
#
# Usage:
#   bash scripts/forecasting/provision_venv.sh            # create / reuse
#   bash scripts/forecasting/provision_venv.sh --upgrade  # also upgrade deps
#
set -euo pipefail

VENV="${RADON_FC_VENV:-/home/radon/forecasting-venv}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REQS="${REPO_ROOT}/requirements-forecasting.txt"

UPGRADE=0
if [[ "${1:-}" == "--upgrade" ]]; then
  UPGRADE=1
fi

log() { echo "[provision-venv] $*" >&2; }

pick_python() {
  for candidate in python3.12 python3.13; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done
  log "ERROR: need python3.12 or python3.13 on PATH"
  exit 1
}

if [[ ! -f "${REQS}" ]]; then
  log "ERROR: requirements file not found: ${REQS}"
  exit 1
fi

if [[ -x "${VENV}/bin/python" ]]; then
  log "reusing existing venv at ${VENV}"
else
  PY="$(pick_python)"
  log "creating venv at ${VENV} using ${PY}"
  "${PY}" -m venv "${VENV}"
fi

PIP="${VENV}/bin/pip"

log "upgrading pip toolchain"
"${PIP}" install --upgrade pip setuptools wheel >&2

if [[ "${UPGRADE}" -eq 1 ]]; then
  log "installing/upgrading forecasting requirements"
  "${PIP}" install --upgrade -r "${REQS}" >&2
else
  log "installing forecasting requirements"
  "${PIP}" install -r "${REQS}" >&2
fi

log "provisioned OK"
echo "${VENV}/bin/python"
