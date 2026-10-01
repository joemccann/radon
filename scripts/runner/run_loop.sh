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
# Resolvers and hooks run under an isolated interpreter (no cwd, no user site).
RUNNER_PYTHON="${RADON_RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
TIMED_OUT=124
HOOK_SECS="${RADON_RUNNER_HOOK_SECS:-900}"
AGENT_PID=""
HOOK_PID=""
PHASE=""
# The runner, its hooks and helpers resolve binaries only from root- or
# admin-owned directories; the bot's own CLI dirs go on the agent's PATH alone.
RUNNER_PATH="${RADON_RUNNER_PATH:-/opt/homebrew/bin:/usr/bin:/bin}"
CURL_BIN="${RADON_RUNNER_CURL:-/usr/bin/curl}"
AGENT_PATH_PREFIX="$HOME/.local/bin:$HOME/.grok/bin:$HOME/.bun/bin"
# Every git the runner, its hooks and the agent run reads the root-owned
# config install.sh writes, never the bot-writable ~/.gitconfig.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="${RADON_RUNNER_GITCONFIG:-$RUNNER_DIR/gitconfig}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"; }

load_config() {
  REPO_URL="https://github.com/joemccann/radon.git"
  AGENTS="claude"
  TIMEOUT_SECS=10800
  KILL_AFTER_SECS=60
  BRANCH_PREFIX="$LOOP"
  PROMPT=".claude/runner-prompts/$LOOP.md"
  LOG_DAYS=14
  # Optional knobs (docs/runner.md "Loop config"); empty means off.
  AGENT_ENV=""
  AGENT_UNSET=""
  ALLOWED_AGENTS=""
  AGENTS_RESOLVER=""
  KEEP_PATHS=""
  PHASES=""
  GH_GUARD=0
  PRE_RUN=""
  POST_RUN=""
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
  # Survives every re-clone; the runner never deletes it.
  LOOP_STATE="$STATE_DIR/state/$LOOP"
  TIMEOUT_BIN="$(PATH="$RUNNER_PATH" command -v gtimeout || PATH="$RUNNER_PATH" command -v timeout || true)"
  mkdir -p "$RUN_DIR" "$LOOP_STATE"
  chmod 700 "$STATE_DIR/state" "$LOOP_STATE"
  [[ -n "$TIMEOUT_BIN" ]] || { log "no timeout or gtimeout in $RUNNER_PATH"; exit 69; }
  export RADON_RUNNER_LOOP_STATE="$LOOP_STATE" RADON_RUNNER_PIDFILE="$LOOP_STATE/pids"
  apply_agent_env
}

# Applied here so the resolver, the hooks and the agent all see the same
# environment. A value is never logged, only the name.
apply_agent_env() {
  local name pair
  for name in $AGENT_UNSET; do
    if [[ -n "${!name:-}" ]]; then log "IGNORING: $name"; fi
    unset "$name"
  done
  for pair in $AGENT_ENV; do
    export "${pair?}"
  done
}

# Literal dotenv assign. No source, no eval, no command substitution on the
# file. Last occurrence wins; an empty value counts as missing.
_dotenv_set() {
  case "$1" in
    PUSHOVER_USER) _DOTENV_PUSHOVER_USER="$2" ;;
    PUSHOVER_TOKEN) _DOTENV_PUSHOVER_TOKEN="$2" ;;
    GH_TOKEN) _DOTENV_GH_TOKEN="$2" ;;
  esac
}

