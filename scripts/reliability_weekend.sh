#!/usr/bin/env bash
# Weekend reliability loop runner — invoked by launchd on the always-on
# runner (Mac mini). One job fires daily and runs `cycle`: the audit phase
# and then the remediate phase, sequentially, in this loop's own clone.
# Sequencing them inside one clone is what keeps two phases from checking
# out over each other. Either phase still runs standalone (`audit` /
# `remediate`). See .claude/skills/reliability-weekend/.
#
# Safety model:
#   - runs ONLY in the dedicated runner clone (marker file required);
#     the clone is hard-reset to origin/main every run, so it must never
#     be a working checkout anyone edits by hand.
#   - the agent never pushes main (skill rail); this wrapper's only git
#     writes are fetch/reset on the runner clone.
#   - deploy this file with git only (fetch + checkout -f + reset --hard):
#     git writes a NEW inode, so a running copy keeps reading the old one.
#     cp / cat / tee rewrite it IN PLACE and strand a live run at a stale
#     byte offset. Never write it in a clone while a run is in flight.
#   - wall-clock capped; every outcome (incl. crash) is reported to the
#     rolling GitHub issue, and each phase also pages Pushover, so a
#     silent-dead runner is visible without waiting for the next run.
# -E: the ERR trap must be inherited by ground_truth/run_phase, otherwise a
# git failure at midnight exits silently with no dead-man comment and no page.
set -Eeuo pipefail

# One writer per runner clone. Both weekend plists drive the SAME tree and
# every entry point (and continuation round) runs `git clean -fdq`, so a
# missed Saturday audit firing on wake near the Sunday slot would delete
# remediate's uncommitted work mid-write with both runs reporting success.
# mkdir is the atomic primitive here: flock(1) does not exist on macOS.
acquire_runner_lock() {
  local dir="$1" held
  if ! mkdir "$dir" 2>/dev/null; then
    held="$(cat "$dir/pid" 2>/dev/null || true)"
    if [[ -n "$held" ]] && kill -0 "$held" 2>/dev/null; then
      echo "weekend runner lock held by pid $held ($dir)" >&2
      return 1
    fi
    echo "[weekend] reclaiming stale runner lock (pid ${held:-unknown})" >&2
    rm -rf -- "$dir"
    mkdir "$dir" 2>/dev/null || { echo "cannot take runner lock $dir" >&2; return 1; }
  fi
  echo "$$" > "$dir/pid"
  return 0
}

release_runner_lock() { rm -rf -- "$1"; }

# The 00:00 fetch is the cycle's single point of failure: 2026-08-23 one run
# died here on a port-22 blackhole (NordVPN) before the agent started.
# Bounded so a genuinely dead uplink still surfaces within minutes.
FETCH_ATTEMPTS="${RADON_WEEKEND_FETCH_ATTEMPTS:-3}"
FETCH_PAUSE_SECS="${RADON_WEEKEND_FETCH_PAUSE_SECS:-60}"
fetch_origin_with_retry() {
  local attempt
  for (( attempt = 1; attempt <= FETCH_ATTEMPTS; attempt++ )); do
    git fetch origin --quiet && return 0
    echo "[weekend] git fetch origin failed — attempt $attempt/$FETCH_ATTEMPTS" >&2
    if (( attempt < FETCH_ATTEMPTS )); then sleep "$FETCH_PAUSE_SECS"; fi
  done
  return 1
}

# `source reliability_weekend.sh --lock-lib-only` exposes the helpers
# above to the contract tests without running a weekend.
[[ "${1:-}" == "--lock-lib-only" ]] && return 0 2>/dev/null

