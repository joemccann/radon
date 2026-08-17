#!/usr/bin/env bash
# One-command setup of the weekend testing loop on the always-on runner
# (Mac mini). Run ON THE MINI from any checkout of the repo:
#
#   bash scripts/setup_testing_weekend.sh
#
# Provisions the testing loop's OWN dedicated clone
# (~/radon-weekend/radon-testing — never edit it by hand; every run
# hard-resets it to origin/main), installs the two launchd jobs (Sat
# 19:00 audit, Sun 17:00 remediate), and verifies the toolchain. The
# clone is deliberately separate from the reliability loop's
# (~/radon-weekend/radon): both loops hard-reset their working tree per
# round and the reliability loop's continuation rounds make its wall
# clock unbounded, so sharing a clone destroys in-flight work
# (2026-08-16 incident).
set -euo pipefail

WEEKEND_ROOT="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
WEEKEND_REPO="$WEEKEND_ROOT/radon-testing"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
ORIGIN_URL="$(git config --get remote.origin.url 2>/dev/null || echo git@github.com:joemccann/radon.git)"

fail=0
check() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ok  $name"
  else
    echo "  MISSING  $name  ($*)"
    fail=1
  fi
}

echo "[1/4] toolchain"
check "claude CLI"        command -v claude
check "gh CLI (authed)"   gh auth status
check "python3.13"        command -v python3.13
check "pytest"            python3.13 -c "import pytest"
check "bun"               command -v bun
check "node"              command -v node
check "git ssh to origin" git ls-remote --exit-code "$ORIGIN_URL" HEAD
if [[ $fail -ne 0 ]]; then
  echo "Fix the MISSING items above, then re-run."
fi

echo "[2/4] dedicated runner clone at $WEEKEND_REPO"
mkdir -p "$WEEKEND_ROOT"
if [[ ! -d "$WEEKEND_REPO/.git" ]]; then
  git clone "$ORIGIN_URL" "$WEEKEND_REPO"
fi
touch "$WEEKEND_REPO/.radon-weekend-runner"
mkdir -p "$WEEKEND_REPO/logs/testing-weekend"

echo "[3/4] python + web dependencies in the runner clone"
( cd "$WEEKEND_REPO" \
  && python3.13 -m pip install -q -r requirements.txt \
  && bun install --frozen-lockfile >/dev/null \
  && cd web && bun install --frozen-lockfile >/dev/null )

echo "[4/4] launchd jobs"
mkdir -p "$LAUNCH_AGENTS"
for name in testing-audit testing-remediate; do
  src="$WEEKEND_REPO/config/com.radon.${name}.plist"
  dst="$LAUNCH_AGENTS/com.radon.${name}.plist"
  sed -e "s|__WEEKEND_REPO__|$WEEKEND_REPO|g" -e "s|__HOME__|$HOME|g" \
    "$src" > "$dst"
  plutil -lint "$dst" >/dev/null
  launchctl unload "$dst" 2>/dev/null || true
  launchctl load "$dst"
  echo "  loaded $dst"
done

echo
echo "Done. Schedule: audit Sat 19:00, remediate Sun 17:00 (local time),"
echo "in the testing loop's own clone at $WEEKEND_REPO."
echo "Dead-man: GitHub issue labeled 'testing-weekend' gets a comment"
echo "per run — no weekend comment means the runner did not fire."
echo "Smoke test now with:"
echo "  RADON_WEEKEND_REPO=$WEEKEND_REPO bash $WEEKEND_REPO/scripts/testing_weekend.sh audit"
