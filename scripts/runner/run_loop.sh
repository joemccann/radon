#!/bin/bash
# One nightly loop, as if the operator opened an agent CLI in a fresh clone of
# main and pasted the loop's prompt: clone, run one agent session, notify.
#
#   run_loop.sh <loop>          loops/<loop>.env next to this script configures it
#
# Safety is the boundary this runs inside (a dedicated unprivileged user, no
# production credentials, a GitHub token that cannot merge to main), not this
# script. See docs/runner.md.
set -uo pipefail

SELF="${BASH_SOURCE[0]}"
RUNNER_DIR="$(cd "$(dirname "$SELF")" && pwd)"
STATE_DIR="${RADON_RUNNER_STATE:-$HOME/radon-runner}"
SECRETS_FILE="${RADON_RUNNER_ENV:-$HOME/.radon-runner.env}"
SECRET_KEYS="GH_TOKEN PUSHOVER_USER PUSHOVER_TOKEN"
TIMED_OUT=124

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

load_config() {
  REPO_URL="https://github.com/joemccann/radon.git"
  AGENTS="claude"
  TIMEOUT_SECS=10800
  KILL_AFTER_SECS=60
  BRANCH_PREFIX="$LOOP"
  PROMPT=".claude/runner-prompts/$LOOP.md"
  LOG_DAYS=14
  local config="$RUNNER_DIR/loops/$LOOP.env"
  [[ -f "$config" ]] || { echo "no config $config" >&2; exit 64; }
  # shellcheck disable=SC1090
  source "$config"
  DATE="$(date +%F)"
  BRANCH="$BRANCH_PREFIX/$DATE"
  WORK="$STATE_DIR/work/$LOOP"
  RUN_DIR="$STATE_DIR/logs/$LOOP"
  LOG="$RUN_DIR/$DATE.log"
  PROMPT_FILE="$RUN_DIR/$DATE.prompt.md"
  TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"
  mkdir -p "$RUN_DIR"
  [[ -n "$TIMEOUT_BIN" ]] || { log "no timeout or gtimeout on PATH"; exit 69; }
}

load_secrets() {
  local key value
  [[ -f "$SECRETS_FILE" ]] || return 0
  for key in $SECRET_KEYS; do
    value="$(sed -n "s/^$key=//p" "$SECRETS_FILE" | tail -n 1 | tr -d "\"'")"
    [[ -n "$value" ]] && export "$key=$value"
  done
}

acquire_lock() {
  LOCK="$STATE_DIR/$LOOP.lock"
  local holder
  if ! mkdir "$LOCK" 2>/dev/null; then
    holder="$(cat "$LOCK/pid" 2>/dev/null)"
    if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
      log "skipped: pid $holder is still running this loop"
      exit 0
    fi
    rm -rf "$LOCK"
    mkdir "$LOCK" || exit 75
  fi
  echo $$ > "$LOCK/pid"
  trap 'rm -rf "$LOCK"' EXIT
}

prune_logs() {
  find "$RUN_DIR" -type f -mtime +"$LOG_DAYS" -delete 2>/dev/null || true
}

fresh_clone() {
  local attempt
  rm -rf "$WORK"
  mkdir -p "$(dirname "$WORK")"
  for attempt in 1 2 3; do
    git clone -q "$REPO_URL" "$WORK" >> "$LOG" 2>&1 && return 0
    rm -rf "$WORK"
    [[ "$attempt" == 3 ]] || sleep "${RADON_RUNNER_RETRY_PAUSE:-30}"
  done
  return 1
}

build_prompt() {
  {
    echo "Date: $DATE"
    echo "Branch: $BRANCH (create it from origin/main; it is the only branch you may push)"
    echo "Time budget: $((TIMEOUT_SECS / 60)) minutes, then this session is stopped."
    echo
    cat "$WORK/$PROMPT"
  } > "$PROMPT_FILE"
}

