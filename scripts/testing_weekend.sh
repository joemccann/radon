#!/usr/bin/env bash
# Weekend testing loop runner — invoked by launchd on the always-on
# runner (Mac mini). One job fires daily and runs `cycle`: the audit phase
# and then the remediate phase, sequentially, in this loop's own clone.
# Sequencing them inside one clone is what keeps two phases from checking
# out over each other. Either phase still runs standalone (`audit` /
# `remediate`). See .claude/skills/testing-weekend/.
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

# One writer per runner clone. The daily fire, a hand-run smoke test and the
# setup script all drive the SAME tree, and every entry point runs
# `git clean -fdq`, so a second run would delete the live agent's uncommitted
# work mid-write with both runs reporting success. The plist pre-reset and
# setup_testing_weekend.sh both stand down on this lock, so it has to exist.
# mkdir is the atomic primitive here: flock(1) does not exist on macOS.
acquire_runner_lock() {
  local dir="$1" held
  if ! mkdir "$dir" 2>/dev/null; then
    held="$(cat "$dir/pid" 2>/dev/null || true)"
    if [[ -z "$held" ]]; then
      # No pid yet means the winner is between its mkdir and its pid write.
      # That window used to skip the `kill -0` test entirely — the loser read
      # an empty pid, called the lock stale, `rm -rf`d it and took it, and two
      # cycles then ran `git clean -fdq` in the same clone. Absent evidence is
      # not evidence of staleness. R-411.
      echo "weekend runner lock held (pid not yet published): $dir" >&2
      return 1
    fi
    if kill -0 "$held" 2>/dev/null; then
      echo "weekend runner lock held by pid $held ($dir)" >&2
      return 1
    fi
    echo "[weekend] reclaiming stale runner lock (pid $held)" >&2
    rm -rf -- "$dir"
    mkdir "$dir" 2>/dev/null || { echo "cannot take runner lock $dir" >&2; return 1; }
  fi
  # Rename the pid in so it is never half-written to a reader. R-411.
  printf '%s\n' "$$" > "$dir/pid.tmp" && mv -f "$dir/pid.tmp" "$dir/pid"
  return 0
}

release_runner_lock() {
  # ONLY the owner unlocks. Unconditional `rm -rf` meant the first run's EXIT
  # trap unlocked the SECOND run's tree on its way out. R-411.
  local dir="${1:-}" held
  [[ -n "$dir" && -d "$dir" ]] || return 0
  held="$(cat "$dir/pid" 2>/dev/null || true)"
  [[ "$held" == "$$" ]] || return 0
  rm -rf -- "$dir"
}

# Every network call in this wrapper is bounded, INCLUDING the dead-man channel
# itself: a hung issue-comment call inside the crash handler wedged the wrapper
# while it was trying to report its own death, holding the runner lock and
# dropping every subsequent daily fire. R-409.
NET_TIMEOUT_SECS="${RADON_WEEKEND_NET_TIMEOUT_SECS:-120}"
net_bounded() { timeout "$NET_TIMEOUT_SECS" "$@"; }

# A VPN flap that establishes TCP and then stalls hangs an ssh transport with
# no keepalive, and the attempt-count retry below bounds attempts, not time.
GIT_SSH_BOUNDED="ssh -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

# `source testing_weekend.sh --lock-lib-only` exposes the helpers above to the
# contract tests without running a weekend.
[[ "${1:-}" == "--lock-lib-only" ]] && return 0 2>/dev/null

