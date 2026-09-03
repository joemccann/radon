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
# Per-loop venv. The legacy $WEEKEND_ROOT/venv is not deleted here
# (operator follow-up after this ships).
WEEKEND_VENV="$WEEKEND_ROOT/venv-testing"
# Pushover creds for the per-phase page. Lives OUTSIDE the runner clone:
# every round hard-resets and cleans that clone.
WEEKEND_ENV="$WEEKEND_ROOT/.env"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
# The checkout this script lives in is the source of the gitignored env files
# the runner clone cannot get from `git clone`. Override for an unusual layout.
SRC_REPO="${RADON_WEEKEND_SRC_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
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

# Same report shape as `check`, but advisory only: an absent env file must not
# block installing the job on a fresh machine — the provisioning step below
# says what it did instead.
advise() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "  ok  $name"
  else
    echo "  MISSING  $name  ($*)"
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
check "bash 4+ (cloud/tests)" bash -c '((BASH_VERSINFO[0] >= 4))'
check "caddy (cloud/tests edge)" command -v caddy
advise "$SRC_REPO/web/.env (else no dev server, so no browser verification)" \
  test -s "$SRC_REPO/web/.env"
if [[ $fail -ne 0 ]]; then
  echo "Fix the MISSING items above, then re-run."
  echo "  bash 3.2 leaves 34 cloud/tests permanently red on this runner (13 in"
  echo "  test_bootstrap_control_plane.py via 'exec {fd}<>', 21 in"
  echo "  test_ib_gateway_control.py via 'mapfile'); no caddy leaves 3 more."
  echo "  Installing either MOVES the recorded darwin baseline — the next audit"
  echo "  must re-record the FAILED list in the same run."
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
# The SIBLING loop's clone too. Each loop now has its own venv; the
# lock stays so a setup does not race a live sibling. R-266.
for SIBLING_REPO in "$WEEKEND_ROOT/radon" "$WEEKEND_ROOT/radon-ci-performance" \
  "$WEEKEND_ROOT/radon-documentation" "$WEEKEND_ROOT/radon-security"; do
  if [[ -d "$SIBLING_REPO" ]] \
    && kill -0 "$(cat "$SIBLING_REPO/.weekend-runner.lock/pid" 2>/dev/null)" 2>/dev/null; then
    echo "  a weekend run is in flight in $SIBLING_REPO; re-run when it finishes"
    exit 1
  fi
done
# An already-provisioned clone must carry the current config/ and scripts/
# before the job is installed from it. main is force-reset; any weekend
# branch and its commits survive.
git -C "$WEEKEND_REPO" fetch origin --quiet
git -C "$WEEKEND_REPO" checkout -f --quiet main
git -C "$WEEKEND_REPO" reset --hard --quiet origin/main
touch "$WEEKEND_REPO/.radon-weekend-runner"
touch "$WEEKEND_REPO/.radon-testing-runner"  # REL-180 (R-504): this loop's own marker
mkdir -p "$WEEKEND_REPO/logs/testing-weekend"

# web/.env is gitignored, so a fresh `git clone` can never carry it and the
# nightly hard-reset would drop it anyway; both wrappers already exclude it
# from their per-round `git clean`. Without it the Next dev server cannot
# boot, so the loop cannot do the browser verification CLAUDE.md requires
# (T-248 filed the resulting permanent local false-red on 2026-08-29).
#
# ONLY web/.env. The root .env is deliberately NOT provisioned: it would put
# IB_FLEX_TOKEN in a second place. web/.env IS read by pytest, not only by
# Next: 50 scripts/**/*.py producers (grep -rl 'load_dotenv(.*web.*\.env'
# scripts --include='*.py') call load_dotenv(web/.env) at import, so the
# clone's TURSO creds land in os.environ under every collected module.
# scripts/tests/conftest.py::_strip_turso_credentials removes them per test;
# that fixture, not this file, keeps the pytest gate host-independent (T-317:
# without it 22 tests red with FlexTokenLocked on a provisioned clone).
# Re-copied every setup run so a rotated key propagates. Never inline a value.
provision_env_file() {
  local rel="$1"
  local src="$SRC_REPO/$rel"
  local dst="$WEEKEND_REPO/$rel"
  if [[ ! -s "$src" ]]; then
    echo "  skipped $rel (none at $src; the loops run without it)"
    return 0
  fi
  if [[ -s "$dst" && "$dst" -nt "$src" ]]; then
    echo "  kept $rel (clone copy is newer than $src)"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  install -m 600 "$src" "$dst"
  echo "  provisioned $rel from $SRC_REPO (0600)"
}
if [[ "$SRC_REPO" == "$WEEKEND_REPO" ]]; then
  echo "  MISSING  env provisioning: run setup from your own checkout, not the runner clone"
else
  for env_rel in web/.env; do
    provision_env_file "$env_rel"
  done
fi

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
# REL-207 (R-569): CI installs the dev deps (pytest-asyncio etc.); a runner
# venv without them false-reds every async test in this loop's own gate.
"$WEEKEND_VENV/bin/pip" install -q -r "$WEEKEND_REPO/requirements-dev.txt"
"$WEEKEND_VENV/bin/python" -c "import pytest_asyncio" \
  || { echo "REFUSING: pytest_asyncio not importable in $WEEKEND_VENV" >&2; exit 1; }
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
