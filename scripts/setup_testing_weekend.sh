#!/usr/bin/env bash
# One-command setup of the weekend testing loop on the always-on runner
# (Mac mini). Run ON THE MINI from any checkout of the repo:
#
#   bash scripts/setup_testing_weekend.sh
#
# Provisions the testing loop's OWN dedicated clone
# (~/radon-weekend/radon-testing — never edit it by hand; every run
# hard-resets it to origin/main), installs the single daily launchd job
# (one cycle: audit then remediate), and verifies the toolchain. The
# clone is deliberately separate from the reliability loop's
# (~/radon-weekend/radon): both loops hard-reset their working tree per
# round and the reliability loop's continuation rounds make its wall
# clock unbounded, so sharing a clone destroys in-flight work
# (2026-08-16 incident).
set -euo pipefail

WEEKEND_ROOT="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
WEEKEND_REPO="$WEEKEND_ROOT/radon-testing"
WEEKEND_VENV="$WEEKEND_ROOT/venv"
# Pushover creds for the per-phase page. Lives OUTSIDE the runner clone:
# every round hard-resets and cleans that clone.
WEEKEND_ENV="$WEEKEND_ROOT/.env"
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
check "pytest (venv)"     "$WEEKEND_VENV/bin/python" -c "import pytest" 2>/dev/null
check "bun"               command -v bun
check "node"              command -v node
check "git ssh to origin" git ls-remote --exit-code "$ORIGIN_URL" HEAD
check "pushover creds"    grep -qE '^PUSHOVER_(USER|TOKEN)=.+' "$WEEKEND_ENV"
if [[ $fail -ne 0 ]]; then
  echo "Fix the MISSING items above, then re-run."
fi

echo "[2/4] dedicated runner clone at $WEEKEND_REPO"
mkdir -p "$WEEKEND_ROOT"
if [[ ! -d "$WEEKEND_REPO/.git" ]]; then
  git clone "$ORIGIN_URL" "$WEEKEND_REPO"
fi
# A live cycle owns this clone. Unloading the job and hard-resetting the tree
# under it orphans the agent onto a reset checkout.
if kill -0 "$(cat "$WEEKEND_REPO/.weekend-runner.lock/pid" 2>/dev/null)" 2>/dev/null; then
  echo "  a weekend run is in flight in $WEEKEND_REPO; re-run when it finishes"
  exit 1
fi
# The SIBLING loop's clone too. WEEKEND_VENV is literally the same path in
# both setups, and both wrappers prepend it to the running agent's PATH — so
# the `python3.13 -m venv` + `pip install` below would mutate the interpreter
# and site-packages a live reliability agent is executing against, mid-run.
# The guard above was written for the clone ("A live cycle owns this clone")
# and never extended to the shared $WEEKEND_ROOT both loops depend on. R-266.
SIBLING_REPO="$WEEKEND_ROOT/radon"
if [[ -d "$SIBLING_REPO" ]] \
  && kill -0 "$(cat "$SIBLING_REPO/.weekend-runner.lock/pid" 2>/dev/null)" 2>/dev/null; then
  echo "  a weekend run is in flight in $SIBLING_REPO and shares $WEEKEND_VENV; re-run when it finishes"
  exit 1
fi
# An already-provisioned clone must carry the current config/ and scripts/
# before the job is installed from it. main is force-reset; any weekend
# branch and its commits survive.
git -C "$WEEKEND_REPO" fetch origin --quiet
git -C "$WEEKEND_REPO" checkout -f --quiet main
git -C "$WEEKEND_REPO" reset --hard --quiet origin/main
touch "$WEEKEND_REPO/.radon-weekend-runner"
mkdir -p "$WEEKEND_REPO/logs/testing-weekend"

if [[ ! -f "$WEEKEND_ENV" ]]; then
  cat > "$WEEKEND_ENV" <<'EOF'
# Pushover creds for the daily cycle's per-phase page.
# Single-quote any value containing $ (see CLAUDE.md).
# PUSHOVER_USER=
# PUSHOVER_TOKEN=
EOF
  chmod 600 "$WEEKEND_ENV"
  echo "  created $WEEKEND_ENV (fill in PUSHOVER_USER / PUSHOVER_TOKEN to enable paging)"
fi

echo "[3/4] python + web dependencies in the runner clone"
python3.13 -m venv "$WEEKEND_VENV"
"$WEEKEND_VENV/bin/pip" install -q --upgrade pip
"$WEEKEND_VENV/bin/pip" install -q pytest
"$WEEKEND_VENV/bin/pip" install -q -r "$WEEKEND_REPO/requirements.txt"
( cd "$WEEKEND_REPO" \
  && bun install --frozen-lockfile >/dev/null \
  && cd web && bun install --frozen-lockfile >/dev/null )

echo "[4/4] launchd jobs"
mkdir -p "$LAUNCH_AGENTS"
# Retire the split audit/remediate jobs. An operator copy left loaded keeps
# firing into the same clone as the daily cycle, which is the same-clone
# collision the single job exists to avoid. Idempotent: absent is fine.
for old in testing-audit testing-remediate; do
  legacy="$LAUNCH_AGENTS/com.radon.${old}.plist"
  if [[ -f "$legacy" ]]; then
    launchctl unload "$legacy" 2>/dev/null || true
    rm -f "$legacy"
    echo "  removed $legacy"
  fi
done
JOB_PLIST="$LAUNCH_AGENTS/com.radon.testing-daily.plist"
sed -e "s|__WEEKEND_REPO__|$WEEKEND_REPO|g" -e "s|__HOME__|$HOME|g" \
  "$WEEKEND_REPO/config/com.radon.testing-daily.plist" > "$JOB_PLIST"
plutil -lint "$JOB_PLIST" >/dev/null
launchctl unload "$JOB_PLIST" 2>/dev/null || true
launchctl load "$JOB_PLIST"
echo "  loaded $JOB_PLIST"

# Read the cadence back off the installed job so this copy cannot drift.
SCHED_HOUR="$(plutil -extract StartCalendarInterval.Hour raw -o - "$JOB_PLIST")"
SCHED_MIN="$(plutil -extract StartCalendarInterval.Minute raw -o - "$JOB_PLIST")"

echo
printf 'Done. Schedule: one cycle daily at %02d:%02d local (audit, then remediate),\n' \
  "$SCHED_HOUR" "$SCHED_MIN"
echo "in the testing loop's own clone at $WEEKEND_REPO."
echo "Dead-man: GitHub issue labeled 'testing-weekend' gets a comment per"
echo "phase, plus a Pushover when $WEEKEND_ENV carries creds."
echo "A quiet day means the runner did not fire OR the previous cycle is"
echo "still running: launchd will not start a second instance of the label."
echo "Check with: launchctl list | grep radon"
echo "Smoke test now with:"
echo "  RADON_WEEKEND_REPO=$WEEKEND_REPO bash $WEEKEND_REPO/scripts/testing_weekend.sh audit"
echo "Upgrading the wrapper in this clone, with no run in flight"
echo "(check: ls -d $WEEKEND_REPO/.weekend-runner.lock):"
echo "  git -C $WEEKEND_REPO fetch origin && git -C $WEEKEND_REPO checkout -f main && git -C $WEEKEND_REPO reset --hard origin/main"
echo "Use git only. cp / cat / tee rewrite the file IN PLACE, which strands a"
echo "running wrapper at a stale byte offset; git writes a new inode instead."
