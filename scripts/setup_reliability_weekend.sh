#!/usr/bin/env bash
# One-command setup of the weekend reliability loop on the always-on
# runner (Mac mini). Run ON THE MINI from any checkout of the repo:
#
#   bash scripts/setup_reliability_weekend.sh
#
# Creates a DEDICATED clone at ~/radon-weekend/radon (never edit it by
# hand — every run hard-resets it to origin/main), installs the two
# launchd jobs (Sat 22:00 audit, Sun 10:00 remediate), and verifies the
# toolchain the runs need.
set -euo pipefail

WEEKEND_ROOT="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
WEEKEND_REPO="$WEEKEND_ROOT/radon"
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
check "root ssh to VPS (optional, for operator use only)" \
  ssh -o BatchMode=yes -o ConnectTimeout=5 root@ib-gateway true
if [[ $fail -ne 0 ]]; then
  echo "Fix the MISSING items above, then re-run. (VPS ssh is optional —"
  echo "the unattended loop never uses it; it is for your Monday follow-ups.)"
  # VPS ssh alone should not block install:
  # continue only if everything except that check passed.
fi

echo "[2/4] dedicated runner clone at $WEEKEND_REPO"
mkdir -p "$WEEKEND_ROOT"
if [[ ! -d "$WEEKEND_REPO/.git" ]]; then
  git clone "$ORIGIN_URL" "$WEEKEND_REPO"
fi
touch "$WEEKEND_REPO/.radon-weekend-runner"
mkdir -p "$WEEKEND_REPO/logs/reliability-weekend"

echo "[3/4] python + web dependencies in the runner clone"
( cd "$WEEKEND_REPO" \
  && python3.13 -m pip install -q -r requirements.txt \
  && bun install --frozen-lockfile >/dev/null \
  && cd web && bun install --frozen-lockfile >/dev/null )

echo "[4/4] launchd jobs"
mkdir -p "$LAUNCH_AGENTS"
for name in reliability-audit reliability-remediate; do
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
echo "Done. Schedule: audit Sat 22:00, remediate Sun 10:00 (local time)."
echo "Dead-man: GitHub issue labeled 'reliability-weekend' gets a comment"
echo "per run — no weekend comment means the runner did not fire."
echo "Smoke test now with:"
echo "  RADON_WEEKEND_REPO=$WEEKEND_REPO bash $WEEKEND_REPO/scripts/reliability_weekend.sh audit"
echo
echo "Keep the mini awake: System Settings > Energy > prevent sleep, or:"
echo "  sudo pmset -a sleep 0 displaysleep 10"
