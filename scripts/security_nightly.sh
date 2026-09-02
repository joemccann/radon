#!/usr/bin/env bash
# Nightly security loop runner — invoked by launchd on the always-on
# runner (Mac mini). One job fires daily and runs `cycle`: the audit phase
# and then the remediate phase, sequentially, in this loop's own clone.
# Sequencing them inside one clone is what keeps two phases from checking
# out over each other. Either phase still runs standalone (`audit` /
# `remediate`). See .claude/skills/security-nightly/.
#
# Runs in its OWN dedicated clone (~/radon-weekend/radon-security), never
# the reliability loop's (~/radon-weekend/radon). Both wrappers hard-reset
# and clean their clone on every round, so two loops in one working tree
# destroy each other's checkouts and in-flight work — observed 2026-08-16
# when the testing loop's audit checked out its branch under a running
# reliability remediation. Schedule slotting is NOT sufficient isolation:
# the reliability loop relaunches continuation rounds until its backlog
# is done, so its wall clock is unbounded. (First observed on the testing
# loop, 2026-08-16.)
#
# SECURITY loop specifics (see .claude/skills/security-nightly/SKILL.md):
#   - canonical clone realpath ~/radon-weekend/radon-security, guarded by
#     TWO markers (.radon-weekend-runner AND .radon-security-runner) so it
#     can never run in a sibling loop's clone or the operator checkout;
#   - the clone and this wrapper carry NO Radon .env / broker / deploy
#     credential (rail 5). launchd hands the job only the plist env, and
#     the setup script never provisions web/.env into this clone;
#   - the dead-man comment is a sanitized PHASE STAMP status line.
#     No three-section write-up, no run-log tail, no log-file pointer,
#     no routes or exploits (rail 7). The issue create body is a
#     timeless rolling-dead-man description; run history stays in comments.
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
# `git clean -fdxq`, so a second run would delete the live agent's uncommitted
# work mid-write with both runs reporting success. The plist pre-reset and
# setup_security_nightly.sh both stand down on this lock, so it has to exist.
# mkdir is the atomic primitive here: flock(1) does not exist on macOS.
acquire_runner_lock() {
  local dir="$1" held
  if ! mkdir "$dir" 2>/dev/null; then
    held="$(cat "$dir/pid" 2>/dev/null || true)"
    if [[ -z "$held" ]]; then
      # No pid yet means the winner is between its mkdir and its pid write.
      # That window used to skip the `kill -0` test entirely — the loser read
      # an empty pid, called the lock stale, `rm -rf`d it and took it, and two
      # cycles then ran `git clean -fdxq` in the same clone. Absent evidence is
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
net_bounded() { "$TIMEOUT_BIN" "$NET_TIMEOUT_SECS" "$@"; }

# A VPN flap that establishes TCP and then stalls hangs an ssh transport with
# no keepalive, and the attempt-count retry below bounds attempts, not time.
GIT_SSH_BOUNDED="ssh -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

# `source security_nightly.sh --lock-lib-only` exposes the helpers above to the
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

MODE="${1:?usage: security_nightly.sh audit|remediate|cycle}"
[[ "$MODE" == "audit" || "$MODE" == "remediate" || "$MODE" == "cycle" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon-security}"
WEEKEND_ROOT="$(dirname "$REPO")"
# Per-loop venv. The legacy $WEEKEND_ROOT/venv is not deleted here
# (operator follow-up after this ships).
VENV="$WEEKEND_ROOT/venv-security"
# Snapshot gh/timeout BEFORE the venv prepend. PATH is $VENV/bin first
# after this, and a planted venv gh can replace the wrapper-only comment.
TIMEOUT_BIN="$(command -v timeout || true)"
GH_BIN="$(command -v gh || true)"
# Activate the venv so any python3.13 calls inside the agent use it.
[[ -f "$VENV/bin/activate" ]] && export PATH="$VENV/bin:$PATH"
DEADMAN_TITLE="Nightly security runner"
DEADMAN_LABEL="security-nightly"
ISSUE_SANITIZE=1
LOOP_SLUG="security"
DEADMAN_CREATE_BODY="Rolling dead-man for the nightly ${LOOP_SLUG} loop. Sanitized status only. Never a route, file, attack, secret, or account. A missing daily comment means the runner did not fire."
# Branch prefix the skill opens/updates its PR from. Matched on the head
# ref, not the title: the title is now `Security <date>`, which a
# hand-written PR could also start with.
PR_BRANCH_PREFIX="security/"

resolve_pr_url() {
  # Newest-updated open PR the skill opened for this loop. A gh failure or
  # no match must yield an empty string, never a non-zero exit under set -e.
  local url
  url="$(net_bounded "$GH_BIN" pr list --state open --limit 20 --json url,headRefName,updatedAt \
    -q "[.[] | select(.headRefName | startswith(\"$PR_BRANCH_PREFIX\"))] | sort_by(.updatedAt) | reverse | .[0].url" \
    2>/dev/null || true)"
  [[ "$url" == "null" ]] && url=""
  printf '%s' "$url"
}

_notify_cred() {
  local key="$1" envf="$WEEKEND_ROOT/.env" line val=""
  val="${!key:-}"
  if [[ -n "$val" ]]; then
    printf '%s' "$val"
    return 0
  fi
  [[ -f "$envf" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    case "$line" in
      "${key}="*)
        val="${line#*=}"
        val="${val#\"}"
        val="${val%\"}"
        val="${val#\'}"
        val="${val%\'}"
        printf '%s' "$val"
        return 0
        ;;
    esac
  done < "$envf"
}

_notify_curl() {
  local loop="$1" phase="$2" status="$3" pr_url="$4" detail="$5"
  local user token title message
  user="$(_notify_cred PUSHOVER_USER || true)"
  token="$(_notify_cred PUSHOVER_TOKEN || true)"
  [[ -n "$user" && -n "$token" ]] || return 0
  [[ -x /usr/bin/curl ]] || return 0
  title="radon ${loop} ${phase}"
  # PATH is $VENV/bin first. A planted or failing PATH tr under set -e
  # aborts before curl; notify_phase's || true would swallow the page.
  message="$(printf '%s' "$status" | /usr/bin/tr -s '[:space:]' ' ')"
  detail="$(printf '%s' "$detail" | /usr/bin/tr -s '[:space:]' ' ')"
  [[ -n "$detail" ]] && message="${message} ${detail}"
  [[ -n "$pr_url" ]] && message="${message} ${pr_url}"
  message="$(printf '%s' "$message" | /usr/bin/tr -s '[:space:]' ' ')"
  local k v
  for k in token user title message; do
    v="${!k}"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    v="${v//$'\n'/ }"
    v="${v//$'\r'/}"
    printf -v "$k" '%s' "$v"
  done
  # Creds stay off curl argv and off disk. -q must be argv[1].
  {
    printf 'url = "https://api.pushover.net/1/messages.json"\n'
    printf 'request = POST\n'
    printf 'silent = true\n'
    printf 'show-error = true\n'
    printf 'max-time = 10\n'
    printf 'output = "/dev/null"\n'
    printf 'data-urlencode = "token=%s"\n' "$token"
    printf 'data-urlencode = "user=%s"\n' "$user"
    printf 'data-urlencode = "title=%s"\n' "$title"
    printf 'data-urlencode = "message=%s"\n' "$message"
    printf 'data-urlencode = "priority=0"\n'
  } | /usr/bin/curl -q --config - >/dev/null 2>&1 || true
}

notify_phase() {
  # One Pushover per phase so a hung phase is visible immediately.
  # Best-effort: it must never change the run's exit code.
  # Never exec python after the agent: clone/venv python3 and
  # /usr/bin/python3 without -I both import agent-writable modules
  # from WEEKEND_ROOT (sys.path[0]). System python also lacks dotenv,
  # so a 0 exit would skip _notify_curl. Always page with /usr/bin/curl.
  local status="$1" pr_url
  pr_url="$(resolve_pr_url)"
  _notify_curl "$LOOP_SLUG" "$PHASE" "$status" "$pr_url" "log: ${RUN_LOG##*/}" || true
}

_sanitize_issue_text() {
  # Parsed with main(). Never exec disk python3 or a formatter file: both
  # are writable by the agent (venv python3, clone file, PID-guessable snapshot).
  local text="$1"
  [[ -n "$text" ]] || { printf '%s' "$text"; return 0; }
  text="${text//https:\/\/claude.ai\/settings\/usage/$'\x01USAGE\x01'}"
  text="${text//http:\/\/claude.ai\/settings\/usage/$'\x01USAGE\x01'}"
  text="${text//claude.ai\/settings\/usage/$'\x01USAGE\x01'}"
  text="$(printf '%s' "$text" | /usr/bin/sed -E \
    -e 's,https?://[^[:space:]]+,[REDACTED],g' \
    -e 's,(^|[^[:alnum:].])/(api|admin)/[A-Za-z0-9._/-]+,\1[REDACTED],g' \
    -e 's,[A-Za-z0-9./_-]+\.(py|ts|tsx|js|mjs|cjs|sh|go|rb|java|json|yml|yaml|toml|md):[0-9]+,[REDACTED],g' \
    -e 's,[Bb]earer [^[:space:]]+,Bearer [REDACTED],g' \
    -e 's,[A-Za-z0-9_]*(TOKEN|SECRET|PASSWORD|PASSWD|PASS|AUTH|CREDENTIAL|API_KEY|APIKEY|_KEY)[A-Za-z0-9_]*[[:space:]]*[=:][[:space:]]*[^[:space:]]+,[REDACTED],g' \
    -e 's,[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z][A-Za-z]+,[REDACTED],g' \
    -e 's,(^|[^A-Za-z0-9])(radon)?(trader|operator)[0-9]+,\1[REDACTED],g' \
    -e 's,[Cc]heck the runner,,g' \
    -e 's,[Oo]n the runner,,g' \
    || true)"
  text="${text//$'\x01USAGE\x01'/claude.ai/settings/usage}"
  printf '%s' "$text"
}

_format_issue_body() {
  # In-main bash only. Wrapper dead-man is PHASE STAMP status, not the
  # three-section agent write-up. Never exec disk python3 after the agent.
  local phase="$1" status="$2" detail="$3"
  local clean body
  if [[ "${ISSUE_SANITIZE:-0}" == "1" ]]; then
    phase="$(_sanitize_issue_text "$phase")"
    status="$(_sanitize_issue_text "$status")"
    detail="$(_sanitize_issue_text "$detail")"
  fi
  clean="${detail%%\`\`\`*}"
  body="$(printf '**%s** %s **%s**' "$phase" "${STAMP:-}" "$status")"
  if [[ -n "$clean" ]]; then
    body="${body}"$'\n'"${clean}"
  fi
  if [[ "${ISSUE_SANITIZE:-0}" == "1" ]]; then
    body="$(_sanitize_issue_text "$body")"
  fi
  printf '%s\n' "$body"
}

report() {
  # Dead-man: one rolling issue, a comment per run. Failure to report
  # must not mask the run's own exit code. Third arg 0 suppresses the
  # Pushover.
  local status="$1" detail="$2" push="${3:-1}"
  local body
  body="$(_format_issue_body "$PHASE" "$status" "$detail")"
  local issue
  issue="$(net_bounded "$GH_BIN" issue list --label "$DEADMAN_LABEL" --state open \
    --json number -q '.[0].number' 2>/dev/null || true)"
  if [[ -z "$issue" ]]; then
    net_bounded "$GH_BIN" issue create --title "$DEADMAN_TITLE" --label "$DEADMAN_LABEL" \
      --body "$DEADMAN_CREATE_BODY" \
      >/dev/null 2>&1 || true
    issue="$(net_bounded "$GH_BIN" issue list --label "$DEADMAN_LABEL" --state open \
      --json number -q '.[0].number' 2>/dev/null || true)"
  fi
  [[ -n "$issue" ]] && net_bounded "$GH_BIN" issue comment "$issue" --body "$body" >/dev/null 2>&1 || true
  if [[ "$push" == "1" ]]; then
    notify_phase "$status"
  fi
  return 0
}

# T-239: `claude -p` terminates unfinished background tasks at its print-mode
# background-wait ceiling and then exits **0**, so a phase cut off with its
# last agent still working is indistinguishable from one that finished. The
# testing loop's 2026-08-28 audit was cut at 600s, filed nothing on a 24-commit delta,
# left an empty branch and no PR — and paged OK. The ceiling is lifted at the
# invocation below (the `timeout` is the cap); this is the second guarantee,
# so that if it is ever cut off anyway the operator is not told it succeeded.
BG_CEILING_MARKER="Background tasks still running after"

# Operator policy 2026-08-31 (run 20260831T000007): `claude -p` exited 0 with
# the remediate phase parked mid-flight ("Suite at ~35%; I'll pick up when the
# background run completes") and text that matched no ceiling marker, so the
# wrapper paged OK while the private run-record still had a full pytest suite
# in flight. Exit code 0 alone is therefore NOT completion for this loop: the
# skill prints this exact prefix as the LAST line of a phase that actually
# finished (a clean fail-closed OPERATOR_REQUIRED night included), and an
# exit-0 round without it is INCOMPLETE — reported as such, exited non-zero,
# audited SHA untouched, so the next fire resumes the same private run in
# ~/radon-weekend/.security-nightly-scratch/ instead of calling the night OK.
PHASE_COMPLETE_MARKER="SECURITY-NIGHTLY PHASE COMPLETE:"

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

on_crash() {
  report "CRASHED (exit $?)" "wrapper died before the agent finished"
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
  # writing into the clone while the next round runs `git clean -fdxq`. R-386.
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
# Rail 1: the security loop refuses unless the SECURITY marker is present too.
# .radon-weekend-runner alone is every sibling loop's clone; only
# ~/radon-weekend/radon-security carries .radon-security-runner, so this is
# what stops a stray RADON_WEEKEND_REPO from running credential-free security
# work — or, worse, scanner egress — inside another loop's or the operator's
# checkout.
[[ -f .radon-security-runner ]] || {
  echo "REFUSING: $REPO is not the dedicated SECURITY runner clone (.radon-security-runner absent)" >&2
  report "REFUSED" "$REPO lacks the .radon-security-runner marker; the security loop runs only in ~/radon-weekend/radon-security" || true
  exit 2
}

# REL-180 (R-506): CREDENTIAL-FREE is a check, not a comment. A credential
# file that ever lands in this clone (an operator copy, a stray provision)
# would ride every nightly reset into the third-party scanners' tree.
for credential_file in .env .env.ib-mode web/.env; do
  [[ -e "$credential_file" ]] || continue
  echo "REFUSING: $REPO holds a credential file ($credential_file); the security clone must stay credential-free — remove it" >&2
  report "REFUSED" "$REPO holds a credential file ($credential_file); the security loop runs credential-free only — remove it from the clone" || true
  exit 2
done

# Rail 5b: model spend rides the operator's claude.ai subscription, never an
# Anthropic API key. Claude Code and the Claude Agent SDK subprocesses that
# `deepsec process`/`revalidate` fan out both PREFER a key over the claude.ai
# login whenever one is visible, and only whisper it on stderr ("claude.ai
# connectors are disabled because ANTHROPIC_API_KEY or another auth source is
# set and takes precedence over your claude.ai login"). On 2026-09-01 that put
# an 83-finding process + revalidate round on metered API billing with nothing
# in the run record to show for it. Strip the environment first: launchd hands
# this job no key today, but a hand-run `cycle` from an operator shell inherits
# the whole environment.
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL \
      CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX AWS_BEARER_TOKEN_BEDROCK

# `unset` cannot reach a key the scanner loads for itself: deepsec reads
# .deepsec/.env*.local out of its own workspace, which is gitignored and
# survives every nightly `git clean`. Refuse rather than edit operator state —
# and never echo the value, only the path the operator has to clear.
for key_file in .deepsec/.env .deepsec/.env.local .deepsec/.env.*.local .env.local; do
  [[ -f "$key_file" ]] || continue
  grep -qE '^[[:space:]]*(export[[:space:]]+)?ANTHROPIC_(API_KEY|AUTH_TOKEN)[[:space:]]*=[[:space:]]*[^[:space:]]' "$key_file" || continue
  echo "REFUSING: $key_file holds Anthropic API key material; this loop bills the claude.ai subscription only — remove the key line" >&2
  report "REFUSED" "$key_file holds Anthropic API key material; the scanners would bill metered API usage instead of the claude.ai subscription — remove the key line (rotate the key too, it has been used)" || true
  exit 2
done

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

LOG_DIR="$REPO/logs/security-nightly"
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
  # One log per phase: the transient-network detector reads $RUN_LOG.
  # Issue comments do not include a run-log tail.
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
  git clean -fdxq --exclude=.radon-weekend-runner --exclude=.radon-security-runner --exclude=.weekend-runner.lock --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env --exclude=node_modules/ --exclude=.next/ --exclude=.deepsec/
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
# A subscription quota is per MODEL, so an exhausted one is a reason to drop a
# rung, not to lose the night. 2026-09-01: `~/.claude/settings.json` carried
# `model: claude-fable-5[1m]`, these wrappers passed no --model and inherited
# it, that model's quota was gone, and `claude -p` printed one line and exited
# 1 in under a second — the security loop's audit AND remediate both died that
# way at 00:00 while `--model opus` and `--model sonnet` answered normally on
# the same Max login. Pinning the model also takes the operator's global
# default off the unattended path: an interactive session changing it must not
# decide what tonight runs on.
QUOTA_EXHAUSTED_MARKER="out of usage credits"
MODEL_LADDER="${RADON_WEEKEND_MODEL_LADDER:-claude-fable-5[1m] claude-opus-5[1m] claude-opus-5 claude-sonnet-5}"
read -r -a MODEL_RUNGS <<< "$MODEL_LADDER"
MODEL_INDEX=0
ALL_MODELS_EXHAUSTED=0
# `--model` binds ONE process. The skill's Stage 4 spawns a SECOND, nested
# `claude` — the Claude Security agent scan, the longest and most expensive
# call of the night — and a flag on the outer process does not reach it, so
# that call still resolved ~/.claude/settings.json: the single-point kill
# switch of 2026-09-01, alive inside the loop it killed. The rung therefore
# travels as environment. Re-exported after every ladder drop below, so a
# dropped or resumed round scans on the rung actually in force. DOC-042.
use_model_rung() { export RADON_WEEKEND_MODEL="${MODEL_RUNGS[$MODEL_INDEX]}"; }
use_model_rung

# Scoped to THIS round's slice of the log, like the ceiling detector: RUN_LOG
# is per phase and every round appends to it, so a whole-file grep would let
# round 1's exhausted rung drop a model on every later round forever. R-426.
is_quota_exhausted() {
  tail -c "+$((ROUND_LOG_MARK + 1))" "$RUN_LOG" 2>/dev/null | grep -qF "$QUOTA_EXHAUSTED_MARKER"
}

is_transient_network_failure() {
  tail -c 500 "$RUN_LOG" | grep -qE 'API Error|ENOTFOUND|Connection lost|Execution error'
}

run_phase() {
  begin_phase "$1"
  trap on_crash ERR
  echo "[security-nightly] $PHASE start $STAMP repo=$REPO cap=${CAP_SECS}s" | tee -a "$RUN_LOG"
  # NOT bare. Under `set -Eeuo pipefail` with the ERR trap armed, a failed
  # fetch made on_crash report and then the shell exit anyway — so
  # `run_phase audit` never returned and `run_phase remediate` was never run
  # and never reported, contradicting the comment on the cycle branch. The
  # testing loop was the exposed one: its fetch was a bare single attempt while
  # reliability already had fetch_origin_with_retry. R-237.
  if ! ground_truth; then
    RC=70
    report "GROUND TRUTH FAILED" "could not refresh the clone (network or git); the phase did not run" || true
    echo "[security-nightly] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
    return 0
  fi

  # Attempt clock is per phase: under cycle the remediate phase must not
  # inherit the audit phase's elapsed seconds and insta-timeout.
  # R-185: `timeout claude` returning 124 (cap) or any non-zero agent exit is
  # an EXPECTED outcome this loop handles — but the ERR trap was still armed
  # over it, so every failed or timed-out run posted a false
  # "CRASHED — wrapper died" dead-man comment AND then its real status.
  trap - ERR
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
    CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 "$TIMEOUT_BIN" -k "$KILL_AFTER_SECS" "$remain" claude -p "/security-nightly $PHASE" \
      --model "${MODEL_RUNGS[$MODEL_INDEX]}" \
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
    # A quota drop is not one of the three transient-network attempts: it
    # costs no wall clock, and the next rung is a different quota, so it
    # retries immediately and `attempt` is untouched. Bounded by the ladder.
    if (( RC != 0 )) && is_quota_exhausted; then
      if (( MODEL_INDEX + 1 < ${#MODEL_RUNGS[@]} )); then
        MODEL_INDEX=$((MODEL_INDEX + 1))
        use_model_rung
        echo "[security-nightly] ${MODEL_RUNGS[$((MODEL_INDEX - 1))]} is out of usage credits; dropping to ${MODEL_RUNGS[$MODEL_INDEX]}" | tee -a "$RUN_LOG"
        continue
      fi
      ALL_MODELS_EXHAUSTED=1
      break
    fi
    [[ $RC -eq 0 || $RC -eq 124 || $attempt -ge $MAX_ATTEMPTS ]] && break
    is_transient_network_failure || break
    echo "[security-nightly] transient network failure (rc=$RC) — attempt $attempt/$MAX_ATTEMPTS, retrying in ${RETRY_PAUSE_SECS}s" | tee -a "$RUN_LOG"
    attempt=$((attempt + 1))
    sleep "$RETRY_PAUSE_SECS"
  done
  set -e
  # A genuine wrapper death AFTER the agent finished must still page.
  trap on_crash ERR

  # Rail 7: Radon is public. The run log holds scanner output, file:line
  # evidence and possibly a matched secret class, so its tail is NEVER posted
  # to the public issue. report() writes a sanitized PHASE STAMP status
  # dead-man comment. The agent writes raw findings to the private
  # mode-0700 run dir and archive.
  local status
  status="$(phase_status "$RC" "$RUN_LOG" "$ROUND_LOG_MARK")"
  # An exhausted ladder is a provider spend stop, which the skill classifies as
  # INCOMPLETE — not failed, and never OK. FAILED is neither OK nor
  # INCOMPLETE*, so it fell to the generic `*)` arm: the one arm that withholds
  # the run log (rail 7, public repo) and states neither of the two things the
  # operator needs from a spend stop — that the audited SHA did not advance,
  # and that the next fire resumes the same private run. Nothing was audited
  # and no state moved, so a refilled quota genuinely does resume it; 75 is
  # this loop's incomplete-and-resumable exit code. DOC-043.
  if (( ALL_MODELS_EXHAUSTED )); then
    status="INCOMPLETE (all model quotas exhausted; top up at claude.ai/settings/usage)"
    RC=75
  fi
  # OK additionally requires the skill's completion marker in THIS round's
  # slice (same scoping as the TRUNCATED detector, R-426). Any incomplete
  # phase — no marker, or ceiling-truncated — must also exit non-zero so
  # neither the dead-man nor launchd is told an unfinished night succeeded.
  if [[ "$status" == "OK" ]] && ! tail -c "+$((ROUND_LOG_MARK + 1))" "$RUN_LOG" 2>/dev/null | grep -qF "$PHASE_COMPLETE_MARKER"; then
    status="INCOMPLETE (exit 0 without the phase-completion marker)"
    RC=75
  elif [[ $RC -eq 0 && "$status" == TRUNCATED* ]]; then
    RC=75
  fi
  case "$status" in
    OK)
      report "$status" "0 public findings to disclose. The phase completed. Verified findings stay private." ;;
    "INCOMPLETE (all model quotas"*)
      report "$status" "every model rung on the ladder reported an exhausted subscription quota — this phase is INCOMPLETE; the audited SHA was NOT advanced and the next fire resumes the same private run once the quota refills" ;;
    INCOMPLETE*)
      report "$status" "the agent exited 0 without declaring the phase complete — this phase is INCOMPLETE; the audited SHA was NOT advanced and the next fire resumes the same private run" ;;
    TRUNCATED*)
      report "$status" "the harness killed unfinished background work and the agent still exited 0 — this phase is INCOMPLETE; the audited SHA was NOT advanced" ;;
    TIMEOUT*)
      report "$status" "the phase hit its wall-clock cap; incomplete, the audited SHA was NOT advanced" ;;
    *)
      report "$status" "the phase ended with a non-zero status; the audited SHA was NOT advanced" ;;
  esac
  echo "[security-nightly] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
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
  echo "[security-nightly] cycle done audit_rc=$RC_AUDIT remediate_rc=$RC_REMEDIATE"
  [[ $RC_AUDIT -ne 0 ]] && exit "$RC_AUDIT"
  exit "$RC_REMEDIATE"
fi

run_phase "$MODE"
exit "$RC"
}
# Call and exit on ONE line: parsed together, so even a returning main can
# never make bash read this file again.
main "$@"; exit