# Bash reads a script LAZILY by byte offset and re-reads it from disk after
# every fork. The agent this wrapper spawns edits files in this clone, this
# one included, so the whole run body is ONE function: bash parses a function
# body in full before its first statement runs, and never reads it from disk
# again. Two invariants keep that true — nothing above this line may fork, and
# the call at the bottom must exit on its own line. Residual window: the
# initial parse itself, before main is defined.
main() {

MODE="${1:?usage: reliability_weekend.sh audit|remediate|cycle}"
[[ "$MODE" == "audit" || "$MODE" == "remediate" || "$MODE" == "cycle" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon}"
WEEKEND_ROOT="$(dirname "$REPO")"
VENV="$WEEKEND_ROOT/venv"
# Activate the venv so any python3.13 calls inside the agent use it.
[[ -f "$VENV/bin/activate" ]] && export PATH="$VENV/bin:$PATH"
DEADMAN_TITLE="Weekend reliability runner"
DEADMAN_LABEL="reliability-weekend"
# Title prefix the skill opens/updates its PR under.
PR_TITLE_PREFIX="Reliability weekend"

resolve_pr_url() {
  # Newest-updated open PR the skill opened for this loop. A gh failure or
  # no match must yield an empty string, never a non-zero exit under set -e.
  local url
  url="$(gh pr list --state open --limit 20 --json url,title,updatedAt \
    -q "[.[] | select(.title | startswith(\"$PR_TITLE_PREFIX\"))] | sort_by(.updatedAt) | reverse | .[0].url" \
    2>/dev/null || true)"
  [[ "$url" == "null" ]] && url=""
  printf '%s' "$url"
}

notify_phase() {
  # One Pushover per phase so a hung phase is visible immediately.
  # Best-effort: it must never change the run's exit code.
  local status="$1" pr_url
  pr_url="$(resolve_pr_url)"
  python3 "$REPO/scripts/weekend_notify.py" \
    --loop reliability --phase "$PHASE" --status "$status" \
    --pr-url "$pr_url" --detail "log: ${RUN_LOG##*/}" \
    --env-file "$WEEKEND_ROOT/.env" >/dev/null 2>&1 || true
}

report() {
  # Dead-man: one rolling issue, a comment per run. Failure to report
  # must not mask the run's own exit code. Third arg 0 suppresses the
  # Pushover (interim continuation rounds stay issue-only).
  local status="$1" detail="$2" push="${3:-1}"
  local body="**${PHASE}** ${STAMP} — **${status}**
${detail}
log: \`${RUN_LOG##*/}\` on the runner"
  local issue
  issue="$(gh issue list --label "$DEADMAN_LABEL" --state open \
    --json number -q '.[0].number' 2>/dev/null || true)"
  if [[ -z "$issue" ]]; then
    gh issue create --title "$DEADMAN_TITLE" --label "$DEADMAN_LABEL" \
      --body "Rolling dead-man for the weekend reliability loop. A missing daily comment means the runner did not fire." \
      >/dev/null 2>&1 || true
    issue="$(gh issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  [[ -n "$issue" ]] && gh issue comment "$issue" --body "$body" >/dev/null 2>&1 || true
  if [[ "$push" == "1" ]]; then
    notify_phase "$status"
  fi
  return 0
}

on_crash() {
  report "CRASHED (exit $?)" "wrapper died before the agent finished — check the runner"
}

# These four are defined HERE, above the trap, and not further down beside
# begin_phase: the prologue's REFUSED branches and the ERR trap below call
# report(), and a function defined later in main() is not yet defined when
# they run — every prologue death died on "report: command not found", then on
# unset PHASE/STAMP/RUN_LOG under `set -u`, so no issue comment and no page
# were ever sent for the very failures the trap exists to catch. T-209.
PHASE="prologue"
STAMP="$(date +%Y%m%dT%H%M%S)"
RUN_LOG="prologue (no phase log yet)"

# R-239: the ERR trap used to be armed only inside run_phase, so everything
# from here down — the cd, the marker check, the lock, the mkdir and the
# rotation pipeline under `set -o pipefail` — exited on a full disk, a moved
# clone or a held lock with nothing but a line on stderr. The file header
# claims every outcome is reported; for the prologue that was false.
trap on_crash ERR

cd "$REPO"
[[ -f .radon-weekend-runner ]] || {
  echo "REFUSING: $REPO is not the dedicated weekend runner clone" >&2
  report "REFUSED" "$REPO is not the dedicated weekend runner clone" 0 || true
  exit 2
}

RUNNER_LOCK="$REPO/.weekend-runner.lock"
acquire_runner_lock "$RUNNER_LOCK" || {
  echo "REFUSING: another weekend run owns $REPO" >&2
  # The expensive instance: acquire_runner_lock only reclaims when
  # `kill -0 $held` fails, so a recorded pid reused by ANY live unrelated
  # process makes every subsequent daily fire exit 3 in under a second. That
  # must page, not vanish. R-239.
  report "REFUSED (lock held)" "another weekend run owns $REPO (pid $(cat "$RUNNER_LOCK/pid" 2>/dev/null || echo unknown)); if no cycle is running, the recorded pid was reused — remove $RUNNER_LOCK" 0 || true
  exit 3
}
trap 'release_runner_lock "$RUNNER_LOCK"' EXIT

LOG_DIR="$REPO/logs/reliability-weekend"
mkdir -p "$LOG_DIR"
# Keep the newest 30 run logs. NEVER the launchd sinks: the plist points
# StandardOutPath/StandardErrorPath at launchd-cycle.log/.err inside this same
# directory, and launchd-cycle.err only gets an mtime bump when something
# writes to stderr — so it sorts old and was unlinked once ~30 per-phase logs
# accumulated, taking the only forensics for a prologue death with it. Worse,
# the rotation runs BEFORE run_phase, so the rest of that same invocation
# wrote to a deleted inode launchd still held open. R-267.
# `|| true` on the grep: it exits 1 when it filters everything out (an empty
# or sinks-only log dir), and `set -o pipefail` would turn that into a
# prologue death — now a REPORTED one, thanks to the trap above.
ls -1t "$LOG_DIR" 2>/dev/null \
  | { grep -v -e '^launchd-cycle\.log$' -e '^launchd-cycle\.err$' || true; } \
  | tail -n +31 | while IFS= read -r old; do
  rm -f -- "$LOG_DIR/$old"
done

CAP_SECS=0
# Whole-cycle wall-clock budget, measured from process start. Worst case
# without it is audit 2h + 8 remediate rounds x 6h = 50h, which would swallow
# two daily fires. The check subtracts the next round's CAP_SECS, so the
# WHOLE cycle is bounded by this number rather than by this number plus one
# more cap — see the deadline test in run_phase. R-238.
CYCLE_BUDGET_SECS="${RADON_WEEKEND_CYCLE_BUDGET_SECS:-72000}"
CYCLE_DEADLINE=$((SECONDS + CYCLE_BUDGET_SECS))
MAX_ROUNDS=1
RUN_LOG="$LOG_DIR/$MODE-$STAMP.log"
RC=0

begin_phase() {
  PHASE="$1"
  # Audit fans out ~6 read agents (cap 2h); remediation is the long half
  # (cap 6h per round). Remediation must finish the backlog, not defer it to
  # a future run: a round that dies on the cap (or crashes) is relaunched as
  # a continuation round against the same weekend branch — per-task commits
  # are the durable state — until a round exits 0 or the backstop trips.
  CAP_SECS=$([[ "$PHASE" == "audit" ]] && echo 7200 || echo 21600)
  MAX_ROUNDS=$([[ "$PHASE" == "remediate" ]] && echo 8 || echo 1)
  # One log per phase: the transient-network detector and the report tail
  # both read $RUN_LOG.
  RUN_LOG="$LOG_DIR/$PHASE-$STAMP.log"
  RC=0
}

# Fresh ground truth. Any leftover state from a killed prior run is
# discarded — the branch/PR on GitHub is the durable state.
ground_truth() {
  fetch_origin_with_retry
  git checkout -f --quiet main
  git reset --hard --quiet origin/main
  git clean -fdq --exclude=.radon-weekend-runner --exclude=.weekend-runner.lock --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env
}

# The agent commits per completed task and the skill resumes from the
# weekend branch, so nothing is lost across attempts or rounds. Two nested
# recovery layers:
#   - inner (both phases): bounded retry within a round, ONLY on a
#     transient-network signature (2026-08-16: five runs died to
#     ENOTFOUND / connection-lost on the runner's flaky uplink); real
#     failures and timeouts surface immediately.
#   - outer (remediate only): continuation rounds until a session exits 0
#     or the backstop trips — the backlog must finish this run, never
#     defer to a future one.
MAX_ATTEMPTS=3
RETRY_PAUSE_SECS=60
is_transient_network_failure() {
  tail -c 500 "$RUN_LOG" | grep -qE 'API Error|ENOTFOUND|Connection lost|Execution error'
}

run_round() {
  local attempt=1 start_ts=$SECONDS remain
  while :; do
    remain=$((CAP_SECS - (SECONDS - start_ts)))
    if [[ $remain -le 60 ]]; then RC=124; break; fi
    timeout "$remain" claude -p "/reliability-weekend $PHASE" \
      --dangerously-skip-permissions \
      --output-format text >> "$RUN_LOG" 2>&1
    RC=$?
    [[ $RC -eq 0 || $RC -eq 124 || $attempt -ge $MAX_ATTEMPTS ]] && break
    is_transient_network_failure || break
    echo "[weekend] transient network failure (rc=$RC) — attempt $attempt/$MAX_ATTEMPTS, retrying in ${RETRY_PAUSE_SECS}s" | tee -a "$RUN_LOG"
    attempt=$((attempt + 1))
    sleep "$RETRY_PAUSE_SECS"
  done
}

run_phase() {
  begin_phase "$1"
  trap on_crash ERR
  # The continuation loop's deadline check guards rounds 2..N, but round 1 of
  # each phase was unchecked — so a cycle already past its budget in the audit
  # phase still launched a full remediate round. With this, the whole cycle is
  # bounded by CYCLE_BUDGET_SECS. R-238.
  if [[ $SECONDS -ge $((CYCLE_DEADLINE - CAP_SECS)) ]]; then
    RC=75
    echo "[weekend] cycle budget cannot cover a ${CAP_SECS}s $PHASE phase — skipping" | tee -a "$RUN_LOG"
    report "SKIPPED (cycle budget)" "the cycle had no room left for a ${CAP_SECS}s $PHASE phase; the next daily fire picks it up" 0 || true
    return 0
  fi
  echo "[weekend] $PHASE start $STAMP repo=$REPO cap=${CAP_SECS}s" | tee -a "$RUN_LOG"
  # NOT bare. Under `set -Eeuo pipefail` with the ERR trap armed, a failed
  # fetch made on_crash report and then the shell exit anyway — so
  # `run_phase audit` never returned and `run_phase remediate` was never run
  # and never reported, contradicting the comment on the cycle branch. The
  # phase now records the failure and carries on to its own reporting. R-237.
  if ! ground_truth; then
    RC=70
    report "GROUND TRUTH FAILED" "could not refresh the clone (network or git); the phase did not run" 0 || true
    echo "[weekend] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
    return 0
  fi

  # R-185: `timeout claude` returning 124 (cap) or any non-zero agent exit is
  # an EXPECTED outcome these rounds handle — but the ERR trap was still armed
  # over them, so every failed or timed-out round posted a false
  # "CRASHED — wrapper died" dead-man comment AND then its real status.
  trap - ERR
  local round=1
  while :; do
    echo "[weekend] $PHASE round $round/$MAX_ROUNDS" | tee -a "$RUN_LOG"
    set +e
    run_round
    set -e
    [[ $RC -ne 0 && $round -lt $MAX_ROUNDS ]] || break
    # A daily job must finish inside its own day. launchd will not start a
    # second instance of a running label, so a cycle that overruns silently
    # eats the next fire and the dead-man goes quiet for a full day.
    # Room for the round's OWN cap, not just for the instant of the check.
    # `SECONDS -ge CYCLE_DEADLINE` passing at CYCLE_BUDGET_SECS-1 still
    # launched a further CAP_SECS, so the effective cap was
    # CYCLE_BUDGET_SECS + CAP_SECS = 26h against a 24h launchd period — and
    # launchd will not start a second instance of a running label, so the next
    # 00:00 fire was dropped with no record anywhere. R-238.
    if [[ $SECONDS -ge $((CYCLE_DEADLINE - CAP_SECS)) ]]; then
      echo "[weekend] cycle budget cannot cover another ${CAP_SECS}s round after round $round — stopping" | tee -a "$RUN_LOG"
      break
    fi
    report "ROUND $round $([[ $RC -eq 124 ]] && echo TIMEOUT || echo "FAILED (exit $RC)") — continuing" \
      "backlog not finished; relaunching a continuation round (committed tasks are durable on the weekend branch)" 0
    round=$((round+1))
    # Re-ground for the continuation: a killed session may leave a dirty tree.
    # main is force-reset; the local weekend branch and its commits survive.
    git checkout -f --quiet main
    git reset --hard --quiet origin/main
    git clean -fdq --exclude=.radon-weekend-runner --exclude=.weekend-runner.lock --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env
  done
  # A genuine wrapper death AFTER the rounds finished must still page.
  trap on_crash ERR

  local tail_text
  tail_text="$(tail -c 1500 "$RUN_LOG" 2>/dev/null || true)"
  if [[ $RC -eq 0 ]]; then
    report "OK" "\`\`\`
${tail_text}
\`\`\`"
  elif [[ $RC -eq 124 ]]; then
    report "TIMEOUT after ${CAP_SECS}s" "partial work may exist on the weekend branch/PR
\`\`\`
${tail_text}
\`\`\`"
  else
    report "FAILED (exit $RC)" "\`\`\`
${tail_text}
\`\`\`"
  fi
  echo "[weekend] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
}

if [[ "$MODE" == "cycle" ]]; then
  # Remediate runs regardless of the audit rc — backlog may exist even when
  # the audit phase failed. Exit non-zero if either phase failed.
  set +e
  run_phase audit
  RC_AUDIT=$RC
  run_phase remediate
  RC_REMEDIATE=$RC
  set -e
  echo "[weekend] cycle done audit_rc=$RC_AUDIT remediate_rc=$RC_REMEDIATE"
  [[ $RC_AUDIT -ne 0 ]] && exit "$RC_AUDIT"
  exit "$RC_REMEDIATE"
fi

run_phase "$MODE"
exit "$RC"
}
# Call and exit on ONE line: parsed together, so even a returning main can
# never make bash read this file again.
main "$@"; exit