_dotenv_parse_value() {
  local val="$1" first rest
  val="${val#"${val%%[![:space:]]*}"}"
  first="${val:0:1}"
  if [[ "$first" == '"' || "$first" == "'" ]]; then
    rest="${val:1}"
    case "$rest" in
      *"$first"*)
        _DOTENV_VAL="${rest%%"$first"*}"
        return 0
        ;;
    esac
  fi
  local s="$val" i=0 n=${#val} c j
  while [[ $i -lt $n ]]; do
    c="${s:$i:1}"
    if [[ "$c" == [[:space:]] ]]; then
      j=$((i + 1))
      while [[ $j -lt $n && "${s:$j:1}" == [[:space:]] ]]; do
        j=$((j + 1))
      done
      if [[ $j -lt $n && "${s:$j:1}" == "#" ]]; then
        s="${s:0:$i}"
        break
      fi
    fi
    i=$((i + 1))
  done
  _DOTENV_VAL="${s%"${s##*[![:space:]]}"}"
}

_dotenv_read_file() {
  local file="$1" allow="$2" line trimmed key
  _DOTENV_PUSHOVER_USER=""
  _DOTENV_PUSHOVER_TOKEN=""
  _DOTENV_GH_TOKEN=""
  _DOTENV_VAL=""
  [[ -f "$file" && -r "$file" ]] || return 0
  while IFS= read -r line || [[ -n "${line:-}" ]]; do
    line="${line%$'\r'}"
    trimmed="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$trimmed" || "${trimmed:0:1}" == "#" ]] && continue
    case "$trimmed" in
      export[\ $'\t']*)
        trimmed="${trimmed#export}"
        trimmed="${trimmed#"${trimmed%%[![:space:]]*}"}"
        ;;
    esac
    [[ "$trimmed" == *=* ]] || continue
    key="${trimmed%%=*}"
    key="${key%"${key##*[![:space:]]}"}"
    case " $allow " in
      *" $key "*) ;;
      *) continue ;;
    esac
    _dotenv_parse_value "${trimmed#*=}"
    _dotenv_set "$key" "${_DOTENV_VAL}"
  done < "$file"
}

