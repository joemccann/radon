#!/usr/bin/env bash
# Weekend reliability loop runner — invoked by launchd on the always-on
# runner (Mac mini). One job fires daily and runs `cycle`: the audit phase
# then the remediate phase, then the deliver phase (push, PR, CI green,
# operator told what to merge), sequentially, in this loop's own clone.
# Sequencing them inside one clone is what keeps two phases from checking
# out over each other. Each phase still runs standalone (`audit` /
# `remediate` / `deliver`). See .claude/skills/reliability-weekend/.
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
# every entry point (and continuation round) runs `git clean -fdxq`, so a
# missed Saturday audit firing on wake near the Sunday slot would delete
# remediate's uncommitted work mid-write with both runs reporting success.
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
# Before --lock-lib-only and before the venv PATH prepend. lock-lib-only
# fetch always calls net_bounded under set -u.
TIMEOUT_BIN="$(command -v timeout || true)"
net_bounded() { "$TIMEOUT_BIN" "$NET_TIMEOUT_SECS" "$@"; }

# A VPN flap that establishes TCP and then stalls hangs an ssh transport with
# no keepalive, and the attempt-count retry below bounds attempts, not time.
GIT_SSH_BOUNDED="ssh -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

# The 00:00 fetch is the cycle's single point of failure: 2026-08-23 one run
# died here on a port-22 blackhole (NordVPN) before the agent started.
# Bounded so a genuinely dead uplink still surfaces within minutes.
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

MODE="${1:?usage: reliability_weekend.sh audit|remediate|deliver|cycle}"
[[ "$MODE" == "audit" || "$MODE" == "remediate" || "$MODE" == "deliver" || "$MODE" == "cycle" ]] || {
  echo "unknown mode: $MODE" >&2; exit 2;
}

REPO="${RADON_WEEKEND_REPO:-$HOME/radon-weekend/radon}"
WEEKEND_ROOT="$(dirname "$REPO")"
# Per-loop venv. The legacy $WEEKEND_ROOT/venv is not deleted here
# (operator follow-up after this ships).
VENV="$WEEKEND_ROOT/venv-reliability"
# Snapshot gh and Pushover BEFORE the venv prepend / agent. PATH is
# $VENV/bin first after this. WEEKEND_ROOT/.env is shared across loops
# and writable by a skip-permissions agent.
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
GH_BIN="$(command -v gh || true)"
NOTIFY_PUSHOVER_USER="$(_notify_cred PUSHOVER_USER || true)"
NOTIFY_PUSHOVER_TOKEN="$(_notify_cred PUSHOVER_TOKEN || true)"
# Activate the venv so any python3.13 calls inside the agent use it.
[[ -f "$VENV/bin/activate" ]] && export PATH="$VENV/bin:$PATH"
DEADMAN_TITLE="Nightly reliability runner"
DEADMAN_LABEL="reliability-nightly"
ISSUE_SANITIZE=0
LOOP_SLUG="reliability"
DEADMAN_CREATE_BODY="Rolling dead-man for the nightly ${LOOP_SLUG} loop. A missing daily comment means the runner did not fire."
# Branch prefix the skill opens/updates its PR from. Matched on the head
# ref, not the title: the title is now `Reliability <date>`, which a
# hand-written PR could also start with.
PR_BRANCH_PREFIX="reliability/"

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

