# Shared Claude ladder for security_nightly.sh and security_deepsec_nightly.sh.
# Source after REPO, LOOP_LOG_TAG, and the billing-reroute unset.
#
# Policy: discover Mini `claude models` (subscription CLI only), rank by
# capability tier, skip the most powerful tier, use the second most powerful
# as primary, then deeper Claude fallbacks. RADON_WEEKEND_MODEL_LADDER / RADON_WEEKEND_PROVIDER_LADDER
# short-circuit discovery. Discovery failure uses SAFETY (opus then sonnet)
# and logs; it never silently puts fable back.
#
# shellcheck shell=bash

_SECURITY_CLAUDE_SAFETY="claude-opus-5 claude-sonnet-5"

_security_claude_log() {
  if [[ -n "${RUN_LOG:-}" && -f "${RUN_LOG:-}" ]]; then
    echo "[${LOOP_LOG_TAG:-security}] $*" | tee -a "$RUN_LOG"
  else
    echo "[${LOOP_LOG_TAG:-security}] $*" >&2
  fi
}

if [[ -n "${RADON_WEEKEND_PROVIDER_LADDER:-}" ]]; then
  PROVIDER_LADDER="$RADON_WEEKEND_PROVIDER_LADDER"
  MODEL_LADDER="${RADON_WEEKEND_MODEL_LADDER:-}"
elif [[ -n "${RADON_WEEKEND_MODEL_LADDER:-}" ]]; then
  MODEL_LADDER="$RADON_WEEKEND_MODEL_LADDER"
  PROVIDER_LADDER=""
else
  _ladder_py="${RADON_SECURITY_CLAUDE_LADDER_PY:-$REPO/scripts/security_claude_ladder.py}"
  _py="$(command -v python3.13 || true)"
  [[ -n "$_py" ]] || _py="$(command -v python3 || true)"
  MODEL_LADDER=""
  if [[ -n "$_py" && -r "$_ladder_py" ]]; then
    # stdout is the ladder; stderr is the skip/failure line (bash 3.2: no
    # process substitution). Discovery never bills an API key.
    MODEL_LADDER="$("$_py" -I "$_ladder_py" || true)"
    MODEL_LADDER="$(printf '%s' "$MODEL_LADDER" | tr -s '[:space:]' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  fi
  if [[ -z "$MODEL_LADDER" ]]; then
    MODEL_LADDER="$_SECURITY_CLAUDE_SAFETY"
    _security_claude_log "claude catalog discovery failed; safety ladder $MODEL_LADDER (newest/fable excluded)"
  else
    _security_claude_log "claude catalog skip-newest → $MODEL_LADDER"
  fi
  PROVIDER_LADDER=""
  unset _ladder_py _py
fi

if [[ -z "${PROVIDER_LADDER:-}" ]]; then
  PROVIDER_LADDER=""
  for _m in $MODEL_LADDER; do
    PROVIDER_LADDER="${PROVIDER_LADDER:+$PROVIDER_LADDER }claude:$_m"
  done
  unset _m
fi
