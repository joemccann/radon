#!/usr/bin/env bash
# Runs inside the joemccann/radon checkout in a Vercel Sandbox (FACTORY_SETUP_COMMAND).
# Installs bun workspaces and Python test deps. Never sources env files.
set -euo pipefail

if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash
  export PATH="${HOME}/.bun/bin:${PATH}"
fi

bun install --frozen-lockfile
bun install --frozen-lockfile --cwd web

PY=python3
if command -v python3.13 >/dev/null 2>&1; then
  PY=python3.13
fi

"${PY}" -m pip install --user \
  -r requirements.txt \
  -r scripts/requirements-api.txt \
  pytest pytest-cov