_notify_curl() {
  local loop="$1" phase="$2" status="$3" pr_url="$4" detail="$5"
  local user token title message
  user="$NOTIFY_PUSHOVER_USER"
  token="$NOTIFY_PUSHOVER_TOKEN"
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
  # Pushover (interim continuation rounds stay issue-only).
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

# Deliver phase (2026-09-02): the skill's last line is a verdict the wrapper
# turns into the cycle's final notification — "N PR(s) green, ready to merge:
# <urls>" or "INCOMPLETE: <check>". Printed by scripts/nightly_deliver.py
# verdict; parsed here in bash (never exec disk python after the agent). An
# exit-0 deliver phase without it is INCOMPLETE, exits 75, and the next fire
# resumes the same branch and PR from the deliver record.
DELIVER_READY_MARKER="NIGHTLY DELIVER READY:"
DELIVER_INCOMPLETE_MARKER="NIGHTLY DELIVER INCOMPLETE:"

deliver_status() {
  # Last verdict line of THIS round's slice of the log (R-426 scoping).
  local line rest tok n="" urls="" check=""
  line="$(tail -c "+$((ROUND_LOG_MARK + 1))" "$RUN_LOG" 2>/dev/null \
    | grep -E "^(${DELIVER_READY_MARKER}|${DELIVER_INCOMPLETE_MARKER})" | tail -n 1 || true)"
  case "$line" in
    "$DELIVER_READY_MARKER"*)
      rest="${line#"$DELIVER_READY_MARKER"}"
      for tok in $rest; do
        case "$tok" in
          prs=*) n="${tok#prs=}" ;;
          http://*|https://*) urls="${urls:+$urls }$tok" ;;
        esac
      done
      if [[ "${n:-0}" == "0" ]]; then
        printf '0 PR(s), nothing to merge'
      else
        printf '%s PR(s) green, ready to merge: %s' "$n" "$urls"
      fi ;;
    "$DELIVER_INCOMPLETE_MARKER"*)
      rest="${line#"$DELIVER_INCOMPLETE_MARKER"}"
      for tok in $rest; do
        case "$tok" in check=*) check="${tok#check=}" ;; esac
      done
      printf 'INCOMPLETE: %s' "${check:-unnamed check}" ;;
    *)
      printf 'INCOMPLETE (exit 0 without the deliver verdict line)' ;;
  esac
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
  # REL-199 (R-531): launchd's default ExitTimeOut is ~20s and report()'s gh
  # ladder is up to five 120s-bounded calls — a bootout produced NO page.
  # Release the lock and fire the 10s-bounded Pushover FIRST, log locally,
  # then give GitHub one short-bounded attempt.
  release_runner_lock "${RUNNER_LOCK:-}"
  notify_phase "KILLED (SIG${sig})" || true
  echo "[weekend] KILLED (SIG${sig}) $(date -u +%FT%TZ)" >> "${RUN_LOG:-/dev/null}" 2>/dev/null || true
  NET_TIMEOUT_SECS=10 report "KILLED (SIG${sig})" "the wrapper was signalled before the phase finished — launchd ExitTimeOut, a bootout, an operator kill or a reboot; partial work may exist on the weekend branch" 0
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

# REL-180 (R-504): .radon-weekend-runner is every loop's clone; this loop's
# OWN marker is what stops a stray RADON_WEEKEND_REPO from running inside a
# sibling loop's clone (taking ITS lock, hard-resetting ITS tree). Installed
# clones predate the marker: the canonical path is admitted once and stamps
# itself, so a merge cannot silence a loop until setup is re-run.
LOOP_MARKER=".radon-reliability-runner"
if [[ ! -f "$LOOP_MARKER" ]]; then
  if [[ "$(pwd -P)" == "$(cd "$HOME/radon-weekend/radon" 2>/dev/null && pwd -P)" ]]; then
    touch "$LOOP_MARKER"
  else
    echo "REFUSING: $REPO is not the dedicated reliability runner clone ($LOOP_MARKER absent)" >&2
    report "REFUSED" "$REPO lacks the $LOOP_MARKER marker; the reliability loop runs only in ~/radon-weekend/radon" || true
    exit 2
  fi
fi