_radon_dotenv_path() {
  local path="" line
  if [[ -n "${RADON_RUNNER_DOTENV:-}" ]]; then
    path="$RADON_RUNNER_DOTENV"
  elif [[ -f "$RUNNER_DIR/dotenv-path" && -r "$RUNNER_DIR/dotenv-path" ]]; then
    IFS= read -r line < "$RUNNER_DIR/dotenv-path" || true
    line="${line%$'\r'}"
    path="$line"
  fi
  case "$path" in
    /*) printf '%s' "$path" ;;
  esac
}

# Pair from the first source that has BOTH keys (env, Radon dotenv, bot file).
# GH_TOKEN loads only from the bot file. Values are never logged.
load_secrets() {
  local dotenv_path from="" checked
  dotenv_path="$(_radon_dotenv_path)"

  _dotenv_read_file "$SECRETS_FILE" "GH_TOKEN"
  if [[ -n "${_DOTENV_GH_TOKEN}" ]]; then
    export GH_TOKEN="$_DOTENV_GH_TOKEN"
  fi

  if [[ -n "${PUSHOVER_USER:-}" && -n "${PUSHOVER_TOKEN:-}" ]]; then
    from="env"
  else
    if [[ -n "${PUSHOVER_USER:-}" || -n "${PUSHOVER_TOKEN:-}" ]]; then
      log "pushover: incomplete env"
      unset PUSHOVER_USER PUSHOVER_TOKEN
    fi
    if [[ -n "$dotenv_path" ]]; then
      _dotenv_read_file "$dotenv_path" "PUSHOVER_USER PUSHOVER_TOKEN"
      if [[ -n "${_DOTENV_PUSHOVER_USER}" && -n "${_DOTENV_PUSHOVER_TOKEN}" ]]; then
        export PUSHOVER_USER="$_DOTENV_PUSHOVER_USER"
        export PUSHOVER_TOKEN="$_DOTENV_PUSHOVER_TOKEN"
        from="dotenv $dotenv_path"
      elif [[ -n "${_DOTENV_PUSHOVER_USER}" || -n "${_DOTENV_PUSHOVER_TOKEN}" ]]; then
        log "pushover: incomplete dotenv $dotenv_path"
      fi
    fi
    if [[ -z "$from" ]]; then
      _dotenv_read_file "$SECRETS_FILE" "PUSHOVER_USER PUSHOVER_TOKEN"
      if [[ -n "${_DOTENV_PUSHOVER_USER}" && -n "${_DOTENV_PUSHOVER_TOKEN}" ]]; then
        export PUSHOVER_USER="$_DOTENV_PUSHOVER_USER"
        export PUSHOVER_TOKEN="$_DOTENV_PUSHOVER_TOKEN"
        from="$SECRETS_FILE"
      elif [[ -n "${_DOTENV_PUSHOVER_USER}" || -n "${_DOTENV_PUSHOVER_TOKEN}" ]]; then
        log "pushover: incomplete $SECRETS_FILE"
      fi
    fi
  fi

  if [[ -n "$from" ]]; then
    log "pushover: PUSHOVER_USER present, PUSHOVER_TOKEN present (source: $from)"
  else
    checked="env"
    [[ -n "$dotenv_path" ]] && checked="$checked, $dotenv_path"
    checked="$checked, $SECRETS_FILE"
    log "pushover: credentials missing (checked: $checked); notifications skipped"
  fi
}

proc_start() { ps -o lstart= -p "$1" 2>/dev/null | tr -s ' ' | sed 's/^ //;s/ $//'; }

# REL-291 / R-710: missing metadata or an unreadable process fingerprint is
# uncertain ownership, never permission to delete the active agent's clone.
lock_state() {
  local holder started observed
  holder="$(cat "$LOCK/pid" 2>/dev/null)"
  started="$(cat "$LOCK/start" 2>/dev/null)"
  case "$holder" in ''|*[!0-9]*|0|1) return 2 ;; esac
  kill -0 "$holder" 2>/dev/null || return 1
  [[ -n "$started" ]] || return 2
  observed="$(proc_start "$holder")"
  [[ -n "$observed" ]] || return 2
  [[ "$observed" == "$started" ]] && return 0
  return 1
}

release_lock() {
  # An EXIT from a displaced process must never remove a successor's lock.
  [[ "$(cat "$LOCK/pid" 2>/dev/null)" == "$$" ]] || return 0
  rm -rf "$LOCK"
}

acquire_lock() {
  LOCK="$STATE_DIR/$LOOP.lock"
  local state
  if ! mkdir "$LOCK" 2>/dev/null; then
    lock_state; state=$?
    if [[ "$state" == 0 ]]; then
      log "skipped: the recorded process is still running this loop"
      notify "radon $LOOP: skipped" "the previous run is still running; this fire did not start"
      exit 0
    fi
    if [[ "$state" != 1 ]]; then
      notify "radon $LOOP: REFUSED" "lock ownership is uncertain; preserve the clone and inspect $LOCK before removing an orphaned lock"
      exit 75
    fi
    # Serialize reclaimers INSIDE the existing directory. Recheck after the
    # claim: a contender that observed the old directory may now see a new
    # owner's directory. Incomplete new metadata remains fail-closed.
    mkdir "$LOCK/reaping" 2>/dev/null || exit 75
    lock_state; state=$?
    if [[ "$state" != 1 ]]; then
      rmdir "$LOCK/reaping" 2>/dev/null || true
      exit 75
    fi
    rm -rf "$LOCK"
    mkdir "$LOCK" || exit 75
  fi
  echo $$ > "$LOCK/pid"
  proc_start $$ > "$LOCK/start"
  trap 'release_lock' EXIT
}

prune_logs() {
  find "$RUN_DIR" -type f -mtime +"$LOG_DAYS" -delete 2>/dev/null || true
}

# The resolver's stdout replaces AGENTS; empty or failed output keeps AGENTS
# as the safety ladder.
resolve_agents() {
  local out
  [[ -n "$AGENTS_RESOLVER" ]] || return 0
  # It runs the agent CLI itself (`claude models`), so it gets the agent's
  # PATH and never the Pushover keys.
  out="$(env -u PUSHOVER_USER -u PUSHOVER_TOKEN PATH="$AGENT_PATH_PREFIX:$RUNNER_PATH" \
    "$TIMEOUT_BIN" 120 "$RUNNER_PYTHON" -I "$RUNNER_DIR/$AGENTS_RESOLVER" --rungs 2>>"$LOG")" || out=""
  out="$(printf '%s' "$out" | tr -s '[:space:]' ' ' | sed 's/^ //;s/ $//')"
  if [[ -n "$out" ]]; then
    AGENTS="$out"
    log "resolver: $AGENTS"
  else
    log "resolver gave no rungs; safety ladder $AGENTS"
  fi
}

refuse() {
  notify "radon $LOOP: REFUSED" "$1"
  exit 2
}

refuse_disallowed_agents() {
  local rung
  [[ -n "$ALLOWED_AGENTS" ]] || return 0
  for rung in $AGENTS; do
    case " $ALLOWED_AGENTS " in
      *" ${rung%%:*} "*) ;;
      *) refuse "agent ${rung%%:*} is not allowed for this loop (ALLOWED_AGENTS=$ALLOWED_AGENTS)" ;;
    esac
  done
}

require_guard() {
  [[ "$GH_GUARD" == 1 ]] || return 0
  [[ -x "$RUNNER_DIR/guard/$LOOP/gh" ]] || refuse "GH_GUARD=1 but $RUNNER_DIR/guard/$LOOP/gh is missing; rerun install.sh $LOOP"
}

keep_path_ok() {
  case "$1" in
    ""|/*|..|../*|*/..|*/../*) log "KEEP_PATHS entry '$1' refused: relative paths only"; return 1 ;;
  esac
}

# KEEP_PATHS live in $LOOP_STATE/keep while the clone is replaced. Whatever is
# in keep/ is always restored, so a crash between the two leaves nothing
# behind; a copy already in keep/ (operator seeded, or stranded) wins.
stash_keep_paths() {
  local p
  for p in $KEEP_PATHS; do
    keep_path_ok "$p" || continue
    [[ -e "$WORK/$p" || -L "$WORK/$p" ]] || continue
    if [[ -L "$WORK/$p" ]]; then log "not keeping $p: it is a symlink"; continue; fi
    if [[ -e "$LOOP_STATE/keep/$p" ]]; then log "keeping the copy of $p already in keep/"; continue; fi
    mkdir -p "$(dirname "$LOOP_STATE/keep/$p")"
    mv "$WORK/$p" "$LOOP_STATE/keep/$p" || log "could not keep $p"
  done
}

restore_keep_paths() {
  local p
  for p in $KEEP_PATHS; do
    keep_path_ok "$p" || continue
    [[ -e "$LOOP_STATE/keep/$p" ]] || continue
    rm -rf "${WORK:?}/$p"
    mkdir -p "$(dirname "$WORK/$p")"
    mv "$LOOP_STATE/keep/$p" "$WORK/$p" || log "could not restore $p"
  done
}

fresh_clone() {
  local attempt
  stash_keep_paths
  rm -rf "$WORK"
  mkdir -p "$(dirname "$WORK")"
  for attempt in 1 2 3; do
    if git clone -q "$REPO_URL" "$WORK" >> "$LOG" 2>&1; then
      restore_keep_paths
      return 0
    fi
    rm -rf "$WORK"
    [[ "$attempt" == 3 ]] || sleep "${RADON_RUNNER_RETRY_PAUSE:-30}"
  done
  return 1
}

build_prompt() {
  {
    echo "Date: $DATE"
    if [[ -n "$PHASE" ]]; then echo "Phase: $PHASE"; fi
    if [[ -n "$PRE_RUN" ]]; then
      echo "Branch: $BRANCH (create it from HEAD, the pinned CI-green base, or resume it; it is the only branch you may push)"
    else
      echo "Branch: $BRANCH (create it from origin/main; it is the only branch you may push)"
    fi
    if [[ -n "$PHASE" ]]; then echo "State: $LOOP_STATE"; fi
    echo "Time budget: $((TIMEOUT_SECS / 60)) minutes, then this session is stopped."
    echo
    cat "$WORK/$PROMPT"
  } > "$PROMPT_FILE"
}

# Runs in the child, inside timeout's process group, with cwd = the clone.
launch_agent() {
  local agent="$1" provider="${2:-}"
  unset PUSHOVER_USER PUSHOVER_TOKEN
  # A nested agent call (the security loops' Stage 4 claude) uses the same rung.
  export RADON_RUNNER_AGENT="$agent" RADON_RUNNER_MODEL="$provider"
  case "$agent" in
    claude) CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 exec claude -p "$(cat "$PROMPT_FILE")" \
              ${provider:+--model "$provider"} --effort medium --dangerously-skip-permissions --output-format text \
              --disallowedTools ScheduleWakeup Monitor CronCreate < /dev/null ;;
    codex)  exec codex exec --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=medium \
              --color never - < "$PROMPT_FILE" ;;
    grok)   exec grok --prompt-file "$PROMPT_FILE" --reasoning-effort medium --cwd "$PWD" \
              --always-approve --output-format plain < /dev/null ;;
    agy)    exec agy -p="$(cat "$PROMPT_FILE")" --effort medium --dangerously-skip-permissions \
              --output-format text < /dev/null ;;
    fx)     [[ "$provider" != nvidia ]] || launch_fx_nvidia
            FX_PROVIDER="$provider" FX_AUTO_UPGRADE=0 FX_SKIP_ONBOARDING=1 FX_DISABLE_KEYCHAIN=1 \
              exec fx ask --full-access --no-save < "$PROMPT_FILE" ;;
    *)      echo "unknown agent $agent" >&2; exit 64 ;;
  esac
}

# fx:nvidia. The NVIDIA key is rate limited per key (about 40 requests a
# minute) and shared with production; fx is a closed binary that cannot pace
# itself, so it runs against the loopback proxy in lib/nvidia_rate_limit.py
# (the `nvidia-paced` provider agent_cli_bootstrap.sh writes). The proxy paces
# every loop on this host through one state file, honours Retry-After, and
# trips NVIDIA after persistent 429s or any 401/403. A trip makes this rung
# exit 75 so the ladder moves on instead of hammering the key.
NVIDIA_PROXY_PORT="${RADON_NVIDIA_PROXY_PORT:-18431}"

nvidia_tripped() {
  local until
  until="$(cat "$RADON_NVIDIA_RATE_STATE.tripped" 2>/dev/null)"
  until="${until//[[:space:]]/}"
  [[ "$until" =~ ^[0-9]+$ ]] && (( until > $(date +%s) ))
}

launch_fx_nvidia() {
  local pid
  export RADON_NVIDIA_RATE_STATE="$STATE_DIR/nvidia-rate.json"
  if nvidia_tripped; then
    echo "[runner] NVIDIA is paused (rate limited or refused) until $(cat "$RADON_NVIDIA_RATE_STATE.tripped"); skipping fx:nvidia"
    exit 75
  fi
  mkdir -p "$STATE_DIR/logs"
  # A clean environment: the proxy outlives this session and never needs
  # the agent's GitHub token.
  if ! env -i HOME="$HOME" PATH="$RUNNER_PATH" "$RUNNER_PYTHON" -I "$RUNNER_DIR/lib/nvidia_rate_limit.py" \
      ensure-proxy --port "$NVIDIA_PROXY_PORT" --state "$RADON_NVIDIA_RATE_STATE" \
      --rpm "${RADON_NVIDIA_RPM:-20}" --log "$STATE_DIR/logs/nvidia-proxy.log"; then
    echo "[runner] the NVIDIA pacing proxy did not start; not calling NVIDIA unpaced"
    exit 75
  fi
  FX_PROVIDER=nvidia-paced FX_AUTO_UPGRADE=0 FX_SKIP_ONBOARDING=1 FX_DISABLE_KEYCHAIN=1 \
    fx ask --full-access --no-save < "$PROMPT_FILE" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if nvidia_tripped; then
      echo "[runner] NVIDIA stays rate limited or refused; stopping fx:nvidia so the next rung runs (see $STATE_DIR/logs/nvidia-proxy.log)"
      kill -TERM "$pid" 2>/dev/null
      wait "$pid"
      exit 75
    fi
    sleep "${RADON_NVIDIA_WATCH_SECS:-10}"
  done
  wait "$pid"
  exit $?
}

# pid<TAB>cwd for every process this user owns.
cwd_listing() {
  local d path
  if [[ -e /proc/self/cwd ]]; then
    for d in /proc/[0-9]*; do
      path="$(readlink "$d/cwd" 2>/dev/null)" && printf '%s\t%s\n' "${d#/proc/}" "$path"
    done
    return 0
  fi
  lsof -nP -w -a -u "$(id -un)" -d cwd -Fpn 2>/dev/null \
    | awk '/^p/ { pid = substr($0, 2) } /^n/ { print pid "\t" substr($0, 2) }'
  return 0
}

# Seconds process $1 has been running (`ps -o etime=`, [[dd-]hh:]mm:ss).
proc_age_secs() {
  local e d=0 h=0 m=0 s=0 a b c
  e="$(ps -o etime= -p "$1" 2>/dev/null)"
  e="${e//[[:space:]]/}"
  [[ -n "$e" ]] || return 1
  if [[ "$e" == *-* ]]; then d="${e%%-*}"; e="${e#*-}"; fi
  IFS=: read -r a b c <<< "$e"
  if [[ -n "$c" ]]; then h="$a"; m="$b"; s="$c"; else m="$a"; s="$b"; fi
  printf '%s' "$(( 10#$d * 86400 + 10#$h * 3600 + 10#$m * 60 + 10#$s ))"
}

# After the group kill: anything that left the group (a new session, a
# daemonized job) and still works in this loop's clone or state, plus pids
# the agent declared in $RADON_RUNNER_PIDFILE that started during this phase.
# Every other loop has its own WORK and state, and a declared pid that is (or
# descends from) another loop's live runner is skipped, so no other loop is
# ever touched.
# Live lock holders of the other loops.
other_runners() {
  local lock
  for lock in "$STATE_DIR"/*.lock; do
    [[ "$lock" != "${LOCK:-}" && -f "$lock/pid" ]] || continue
    cat "$lock/pid" 2>/dev/null
  done
}

# True when $1 is one of the pids in $2 or a descendant of one.
descends_from() {
  local pid="$1" others="$2" depth
  for depth in $(seq 1 64); do
    case " $others " in *" $pid "*) return 0 ;; esac
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [[ -n "$pid" && "$pid" != 0 && "$pid" != 1 ]] || return 1
  done
  return 1
}

reap_leftovers() {
  local work state pid path rest age now others victims=""
  work="$(cd -P "$WORK" 2>/dev/null && pwd -P)"
  state="$(cd -P "$LOOP_STATE" 2>/dev/null && pwd -P)"
  while IFS=$'\t' read -r pid path; do
    [[ -n "$pid" && "$pid" != "$$" ]] || continue
    if [[ -n "$work" ]]; then
      case "$path" in "$work"|"$work"/*) victims="$victims $pid"; continue ;; esac
    fi
    if [[ -n "$state" ]]; then
      case "$path" in "$state"|"$state"/*) victims="$victims $pid" ;; esac
    fi
  done <<< "$(cwd_listing)"
  if [[ -f "$RADON_RUNNER_PIDFILE" && ! -L "$RADON_RUNNER_PIDFILE" ]]; then
    now="$(date +%s)"
    others="$(other_runners | tr '\n' ' ')"
    while read -r pid rest; do
      [[ -z "$rest" ]] || continue
      case "$pid" in ''|*[!0-9]*|0|1) continue ;; esac
      [[ "$pid" != "$$" ]] || continue
      age="$(proc_age_secs "$pid")" || continue
      (( now - age >= ${PHASE_STARTED_AT:-0} - 2 )) || continue
      if [[ -n "${others// /}" ]] && descends_from "$pid" "$others"; then
        log "not reaping declared pid $pid: it belongs to another loop's run"
        continue
      fi
      victims="$victims $pid"
    done < "$RADON_RUNNER_PIDFILE"
  fi
  [[ -n "${victims// /}" ]] || return 0
  log "reaping leftover processes:$victims"
  # shellcheck disable=SC2086
  kill -KILL $victims 2>/dev/null || true
}

# GNU timeout leads its own process group, so one group kill afterwards also
# reaps anything the agent left running (dev servers, watchers).
run_agent() {
  local agent="${1%%:*}" provider=""
  [[ "$1" == *:* ]] && provider="${1#*:}"
  ( cd "$WORK" || exit 70
    # The security prompts' native audit workflows resolve the clone from it.
    export PROMPT_FILE RADON_REPO_ROOT="$WORK"
    export PATH="$AGENT_PATH_PREFIX:$PATH"
    [[ "$GH_GUARD" != 1 ]] || export PATH="$RUNNER_DIR/guard/$LOOP:$PATH"
    exec "$TIMEOUT_BIN" -k "$KILL_AFTER_SECS" "$TIMEOUT_SECS" \
      /bin/bash "$SELF" --launch "$agent" "$provider" ) >> "$LOG" 2>&1 &
  AGENT_PID=$!
  wait "$AGENT_PID"
  AGENT_RC=$?
  kill -KILL -- "-$AGENT_PID" 2>/dev/null || true
  AGENT_PID=""
  reap_leftovers
  return "$AGENT_RC"
}

find_pr() {
  (cd "$WORK" && gh pr list --head "$BRANCH" --state all --json url,isCrossRepository \
    --jq '[.[]|select(.isCrossRepository==false)][0].url // empty' 2>/dev/null)
}

# One curl config line, form-string = "key=value", with backslash, double quote,
# CR and LF escaped so a value can neither end the string nor add an option.
curl_field() {
  local v="$2"
  v="${v//\\/\\\\}"; v="${v//\"/\\\"}"; v="${v//$'\n'/\\n}"; v="${v//$'\r'/\\r}"
  printf 'form-string = "%s=%s"\n' "$1" "$v"
}

# Credentials reach curl as a config on stdin, never on its argv (ps shows argv
# to every local user); -q first so no curlrc is read.
notify() {
  local title="$1" message="$2" url="${3:-}" url_title="${4:-}" rc=0 status
  log "$title: $message ${url}"
  [[ -n "${PUSHOVER_USER:-}" && -n "${PUSHOVER_TOKEN:-}" ]] || return 0
  status="$(
    {
      curl_field token "$PUSHOVER_TOKEN"
      curl_field user "$PUSHOVER_USER"
      curl_field title "$title"
      curl_field message "$message"
      [[ -z "$url" ]] || curl_field url "$url"
      [[ -z "$url_title" ]] || curl_field url_title "$url_title"
    } | "$CURL_BIN" -q -sS -m 20 -o /dev/null -w '%{http_code}' --config - \
      https://api.pushover.net/1/messages.json 2>/dev/null
  )" || rc=$?
  if [[ "$rc" -ne 0 || ( -n "${status:-}" && "$status" != "200" ) ]]; then
    log "pushover: send failed status=${status:-$rc}"
  fi
  return 0
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

# PRE_RUN / POST_RUN: root-owned scripts under $RUNNER_DIR, bounded, run in
# the clone with the phase's facts in their environment and the runner's
# PATH. Run in the background and waited on, so a SIGTERM reaches on_signal
# at once instead of after the hook. Sets HOOK_OUT (whatever the hook printed,
# even if it was killed) and returns the hook's rc.
run_hook() {
  local out_file rc old_umask
  old_umask="$(umask)"
  umask 077
  out_file="$(mktemp "$STATE_DIR/.hook.out.XXXXXX")" || { umask "$old_umask"; return 1; }
  umask "$old_umask"
  ( cd "$WORK" 2>/dev/null || cd /
    export PATH="$RUNNER_PATH"
    export LOOP PHASE WORK LOOP_STATE BRANCH RUNNER_DIR REPO_URL KEEP_PATHS AGENT_UNSET \
      PHASE_RC PHASE_LOG PHASE_START_MARK PHASE_REFUSED PHASE_STATUS RUNNER_PYTHON
    exec "$TIMEOUT_BIN" -k 10 "$HOOK_SECS" /bin/bash "$RUNNER_DIR/$1" ) > "$out_file" 2>> "$LOG" &
  HOOK_PID=$!
  wait "$HOOK_PID"
  rc=$?
  kill -KILL -- "-$HOOK_PID" 2>/dev/null || true
  HOOK_PID=""
  HOOK_OUT="$(cat "$out_file" 2>/dev/null)"
  rm -f "$out_file"
  return "$rc"
}

hook_value() { printf '%s\n' "$1" | sed -n "s/^$2=//p" | tail -n 1; }

finish_phase() {
  local out="" status="" pr="" report_url=""
  if [[ -n "$POST_RUN" ]]; then
    run_hook "$POST_RUN"
    out="$HOOK_OUT"
    [[ -z "$out" ]] || printf '%s\n' "$out" >> "$LOG"
    status="$(hook_value "$out" status)"
    pr="$(hook_value "$out" pr_url)"
    report_url="$(hook_value "$out" report_url)"
    # Fail closed: a hook that timed out, crashed or is missing decided nothing
    # (a refused phase still pages REFUSED below).
    [[ -n "$status" || -n "$PHASE_REFUSED" ]] || status="FAILED (post-run hook gave no status)"
  else
    pr="$(find_pr)"
  fi
  if [[ -z "$status" ]]; then
    if [[ -n "$PHASE_REFUSED" ]]; then status="REFUSED: $PHASE_REFUSED"
    elif [[ "$PHASE_RC" == 0 ]]; then status="OK"
    elif [[ "$PHASE_RC" == "$TIMED_OUT" ]]; then status="TIMEOUT"
    else status="FAILED (exit $PHASE_RC)"; fi
  fi
  [[ -z "$PHASE_REFUSED" ]] || ANY_REFUSED=1
  [[ "$status" == OK* ]] || ANY_NOT_OK=1
  notify "radon $LOOP $PHASE" "$status${pr:+ $pr}" "$report_url" "${report_url:+Open private report}"
}

# PHASES="name:secs ...": one agent session per phase, each with its own
# header and timeout. Every phase runs whatever the earlier rc.
run_phases() {
  local spec out rc offset
  ANY_REFUSED=0
  ANY_NOT_OK=0
  for spec in $PHASES; do
    PHASE="${spec%%:*}"
    TIMEOUT_SECS="${spec#*:}"
    PROMPT_FILE="$RUN_DIR/$DATE.$PHASE.prompt.md"
    PHASE_LOG="$RUN_DIR/$DATE.$PHASE.slice.log"
    PHASE_START_MARK="$LOOP_STATE/.phase-start-$PHASE"
    : > "$PHASE_START_MARK"
    PHASE_STARTED_AT="$(date +%s)"
    rm -f "$RADON_RUNNER_PIDFILE"
    PHASE_REFUSED=""
    PHASE_STATUS=""
    PHASE_RC=0
    log "phase $PHASE: budget ${TIMEOUT_SECS}s"
    if [[ -n "$PRE_RUN" ]]; then
      run_hook "$PRE_RUN"
      rc=$?
      out="$HOOK_OUT"
      [[ -z "$out" ]] || printf '%s\n' "$out" >> "$LOG"
      if (( rc != 0 )); then
        PHASE_REFUSED="$(hook_value "$out" refused)"
        PHASE_REFUSED="${PHASE_REFUSED:-the pre-run check failed (exit $rc)}"
        PHASE_RC=2
      fi
    fi
    offset="$(wc -c < "$LOG" | tr -d ' ')"
    if [[ -z "$PHASE_REFUSED" ]]; then
      build_prompt
      run_agents_in_order
      PHASE_RC=$AGENT_RC
      [[ -z "$USED_AGENT" ]] || PHASE_RC=0
    fi
    tail -c "+$((offset + 1))" "$LOG" > "$PHASE_LOG"
    finish_phase
  done
  (( ANY_REFUSED )) && return 2
  (( ANY_NOT_OK )) && return 75
  return 0
}

# launchd stops a job with SIGTERM and SIGKILLs it ExitTimeOut seconds later:
# page first, then give POST_RUN ten seconds for the dead-man.
on_signal() {
  trap - TERM INT
  [[ -z "$AGENT_PID" ]] || kill -KILL -- "-$AGENT_PID" 2>/dev/null || true
  [[ -z "$HOOK_PID" ]] || kill -KILL -- "-$HOOK_PID" 2>/dev/null || true
  HOOK_PID=""
  reap_leftovers
  notify "radon $LOOP${PHASE:+ $PHASE}" "KILLED (SIG$1)"
  if [[ -n "$POST_RUN" && -n "$PHASE" ]]; then
    PHASE_STATUS="KILLED (SIG$1)"
    PHASE_RC=143
    HOOK_SECS=10
    run_hook "$POST_RUN"
    [[ -z "$HOOK_OUT" ]] || printf '%s\n' "$HOOK_OUT" >> "$LOG"
  fi
  exit 143
}

main() {
  LOOP="${1:?usage: run_loop.sh <loop>}"
  load_config
  load_secrets
  acquire_lock
  trap 'on_signal TERM' TERM
  trap 'on_signal INT' INT
  prune_logs
  resolve_agents
  refuse_disallowed_agents
  require_guard
  if ! fresh_clone; then
    notify "radon $LOOP: FAILED" "could not clone $REPO_URL"
    return 70
  fi
  if [[ -n "$PHASES" ]]; then
    run_phases
    return
  fi
  build_prompt
  PHASE_STARTED_AT="$(date +%s)"
  rm -f "$RADON_RUNNER_PIDFILE"
  run_agents_in_order
  report
}

if [[ "${1:-}" == "--launch" ]]; then
  launch_agent "${2:?}" "${3:-}"
fi
main "$@"
