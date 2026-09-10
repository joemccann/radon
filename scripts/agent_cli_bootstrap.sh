#!/usr/bin/env bash
# Provision the non-Claude agent-CLI providers the nightly loops fall back to.
#
# Everything this writes lives OUTSIDE any Radon clone, under
# ~/.radon/agent-cli. That is deliberate: the loops' credential rails refuse a
# key file inside the clone, and a `git clean` between phases would delete one
# anyway. Nothing here is secret-generating — it only records where a key
# already on this host can be read from, and how to reach each provider.
#
# Idempotent. Safe to re-run. Never invoked from inside a nightly wrapper.
#
#   scripts/agent_cli_bootstrap.sh          # provision, then report
#   scripts/agent_cli_bootstrap.sh --check  # report only, exit 1 if incomplete
set -Eeuo pipefail

ROOT="${RADON_AGENT_CLI_ROOT:-$HOME/.radon/agent-cli}"
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

# grok's CLI is the host for every OpenAI-compatible provider, because it is
# the only agent CLI here that speaks /chat/completions. codex speaks only
# /responses, which NVIDIA NIM and Cerebras do not serve (verified 2026-09-06:
# both 404).
#
# MODEL SELECTION (operator's decision, 2026-09-07: "do not pin a particular
# model — new models are released all the time, select the most recent one
# dynamically"). The codex and grok rungs carry NO model at all: their CLI uses
# the account default and the vendor migrates it forward. These two providers
# cannot do that — grok resolves them through a `[model."<key>"]` block and a
# concrete upstream id MUST be named — so the resolution happens HERE, at
# provision time, against the provider's live /v1/models, and is written under
# a STABLE key. The ladder rung names only that key (nvidia:nvidia-latest,
# cerebras:cerebras-latest), so a new model release is picked up by re-running
# this script and never by editing a wrapper.
#
# Selection rule:
#   nvidia   — restrict to ids beginning `nvidia/`, then take the largest
#              parameter count in the id (…-550b…), ties broken by the newest
#              `created`, then lexically for determinism.
#   cerebras — the newest `created`, ties broken lexically.
# If the call fails (no key, no network, unparsable body) the id already in the
# config is carried forward, and only if there is none does the pinned default
# below apply. Provisioning must never leave a rung pointing at nothing.
#
# NVIDIA's own nvidia/* models are the only NIM models usable through grok:
# third-party ones (kimi-k3, deepseek-v4-pro, minimax-m3) answer correctly and
# then exit 1 on `serialization error: invalid type: null, expected u32`,
# because NIM returns `usage.prompt_tokens_details.audio_tokens: null` for
# them and grok's client wants a u32. An exit code is what the wrapper reads,
# so a model that cannot produce a clean one is not a rung. That is why the
# nvidia rule filters on the `nvidia/` prefix rather than taking the newest.
NVIDIA_MODEL_FALLBACK="${RADON_NVIDIA_MODEL:-nvidia/nemotron-3-ultra-550b-a55b}"
CEREBRAS_MODEL_FALLBACK="${RADON_CEREBRAS_MODEL:-qwen-3.8-27b}"
# The rung names these, never a vendor id. Changing one is a ladder change.
NVIDIA_MODEL_KEY="nvidia-latest"
CEREBRAS_MODEL_KEY="cerebras-latest"

log() { printf '[agent-cli] %s\n' "$*"; }

# The id currently provisioned for a tag, so a failed refresh carries forward
# rather than reverting a live rung to a stale pinned default.
current_model() {
  local tag="$1"
  local f="$ROOT/grok-home-$tag/config.toml"
  [[ -r "$f" ]] || return 0
  sed -n 's/^model = "\(.*\)"$/\1/p' "$f" | head -1
}

# Live /v1/models, filtered and ranked by the rule documented above. Bounded;
# any failure prints nothing and the caller falls back.
resolve_model() {
  local tag="$1"
  local base_url="$2"
  local key_name="$3"
  # `${!3}` indirects through a POSITIONAL parameter, which is already set, so
  # it is safe here — unlike the `local`-in-the-same-statement form. Split
  # anyway so the two functions read the same way.
  local token="${!key_name:-}"
  [[ -n "$token" ]] || return 0
  curl -fsS --max-time 20 -H "Authorization: Bearer $token" \
    "$base_url/models" 2>/dev/null | RADON_SELECT_TAG="$tag" python3 -c '
import json, os, re, sys

tag = os.environ["RADON_SELECT_TAG"]
try:
    rows = json.load(sys.stdin).get("data") or []
except Exception:
    sys.exit(0)
ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
created = {r.get("id"): r.get("created") or 0 for r in rows if isinstance(r, dict)}


def params(mid):
    """Largest parameter count named in the id: …-550b… -> 550."""
    return max([int(m) for m in re.findall(r"(\d+)b(?:[-_.]|$)", mid.lower())] or [0])


if tag == "nvidia":
    # Only NVIDIA-built models deserialize cleanly through the grok client.
    ids = [i for i in ids if i.startswith("nvidia/")]
    ids.sort(key=lambda i: (params(i), created.get(i, 0), i), reverse=True)
else:
    ids.sort(key=lambda i: (created.get(i, 0), i), reverse=True)
if ids:
    print(ids[0])
' 2>/dev/null || true
}

write_grok_home() {
  local tag="$1" base_url="$2" env_key="$3" key="$4" model="$5"
  local home="$ROOT/grok-home-$tag"
  mkdir -p "$home"
  cat > "$home/config.toml.new" <<EOF
# Generated by scripts/agent_cli_bootstrap.sh. Do not hand-edit.
# The grok CLI hosts this provider. The BLOCK KEY is what a ladder rung's
# --model names and is stable across model releases; \`model\` is the upstream
# id resolved from $base_url/models at provision time. Re-run this script to
# pick up a newer model; never edit a wrapper's ladder for one.
[model."$key"]
base_url = "$base_url"
env_key = "$env_key"
model = "$model"
EOF
  mv -f "$home/config.toml.new" "$home/config.toml"
  chmod 0700 "$home"
  log "wrote $home/config.toml ($key -> $model)"
}

