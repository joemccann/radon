#!/usr/bin/env bash
# One-command setup of the nightly security loop on the always-on runner
# (Mac mini). Run ON THE MINI from any checkout of the repo:
#
#   bash scripts/setup_security_nightly.sh
#
# Provisions the security loop's OWN dedicated clone
# (~/radon-weekend/radon-security — never edit it by hand; every run
# hard-resets it to origin/main), stamps BOTH runner markers, installs the
# daily launchd jobs (security cycle plus the DeepSec cycle, its own loop
# with the same three phases), creates both dead-man labels, and verifies
# the toolchain. The clone is deliberately
# separate from every other loop's; all loops hard-reset their tree per
# round, so a shared clone destroys in-flight work (2026-08-16 incident).
#
# SECURITY loop rails this setup enforces:
#   - it NEVER provisions web/.env or any Radon credential into the clone
#     (rail 5); the security agent runs credential-free by design;
#   - it stamps .radon-security-runner alongside .radon-weekend-runner so
#     the wrapper's two-marker gate can distinguish this clone;
#   - DeepSec and the Claude Security plugin are OPERATOR-bootstrapped, not
#     installed here (rail 8); this script only checks and reports them.
set -euo pipefail

# Runner clones' .git is agent-writable; host git here never runs its hooks
# or fsmonitor (same pin as the loop wrappers and their launchd pre-reset).
export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null \
  GIT_CONFIG_KEY_1=core.fsmonitor GIT_CONFIG_VALUE_1=false

WEEKEND_ROOT="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
WEEKEND_REPO="$WEEKEND_ROOT/radon-security"

HOST_GITDIR="$WEEKEND_ROOT/.gitdirs/security.git"
DEEPSEC_GITDIR="$WEEKEND_ROOT/.gitdirs/security-deepsec.git"
DEEPSEC_REPO="$WEEKEND_ROOT/radon-security-deepsec"
# Per-loop venv. The legacy $WEEKEND_ROOT/venv is not deleted here
# (operator follow-up after this ships).
WEEKEND_VENV="$WEEKEND_ROOT/venv-security"
DEEPSEC_VENV="$WEEKEND_ROOT/venv-security-deepsec"
# Pushover creds for the per-phase page. Lives OUTSIDE the runner clone:
# every round hard-resets and cleans that clone.
WEEKEND_ENV="$WEEKEND_ROOT/.env"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
# The checkout this script lives in is the source of the gitignored env files
# the runner clone cannot get from `git clone`. Override for an unusual layout.
SRC_REPO="${RADON_WEEKEND_SRC_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# The clone origin is read from THAT checkout, never the caller's cwd: run
# from inside another repository, a bare `git config` answered with that
# repository's origin and the runner clone was cut from it.
if [[ ! -f "$SRC_REPO/scripts/security_nightly.sh" ]] \
   || ! git -C "$SRC_REPO" rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "REFUSING: $SRC_REPO is not a Radon checkout (set RADON_WEEKEND_SRC_REPO to one)" >&2
  exit 2
fi
ORIGIN_URL="$(git -C "$SRC_REPO" config --get remote.origin.url 2>/dev/null || echo git@github.com:joemccann/radon.git)"

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
# Rail 8/9: these are OPERATOR-bootstrapped and pinned; setup only reports
# their presence. Absent ones make the matching audit stage OPERATOR_REQUIRED
# (fail-closed), never an install trigger.
advise "DeepSec workspace (.deepsec pinned, OPERATOR-bootstrapped)" \
  test -x "$DEEPSEC_REPO/.deepsec/node_modules/.bin/deepsec"
advise "private reports deploy key (~/radon-weekend/.security-reports-deploy-key, OPERATOR-provisioned)" \
  test -f "$WEEKEND_ROOT/.security-reports-deploy-key"
advise "Claude Security plugin (official, OPERATOR-installed)" \
  bash -c 'claude plugin list --json 2>/dev/null | grep -q claude-security'
