#!/usr/bin/env bash
#
# provision_venv.sh — provision the dedicated Chronos-2 forecasting venv.
#
# This venv carries the FULL Radon app dependencies (libsql, python-dotenv,
# pandas, numpy, ...) PLUS torch + chronos-forecasting. The forecasting code
# imports db.writer / flow_history, so app deps are required, not just torch.
#
# torch is installed from the CPU wheel index — the forecasting host has no GPU,
# and the default PyPI wheel drags in multi-GB CUDA libraries. These heavy deps
# must NEVER land on the standard radon-* fleet venv (/home/radon/radon/.venv);
# they live only on the dedicated forecasting host that runs the nightly timer
# (see docs/forecasting-deploy.md). Idempotent: reuses an existing venv.
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
APP_REQS="${REPO_ROOT}/requirements.txt"
FC_REQS="${REPO_ROOT}/requirements-forecasting.txt"
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"

UPGRADE=0
if [[ "${1:-}" == "--upgrade" ]]; then
  UPGRADE=1
fi

log() { echo "[provision-venv] $*" >&2; }

# Pick an interpreter whose venv module actually works (ensurepip present).
# python3.13 is preferred: it matches the app venv and is the interpreter the
# Chronos-2 API was verified against. python3.12 is a fallback only when its
# venv support is installed.
pick_python() {
  for candidate in python3.13 python3.12; do
    if command -v "${candidate}" >/dev/null 2>&1 \
       && "${candidate}" -c "import ensurepip" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done
  log "ERROR: need python3.13 (or python3.12 with the venv module) on PATH"
  exit 1
}

for reqs in "${APP_REQS}" "${FC_REQS}"; do
  if [[ ! -f "${reqs}" ]]; then
    log "ERROR: requirements file not found: ${reqs}"
    exit 1
  fi
done

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

UP=""
if [[ "${UPGRADE}" -eq 1 ]]; then
  UP="--upgrade"
fi

# torch from the CPU wheel index FIRST so the requirements installs below see it
# already satisfied and never pull the default CUDA wheel from PyPI.
log "installing CPU torch from ${TORCH_CPU_INDEX}"
# shellcheck disable=SC2086
"${PIP}" install ${UP} "torch>=2.2.0" --index-url "${TORCH_CPU_INDEX}" >&2

log "installing app + forecasting requirements"
# shellcheck disable=SC2086
"${PIP}" install ${UP} -r "${APP_REQS}" -r "${FC_REQS}" >&2

log "provisioned OK"
echo "${VENV}/bin/python"