# One env file, mode 0600, read by the wrappers before they launch a rung.
# Values are sourced from the environment if present, else carried forward
# from the existing file, so re-running never blanks a key.
carry() {
  # SPLIT declarations on purpose. In bash 3.2 (the /bin/bash on this runner)
  # `local key="$1" val="${!key:-}"` expands the indirection before `key` is
  # visible, so `val` comes back EMPTY and this function silently blanks the
  # key it was asked to carry — which is exactly how ~/.radon/agent-cli/env
  # ended up with two empty API keys, skipping the nvidia and cerebras rungs.
  local key="$1"
  local cur="${2:-}"
  local val="${!key:-}"
  [[ -n "$val" ]] && { printf '%s' "$val"; return 0; }
  printf '%s' "$cur"
}

provision() {
  mkdir -p "$ROOT"
  chmod 0700 "$ROOT"
  local envf="$ROOT/env" old_nv="" old_cb="" old_xai=""
  if [[ -r "$envf" ]]; then
    old_nv="$(sed -n 's/^NVIDIA_API_KEY=//p' "$envf" | head -1)"
    old_cb="$(sed -n 's/^CEREBRAS_API_KEY=//p' "$envf" | head -1)"
    old_xai="$(sed -n 's/^XAI_API_KEY=//p' "$envf" | head -1)"
  fi
  umask 077
  {
    echo "# Generated by scripts/agent_cli_bootstrap.sh. Mode 0600."
    echo "# Provider keys for the nightly fallback ladder. NOT a Radon .env:"
    echo "# it holds no broker, Turso or Anthropic credential, and lives"
    echo "# outside every clone so the loops' rails never have to trust it."
    echo "NVIDIA_API_KEY=$(carry NVIDIA_API_KEY "$old_nv")"
    echo "CEREBRAS_API_KEY=$(carry CEREBRAS_API_KEY "$old_cb")"
    echo "XAI_API_KEY=$(carry XAI_API_KEY "$old_xai")"
  } > "$envf.new"
  mv -f "$envf.new" "$envf"
  chmod 0600 "$envf"
  log "wrote $envf"

  # Resolve now, while a key is exported and the network is available. Order:
  # live /v1/models, else the id already provisioned, else the pinned default.
  local nv_url="https://integrate.api.nvidia.com/v1"
  local cb_url="https://api.cerebras.ai/v1"
  # Read the keys back out of the file we just wrote rather than sourcing it:
  # a key containing `$` is shell-expanded by `.` under set -u and aborts.
  NVIDIA_API_KEY="$(sed -n 's/^NVIDIA_API_KEY=//p' "$envf" | head -1)"
  CEREBRAS_API_KEY="$(sed -n 's/^CEREBRAS_API_KEY=//p' "$envf" | head -1)"
  export NVIDIA_API_KEY CEREBRAS_API_KEY
  local nv cb
  nv="$(resolve_model nvidia "$nv_url" NVIDIA_API_KEY || true)"
  if [[ -z "$nv" ]]; then
    nv="$(current_model nvidia)"
    if [[ -n "$nv" ]]; then log "nvidia   /v1/models unavailable — carrying forward $nv"; fi
  fi
  if [[ -z "$nv" ]]; then nv="$NVIDIA_MODEL_FALLBACK"; fi
  cb="$(resolve_model cerebras "$cb_url" CEREBRAS_API_KEY || true)"
  if [[ -z "$cb" ]]; then
    cb="$(current_model cerebras)"
    if [[ -n "$cb" ]]; then log "cerebras /v1/models unavailable — carrying forward $cb"; fi
  fi
  if [[ -z "$cb" ]]; then cb="$CEREBRAS_MODEL_FALLBACK"; fi

  write_grok_home nvidia   "$nv_url" NVIDIA_API_KEY   "$NVIDIA_MODEL_KEY"   "$nv"
  write_grok_home cerebras "$cb_url" CEREBRAS_API_KEY "$CEREBRAS_MODEL_KEY" "$cb"
}

report() {
  local missing=0 v
  # codex: ChatGPT subscription, refreshed by the CLI itself.
  if [[ -r "${CODEX_HOME:-$HOME/.codex}/auth.json" ]]; then
    log "codex     OK   ${CODEX_HOME:-$HOME/.codex}/auth.json"
  else
    log "codex     MISSING  run: codex login"; missing=1
  fi
  # grok: x.ai subscription (auth.json) or XAI_API_KEY.
  if [[ -r "$HOME/.grok/auth.json" ]] || [[ -n "${XAI_API_KEY:-}" ]]; then
    log "grok      OK"
  else
    log "grok      MISSING  run: grok (sign in), or set XAI_API_KEY"; missing=1
  fi
  for p in nvidia cerebras; do
    case "$p" in
      nvidia)   v="${NVIDIA_API_KEY:-}" ;;
      cerebras) v="${CEREBRAS_API_KEY:-}" ;;
    esac
    if [[ -n "$v" ]] || grep -qE "^$(printf '%s' "${p}" | tr '[:lower:]' '[:upper:]')_API_KEY=." "$ROOT/env" 2>/dev/null; then
      log "$p  OK   $ROOT/grok-home-$p/config.toml"
    else
      log "$p  MISSING  no API key"; missing=1
    fi
  done
  return "$missing"
}

[[ "$CHECK_ONLY" == "1" ]] || provision
report