# Operator policy 2026-08-31: the Claude Security spend cap is derived at run
# time from `claude auth status` (claude.ai subscription -> no --max-budget-usd;
# API key -> --max-budget-usd 50; logged out -> OPERATOR_REQUIRED). No
# operator budget env var exists any more, so nothing here provisions one;
# this advisory just shows which budget path the run will take.
advise "claude authed (subscription: no cap; API key: \$50 cap)" \
  bash -c 'claude auth status 2>/dev/null | grep -q "\"loggedIn\"[[:space:]]*:[[:space:]]*true"'
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
mkdir -p "$(dirname "$HOST_GITDIR")"
chmod 700 "$(dirname "$HOST_GITDIR")" 2>/dev/null || true
# Split gitdirs: the rungs' own gitdirs live under here, outside the host
# gitdir. Each wrapper rebuilds <loop>.git from host state before every round.
AGENT_GITDIR_ROOT="$WEEKEND_ROOT/.gitdirs-agent"
mkdir -p "$AGENT_GITDIR_ROOT"
chmod 700 "$AGENT_GITDIR_ROOT"
if [[ ! -d "$HOST_GITDIR" && ! -e "$WEEKEND_REPO/.git" ]]; then
  git clone --separate-git-dir="$HOST_GITDIR" "$ORIGIN_URL" "$WEEKEND_REPO"
elif [[ -d "$WEEKEND_REPO/.git" && ! -d "$HOST_GITDIR" ]]; then
  git -C "$WEEKEND_REPO" init --separate-git-dir="$HOST_GITDIR"
fi

# Host-bash lock hygiene (L1-L2). Source the trusted operator checkout, never
# the runner clone. Dead clone locks move aside; a live pid stands down with
# pid/owner/start. Shared-parent FILE/DIR is swept; a live shared-parent
# refuses setup so unload/reload cannot race an unknown process.
STAMP="${STAMP:-$(date +%Y%m%dT%H%M%S)}"
# shellcheck disable=SC1091
source "$SRC_REPO/scripts/security_nightly.sh" --lock-lib-only

