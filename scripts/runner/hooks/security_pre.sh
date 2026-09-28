#!/bin/bash
# PRE_RUN hook for loops/security.env and loops/security-deepsec.env.
# Installed root-owned in /usr/local/radon-runner/hooks; run_loop.sh runs it
# before every phase with cwd = the clone. A non-zero exit refuses the phase
# (the agent does not start) and a `refused=<reason>` line names why; the
# reason reaches the public dead-man only through security_post.sh's
# sanitizer.
#
# Ported from scripts/security_nightly.sh (prologue and run_phase):
# the clone and origin checks, refuse_credential_files,
# refuse_billing_reroute_files, the CLI env-drift page (the old 23:50 job),
# ground_truth pinned to the newest CI-green main, write_audit_context and
# arm_deliver_record.
set -uo pipefail

: "${LOOP:?}" "${PHASE:?}" "${WORK:?}" "${LOOP_STATE:?}" "${RUNNER_DIR:?}" "${REPO_URL:?}"
PY="${RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
GH="${RADON_RUNNER_GH:-$(command -v gh || true)}"
GH_REPO="${RADON_WEEKEND_GH_REPO:-joemccann/radon}"
SCRATCH="$LOOP_STATE/scratch"
TIMEOUT_BIN="$(command -v timeout || command -v gtimeout || true)"

case "$LOOP" in
  security) LABEL=security-nightly; CHECKPOINT_KEY=head_sha ;;
  security-deepsec) LABEL=security-deepsec; CHECKPOINT_KEY=last_audited_sha ;;
  *) echo "refused=security_pre.sh does not know loop $LOOP"; exit 2 ;;
esac

refuse() {
  echo "REFUSING: $1" >&2
  echo "refused=$1"
  exit 2
}

hostgit() { git -c core.hooksPath=/dev/null -c core.fsmonitor=false -C "$WORK" "$@"; }
# The helpers' own git calls get the same pins.
export GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null \
  GIT_CONFIG_KEY_1=core.fsmonitor GIT_CONFIG_VALUE_1=false
bounded() { "$TIMEOUT_BIN" "$1" "${@:2}"; }

# Rail 1: this loop's own runner clone of joemccann/radon, nothing else.
[[ "$(pwd -P)" == "$(cd -P "$HOME/radon-runner/work/$LOOP" 2>/dev/null && pwd -P)" ]] \
  || refuse "the phase is not running in the runner clone of this loop"
[[ "$(hostgit config --get remote.origin.url 2>/dev/null)" == "$REPO_URL" ]] \
  || refuse "the clone's origin is not the loop's repository"

# REL-180 (R-506): CREDENTIAL-FREE is a check, not a comment.
refuse_credential_files() {
  local credential_file
  for credential_file in .env .env.ib-mode web/.env; do
    [[ -e "$credential_file" ]] || continue
    refuse "the clone holds a credential file ($credential_file); the security loop runs credential-free only, remove it"
  done
}