# Subscription only. Claude Code prefers an API key / Bedrock / Vertex /
# Foundry / gateway reroute over the claude.ai login whenever one is visible.
# Operator rule 2026-09-01: a reroute variable the launch shell carries is
# IGNORED, not fatal. Name it on stderr (never the value), unset it, run on
# the subscription. Refuse only what `unset` cannot reach: a key file a
# scanner reloads itself, or a Claude Code settings file carrying an
# apiKeyHelper / env reroute. The lists are what `strings` on the installed
# CLI (2.1.258) actually honors; a hand copy of six of them was the old hole.
BILLING_REROUTE_KEYS="ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL CLAUDE_CODE_API_KEY CLAUDE_API_KEY CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR AWS_BEARER_TOKEN_BEDROCK ANTHROPIC_AWS_API_KEY ANTHROPIC_AWS_BASE_URL ANTHROPIC_BEDROCK_BASE_URL ANTHROPIC_BEDROCK_MANTLE_BASE_URL ANTHROPIC_VERTEX_BASE_URL ANTHROPIC_GOOGLE_CLOUD_BASE_URL ANTHROPIC_FOUNDRY_API_KEY ANTHROPIC_FOUNDRY_AUTH_TOKEN ANTHROPIC_FOUNDRY_BASE_URL ANTHROPIC_FOUNDRY_RESOURCE ANTHROPIC_IDENTITY_TOKEN ANTHROPIC_IDENTITY_TOKEN_FILE"
BILLING_REROUTE_FLAGS="CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY CLAUDE_CODE_USE_GATEWAY CLAUDE_CODE_USE_MANTLE CLAUDE_CODE_USE_ANTHROPIC_AWS CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD"
BILLING_IGNORED=""
for _billing_var in $BILLING_REROUTE_KEYS; do
  [[ -n "${!_billing_var:-}" ]] || continue
  echo "IGNORING: $_billing_var is set in the launch environment; unset for the agent, this loop bills the claude.ai subscription only" >&2
  BILLING_IGNORED="$BILLING_IGNORED $_billing_var"
done
# 0/false/no lock the reroute OFF: that is subscription-only and not worth an
# operator line. Only a truthy value (1/true/yes) actually reroutes billing.
for _billing_var in $BILLING_REROUTE_FLAGS; do
  case "${!_billing_var:-}" in
    1|[Tt][Rr][Uu][Ee]|[Yy][Ee][Ss])
      echo "IGNORING: $_billing_var is set in the launch environment; unset for the agent, this loop bills the claude.ai subscription only" >&2
      BILLING_IGNORED="$BILLING_IGNORED $_billing_var"
      ;;
  esac
done
# shellcheck disable=SC2086
unset $BILLING_REROUTE_KEYS $BILLING_REROUTE_FLAGS _billing_var
BILLING_IGNORED="${BILLING_IGNORED# }"

