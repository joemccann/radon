#!/usr/bin/env bash
# Cloud Agent install — idempotent bootstrap for the Radon dev environment.
#
# Layers:
#   1. Toolchains not in the base image: bun (root + web/) and Python 3.13 (uv).
#   2. JS deps (bun) for the repo root and the Next.js terminal in web/.
#   3. Python deps into a project venv (.venv) via uv pip.
#   4. Dev-safe env files, if absent, so Next.js boots into first-run setup mode
#      and FastAPI runs in test mode without live broker credentials.
#
# Runtime services are launched by the `terminals` in .cursor/environment.json,
# never here. No process started by this script is expected to outlive it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"

echo "== [1/4] toolchains =="

if ! command -v bun >/dev/null 2>&1; then
  echo "installing bun..."
  curl -fsSL https://bun.sh/install | bash
fi
export PATH="$HOME/.bun/bin:$PATH"
bun --version

if ! command -v uv >/dev/null 2>&1; then
  echo "installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# Python 3.13 — the project pins it (3.14 breaks ib_insync/eventkit). uv fetches
# a standalone build and we expose it on PATH as `python3.13`, which scripts call
# directly.
uv python install 3.13
ln -sf "$(uv python find 3.13)" "$HOME/.local/bin/python3.13"
python3.13 --version

echo "== [2/4] JS deps (bun) =="
bun install
( cd web && bun install )

echo "== [3/4] Python deps (uv venv + uv pip) =="
if [ ! -d .venv ]; then
  uv venv --python 3.13 .venv
fi
VIRTUAL_ENV="$REPO_ROOT/.venv" uv pip install -r requirements.txt

echo "== [4/4] dev env files =="
# Root .env — python-dotenv for FastAPI/scripts. Placeholders only.
if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi
# web/.env — Next.js. Blank the Clerk placeholders so the app boots into
# first-run setup mode (ClerkProvider refuses an invalid key). A real deploy
# fills these in and setup mode switches off automatically.
if [ ! -f web/.env ]; then
  cp web/.env.example web/.env
  sed -i 's/^NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=.*/NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=/' web/.env
  sed -i 's/^CLERK_SECRET_KEY=.*/CLERK_SECRET_KEY=/' web/.env
  echo "created web/.env from web/.env.example (Clerk keys blanked for setup mode)"
fi

echo "== install complete =="