# Bash reads a script LAZILY by byte offset and re-reads it from disk after
# every fork. The agent this wrapper spawns edits files in this clone, this
# one included, so the whole run body is ONE function: bash parses a function
# body in full before its first statement runs, and never reads it from disk
# again. Two invariants keep that true — nothing above this line may fork, and
# the call at the bottom must exit on its own line. Residual window: the
# initial parse itself, before main is defined.
main() {

MODE="${1:?usage: testing_weekend.sh audit|remediate|cycle}"
[[ "$MODE" == "audit" || "$MODE" == "remediate" || "$MODE" == "cycle" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon-testing}"
WEEKEND_ROOT="$(dirname "$REPO")"
VENV="$WEEKEND_ROOT/venv"
# Activate the venv so any python3.13 calls inside the agent use it.
[[ -f "$VENV/bin/activate" ]] && export PATH="$VENV/bin:$PATH"
DEADMAN_TITLE="Nightly testing runner"
DEADMAN_LABEL="testing-nightly"
# Branch prefix the skill opens/updates its PR from. Matched on the head
# ref, not the title: the title is now `Testing <date>`, which a
# hand-written PR could also start with.
PR_BRANCH_PREFIX="testing/"

resolve_pr_url() {
  # Newest-updated open PR the skill opened for this loop. A gh failure or
  # no match must yield an empty string, never a non-zero exit under set -e.
  local url
  url="$(net_bounded gh pr list --state open --limit 20 --json url,headRefName,updatedAt \
    -q "[.[] | select(.headRefName | startswith(\"$PR_BRANCH_PREFIX\"))] | sort_by(.updatedAt) | reverse | .[0].url" \
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
    --loop testing --phase "$PHASE" --status "$status" \
    --pr-url "$pr_url" --detail "log: ${RUN_LOG##*/}" \
    --env-file "$WEEKEND_ROOT/.env" >/dev/null 2>&1 || true
}

report() {
  # Dead-man: one rolling issue, a comment per run. Failure to report
  # must not mask the run's own exit code. Third arg 0 suppresses the
  # Pushover.
  local status="$1" detail="$2" push="${3:-1}"
  local body="**${PHASE}** ${STAMP} — **${status}**
${detail}
log: \`${RUN_LOG##*/}\` on the runner"
  local issue
  issue="$(net_bounded gh issue list --label "$DEADMAN_LABEL" --state open \
    --json number -q '.[0].number' 2>/dev/null || true)"
  if [[ -z "$issue" ]]; then
    net_bounded gh issue create --title "$DEADMAN_TITLE" --label "$DEADMAN_LABEL" \
      --body "Rolling dead-man for the nightly testing loop. A missing daily comment means the runner did not fire." \
      >/dev/null 2>&1 || true
    issue="$(net_bounded gh issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  [[ -n "$issue" ]] && net_bounded gh issue comment "$issue" --body "$body" >/dev/null 2>&1 || true
  if [[ "$push" == "1" ]]; then
    notify_phase "$status"
  fi
  return 0
}

# T-239: `claude -p` terminates unfinished background tasks at its print-mode
# background-wait ceiling and then exits **0**, so a phase cut off with its
# last agent still working is indistinguishable from one that finished. The
# 2026-08-28 audit was cut at 600s, filed nothing against a 24-commit delta,
# left an empty branch and no PR — and paged OK. The ceiling is lifted at the
# invocation below (the `timeout` is the cap); this is the second guarantee,
# so that if it is ever cut off anyway the operator is not told it succeeded.
BG_CEILING_MARKER="Background tasks still running after"

phase_status() {
  # rc + run log -> the one status string every dead-man channel carries.
  # `mark` is the size of $run_log before THIS round's invocation. RUN_LOG is
  # per PHASE and appended by every retry and continuation round, so grepping
  # the whole file made a round-1 ceiling message report a finished round 8 as
  # TRUNCATED forever — a permanent false alarm on exactly the long
  # remediations the detector was added to protect. R-426.
  local rc="$1" run_log="$2" mark="${3:-0}"
  if [[ $rc -eq 124 ]]; then
    printf 'TIMEOUT after %ss' "$CAP_SECS"
  elif [[ $rc -ne 0 ]]; then
    printf 'FAILED (exit %s)' "$rc"
  elif tail -c "+$((mark + 1))" "$run_log" 2>/dev/null | grep -qF "$BG_CEILING_MARKER"; then
    printf 'TRUNCATED (background work killed before the phase finished)'
  else
    printf 'OK'
  fi
}

# T-379 (T-239 recurring): `claude -p` also exits 0 when the agent answers a
# mid-run nudge with text and no tool call — print mode reads that as the end
# of the turn. The 2026-08-31 audit ended that way after 18 minutes: zero
# commits, no ledger advance, no PR, and rc 0 with no ceiling marker, so every
# dead-man channel said OK. SKILL.md's contract is that every phase commits at
# least once on the nightly branch (audit: the ledger line + PR, even for an
# empty range; remediate: the gate-count rows), so "exit 0 and nothing was
# committed during the phase" is INCOMPLETE. Both the SHA and the committer
# date are checked: the remediate phase legitimately moves HEAD from main onto
# the audit's branch tip without committing, so a moved HEAD alone is not
# evidence — the commit under HEAD must also be newer than the phase start.
INCOMPLETE_STATUS="INCOMPLETE (agent exited 0 without committing to the nightly branch)"
PHASE_HEAD_BEFORE=""
PHASE_START_EPOCH=0
phase_committed() {
  local head epoch
  head="$(git rev-parse HEAD 2>/dev/null || true)"
  [[ -n "$head" && "$head" != "$PHASE_HEAD_BEFORE" ]] || return 1
  epoch="$(git log -1 --format=%ct HEAD 2>/dev/null || true)"
  [[ -n "$epoch" && "$epoch" -ge "$PHASE_START_EPOCH" ]]
}

on_crash() {
  report "CRASHED (exit $?)" "wrapper died before the agent finished — check the runner"
}

# Bash runs the EXIT trap on an untrapped SIGTERM, so the lock released and the
# process exited 143 — but `on_crash` never ran, so launchd's ExitTimeOut kill,
# a `launchctl bootout`, an operator kill and a reboot mid-cycle all produced
# exactly the same observable as "the runner did not fire". SKILL.md tells the
# operator to read quiet as "still running", so they waited. R-384.
ROUND_PID=""
kill_round_group() {
  [[ -n "$ROUND_PID" ]] || return 0
  # `timeout` (deliberately WITHOUT --foreground) makes itself the round's
  # process-group leader, so the negative pid reaches claude and anything it
  # left behind. --foreground would signal only timeout's direct child, which
  # is the opposite of what reaping orphaned subagents needs — they would keep
  # writing into the clone while the next round runs `git clean -fdq`. R-386.
  kill -TERM -- "-$ROUND_PID" 2>/dev/null || kill -TERM "$ROUND_PID" 2>/dev/null || true
  ROUND_PID=""
}

on_signal() {
  local sig="$1"
  trap - INT TERM HUP ERR EXIT
  kill_round_group
  report "KILLED (SIG${sig})" "the wrapper was signalled before the phase finished — launchd ExitTimeOut, a bootout, an operator kill or a reboot; partial work may exist on the weekend branch"
  release_runner_lock "${RUNNER_LOCK:-}"
  exit 143
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
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
trap 'on_signal HUP' HUP

cd "$REPO"
[[ -f .radon-weekend-runner ]] || {
  echo "REFUSING: $REPO is not the dedicated weekend runner clone" >&2
  report "REFUSED" "$REPO is not the dedicated weekend runner clone" || true
  exit 2
}

RUNNER_LOCK="$REPO/.weekend-runner.lock"
acquire_runner_lock "$RUNNER_LOCK" || {
  echo "REFUSING: another weekend run owns $REPO" >&2
  # The expensive instance: acquire_runner_lock only reclaims when
  # `kill -0 $held` fails, so a recorded pid reused by ANY live unrelated
  # process makes every subsequent daily fire exit 3 in under a second. That
  # must page, not vanish. R-239.
  report "REFUSED (lock held)" "another weekend run owns $REPO (pid $(cat "$RUNNER_LOCK/pid" 2>/dev/null || echo unknown)); if no cycle is running, the recorded pid was reused — remove $RUNNER_LOCK" || true
  exit 3
}
trap 'release_runner_lock "$RUNNER_LOCK"' EXIT

LOG_DIR="$REPO/logs/testing-weekend"
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
RUN_LOG="$LOG_DIR/$MODE-$STAMP.log"
RC=0

begin_phase() {
  PHASE="$1"
  # Audit fans out read agents (cap 2h); remediation is the long half (cap 6h).
  CAP_SECS=$([[ "$PHASE" == "audit" ]] \
    && echo "${RADON_WEEKEND_AUDIT_CAP_SECS:-7200}" \
    || echo "${RADON_WEEKEND_REMEDIATE_CAP_SECS:-21600}")
  # One log per phase: the transient-network detector and the report tail
  # both read $RUN_LOG.
  RUN_LOG="$LOG_DIR/$PHASE-$STAMP.log"
  RC=0
}

# Fresh ground truth. Any leftover state from a killed prior run is
# discarded — the branch/PR on GitHub is the durable state.
# R-237: this loop's fetch was a bare single attempt while the reliability
# loop already had a bounded retry (3f96dc31, added for the 2026-08-23 port-22
# blackhole). Same shape, same reason.
FETCH_ATTEMPTS="${RADON_WEEKEND_FETCH_ATTEMPTS:-3}"
FETCH_PAUSE_SECS="${RADON_WEEKEND_FETCH_PAUSE_SECS:-60}"

fetch_origin_with_retry() {
  local attempt
  for (( attempt = 1; attempt <= FETCH_ATTEMPTS; attempt++ )); do
    net_bounded git -c "core.sshCommand=$GIT_SSH_BOUNDED" fetch origin --quiet && return 0
    echo "[weekend] git fetch origin failed — attempt $attempt/$FETCH_ATTEMPTS" >&2
    if (( attempt < FETCH_ATTEMPTS )); then sleep "$FETCH_PAUSE_SECS"; fi
  done
  return 1
}

ground_truth() {
  fetch_origin_with_retry
  git checkout -f --quiet main
  git reset --hard --quiet origin/main
  git clean -fdq --exclude=.radon-weekend-runner --exclude=.weekend-runner.lock --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env
}

# The agent commits per completed task and the skill resumes from the
# weekend branch, so a fresh attempt after a dropped API connection loses
# nothing. Retry ONLY on a transient-network signature (2026-08-16: five
# runs died to ENOTFOUND / connection-lost on the runner's flaky uplink);
# real failures and timeouts surface immediately.
KILL_AFTER_SECS="${RADON_WEEKEND_KILL_AFTER_SECS:-60}"
# Production always gives `timeout` the real phase remainder after the 60s
# launch floor below. The survivability regression needs to exercise the real
# process group and SIGKILL path without sleeping for that production-sized
# deadline. Refuse this narrow override unless pytest explicitly owns the
# process, and accept only the bounded values the regression needs.
TEST_ROUND_TIMEOUT_SECS=""
if [[ -n "${RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS:-}" ]]; then
  if [[ -z "${PYTEST_CURRENT_TEST:-}" ]]; then
    echo "REFUSING: RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS is test-only" >&2
    report "REFUSED" "RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS is test-only and cannot alter a production deadline" || true
    exit 2
  fi
  case "$RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS" in 1|2|5) ;;
    *)
      echo "REFUSING: RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS must be 1, 2, or 5" >&2
      report "REFUSED" "RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS must be the bounded test value 1, 2, or 5" || true
      exit 2
      ;;
  esac
  TEST_ROUND_TIMEOUT_SECS="$RADON_WEEKEND_TEST_ROUND_TIMEOUT_SECS"
fi
ROUND_LOG_MARK=0
MAX_ATTEMPTS=3
RETRY_PAUSE_SECS=60
is_transient_network_failure() {
  tail -c 500 "$RUN_LOG" | grep -qE 'API Error|ENOTFOUND|Connection lost|Execution error'
}

run_phase() {
  begin_phase "$1"
  trap on_crash ERR
  echo "[testing-weekend] $PHASE start $STAMP repo=$REPO cap=${CAP_SECS}s" | tee -a "$RUN_LOG"
  # NOT bare. Under `set -Eeuo pipefail` with the ERR trap armed, a failed
  # fetch made on_crash report and then the shell exit anyway — so
  # `run_phase audit` never returned and `run_phase remediate` was never run
  # and never reported, contradicting the comment on the cycle branch. This
  # loop was the exposed one: its fetch was a bare single attempt while
  # reliability already had fetch_origin_with_retry. R-237.
  if ! ground_truth; then
    RC=70
    report "GROUND TRUTH FAILED" "could not refresh the clone (network or git); the phase did not run" || true
    echo "[testing-weekend] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
    return 0
  fi

  # Attempt clock is per phase: under cycle the remediate phase must not
  # inherit the audit phase's elapsed seconds and insta-timeout.
  # R-185: `timeout claude` returning 124 (cap) or any non-zero agent exit is
  # an EXPECTED outcome this loop handles — but the ERR trap was still armed
  # over it, so every failed or timed-out run posted a false
  # "CRASHED — wrapper died" dead-man comment AND then its real status.
  trap - ERR
  PHASE_HEAD_BEFORE="$(git rev-parse HEAD 2>/dev/null || true)"
  PHASE_START_EPOCH="$(date +%s)"
  local attempt=1 start_ts=$SECONDS remain round_start
  set +e
  while :; do
    remain=$((CAP_SECS - (SECONDS - start_ts)))
    if [[ $remain -le 60 ]]; then RC=124; break; fi
    [[ -z "$TEST_ROUND_TIMEOUT_SECS" ]] || remain="$TEST_ROUND_TIMEOUT_SECS"
    ROUND_LOG_MARK=$(( $(wc -c < "$RUN_LOG" 2>/dev/null || echo 0) ))
    # Backgrounded and `wait`ed rather than run in the foreground: bash defers
    # trap handling until a foreground child completes, so a SIGTERM to the
    # wrapper was not acted on until `claude` finished on its own — which is
    # never, in the case that matters. `-k` escalates to SIGKILL so a claude
    # blocked on a hung child cannot make the cap advisory. R-384, R-386.
    CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 timeout -k "$KILL_AFTER_SECS" "$remain" claude -p "/testing-weekend $PHASE" \
      --dangerously-skip-permissions \
      --output-format text >> "$RUN_LOG" 2>&1 &
    ROUND_PID=$!
    round_start=$SECONDS
    wait "$ROUND_PID"
    RC=$?
    ROUND_PID=""
    kill_round_group
    # The exit code for a `-k` escalation is not portable: GNU coreutils 9.4
    # reports 137 when the SIGKILL is what actually ended the child, not 124.
    # The cap is OUR clock, so classify from it rather than from the code, or a
    # round the cap genuinely killed is reported as a crash. R-386.
    if (( RC != 0 && SECONDS - round_start >= remain )); then RC=124; fi
    [[ $RC -eq 0 || $RC -eq 124 || $attempt -ge $MAX_ATTEMPTS ]] && break
    is_transient_network_failure || break
    echo "[testing-weekend] transient network failure (rc=$RC) — attempt $attempt/$MAX_ATTEMPTS, retrying in ${RETRY_PAUSE_SECS}s" | tee -a "$RUN_LOG"
    attempt=$((attempt + 1))
    sleep "$RETRY_PAUSE_SECS"
  done
  set -e
  # A genuine wrapper death AFTER the agent finished must still page.
  trap on_crash ERR

  local tail_text
  tail_text="$(tail -c 1500 "$RUN_LOG" 2>/dev/null || true)"
  local status
  status="$(phase_status "$RC" "$RUN_LOG" "$ROUND_LOG_MARK")"
  # Only the rc-0-no-marker arm gains this check; TIMEOUT / FAILED /
  # TRUNCATED keep their precedence. T-379.
  if [[ "$status" == OK ]] && ! phase_committed; then status="$INCOMPLETE_STATUS"; fi
  case "$status" in
    OK)
      report "$status" "\`\`\`
${tail_text}
\`\`\`" ;;
    INCOMPLETE*)
      report "$status" "the agent exited 0 but no commit landed on the nightly branch during this phase (no ledger line / PR / gate rows) — this phase's output is INCOMPLETE and must not be read as a finished run
\`\`\`
${tail_text}
\`\`\`" ;;
    TRUNCATED*)
      report "$status" "the harness killed unfinished background work and the agent still exited 0 — this phase's output is INCOMPLETE and must not be read as a finished run
\`\`\`
${tail_text}
\`\`\`" ;;
    TIMEOUT*)
      report "$status" "partial work may exist on the weekend branch/PR
\`\`\`
${tail_text}
\`\`\`" ;;
    *)
      report "$status" "\`\`\`
${tail_text}
\`\`\`" ;;
  esac
  echo "[testing-weekend] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
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
  echo "[testing-weekend] cycle done audit_rc=$RC_AUDIT remediate_rc=$RC_REMEDIATE"
  [[ $RC_AUDIT -ne 0 ]] && exit "$RC_AUDIT"
  exit "$RC_REMEDIATE"
fi

run_phase "$MODE"
exit "$RC"
}
# Call and exit on ONE line: parsed together, so even a returning main can
# never make bash read this file again.
main "$@"; exit
