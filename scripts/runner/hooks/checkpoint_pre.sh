#!/bin/bash
# PRE_RUN hook for the maintenance loops (reliability, testing, documentation,
# ci-performance). Installed root-owned in /usr/local/radon-runner/hooks;
# run_loop.sh runs it before the agent with cwd = the clone.
#
# 1. Pins the clone to the newest CI-green main, so a night after a red push
#    never audits or patches a tree CI rejected.
# 2. Writes the loop's checkpoint to $LOOP_STATE/scratch/audit-context.md
#    with scripts/nightly_audit_context.py, which reads the rolling issue and
#    keeps only comments by the repository owner, members or collaborators.
#    Anyone can comment on a public issue, so the prompt reads this file and
#    never the comments themselves; the author filter is code, not prose.
#
# GitHub being unreachable never refuses the phase: the file then says the
# base is UNRESOLVED and the prompt falls back to its ledger.
set -uo pipefail

: "${LOOP:?}" "${WORK:?}" "${LOOP_STATE:?}" "${RUNNER_DIR:?}" "${REPO_URL:?}"
export PATH="${RADON_RUNNER_PATH:-/opt/homebrew/bin:/usr/bin:/bin}"
PY="${RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
GH="${RADON_RUNNER_GH:-/opt/homebrew/bin/gh}"
GH_REPO="${RADON_WEEKEND_GH_REPO:-joemccann/radon}"
TIMEOUT_BIN="$(command -v gtimeout || command -v timeout || true)"
SCRATCH="$LOOP_STATE/scratch"

case "$LOOP" in
  reliability|testing|documentation|ci-performance) LABEL="$LOOP-nightly" ;;
  *) echo "refused=checkpoint_pre.sh does not know loop $LOOP"; exit 2 ;;
esac

refuse() {
  echo "REFUSING: $1" >&2
  echo "refused=$1"
  exit 2
}

bounded() { "$TIMEOUT_BIN" "$1" "${@:2}"; }
hostgit() { git -c core.hooksPath=/dev/null -c core.fsmonitor=false -C "$WORK" "$@"; }

[[ -n "$TIMEOUT_BIN" ]] || refuse "no timeout binary on the runner PATH"
[[ "$(hostgit config --get remote.origin.url 2>/dev/null)" == "$REPO_URL" ]] \
  || refuse "the clone's origin is not the loop's repository"

hostgit fetch --quiet origin || refuse "could not fetch origin"
green="$(bounded 60 "$PY" -I "$RUNNER_DIR/lib/nightly_green_base.py" --repo "$GH_REPO" \
  --repo-dir "$WORK" --head origin/main --gh-bin "$GH" 2>/dev/null || true)"
if [[ ! "$green" =~ ^[0-9a-f]{40}$ ]] || ! hostgit rev-parse --verify --quiet "${green}^{commit}" >/dev/null; then
  green="$(hostgit rev-parse origin/main)"
fi
hostgit checkout -f --quiet --detach "$green" || refuse "could not check out the green base"
echo "base=$green"

umask 077
install -d -m 700 "$SCRATCH" || refuse "could not create the private scratch directory"
rm -f -- "$SCRATCH/audit-context.md"
if bounded 180 "$PY" -I "$RUNNER_DIR/lib/nightly_audit_context.py" --repo "$GH_REPO" --repo-dir "$WORK" \
    --head HEAD --gh-bin "$GH" --label "$LABEL" --out "$SCRATCH/audit-context.md" >&2; then
  echo "checkpoint=written"
else
  echo "checkpoint=unavailable"
fi
exit 0
