#!/bin/bash
# PRE_RUN hook for loops/performance.env. Installed root-owned in
# /usr/local/radon-runner/hooks; run_loop.sh runs it before the phase with
# cwd = the clone.
#
# 1. Pins the clone to the newest CI-green main (REL-187), so a night after a
#    red push never measures or patches a tree CI rejected.
# 2. Pulls one read-only production snapshot (cloud/scripts/perf_probe.py)
#    over ssh with the bot's forced-command key and writes it to
#    $LOOP_STATE/prod-probe.json, where the prompt tells the agent to read it.
#    The key's authorized_keys line pins the command, so whatever this side
#    sends, the host runs the probe and nothing else (docs/runner.md 8c).
# 3. Prunes superseded comments from the rolling dead-man issue once any
#    prior PR has merged, keeping the latest durable checkpoint.
#
# Production being unreachable never refuses the phase: the agent still has
# the local surfaces, and the probe file records why the snapshot is missing.
set -uo pipefail

: "${LOOP:?}" "${WORK:?}" "${LOOP_STATE:?}" "${RUNNER_DIR:?}" "${REPO_URL:?}"
export PATH="${RADON_RUNNER_PATH:-/opt/homebrew/bin:/usr/bin:/bin}"
PY="${RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
GH="${RADON_RUNNER_GH:-/opt/homebrew/bin/gh}"
SSH="${RADON_PERF_SSH:-/usr/bin/ssh}"
GH_REPO="${RADON_WEEKEND_GH_REPO:-joemccann/radon}"
TIMEOUT_BIN="$(command -v gtimeout || command -v timeout || true)"
PROBE_OUT="$LOOP_STATE/prod-probe.json"
KEY="${RADON_PERF_SSH_KEY:-$HOME/.ssh/radon_perf_probe}"
KNOWN_HOSTS="${RADON_PERF_KNOWN_HOSTS:-$HOME/.ssh/radon_perf_known_hosts}"

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

# The SSH target lives in the root-owned loop config, never the clone.
PERF_SSH_TARGET="$(sed -n 's/^PERF_SSH_TARGET=//p' "$RUNNER_DIR/loops/$LOOP.env" 2>/dev/null | tail -n 1 | tr -d "\"'")"

hostgit fetch --quiet origin || refuse "could not fetch origin"
green="$(bounded 60 "$PY" -I "$RUNNER_DIR/lib/nightly_green_base.py" --repo "$GH_REPO" \
  --repo-dir "$WORK" --head origin/main --gh-bin "$GH" 2>/dev/null || true)"
if [[ ! "$green" =~ ^[0-9a-f]{40}$ ]] || ! hostgit rev-parse --verify --quiet "${green}^{commit}" >/dev/null; then
  green="$(hostgit rev-parse origin/main)"
fi
hostgit checkout -f --quiet --detach "$green" || refuse "could not check out the green base"
echo "base=$green"

prune_deadman() {
  [[ "${RADON_WEEKEND_SKIP_ISSUE_PRUNE:-0}" == 1 ]] && return 0
  local pruner="$RUNNER_DIR/lib/nightly_issue_prune.py"
  [[ -f "$pruner" ]] || pruner="$WORK/scripts/nightly_issue_prune.py"
  [[ -f "$pruner" ]] || return 0
  local issue
  issue="$(bounded 30 "$GH" issue list -R "$GH_REPO" --label performance-nightly --state open \
    --json number -q '.[0].number' 2>/dev/null || true)"
  [[ -n "$issue" ]] || return 0
  bounded 30 "$PY" -I "$pruner" --gh-bin "$GH" --issue "$issue" \
    --branch-prefix "performance/" >/dev/null 2>&1 || true
}

prune_deadman

probe_error() {
  "$PY" -I -c 'import json,sys,time; json.dump({"error": sys.argv[1], "taken_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(sys.argv[2], "w"))' \
    "$1" "$PROBE_OUT"
  echo "probe=unavailable: $1"
}

umask 077
rm -f "$PROBE_OUT"
if [[ -z "$PERF_SSH_TARGET" ]]; then
  probe_error "PERF_SSH_TARGET is not set in the loop config"
elif [[ ! -f "$KEY" || ! -f "$KNOWN_HOSTS" ]]; then
  probe_error "the forced-command key or its pinned known_hosts is not installed for the runner user"
else
  raw="$(mktemp "$LOOP_STATE/.probe.XXXXXX")"
  # BatchMode: never prompt. StrictHostKeyChecking against a pinned file:
  # never trust on first use. The remote side ignores the command we send.
  if bounded 180 "$SSH" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$KNOWN_HOSTS" \
      -o ConnectTimeout=20 -o ServerAliveInterval=15 -T "$PERF_SSH_TARGET" probe \
      > "$raw" 2>> "$LOOP_STATE/.probe.err" \
     && "$PY" -I -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d, dict) and d.get("schema")' "$raw" 2>/dev/null; then
    mv -f "$raw" "$PROBE_OUT"
    echo "probe=ok"
  else
    rm -f "$raw"
    probe_error "ssh probe failed or returned invalid JSON (see the runner log)"
  fi
fi
exit 0