# Rail 5b: subscription only. `unset` (AGENT_UNSET, applied by run_loop.sh)
# cannot reach a key a scanner loads itself or a Claude Code settings-level
# apiKeyHelper / env reroute, so those files are refused. The names come from
# AGENT_UNSET; the CLAUDE_CODE_USE_* and CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST
# switches reroute only when truthy.
refuse_billing_reroute_files() {
  local name keys="" flags="" key_assign flag_assign settings_key settings_flag file
  for name in $AGENT_UNSET; do
    case "$name" in
      CLAUDE_CODE_USE_*|CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST) flags="${flags:+$flags|}$name" ;;
      *) keys="${keys:+$keys|}$name" ;;
    esac
  done
  [[ -n "$keys" ]] || keys='a\{0\}b'
  [[ -n "$flags" ]] || flags='a\{0\}b'
  key_assign="^[[:space:]]*(export[[:space:]]+)?($keys)[[:space:]]*=[[:space:]]*[^[:space:]#]"
  flag_assign="^[[:space:]]*(export[[:space:]]+)?($flags)[[:space:]]*=[[:space:]]*[\"']?(1|true|yes)[\"']?([#[:space:]]|\$)"
  settings_key="\"($keys)\"[[:space:]]*:[[:space:]]*\"[^\"]"
  settings_flag="\"($flags)\"[[:space:]]*:[[:space:]]*\"?(1|true|yes)\"?"

  # .deepsec/ survives the re-clone (KEEP_PATHS), so walk all of it.
  while IFS= read -r file; do
    [[ -f "$file" ]] || continue
    if grep -qE "$key_assign" "$file" || grep -qiE "$flag_assign" "$file"; then
      refuse "$file holds billing-reroute credentials; this loop bills the claude.ai subscription only, remove the key line"
    fi
  done < <(
    for file in .env.local .env.*.local; do [[ -f "$file" ]] && printf '%s\n' "$file"; done
    find .deepsec -type f -name '.env*' 2>/dev/null || true
  )

  for file in "$HOME/.claude/settings.json" .claude/settings*.json \
      "/Library/Application Support/ClaudeCode/managed-settings.json" /etc/claude-code/managed-settings.json; do
    [[ -f "$file" ]] || continue
    if grep -qE '"apiKeyHelper"[[:space:]]*:[[:space:]]*"[^"]' "$file" \
       || grep -qE "$settings_key" "$file" || grep -qiE "$settings_flag" "$file"; then
      refuse "$file holds an apiKeyHelper or billing-reroute env entry; this loop bills the claude.ai subscription only, remove it"
    fi
  done
}

refuse_credential_files
refuse_billing_reroute_files

install -d -m 700 "$SCRATCH"

# The Claude Code CLI is not version-pinned: page once per version that
# references an env name nobody reviewed. Non-fatal, as the old 23:50 job was.
bounded 60 "$PY" -I "$RUNNER_DIR/lib/claude_cli_env_drift.py" --notify --state-dir "$LOOP_STATE" \
  --reviewed "$RUNNER_DIR/lib/claude_cli_env_reviewed.txt" >&2 || true

# REL-187: ground truth is the newest CI-green main, stale-but-green over
# fresh-but-red; GitHub unreachable keeps origin/main. The agent's local
# branches survive; only HEAD moves.
hostgit fetch --quiet origin || refuse "could not fetch origin"
green="$(bounded 60 "$PY" -I "$RUNNER_DIR/lib/nightly_green_base.py" --repo "$GH_REPO" \
  --repo-dir "$WORK" --head origin/main --gh-bin "$GH" 2>/dev/null || true)"
if [[ ! "$green" =~ ^[0-9a-f]{40}$ ]] || ! hostgit rev-parse --verify --quiet "${green}^{commit}" >/dev/null; then
  green="$(hostgit rev-parse origin/main)"
fi
hostgit checkout -f --quiet --detach "$green" || refuse "could not check out the green base"
excludes=(--exclude=/.venv/)
for keep in ${KEEP_PATHS:-}; do excludes+=("--exclude=/$keep/"); done
hostgit clean -fdxq "${excludes[@]}"

[[ -d "$LOOP_STATE/held.git" ]] || git init -q --bare "$LOOP_STATE/held.git"

# Written only for the audit phase and removed before every other phase, so
# remediate and deliver never read a stale one.
rm -f -- "$SCRATCH/audit-context.md"
if [[ "$PHASE" == audit ]]; then
  bounded 180 "$PY" -I "$RUNNER_DIR/lib/nightly_audit_context.py" --repo "$GH_REPO" --repo-dir "$WORK" \
    --head HEAD --gh-bin "$GH" --label "$LABEL" --out "$SCRATCH/audit-context.md" \
    --checkpoint-json "$SCRATCH/last-audited.json" --checkpoint-key "$CHECKPOINT_KEY" >&2 || true
fi

# R-611: a deliver killed at its cap still leaves a branch to resume.
if [[ "$PHASE" == deliver ]]; then
  RADON_WEEKEND_ROOT="$LOOP_STATE" bounded 30 "$PY" -I "$RUNNER_DIR/lib/nightly_deliver.py" record \
    --loop "$LOOP" --branch "$BRANCH" --status launched >&2 || true
fi
exit 0