# `unset` cannot reach a key the scanner loads for itself: deepsec reads
# .deepsec/.env*.local out of its own workspace, which is gitignored and
# survives every nightly `git clean`. Refuse rather than edit operator state.
BILLING_REROUTE_KEY_ASSIGN="^[[:space:]]*(export[[:space:]]+)?(${BILLING_REROUTE_KEYS// /|})[[:space:]]*=[[:space:]]*[^[:space:]#]"
BILLING_REROUTE_FLAG_ASSIGN="^[[:space:]]*(export[[:space:]]+)?(${BILLING_REROUTE_FLAGS// /|})[[:space:]]*=[[:space:]]*[\"']?(1|true|yes)[\"']?([#[:space:]]|\$)"
# A settings-level apiKeyHelper or env reroute reaches the agent past any
# unset. Refuse and name the file; the operator edits it, not the loop.
BILLING_REROUTE_SETTINGS_KEY="\"(${BILLING_REROUTE_KEYS// /|})\"[[:space:]]*:[[:space:]]*\"[^\"]"
BILLING_REROUTE_SETTINGS_FLAG="\"(${BILLING_REROUTE_FLAGS// /|})\"[[:space:]]*:[[:space:]]*\"?(1|true|yes)\"?"
# Checked in the prologue and AGAIN at the start of every phase and
# continuation round, after the reset and before `claude` launches: the
# in-phase agent can write any of these files, and .deepsec/, web/.env and
# the settings files all survive `git clean`, so a one-time prologue check
# let the next phase inherit what the previous phase planted.
refuse_billing_reroute_files() {
  local key_file settings_file
  for key_file in .deepsec/.env .deepsec/.env.local .deepsec/.env.*.local .env.local; do
    [[ -f "$key_file" ]] || continue
    if grep -qE "$BILLING_REROUTE_KEY_ASSIGN" "$key_file" || grep -qiE "$BILLING_REROUTE_FLAG_ASSIGN" "$key_file"; then
      echo "REFUSING: $key_file holds billing-reroute credentials; this loop bills the claude.ai subscription only, remove the key line" >&2
      report "REFUSED" "$key_file holds billing-reroute credentials; the agent would bill metered API usage instead of the claude.ai subscription, remove the key line" || true
      exit 2
    fi
  done

  # web/.env is provisioned into the Radon-credential clones for the Next dev
  # server and pytest's load_dotenv, and the product copy carries
  # ANTHROPIC_API_KEY. Neither the agent nor any child may use it: scrub the
  # reroute lines in place (same inode, mode kept) so the subscription is the
  # only model route in this clone. The security clone refuses the file above.
  if [[ -f web/.env ]] && { grep -qE "$BILLING_REROUTE_KEY_ASSIGN" web/.env || grep -qiE "$BILLING_REROUTE_FLAG_ASSIGN" web/.env; }; then
    echo "IGNORING: web/.env holds billing-reroute credentials; removed from the clone copy, this loop bills the claude.ai subscription only" >&2
    { grep -vE "$BILLING_REROUTE_KEY_ASSIGN" web/.env | grep -viE "$BILLING_REROUTE_FLAG_ASSIGN" || true; } > web/.env.scrub
    cat web/.env.scrub > web/.env
    rm -f web/.env.scrub
    [[ " $BILLING_IGNORED " == *" web/.env "* ]] || BILLING_IGNORED="${BILLING_IGNORED:+$BILLING_IGNORED }web/.env"
  fi

  for settings_file in "$HOME/.claude/settings.json" .claude/settings.json .claude/settings.local.json "/Library/Application Support/ClaudeCode/managed-settings.json" /etc/claude-code/managed-settings.json; do
    [[ -f "$settings_file" ]] || continue
    if grep -qE '"apiKeyHelper"[[:space:]]*:[[:space:]]*"[^"]' "$settings_file" \
       || grep -qE "$BILLING_REROUTE_SETTINGS_KEY" "$settings_file" \
       || grep -qiE "$BILLING_REROUTE_SETTINGS_FLAG" "$settings_file"; then
      echo "REFUSING: $settings_file holds an apiKeyHelper or billing-reroute env entry; this loop bills the claude.ai subscription only, remove it" >&2
      report "REFUSED" "$settings_file holds an apiKeyHelper or billing-reroute env entry; the agent would bill metered API usage instead of the claude.ai subscription, remove it" || true
      exit 2
    fi
  done
}
refuse_billing_reroute_files

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
  # Deliver pushes, opens the PR and waits on CI (cap 3h).
  case "$PHASE" in
    audit) CAP_SECS="${RADON_WEEKEND_AUDIT_CAP_SECS:-7200}" ;;
    deliver) CAP_SECS="${RADON_WEEKEND_DELIVER_CAP_SECS:-10800}" ;;
    *) CAP_SECS="${RADON_WEEKEND_REMEDIATE_CAP_SECS:-21600}" ;;
  esac
  MAX_ROUNDS=$([[ "$PHASE" == "remediate" ]] && echo 8 || echo 1)
  # One log per phase: the transient-network detector reads $RUN_LOG.
  # Issue comments do not include a run-log tail.
  RUN_LOG="$LOG_DIR/$PHASE-$STAMP.log"
  RC=0
  # REL-198 (R-533): rung carry across phases is intended; flag carry is
  # not — an audit-phase exhaustion mislabelled a successful remediate.
  ALL_MODELS_EXHAUSTED=0
}

# Fresh ground truth. Any leftover state from a killed prior run is
# discarded — the branch/PR on GitHub is the durable state.
# Re-ground for a continuation round: a killed session may leave a dirty tree.
# main is force-reset; the local weekend branch and its commits survive. Bare,
# this ran with the ERR trap disarmed and `set -e` on, so the cap SIGTERMing
# claude mid-commit left `.git/index.lock`, the next `git checkout -f` failed,
# errexit terminated main, and the whole daily run ended with no comment and no
# page while the backlog was unfinished. R-385.
reground_for_continuation() {
  rm -f .git/index.lock
  git checkout -f --quiet main \
    && git reset --hard --quiet origin/main \
    && git clean -fdxq --exclude=.radon-weekend-runner --exclude=.radon-reliability-runner --exclude=.weekend-runner.lock --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env --exclude=node_modules/ --exclude=.next/ --exclude=.deepsec/
}