_setup_refuse_or_reclaim_lock() {
  local dir="$1" held start dest owner cmd
  [[ -e "$dir" ]] || return 0
  if [[ -d "$dir" ]]; then
    held="$(cat "$dir/pid" 2>/dev/null || true)"
    start="$(cat "$dir/start" 2>/dev/null || true)"
  elif [[ -f "$dir" ]]; then
    held="$(cat "$dir" 2>/dev/null || true)"
    start=""
  else
    return 0
  fi
  held="${held#"${held%%[![:space:]]*}"}"
  held="${held%"${held##*[![:space:]]}"}"
  start="${start#"${start%%[![:space:]]*}"}"
  start="${start%"${start##*[![:space:]]}"}"
  case "$held" in
    ''|*[!0-9]*)
      if [[ -d "$dir" ]]; then
        echo "  runner lock $dir has unpublished pid; re-run when the cycle finishes"
        exit 1
      fi
      dest="$(_stale_lock_dest "$dir" "nopid")"
      mv -f -- "$dir" "$dest" || true
      echo "  moved stale lock $dir -> $dest"
      return 0
      ;;
  esac
  if pid_alive "$held" "$start"; then
    owner="$(/bin/ps -p "$held" -o user= 2>/dev/null || true)"
    cmd="$(/bin/ps -p "$held" -o command= 2>/dev/null || true)"
    owner="${owner#"${owner%%[![:space:]]*}"}"
    owner="${owner%"${owner##*[![:space:]]}"}"
    cmd="${cmd#"${cmd%%[![:space:]]*}"}"
    cmd="${cmd%"${cmd##*[![:space:]]}"}"
    echo "  a weekend run is in flight in $dir (pid $held, started ${start:-unknown}, owner ${owner:-unknown}, cmd ${cmd:-unknown}); re-run when it finishes"
    exit 1
  fi
  dest="$(_stale_lock_dest "$dir" "$held")"
  mv -f -- "$dir" "$dest" || true
  echo "  moved stale lock $dir (pid $held) -> $dest"
}

_setup_refuse_or_reclaim_lock "$WEEKEND_REPO/.weekend-runner.lock"
_setup_refuse_or_reclaim_lock "$DEEPSEC_REPO/.weekend-runner.lock"

for SIBLING_REPO in "$WEEKEND_ROOT/radon" "$WEEKEND_ROOT/radon-testing" \
  "$WEEKEND_ROOT/radon-ci-performance" "$WEEKEND_ROOT/radon-documentation" \
  "$WEEKEND_ROOT/radon-security-deepsec"; do
  [[ -d "$SIBLING_REPO" ]] || continue
  _setup_refuse_or_reclaim_lock "$SIBLING_REPO/.weekend-runner.lock"
done
sweep_shared_parent_lock "$WEEKEND_ROOT/.weekend-runner.lock"
case "${FOREIGN_LOCK:-}" in
  live:*)
    echo "  shared-parent lock is live (${FOREIGN_LOCK#live:}); re-run when it finishes"
    exit 1
    ;;
esac
check "no shared-parent lock" test ! -e "$WEEKEND_ROOT/.weekend-runner.lock"

# Dedicated DeepSec clone: its own loop, so its own hard-reset tree. It was
# a worktree of the security clone until 2026-09-18, and a worktree cannot
# check out main while the security clone holds it (every pre-reset died
# with "fatal: 'main' is already used by worktree"). Convert in place,
# keeping the operator-bootstrapped .deepsec/ workspace and DeepSec's
# untracked data/radon/ state.
if [[ -f "$DEEPSEC_REPO/.git" && ! -d "$DEEPSEC_GITDIR" ]]; then
  OLD_WT="$DEEPSEC_REPO.worktree-$(date +%Y%m%d%H%M%S)"
  mv "$DEEPSEC_REPO" "$OLD_WT"
  git --git-dir="$HOST_GITDIR" --work-tree="$WEEKEND_REPO" worktree prune
  mkdir -p "$(dirname "$DEEPSEC_GITDIR")"
  git clone --separate-git-dir="$DEEPSEC_GITDIR" "$ORIGIN_URL" "$DEEPSEC_REPO"
  [[ -d "$OLD_WT/.deepsec" ]] && mv "$OLD_WT/.deepsec" "$DEEPSEC_REPO/.deepsec"
  if [[ -d "$OLD_WT/data/radon" ]]; then
    mkdir -p "$DEEPSEC_REPO/data" && mv "$OLD_WT/data/radon" "$DEEPSEC_REPO/data/radon"
  fi
  rm -rf "$OLD_WT"
  echo "  converted $DEEPSEC_REPO from a worktree into a standalone clone"
fi
mkdir -p "$(dirname "$DEEPSEC_GITDIR")"
if [[ ! -d "$DEEPSEC_GITDIR" && ! -e "$DEEPSEC_REPO/.git" ]]; then
  git clone --separate-git-dir="$DEEPSEC_GITDIR" "$ORIGIN_URL" "$DEEPSEC_REPO"
elif [[ -d "$DEEPSEC_REPO/.git" && ! -d "$DEEPSEC_GITDIR" ]]; then
  git -C "$DEEPSEC_REPO" init --separate-git-dir="$DEEPSEC_GITDIR"
fi
git --git-dir="$DEEPSEC_GITDIR" --work-tree="$DEEPSEC_REPO" fetch origin --quiet
git --git-dir="$DEEPSEC_GITDIR" --work-tree="$DEEPSEC_REPO" checkout -f --quiet main
git --git-dir="$DEEPSEC_GITDIR" --work-tree="$DEEPSEC_REPO" reset --hard --quiet origin/main
mkdir -p "$DEEPSEC_REPO/logs/security-deepsec"
touch "$DEEPSEC_REPO/.radon-weekend-runner"
touch "$DEEPSEC_REPO/.radon-security-deepsec-runner"
touch "$DEEPSEC_REPO/.weekend-keep"
echo "  rail 5: web/.env and Radon credentials are NOT provisioned into the DeepSec clone either"
# An already-provisioned clone must carry the current config/ and scripts/
# before the job is installed from it. main is force-reset; any weekend
# branch and its commits survive.
git --git-dir="$HOST_GITDIR" --work-tree="$WEEKEND_REPO" fetch origin --quiet
git --git-dir="$HOST_GITDIR" --work-tree="$WEEKEND_REPO" checkout -f --quiet main
git --git-dir="$HOST_GITDIR" --work-tree="$WEEKEND_REPO" reset --hard --quiet origin/main
touch "$WEEKEND_REPO/.radon-weekend-runner"
# Rail 1: the security marker the wrapper additionally requires. Without
# it the wrapper refuses, so a stray RADON_WEEKEND_REPO can never run
# credential-free security work in a sibling loop or operator checkout.
touch "$WEEKEND_REPO/.radon-security-runner"
mkdir -p "$WEEKEND_REPO/logs/security-nightly"

# Rail 5: the security clone receives NO Radon credential. Unlike the other
# nightly loops, this setup deliberately does NOT provision web/.env (or the
# root .env, or .env.ib-mode). The security agent runs credential-free — its
# only allowed secrets are the narrowly scoped model credential launchd/gh
# already hold, a write-only private-archive credential, and the sanitized
# dead-man channel — none of which live in the clone. The per-round
# `git clean` and the launchd env carry nothing broker/deploy/db-scoped.
echo "  rail 5: web/.env and Radon credentials are NOT provisioned into the security clone"

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
# The DeepSec loop runs the same remediate gates in its own clone.
python3.13 -m venv "$DEEPSEC_VENV"
"$DEEPSEC_VENV/bin/pip" install -q --upgrade pip
"$DEEPSEC_VENV/bin/pip" install -q pytest
"$DEEPSEC_VENV/bin/pip" install -q -r "$DEEPSEC_REPO/requirements.txt"
"$DEEPSEC_VENV/bin/pip" install -q -r "$DEEPSEC_REPO/requirements-dev.txt"
( cd "$DEEPSEC_REPO" \
  && bun install --frozen-lockfile >/dev/null \
  && cd web && bun install --frozen-lockfile >/dev/null )

echo "[4/4] dead-man label + launchd jobs"
# `gh issue create --label` FAILS on a label that does not exist, and the
# wrapper swallows that failure — so a missing label turns the dead-man
# channel off silently. Idempotent: an existing label makes this a no-op.
gh label create security-nightly \
  --description "Nightly security loop dead-man (sanitized status only)" --color B60205 \
  >/dev/null 2>&1 || true
gh label create security-deepsec \
  --description "DeepSec nightly loop dead-man (sanitized status only)" --color B60205 \
  >/dev/null 2>&1 || true
if gh label list --limit 200 2>/dev/null | grep -q '^security-nightly'; then
  echo "  ok  label security-nightly"
else
  echo "  MISSING  label security-nightly (dead-man comments will be dropped)"
fi
if gh label list --limit 200 2>/dev/null | grep -q '^security-deepsec'; then
  echo "  ok  label security-deepsec"
else
  echo "  MISSING  label security-deepsec (DeepSec dead-man comments will be dropped)"
fi
mkdir -p "$LAUNCH_AGENTS"
JOB_PLIST="$LAUNCH_AGENTS/com.radon.security-daily.plist"
sed -e "s|__WEEKEND_REPO__|$WEEKEND_REPO|g" -e "s|__HOME__|$HOME|g" \
  "$WEEKEND_REPO/config/com.radon.security-daily.plist" > "$JOB_PLIST"
plutil -lint "$JOB_PLIST" >/dev/null
launchctl unload "$JOB_PLIST" 2>/dev/null || true
launchctl load "$JOB_PLIST"
echo "  loaded $JOB_PLIST"
DEEPSEC_PLIST="$LAUNCH_AGENTS/com.radon.security-deepsec.plist"
sed -e "s|__DEEPSEC_REPO__|$DEEPSEC_REPO|g" -e "s|__HOME__|$HOME|g" \
  "$WEEKEND_REPO/config/com.radon.security-deepsec.plist" > "$DEEPSEC_PLIST"
plutil -lint "$DEEPSEC_PLIST" >/dev/null
launchctl unload "$DEEPSEC_PLIST" 2>/dev/null || true
launchctl load "$DEEPSEC_PLIST"
echo "  loaded $DEEPSEC_PLIST"

# Read the cadence back off the installed job so this copy cannot drift.
SCHED_HOUR="$(plutil -extract StartCalendarInterval.Hour raw -o - "$JOB_PLIST")"
SCHED_MIN="$(plutil -extract StartCalendarInterval.Minute raw -o - "$JOB_PLIST")"

echo
printf 'Done. Schedule: one cycle daily at %02d:%02d local (audit, then remediate),\n' \
  "$SCHED_HOUR" "$SCHED_MIN"
echo "in the security loop's own clone at $WEEKEND_REPO."
echo "DeepSec is its own loop (com.radon.security-deepsec) in $DEEPSEC_REPO:"
echo "the same audit, remediate, deliver cycle, its own runner lock, venv,"
echo "dead-man label and PR branch prefix (security-deepsec/<date>)."
echo "Dead-men (two issues, one per loop):"
echo "  security-nightly  audit/remediate/deliver of the fast scanners."
echo "  security-deepsec  audit/remediate/deliver of DeepSec (8h audit cap)."
echo "A quiet security-nightly day means that runner did not fire OR the"
echo "previous cycle is still running: launchd will not start a second instance."
echo "Check with: launchctl list | grep radon"
echo "Smoke test now with:"
echo "  RADON_WEEKEND_REPO=$WEEKEND_REPO bash $WEEKEND_REPO/scripts/security_nightly.sh audit"
echo "Upgrading the wrapper in this clone, with no run in flight"
echo "(check: ls -d $WEEKEND_REPO/.weekend-runner.lock):"
echo "  git -C $WEEKEND_REPO fetch origin && git -C $WEEKEND_REPO checkout -f main && git -C $WEEKEND_REPO reset --hard origin/main"
echo "Use git only. cp / cat / tee rewrite the file IN PLACE, which strands a"
echo "running wrapper at a stale byte offset; git writes a new inode instead."
