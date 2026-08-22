#!/usr/bin/env bash
# Weekend testing loop runner — invoked by launchd on the always-on
# runner (Mac mini). Saturday: /testing-weekend audit. Sunday:
# /testing-weekend remediate. See .claude/skills/testing-weekend/.
#
# Runs in its OWN dedicated clone (~/radon-weekend/radon-testing), never
# the reliability loop's (~/radon-weekend/radon). Both wrappers hard-reset
# and clean their clone on every round, so two loops in one working tree
# destroy each other's checkouts and in-flight work — observed 2026-08-16
# when this loop's audit checked out its branch under a running
# reliability remediation. Schedule slotting is NOT sufficient isolation:
# the reliability loop relaunches continuation rounds until its backlog
# is done, so its wall clock is unbounded.
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

MODE="${1:?usage: testing_weekend.sh audit|remediate}"
[[ "$MODE" == "audit" || "$MODE" == "remediate" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon-testing}"
WEEKEND_ROOT="$(dirname "$REPO")"
VENV="$WEEKEND_ROOT/venv"
# Activate the venv so any python3.13 calls inside the agent use it.
[[ -f "$VENV/bin/activate" ]] && export PATH="$VENV/bin:$PATH"
# Audit fans out read agents (cap 2h); remediation is the long half (cap 6h).
CAP_SECS=$([[ "$MODE" == "audit" ]] && echo 7200 || echo 21600)
DEADMAN_TITLE="Weekend testing runner"
DEADMAN_LABEL="testing-weekend"

cd "$REPO"
[[ -f .radon-weekend-runner ]] || {
  echo "REFUSING: $REPO is not the dedicated weekend runner clone" >&2
  exit 2
}

LOG_DIR="$REPO/logs/testing-weekend"
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
      --body "Rolling dead-man for the weekend testing loop. A missing weekend comment means the runner did not fire." \
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

echo "[testing-weekend] $MODE start $STAMP repo=$REPO cap=${CAP_SECS}s" | tee -a "$RUN_LOG"

# Fresh ground truth. Any leftover state from a killed prior run is
# discarded — the branch/PR on GitHub is the durable state.
git fetch origin --quiet
git checkout -f --quiet main
git reset --hard --quiet origin/main
git clean -fdq --exclude=.radon-weekend-runner --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env

# The agent commits per completed task and the skill resumes from the
# weekend branch, so a fresh attempt after a dropped API connection loses
# nothing. Retry ONLY on a transient-network signature (2026-08-16: five
# runs died to ENOTFOUND / connection-lost on the runner's flaky uplink);
# real failures and timeouts surface immediately.
MAX_ATTEMPTS=3
RETRY_PAUSE_SECS=60
is_transient_network_failure() {
  tail -c 500 "$RUN_LOG" | grep -qE 'API Error|ENOTFOUND|Connection lost|Execution error'
}

set +e
ATTEMPT=1
START_TS=$SECONDS
while :; do
  REMAIN=$((CAP_SECS - (SECONDS - START_TS)))
  if [[ $REMAIN -le 60 ]]; then RC=124; break; fi
  timeout "$REMAIN" claude -p "/testing-weekend $MODE" \
    --dangerously-skip-permissions \
    --output-format text >> "$RUN_LOG" 2>&1
  RC=$?
  [[ $RC -eq 0 || $RC -eq 124 || $ATTEMPT -ge $MAX_ATTEMPTS ]] && break
  is_transient_network_failure || break
  echo "[testing-weekend] transient network failure (rc=$RC) — attempt $ATTEMPT/$MAX_ATTEMPTS, retrying in ${RETRY_PAUSE_SECS}s" | tee -a "$RUN_LOG"
  ATTEMPT=$((ATTEMPT + 1))
  sleep "$RETRY_PAUSE_SECS"
done
set -e
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
echo "[testing-weekend] $MODE done rc=$RC" | tee -a "$RUN_LOG"
exit "$RC"