ground_truth() {
  rm -f .git/index.lock
  fetch_origin_with_retry
  git checkout -f --quiet main
  git reset --hard --quiet origin/main
  git clean -fdxq --exclude=.radon-weekend-runner --exclude=.radon-reliability-runner --exclude=.weekend-runner.lock --exclude=logs/ --exclude=.env --exclude=.env.ib-mode --exclude=web/.env --exclude=node_modules/ --exclude=.next/ --exclude=.deepsec/
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
# Credits, per-model rate limit, and provider capacity are per MODEL, so an
# exhausted one is a reason to drop a rung, not to lose the night. 2026-09-01:
# `~/.claude/settings.json` carried `model: claude-fable-5[1m]`, these wrappers
# passed no --model and inherited it, that model's quota was gone, and
# `claude -p` printed one line and exited 1. Pinning the model also takes the
# operator's global default off the unattended path. Session/weekly caps are
# shared across models and must not match here.
MODEL_LADDER="${RADON_WEEKEND_MODEL_LADDER:-claude-fable-5[1m] claude-opus-5[1m] claude-opus-5 claude-sonnet-5}"
read -r -a MODEL_RUNGS <<< "$MODEL_LADDER"
MODEL_INDEX=0
ALL_MODELS_EXHAUSTED=0

# Scoped to THIS round's slice of the log, like the ceiling detector: RUN_LOG
# is per phase and every round appends to it, so a whole-file grep would let
# round 1's exhausted rung drop a model on every later round forever. R-426.
is_quota_exhausted() {
  # REL-198 (R-530): the detector reads the agent's own transcript, so a
  # crashing round that QUOTES a pattern mid-run must not read as quota
  # exhaustion. The CLI prints its refusal in its FINAL lines: scan only the
  # last 40 lines of the round slice, and never the wrapper's own markers.
  tail -c "+$((ROUND_LOG_MARK + 1))" "$RUN_LOG" 2>/dev/null \
    | grep -v '^\[' | tail -n 40 | grep -qiE \
    'out of usage credits|You.ve hit your (Opus|Sonnet) limit|Request rejected \(429\)|529 Overloaded|experiencing high load'
}

is_transient_network_failure() {
  tail -c 500 "$RUN_LOG" | grep -qE 'API Error|ENOTFOUND|Connection lost|Execution error'
}

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

run_round() {
  local attempt=1 start_ts=$SECONDS remain
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
    CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 "$TIMEOUT_BIN" -k "$KILL_AFTER_SECS" "$remain" claude -p "/reliability-weekend $PHASE" \
      --model "${MODEL_RUNGS[$MODEL_INDEX]}" \
      --dangerously-skip-permissions \
      --output-format text >> "$RUN_LOG" 2>&1 &
    ROUND_PID=$!
    local round_start=$SECONDS
    wait "$ROUND_PID"
    RC=$?
    # REL-198 (R-532): reap the round's process group BEFORE clearing the
    # pid — the guard early-returns on an empty ROUND_PID, so the old order
    # made orphan reaping after a normal exit dead code.
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
        echo "[weekend] ${MODEL_RUNGS[$((MODEL_INDEX - 1))]} is exhausted; dropping to ${MODEL_RUNGS[$MODEL_INDEX]}" | tee -a "$RUN_LOG"
        continue
      fi
      ALL_MODELS_EXHAUSTED=1
      break
    fi
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
    report "SKIPPED (cycle budget)" "the cycle had no room left for a ${CAP_SECS}s $PHASE phase; the next daily fire picks it up" || true
    return 0
  fi
  echo "[weekend] $PHASE start $STAMP repo=$REPO cap=${CAP_SECS}s${BILLING_IGNORED:+ ignored=${BILLING_IGNORED// /,}}" | tee -a "$RUN_LOG"
  # NOT bare. Under `set -Eeuo pipefail` with the ERR trap armed, a failed
  # fetch made on_crash report and then the shell exit anyway — so
  # `run_phase audit` never returned and `run_phase remediate` was never run
  # and never reported, contradicting the comment on the cycle branch. The
  # phase now records the failure and carries on to its own reporting. R-237.
  if ! ground_truth; then
    RC=70
    report "GROUND TRUTH FAILED" "could not refresh the clone (network or git); the phase did not run" || true
    echo "[weekend] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
    return 0
  fi
  # Rail 5b again: the previous phase's agent may have planted a key file
  # or a settings reroute the reset does not remove; refuse before this
  # phase's `claude` launches.
  refuse_billing_reroute_files

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
    [[ $RC -ne 0 && $round -lt $MAX_ROUNDS && $ALL_MODELS_EXHAUSTED -eq 0 ]] || break
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
    if ! reground_for_continuation; then
      RC=70
      report "GROUND TRUTH FAILED (round $round)" "could not re-ground the clone for a continuation round; the backlog is UNFINISHED and partial work is on the weekend branch" || true
      break
    fi
    refuse_billing_reroute_files
  done
  # A genuine wrapper death AFTER the rounds finished must still page.
  trap on_crash ERR

  local status
  status="$(phase_status "$RC" "$RUN_LOG" "$ROUND_LOG_MARK")"
  # An exhausted ladder is not a generic non-zero exit: the operator needs the
  # cause and the one place it is fixed, or they re-fire into the same wall.
  if (( ALL_MODELS_EXHAUSTED )); then
    status="FAILED (all model quotas exhausted; top up at claude.ai/settings/usage)"
  fi
  # Deliver: the operator's merge cue is the verdict line, not exit 0. A cap
  # hit is the likeliest incomplete deliver, so it is named as one.
  if [[ "$PHASE" == "deliver" ]]; then
    if [[ "$status" == "OK" ]]; then
      status="$(deliver_status)"
      [[ "$status" == INCOMPLETE* ]] && RC=75
    elif [[ "$status" == TIMEOUT* ]]; then
      status="INCOMPLETE: deliver cap hit before CI went green ($status)"
    fi
  fi
  case "$status" in
    *"ready to merge: "*|"0 PR(s), nothing to merge")
      report "$status" "CI is green on every PR this cycle delivered; merging is the operator's call" ;;
    "INCOMPLETE: "*)
      report "$status" "CI could not be made green inside the deliver cap — this phase is INCOMPLETE; the branch and PR number are in the deliver record and the next fire resumes them" ;;
    "INCOMPLETE (exit 0 without the deliver verdict line)")
      report "$status" "the agent exited 0 without declaring the deliver verdict — this phase is INCOMPLETE; the next fire resumes the same branch and PR" ;;
    OK)
      report "$status" "The audit or remediate phase completed." ;;
    TRUNCATED*)
      report "$status" "the harness killed unfinished background work and the agent still exited 0 — this phase's output is INCOMPLETE and must not be read as a finished run" ;;
    TIMEOUT*)
      report "$status" "partial work may exist on the weekend branch/PR" ;;
    *)
      report "$status" "" ;;
  esac
  echo "[weekend] $PHASE done rc=$RC" | tee -a "$RUN_LOG"
}

if [[ "$MODE" == "cycle" ]]; then
  # Remediate runs regardless of the audit rc — backlog may exist even when
  # the audit phase failed. Deliver runs regardless of the remediate rc —
  # committed fixes on the dated branch are durable and CI, not the remediate
  # exit code, decides whether they are mergeable. Exit non-zero if any
  # phase failed; the deliver report is the cycle's final notification.
  set +e
  run_phase audit
  RC_AUDIT=$RC
  run_phase remediate
  RC_REMEDIATE=$RC
  run_phase deliver
  RC_DELIVER=$RC
  set -e
  echo "[weekend] cycle done audit_rc=$RC_AUDIT remediate_rc=$RC_REMEDIATE deliver_rc=$RC_DELIVER"
  [[ $RC_AUDIT -ne 0 ]] && exit "$RC_AUDIT"
  [[ $RC_REMEDIATE -ne 0 ]] && exit "$RC_REMEDIATE"
  exit "$RC_DELIVER"
fi

run_phase "$MODE"
exit "$RC"
}
# Call and exit on ONE line: parsed together, so even a returning main can
# never make bash read this file again.
main "$@"; exit