# Runs in the child, inside timeout's process group, with cwd = the clone.
launch_agent() {
  local agent="$1" provider="${2:-}"
  unset PUSHOVER_USER PUSHOVER_TOKEN
  case "$agent" in
    claude) exec claude -p "$(cat "$PROMPT_FILE")" --dangerously-skip-permissions --output-format text \
              --disallowedTools ScheduleWakeup Monitor CronCreate < /dev/null ;;
    codex)  exec codex exec --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=medium \
              --color never - < "$PROMPT_FILE" ;;
    grok)   exec grok --prompt-file "$PROMPT_FILE" --reasoning-effort medium --cwd "$PWD" \
              --always-approve --output-format plain < /dev/null ;;
    agy)    exec agy -p="$(cat "$PROMPT_FILE")" --effort medium --dangerously-skip-permissions \
              --output-format text < /dev/null ;;
    fx)     FX_PROVIDER="$provider" FX_AUTO_UPGRADE=0 FX_SKIP_ONBOARDING=1 FX_DISABLE_KEYCHAIN=1 \
              exec fx ask --full-access --no-save < "$PROMPT_FILE" ;;
    *)      echo "unknown agent $agent" >&2; exit 64 ;;
  esac
}

# GNU timeout leads its own process group, so one group kill afterwards also
# reaps anything the agent left running (dev servers, watchers).
run_agent() {
  local agent="${1%%:*}" provider="" pid
  [[ "$1" == *:* ]] && provider="${1#*:}"
  ( cd "$WORK" && export PROMPT_FILE && exec "$TIMEOUT_BIN" -k "$KILL_AFTER_SECS" "$TIMEOUT_SECS" \
      /bin/bash "$SELF" --launch "$agent" "$provider" ) >> "$LOG" 2>&1 &
  pid=$!
  wait "$pid"
  AGENT_RC=$?
  kill -KILL -- "-$pid" 2>/dev/null || true
  return "$AGENT_RC"
}

find_pr() {
  (cd "$WORK" && gh pr list --head "$BRANCH" --state all --json url --jq '.[0].url // empty' 2>/dev/null)
}

notify() {
  local title="$1" message="$2" url="${3:-}"
  log "$title: $message ${url}"
  [[ -n "${PUSHOVER_USER:-}" && -n "${PUSHOVER_TOKEN:-}" ]] || return 0
  curl -fsS -m 20 --form-string "token=$PUSHOVER_TOKEN" --form-string "user=$PUSHOVER_USER" \
    --form-string "title=$title" --form-string "message=$message" \
    ${url:+--form-string "url=$url"} https://api.pushover.net/1/messages.json >/dev/null 2>&1 || true
}

run_agents_in_order() {
  local agent
  USED_AGENT=""
  FAILURES=""
  for agent in $AGENTS; do
    log "starting $agent"
    if run_agent "$agent"; then USED_AGENT="$agent"; return 0; fi
    FAILURES="$FAILURES $agent=$AGENT_RC"
    log "$agent exited $AGENT_RC"
    [[ "$AGENT_RC" == "$TIMED_OUT" ]] && return 1
  done
  return 1
}

report() {
  local pr result
  pr="$(find_pr)"
  result="$(grep -ao 'RESULT:.*' "$LOG" | tail -n 1)"
  if [[ -n "$USED_AGENT" ]]; then
    notify "radon $LOOP: done via $USED_AGENT" "${result:-no RESULT line}" "$pr"
    return 0
  fi
  notify "radon $LOOP: FAILED" "agents:${FAILURES}${result:+ | $result}" "$pr"
  return 1
}

main() {
  LOOP="${1:?usage: run_loop.sh <loop>}"
  load_config
  load_secrets
  acquire_lock
  prune_logs
  if ! fresh_clone; then
    notify "radon $LOOP: FAILED" "could not clone $REPO_URL"
    return 70
  fi
  build_prompt
  run_agents_in_order
  report
}

if [[ "${1:-}" == "--launch" ]]; then
  launch_agent "${2:?}" "${3:-}"
fi
main "$@"
