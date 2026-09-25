#!/bin/bash
# Preflight for tonight's nightly fires, run by hand on the runner host:
#   bash scripts/nightly_smoke.sh        exit 0 = every loop will start
#
# Mirrors each launchd ProgramArguments step up to (not including) the
# wrapper exec, then checks the wrapper as it will exist after the reset to
# origin/main: syntax, provider-ladder readiness via the wrapper's own
# provider_ready, and the test venv. Launches no agent and never takes a
# runner lock. The only write is the bounded `git fetch` launchd itself runs.
set -u
W="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
LAUNCHD_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$HOME/.bun/bin"
REF="$W/radon"                      # any clone: used only to read origin/main blobs
FAILS=0; WARNS=0
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/radon-nightly-smoke.XXXXXX")" || exit 2
trap 'rm -rf "$SCRATCH"' EXIT
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILS=$((FAILS+1)); }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$*"; WARNS=$((WARNS+1)); }

git -C "$REF" fetch -q origin main || { echo "cannot fetch origin/main"; exit 2; }
MAIN="$(git -C "$REF" rev-parse origin/main)"
blob() { git -C "$REF" show "origin/main:$1" 2>/dev/null; }
echo "origin/main = ${MAIN:0:8}"

echo "== host"
T="$(PATH=$LAUNCHD_PATH command -v timeout || PATH=$LAUNCHD_PATH command -v gtimeout || true)"
[[ -n "$T" ]] && ok "timeout on launchd PATH: $T" || bad "no timeout/gtimeout on launchd PATH (plist exits 78)"
s="$(pmset -g | awk '$1=="sleep"{print $2}')"
[[ "$s" == "0" ]] && ok "system sleep disabled" || warn "system sleep=$s min: a sleeping Mac fires late (on wake)"
for f in "$HOME/.claude/settings.json" "/Library/Application Support/ClaudeCode/managed-settings.json" /etc/claude-code/managed-settings.json; do
  [[ -f "$f" ]] || continue
  if grep -qE '"apiKeyHelper"[[:space:]]*:[[:space:]]*"[^"]|ANTHROPIC_(API_KEY|AUTH_TOKEN|BASE_URL)' "$f"; then bad "$f holds apiKeyHelper/billing reroute (prologue REFUSES)"; else ok "no billing reroute in $f"; fi
done

