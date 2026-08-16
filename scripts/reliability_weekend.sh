#!/usr/bin/env bash
# Weekend reliability loop runner — invoked by launchd on the always-on
# runner (Mac mini). Saturday: /reliability-weekend audit. Sunday:
# /reliability-weekend remediate. See .claude/skills/reliability-weekend/.
#
# Safety model:
#   - runs ONLY in the dedicated runner clone (marker file required);
#     the clone is hard-reset to origin/main every run, so it must never
#     be a working checkout anyone edits by hand.
#   - the agent never pushes main (skill rail); this wrapper's only git
#     writes are fetch/reset on the runner clone.
#   - wall-clock capped; every outcome (incl. crash) is reported to the
#     rolling GitHub issue so a silent-dead runner is visible Monday.
set -euo pipefail

MODE="${1:?usage: reliability_weekend.sh audit|remediate}"
[[ "$MODE" == "audit" || "$MODE" == "remediate" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon}"
# Audit fans out ~6 read agents (cap 2h); remediation is the long half (cap 6h
# per round). Remediation must finish the backlog, not defer it to a future
# weekend: a round that dies on the cap (or crashes) is relaunched as a
# continuation round against the same weekend branch — per-task commits are the
# durable state — until a round exits 0 or the backstop trips.
CAP_SECS=$([[ "$MODE" == "audit" ]] && echo 7200 || echo 21600)
MAX_ROUNDS=$([[ "$MODE" == "remediate" ]] && echo 8 || echo 1)
DEADMAN_TITLE="Weekend reliability runner"
DEADMAN_LABEL="reliability-weekend"

cd "$REPO"
[[ -f .radon-weekend-runner ]] || {
  echo "REFUSING: $REPO is not the dedicated weekend runner clone" >&2
  exit 2
}

LOG_DIR="$REPO/logs/reliability-weekend"
mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%dT%H%M%S)"
RUN_LOG="$LOG_DIR/$MODE-$STAMP.log"
# Keep the newest 30 run logs.
ls -1t "$LOG_DIR" 2>/dev/null | tail -n +31 | while IFS= read -r old; do
  rm -f -- "$LOG_DIR/$old"
done

report() {
  # Dead-man: one rolling issue, a comment per run. Failure to report
  # must not mask the run's own exit code.
  local status="$1" detail="$2"
  local body="**${MODE}** ${STAMP} — **${status}**
${detail}
log: \`${RUN_LOG##*/}\` on the runner"
  local issue
  issue="$(gh issue list --label "$DEADMAN_LABEL" --state open \
    --json number -q '.[0].number' 2>/dev/null || true)"
  if [[ -z "$issue" ]]; then
    gh issue create --title "$DEADMAN_TITLE" --label "$DEADMAN_LABEL" \
      --body "Rolling dead-man for the weekend reliability loop. A missing weekend comment means the runner did not fire." \
      >/dev/null 2>&1 || return 0
    issue="$(gh issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  [[ -n "$issue" ]] && gh issue comment "$issue" --body "$body" >/dev/null 2>&1 || true
}

on_crash() {
  report "CRASHED (exit $?)" "wrapper died before the agent finished — check the runner"
}
trap on_crash ERR

echo "[weekend] $MODE start $STAMP repo=$REPO cap=${CAP_SECS}s" | tee -a "$RUN_LOG"

# Fresh ground truth. Any leftover state from a killed prior run is
# discarded — the branch/PR on GitHub is the durable state.
git fetch origin --quiet
git checkout -f --quiet main
git reset --hard --quiet origin/main
git clean -fdq --exclude=.radon-weekend-runner --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env

ROUND=1
while :; do
  echo "[weekend] $MODE round $ROUND/$MAX_ROUNDS" | tee -a "$RUN_LOG"
  set +e
  timeout "$CAP_SECS" claude -p "/reliability-weekend $MODE" \
    --dangerously-skip-permissions \
    --output-format text >> "$RUN_LOG" 2>&1
  RC=$?
  set -e
  [[ $RC -ne 0 && $ROUND -lt $MAX_ROUNDS ]] || break
  report "ROUND $ROUND $([[ $RC -eq 124 ]] && echo TIMEOUT || echo "FAILED (exit $RC)") — continuing" \
    "backlog not finished; relaunching a continuation round (committed tasks are durable on the weekend branch)"
  ROUND=$((ROUND+1))
  # Re-ground for the continuation: a killed session may leave a dirty tree.
  # main is force-reset; the local weekend branch and its commits survive.
  git checkout -f --quiet main
  git reset --hard --quiet origin/main
  git clean -fdq --exclude=.radon-weekend-runner --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env
done
trap - ERR

TAIL="$(tail -c 1500 "$RUN_LOG" 2>/dev/null || true)"
if [[ $RC -eq 0 ]]; then
  report "OK" "\`\`\`
${TAIL}
\`\`\`"
elif [[ $RC -eq 124 ]]; then
  report "TIMEOUT after ${CAP_SECS}s" "partial work may exist on the weekend branch/PR
\`\`\`
${TAIL}
\`\`\`"
else
  report "FAILED (exit $RC)" "\`\`\`
${TAIL}
\`\`\`"
fi
echo "[weekend] $MODE done rc=$RC" | tee -a "$RUN_LOG"
exit "$RC"