loop() {  # label clone wrapper skill slug venv
  local label="$1" C="$2" wrapper="$3" skill="$4" slug="${5}"
  local P="$HOME/Library/LaunchAgents/com.radon.$label.plist" G="$W/.gitdirs/$slug.git" A="$W/.gitdirs-agent/$slug.git"
  echo "== $label  ($(plutil -extract StartCalendarInterval.Hour raw "$P"):$(printf %02d "$(plutil -extract StartCalendarInterval.Minute raw "$P")"))"
  if launchctl print "gui/$(id -u)/com.radon.$label" >/dev/null 2>&1; then
    ok "launchd job loaded ($(launchctl print "gui/$(id -u)/com.radon.$label" | awk -F'= ' '/last exit code/{print "last exit " $2; exit}'))"
  else bad "launchd job com.radon.$label NOT loaded"; fi
  [[ -d "$C" ]] && ok "clone $C" || { bad "clone missing $C"; return; }
  [[ -d "$G" ]] && ok "host gitdir $G" || bad "host gitdir missing $G"
  local gf; gf="$(sed -n 's/^gitdir: //p' "$C/.git" 2>/dev/null)"
  [[ "$gf" == "$A" && -d "$A" ]] && ok "clone gitfile -> agent gitdir" || warn "clone .git -> '${gf:-?}' (wrapper re-points it, expected $A)"
  local pid; pid="$(cat "$C/.weekend-runner.lock/pid" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then bad "runner lock HELD by live pid $pid: launchd will exit 0 without running"
  else ok "runner lock free"; fi
  # launchd step 1: the exact fetch it runs (bounded, via host gitdir)
  if GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null GIT_CONFIG_KEY_1=core.fsmonitor GIT_CONFIG_VALUE_1=false \
     PATH="$LAUNCHD_PATH" "$T" -k 15 180 git --git-dir="$G" --work-tree="$C" \
     -c core.sshCommand="ssh -o ConnectTimeout=20 -o BatchMode=yes" fetch -q origin 2>"$SCRATCH/fetch.err"; then
    [[ "$(git --git-dir="$G" rev-parse origin/main)" == "$MAIN" ]] && ok "launchd fetch works; host origin/main = ${MAIN:0:8}" || warn "fetch ok but host origin/main != $MAIN"
  else bad "launchd fetch FAILS: $(head -c 200 "$SCRATCH/fetch.err")"; fi
  # wrapper as it will run after `reset --hard origin/main`
  local wf="$SCRATCH/$slug.sh"
  blob "scripts/$wrapper" > "$wf"
  bash -n "$wf" 2>/dev/null && ok "wrapper parses (bash -n scripts/$wrapper)" || bad "wrapper syntax error: scripts/$wrapper"
  # provider ladder readiness, evaluated with the wrapper's own functions
  local fns; fns="$(awk '/^(rung_model|provider_bin|provider_key_present|provider_ready)\(\) \{/,/^\}/' "$wf")"
  local ladder; ladder="$(grep -oE 'PROVIDER_LADDER="\$\{RADON_WEEKEND_PROVIDER_LADDER:-[^}]+' "$wf" | sed 's/.*:-//')"
  if [[ -z "$ladder" ]]; then
    local lf="$SCRATCH/$slug-ladder.sh"; blob scripts/security_claude_ladder.sh > "$lf"
    ladder="$(cd "$C" && env -i HOME="$HOME" PATH="$LAUNCHD_PATH" REPO="$C" LOOP_LOG_TAG=smoke "$T" 60 bash -c ". '$lf' >/dev/null 2>&1; printf '%s' \"\$PROVIDER_LADDER\"")"
  fi
  [[ -n "$ladder" ]] || { bad "could not resolve provider ladder"; rm -f "$wf"; return; }
  local ready=""; local r
  for r in $ladder; do
    for phase in audit remediate deliver; do
      if env -i HOME="$HOME" PATH="$LAUNCHD_PATH" PHASE="$phase" LOOP_SKILL="$skill" \
           AGENT_CLI_ROOT="$HOME/.radon/agent-cli" PORTABLE_PROMPT_DIR="$SCRATCH/prompts-$slug" \
           bash -c "$fns"$'\n'"mkdir -p \"\$PORTABLE_PROMPT_DIR\"; git -C '$REF' show 'origin/main:.claude/portable-prompts/$skill.$phase.md' > \"\$PORTABLE_PROMPT_DIR/$skill.$phase.md\" 2>/dev/null || rm -f \"\$PORTABLE_PROMPT_DIR/$skill.$phase.md\"; provider_ready '${r%%:*}'"; then :; else continue 2; fi
    done
    ready="${ready:+$ready }$r"
  done
  if [[ -n "$ready" ]]; then
    local first="${ladder%% *}"
    [[ " $ready " == *" $first "* ]] && ok "primary rung ready: $first (ready: $ready)" || warn "primary rung $first NOT ready; will fall to: $ready"
  else bad "NO rung in ladder is ready ($ladder)"; fi
  # venv the phases run tests with
  local v="$W/venv-${6}"
  [[ -x "$v/bin/python" ]] && "$v/bin/python" -c 'import pytest' 2>/dev/null && ok "venv $v has pytest" || bad "venv $v missing or no pytest"
}

loop reliability-daily      "$W/radon"                  reliability_weekend.sh       reliability-weekend   reliability      reliability
loop testing-daily          "$W/radon-testing"          testing_weekend.sh           testing-weekend       testing          testing
loop ci-performance-daily   "$W/radon-ci-performance"   ci_performance_nightly.sh    ci-performance        ci-performance   ci-performance
loop documentation-daily    "$W/radon-documentation"    documentation_nightly.sh     documentation-nightly documentation    documentation
loop security-daily         "$W/radon-security"         security_nightly.sh          security-nightly      security         security
loop security-deepsec       "$W/radon-security-deepsec" security_deepsec_nightly.sh  security-deepsec      security-deepsec security-deepsec

echo "== summary: $FAILS fail, $WARNS warn"
exit $(( FAILS > 0 ))
