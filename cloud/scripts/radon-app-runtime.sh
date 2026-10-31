#!/bin/bash
set -euo pipefail

# Root-owned app-plane image runner. systemd ExecStart calls `run <unit>`.
# Service starts do not take the deploy lifecycle lock; optional pruning does.
# radon is not in group docker.
# Never Gateway, Caddy, health, or the engine socket.

readonly APP_UNITS="radon-model-gateway.service radon-uw-stream.service radon-uw-research.service radon-api.service radon-nextjs.service radon-relay.service radon-monitor.service radon-newsfeed.service radon-research.service radon-research-archive.service radon-research-asset-archive.service radon-marketdesk.service radon-marketdesk-pdf.service radon-subscription-vault.service radon-ai-cycle.service radon-ai-cycle-backfill.service radon-aa-frontier-refresh.service"
# DS-2026-10-05-05: every unit that opens the encrypted secret store. Each gets
# the master key only through the radon-secrets staging below, inside image
# code, never as a User=radon host process running the radon-writable venv.
readonly SECRET_STORE_UNITS="radon-uw-stream.service radon-uw-research.service radon-api.service radon-subscription-vault.service radon-ai-cycle.service radon-ai-cycle-backfill.service radon-aa-frontier-refresh.service"
# Store-only jobs: no media, no 2FA lease, no subscription grants, no control
# socket. Only the secret_store directory and their own state.
readonly STORE_JOB_UNITS="radon-subscription-vault.service radon-ai-cycle.service radon-ai-cycle-backfill.service radon-aa-frontier-refresh.service"

# Where the media volume lands INSIDE the container. Fixed regardless of the
# host path, because Caddy's root and the newsfeed's download dir must agree.
readonly MEDIA_DIR_IN_CONTAINER=/var/lib/radon/media
# Host control socket directory (radon-control.service RuntimeDirectory), at
# the same path inside the radon-api container. Never mounted anywhere else.
readonly CONTROL_DIR_IN_CONTAINER=/run/radon-control

if [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" == "1" ]]; then
  DOCKER="${RADON_TEST_DOCKER:?test docker is required}"
  ENGINE="${RADON_TEST_ENGINE:-docker}"
  LEGACY_DOCKER="${RADON_TEST_LEGACY_DOCKER:-}"
  ID_BIN="${RADON_TEST_ID:?test id is required}"
  ENV_FILE="${RADON_TEST_ENV_FILE:?test env file is required}"
  DATA_DIR="${RADON_TEST_DATA_DIR:?test data dir is required}"
  MEDIA_DIR="${RADON_TEST_MEDIA_DIR:?test media dir is required}"
  STATE_DIR="${RADON_TEST_STATE_DIR:?test state dir is required}"
  LEASE_DIR="${STATE_DIR}/ib-lease"
  CHOWN="${RADON_TEST_CHOWN:?test chown is required}"
  PYTHON="${RADON_TEST_PYTHON:-$(command -v python3)}"
  GETENT="${RADON_TEST_GETENT:?test getent is required}"
  NOTIFY_PROXY_DIR="${RADON_TEST_NOTIFY_PROXY_DIR:-${STATE_DIR}/notify}"
  CONTROL_DIR="${RADON_TEST_CONTROL_DIR:-${STATE_DIR}/control}"
  DEPLOY_LOCK_FILE="${RADON_TEST_DEPLOY_LOCK:-${STATE_DIR}/deploy.lock}"
  LEGACY_DEPLOY_LOCK_FILE="${RADON_TEST_DEPLOY_LEGACY_LOCK:-}"
  PULL_LOCK_FILE="${RADON_TEST_PULL_LOCK:-${STATE_DIR}/image-pull.lock}"
  PULL_LOCK_WAIT_SECS="${RADON_TEST_PULL_LOCK_WAIT_SECS:-300}"
  GREEN_MARKER_FILE="${RADON_TEST_GREEN_MARKER:-${STATE_DIR}/last-green}"
  TRANSITION_JOURNAL_FILE="${RADON_TEST_TRANSITION_JOURNAL:-${STATE_DIR}/transition.json}"
  CHROMIUM_SECCOMP_PROFILE="${RADON_TEST_SECCOMP_PROFILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/seccomp/chromium.json}"
  AI_CYCLE_DIR="${RADON_TEST_AI_CYCLE_DIR:-${STATE_DIR}/ai-cycle}"
  SUBSCRIPTION_VAULT_DIR="${RADON_TEST_SUBSCRIPTION_VAULT_DIR:-${STATE_DIR}/subscription-vault}"
  MODEL_GATEWAY_DIR="${RADON_TEST_MODEL_GATEWAY_DIR:-${STATE_DIR}/model-gateway}"
  MODEL_CONTROL_DIR="${RADON_TEST_MODEL_CONTROL_DIR:-${STATE_DIR}/model-control}"
  MODEL_TRANSPORT_HELPER="${RADON_TEST_MODEL_TRANSPORT_HELPER:-/usr/local/lib/radon/model_transport.py}"
  SUBSCRIPTION_STATE_DIR="${STATE_DIR}/subscription-tokens"
else
  if (( EUID != 0 )); then
    echo "radon-app-runtime must run as root" >&2
    exit 77
  fi
  # REL-087 / R-232: Podman is the engine whenever it is installed, because
  # `--cgroups=split` keeps conmon and the container inside the unit's own
  # cgroup. Docker stays as the staged-cutover fallback (podman not installed
  # yet) and the rollback lever (drop-in Environment=RADON_CONTAINER_ENGINE=docker).
  ENGINE="${RADON_CONTAINER_ENGINE:-}"
  if [[ -z "$ENGINE" ]]; then
    if [[ -x /usr/bin/podman ]]; then ENGINE=podman; else ENGINE=docker; fi
  fi
  LEGACY_DOCKER=""
  case "$ENGINE" in
    podman)
      DOCKER=/usr/bin/podman
      # A docker-era container of the same unit must be reaped too.
      [[ -x /usr/bin/docker ]] && LEGACY_DOCKER=/usr/bin/docker
      ;;
    docker) DOCKER=/usr/bin/docker ;;
    *)
      echo "radon-app-runtime: RADON_CONTAINER_ENGINE must be podman or docker" >&2
      exit 64
      ;;
  esac
  ID_BIN=/usr/bin/id
  ENV_FILE=/etc/radon/env
  DATA_DIR=/home/radon/radon/data
  MEDIA_DIR=/var/lib/radon/media
  STATE_DIR=/var/lib/radon
  LEASE_DIR=/var/lib/radon/ib-lease
  CHOWN=/usr/bin/chown
  PYTHON=/usr/bin/python3
  GETENT=/usr/bin/getent
  NOTIFY_PROXY_DIR=/run/radon-app-runtime
  CONTROL_DIR=/run/radon-control
  DEPLOY_LOCK_FILE=/var/lib/radon-lock/deploy.lock
  LEGACY_DEPLOY_LOCK_FILE=/home/radon/.radon-deploy.lock
  # Root-only: /run is root-owned and this verb creates the directory 0755.
  PULL_LOCK_FILE=/run/radon-app-runtime/image-pull.lock
  PULL_LOCK_WAIT_SECS=300
  GREEN_MARKER_FILE=/home/radon/.radon-last-green-deploy
  TRANSITION_JOURNAL_FILE=/home/radon/.radon-deploy-transition.json
  # Root-owned control-plane copy of cloud/config/seccomp/chromium.json. Never
  # the checkout: radon can write that, and could widen its own filter.
  CHROMIUM_SECCOMP_PROFILE=/etc/radon/seccomp/chromium.json
  # ai-cycle collector state (raw archive, checkpoints, budgets). Never the
  # rest of ~/.radon, which holds an unrelated legacy store and its key.
  AI_CYCLE_DIR=/home/radon/.radon/ai-cycle
  # Broker socket directory; /run is root-owned, so the anchor is trusted.
  SUBSCRIPTION_VAULT_DIR=/run/radon-subscription-vault
  MODEL_GATEWAY_DIR=/run/radon-model-gateway
  MODEL_CONTROL_DIR=/run/radon-model-control
  MODEL_TRANSPORT_HELPER=/usr/local/lib/radon/model_transport.py
  SUBSCRIPTION_STATE_DIR=/var/lib/radon/subscription-tokens
fi
readonly AI_CYCLE_DIR_IN_CONTAINER=/home/radon/.radon/ai-cycle
readonly SUBSCRIPTION_VAULT_DIR_IN_CONTAINER=/run/radon-subscription-vault
readonly MODEL_GATEWAY_DIR_IN_CONTAINER=/run/radon-model-gateway
readonly MODEL_CONTROL_DIR_IN_CONTAINER=/run/radon-model-control

readonly SECRET_STORE_CREDENTIAL_NAME=radon-secret-store-key
# R-619. The container runs --user radon, so a staged key owned by uid radon
# is readable by every other thing that account can start. The plaintext key
# is therefore handed over through a group the radon account is NOT in:
# root:radon-secrets 0040, and the container gets the gid at start via
# --group-add. Only root can grant that group, and only for this container.
readonly SECRET_STORE_CREDENTIAL_GROUP=radon-secrets
readonly SECRET_STORE_DB_CONTAINER_PATH=/home/radon/radon/data/secret_store/secrets.db
readonly SECRET_STORE_CREDENTIAL_STAGE_ROOT="${NOTIFY_PROXY_DIR}/credentials"
STAGED_CREDENTIAL_UNIT=""

usage() {
  echo "usage: radon-app-runtime {pull [<sha>]|prune|prune-locked [<sha>]|check-model-space|install-model-clients|recover-model-clients|commit-model-clients|run <unit>|halt <unit> <grace-seconds>|stop <unit>|notify-proxy <listen> <upstream>}" >&2
  exit 64
}

in_list() {
  local candidate="$1" item
  for item in $2; do
    [[ "$candidate" == "$item" ]] && return 0
  done
  return 1
}

uses_secret_store() { in_list "$1" "$SECRET_STORE_UNITS"; }
is_store_job() { in_list "$1" "$STORE_JOB_UNITS"; }
is_model_gateway() { [[ "$1" == radon-model-gateway.service ]]; }
# PDF archiving and the master-key broker have no inference capability.
# Every other app container uses the same private service interface.
is_model_consumer() { ! is_model_gateway "$1" && [[ "$1" != radon-marketdesk-pdf.service && "$1" != radon-subscription-vault.service ]]; }
# Units that own the private research bind read-write and nothing else.
is_research_unit() { [[ "$1" == "radon-research.service" || "$1" == "radon-research-archive.service" || "$1" == "radon-research-asset-archive.service" || "$1" == "radon-marketdesk.service" || "$1" == "radon-marketdesk-pdf.service" ]]; }
# Units whose Chromium renders third-party web content. Each gets an isolated
# bridge network, the vendored seccomp profile, a sized /dev/shm, an env
# allowlist and no writable host-shared credential grant.
is_chromium_unit() { [[ "$1" == "radon-newsfeed.service" || "$1" == "radon-marketdesk.service" ]]; }

cleanup_runtime_credential() {
  local unit="$1"
  local credential_dir="${SECRET_STORE_CREDENTIAL_STAGE_ROOT}/${unit}"
  local credential_file="${credential_dir}/${SECRET_STORE_CREDENTIAL_NAME}"
  uses_secret_store "$unit" || return 0
  if [[ -d "$credential_dir" ]]; then
    chmod 0700 "$credential_dir" 2>/dev/null || true
    rm -f "$credential_file"
    rmdir "$credential_dir" 2>/dev/null || true
  fi
  if [[ "$STAGED_CREDENTIAL_UNIT" == "$unit" ]]; then
    STAGED_CREDENTIAL_UNIT=""
  fi
  return 0
}

cleanup_staged_credential_on_exit() {
  [[ -n "$STAGED_CREDENTIAL_UNIT" ]] || return 0
  cleanup_runtime_credential "$STAGED_CREDENTIAL_UNIT"
}

# The gid the plaintext key is handed to. Absent group, or a radon account that
# has been added to it, means the delivery channel is open to the account the
# container runs as -- refuse to start rather than stage a readable key.
credential_group_gid() {
  local entry gid
  entry="$("$GETENT" group "$SECRET_STORE_CREDENTIAL_GROUP" 2>/dev/null)" || entry=""
  gid="$(printf '%s' "$entry" | cut -d: -f3)"
  [[ "$gid" =~ ^[0-9]+$ ]] || {
    echo "radon-app-runtime: group ${SECRET_STORE_CREDENTIAL_GROUP} does not exist" >&2
    return 78
  }
  printf '%s\n' "$gid"
}

assert_radon_cannot_open_credential_group() {
  local groups
  groups="$("$ID_BIN" -nG radon 2>/dev/null)" || {
    echo "radon-app-runtime: cannot verify radon credential-group isolation" >&2
    return 78
  }
  [[ -n "$groups" ]] || return 78
  case " $groups " in
    *" ${SECRET_STORE_CREDENTIAL_GROUP} "*)
      echo "radon-app-runtime: radon must not be a member of ${SECRET_STORE_CREDENTIAL_GROUP}" >&2
      return 78
      ;;
  esac
  return 0
}

stage_secret_store_credential() {
  local unit="$1" gid="$2"
  local source credential_dir credential_file staged_size
  validate_api_startup_inputs
  source="${CREDENTIALS_DIRECTORY}/${SECRET_STORE_CREDENTIAL_NAME}"

  credential_dir="${SECRET_STORE_CREDENTIAL_STAGE_ROOT}/${unit}"
  credential_file="${credential_dir}/${SECRET_STORE_CREDENTIAL_NAME}"
  cleanup_runtime_credential "$unit"
  STAGED_CREDENTIAL_UNIT="$unit"
  install -d -m 0700 "$SECRET_STORE_CREDENTIAL_STAGE_ROOT" "$credential_dir"
  install -m 0400 "$source" "$credential_file"
  staged_size="$(wc -c < "$credential_file" | tr -d '[:space:]')"
  [[ "$staged_size" == "32" ]] || {
    echo "radon-app-runtime: staged ${SECRET_STORE_CREDENTIAL_NAME} must be exactly 32 bytes" >&2
    return 78
  }
  "$CHOWN" "root:${gid}" "$credential_dir" "$credential_file"
  chmod 0040 "$credential_file"
  chmod 0050 "$credential_dir"
}

validate_api_startup_inputs() {
  local credentials_dir="${CREDENTIALS_DIRECTORY:-}"
  local source source_size configured_db_path
  configured_db_path="${RADON_SECRET_STORE_PATH:-$SECRET_STORE_DB_CONTAINER_PATH}"
  [[ "$configured_db_path" == "$SECRET_STORE_DB_CONTAINER_PATH" ]] || {
    echo "radon-app-runtime: RADON_SECRET_STORE_PATH must be ${SECRET_STORE_DB_CONTAINER_PATH}" >&2
    return 78
  }
  [[ "$credentials_dir" == /* ]] || {
    echo "radon-app-runtime: CREDENTIALS_DIRECTORY must be an absolute path" >&2
    return 78
  }
  source="${credentials_dir}/${SECRET_STORE_CREDENTIAL_NAME}"
  if [[ -L "$source" || ! -f "$source" || ! -r "$source" ]]; then
    echo "radon-app-runtime: ${SECRET_STORE_CREDENTIAL_NAME} must be a readable, regular, non-symlink file" >&2
    return 78
  fi
  source_size="$(wc -c < "$source" | tr -d '[:space:]')"
  [[ "$source_size" == "32" ]] || {
    echo "radon-app-runtime: ${SECRET_STORE_CREDENTIAL_NAME} must be exactly 32 bytes" >&2
    return 78
  }
}

trap cleanup_staged_credential_on_exit EXIT

image_tag() {
  local tag
  if [[ -n "${RADON_APP_IMAGE_TAG:-}" ]]; then
    tag="$RADON_APP_IMAGE_TAG"
  elif [[ -d /home/radon/radon/.git ]]; then
    tag="$(/usr/bin/git -C /home/radon/radon rev-parse HEAD)"
  else
    echo "radon-app-runtime: exact release SHA is unavailable" >&2
    return 69
  fi
  [[ "$tag" =~ ^[0-9a-f]{40}$ ]] || {
    echo "radon-app-runtime: image tag must be a 40-hex release SHA" >&2
    return 69
  }
  printf '%s\n' "$tag"
}

image_in_registry() {
  "$DOCKER" manifest inspect "$1" >/dev/null 2>&1
}

image_in_local_store() {
  "$DOCKER" image inspect "$1" >/dev/null 2>&1
}

# The deploy pre-pull must detect a rebuilt tag even when its old image is local.
# Runtime starts retain image_available's offline fallback.
image_matches_registry() {
  # `buildx imagetools` is docker-only; podman re-pulls, which is layer-incremental.
  [[ "$ENGINE" == podman ]] && return 1
  "$PYTHON" -I - "$DOCKER" "$1" <<'PYCODE'
import json, re, subprocess, sys
docker, image = sys.argv[1:]
def read(args):
    result = subprocess.run([docker, *args], capture_output=True, text=True,
                            check=True, timeout=20)
    return json.loads(result.stdout)
try:
    remote = read(["buildx", "imagetools", "inspect", image, "--format", "{{json .Manifest}}"])
    local = read(["image", "inspect", image, "--format", "{{json .RepoDigests}}"])
except (OSError, ValueError, subprocess.SubprocessError):
    sys.exit(1)
digest = remote.get("digest") if isinstance(remote, dict) else None
if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
    sys.exit(1)
expected = image.rsplit(":", 1)[0] + "@" + digest
sys.exit(0 if isinstance(local, list) and expected in local else 1)
PYCODE
}

# A registry probe alone is not a liveness contract. A GHCR 429, an outage or
# an expired root credential fails BOTH manifest probes under `set -euo
# pipefail`, resolve_image returns 69 and every ExecStart exits — while the
# correct image is already pulled. With Restart=always + StartLimitBurst=5
# that parks radon-api, radon-nextjs, radon-relay, radon-monitor and
# radon-newsfeed start-limit-hit on a registry outage alone. T-198.
image_available() {
  if image_in_registry "$1"; then
    return 0
  fi
  if image_in_local_store "$1"; then
    printf 'image %s is not reachable in the registry; using the local store copy\n' "$1" >&2
    return 0
  fi
  return 1
}

# Resolve a repo to the exact tested release. A moving tag can combine code
# from a later push with an earlier deploy and is never a safe fallback.
resolve_image() {
  local repo="$1"
  local tag pinned
  tag="$(image_tag)" || return $?
  pinned="${repo}:${tag}"
  if image_available "$pinned"; then
    printf '%s\n' "$pinned"
    return 0
  fi
  printf 'exact release image %s is unavailable\n' "$pinned" >&2
  return 69
}

python_image() {
  resolve_image 'ghcr.io/joemccann/radon-python'
}

node_image() {
  resolve_image 'ghcr.io/joemccann/radon-node'
}

model_gateway_image() {
  resolve_image 'ghcr.io/joemccann/radon-model-gateway'
}

is_app_unit() { in_list "$1" "$APP_UNITS"; }

refuse_host_plane() {
  local unit="$1"
  case "$unit" in
    *ib-gateway*|radon-health.service|caddy.service|caddy)
      echo "radon-app-runtime refuses host/broker units: ${unit}" >&2
      exit 64
      ;;
  esac
}

# `pull <sha>` is the deploy's pre-teardown step (R-431): it pulls exactly the
# pair `run` will resolve for that release while the current release still
# serves, then drops SHA-tagged pairs that are neither the target, the
# durable rollback SHA, nor in use by a running app container. Every deploy since
# the drop-ins went live had pulled a 4.8G node image AFTER teardown and
# failed the ~60s HTTP gate on a container still downloading (2026-08-28
# 4b332fd8 was the last green deploy; 33265501795 and 33266517375 rolled back
# mid-pull). Untagged SHA pairs at ~5.8G each also filled the 75G disk.
#
# Every pull holds a SHARED lock on PULL_LOCK_FILE (fd 9) from before the
# first layer fetch until the verb exits, so concurrent pulls never wait on
# each other. Every prune takes it EXCLUSIVE without blocking and skips while
# any other pull holds it: the CI prepull runs without the deploy lock, and a
# post-green prune from the previous run's deploy could otherwise delete the
# tag it just pulled. A pull waits at most PULL_LOCK_WAIT_SECS for a running
# prune and never waits on the deploy lock, so the two cannot deadlock.
acquire_pull_lock() {
  mkdir -p -m 0755 "${PULL_LOCK_FILE%/*}" || {
    echo "radon-app-runtime: pull lock directory is unavailable" >&2
    return 71
  }
  if [[ -L "$PULL_LOCK_FILE" ]] || { [[ -e "$PULL_LOCK_FILE" ]] && [[ ! -f "$PULL_LOCK_FILE" ]]; }; then
    echo "radon-app-runtime: pull lock is not a regular file" >&2
    return 71
  fi
  exec 9<>"$PULL_LOCK_FILE" || return 71
  "$PYTHON" -I - "$PULL_LOCK_FILE" "$PULL_LOCK_WAIT_SECS" <<'PYCODE'
import fcntl, os, stat, sys, time
path, wait = sys.argv[1], float(sys.argv[2])
opened, named = os.fstat(9), os.lstat(path)
if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode) \
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
    print("radon-app-runtime: pull lock is not a regular file", file=sys.stderr)
    raise SystemExit(71)
deadline = time.monotonic() + wait
while True:
    try:
        fcntl.flock(9, fcntl.LOCK_SH | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.monotonic() >= deadline:
            print("radon-app-runtime: an image prune still holds the pull lock", file=sys.stderr)
            raise SystemExit(75)
        time.sleep(0.2)
PYCODE
}

cmd_capture_model_pacing() {
  local ids cache
  ids="$("$ID_BIN" -u radon):$("$ID_BIN" -g radon)"
  cache="$(prepare_research_dir "$ids" model-gateway)" || return $?
  prepare_private_dir "$ids" "$cache/pacing-imports" "model pacing imports"
  "$PYTHON" -I /usr/local/lib/radon/model_pacing.py "$DOCKER"
  if [[ -n "$LEGACY_DOCKER" && "$LEGACY_DOCKER" != "$DOCKER" ]]; then
    "$PYTHON" -I /usr/local/lib/radon/model_pacing.py "$LEGACY_DOCKER"
  fi
}

cmd_check_model_space() {
  # Read-only fixed-filesystem admission; accepts no destination or threshold.
  "$PYTHON" -I /usr/local/lib/radon/model_clients.py check-space
}

cmd_install_model_clients() {
  # Fixed destination/export policy lives in the root-owned control plane.
  # No caller-provided path, executable, registry or moving image tag.
  local tag image
  tag="$(image_tag)" || return $?
  image="$(model_gateway_image)" || return $?
  "$PYTHON" -I /usr/local/lib/radon/model_clients.py "$DOCKER" "$image" "$tag"
}

cmd_recover_model_clients() {
  local tag
  tag="$(image_tag)" || return $?
  "$PYTHON" -I /usr/local/lib/radon/model_clients.py recover "$tag"
}

cmd_commit_model_clients() {
  local tag
  tag="$(image_tag)" || return $?
  "$PYTHON" -I /usr/local/lib/radon/model_clients.py commit "$tag"
}

cmd_pull() {
  local target="${1:-}" tag python_ref node_ref gateway_ref python_pid node_pid gateway_pid
  local status=0
  if [[ -n "$target" ]]; then
    [[ "$target" =~ ^[0-9a-f]{40}$ ]] || {
      echo "radon-app-runtime: pull takes a 40-hex release SHA" >&2
      exit 64
    }
    tag="$target"
  else
    tag="$(image_tag)" || return $?
  fi
  python_ref="ghcr.io/joemccann/radon-python:${tag}"
  node_ref="ghcr.io/joemccann/radon-node:${tag}"
  gateway_ref="ghcr.io/joemccann/radon-model-gateway:${tag}"
  # Prune BEFORE fetching layers. The prepull job used to pull a whole new set
  # into a full disk and prune only afterwards (2026-10-31 Caddy ENOSPC). This
  # prune takes the pull lock itself, so it runs before this pull's shared hold.
  if [[ -n "$target" ]]; then
    mkdir -p -m 0755 "${PULL_LOCK_FILE%/*}" 2>/dev/null || true
    prune_stale_app_images "$target" 0 || true
  fi
  acquire_pull_lock || return $?

  # The gated prepull job and the deploy both call this exact verb. When the
  # pair is local and matches registry digests, no layers need pulling.
  # Missing, changed, or unverified metadata requires a fresh parallel pull.
  if [[ -n "$target" ]] \
    && image_in_local_store "$python_ref" \
    && image_in_local_store "$node_ref" \
    && image_in_local_store "$gateway_ref" \
    && image_matches_registry "$python_ref" \
    && image_matches_registry "$node_ref" \
    && image_matches_registry "$gateway_ref"; then
    printf 'exact release image set already local: %s\n' "$tag" >&2
  else
    "$DOCKER" pull "$python_ref" 9>&- &
    python_pid=$!
    "$DOCKER" pull "$node_ref" 9>&- &
    node_pid=$!
    "$DOCKER" pull "$gateway_ref" 9>&- &
    gateway_pid=$!
    if ! wait "$python_pid"; then
      status=1
    fi
    if ! wait "$node_pid"; then
      status=1
    fi
    if ! wait "$gateway_pid"; then
      status=1
    fi
    if (( status != 0 )); then
      echo "radon-app-runtime: exact release image pull failed" >&2
      return 69
    fi
    if ! image_in_local_store "$python_ref" || ! image_in_local_store "$node_ref" || ! image_in_local_store "$gateway_ref"; then
      echo "radon-app-runtime: exact release image set is incomplete after pull" >&2
      return 69
    fi
  fi
  return 0
}

# `prune` (no argument) is the post-deploy cleanup. deploy.sh calls it after
# the supervisor closes the lock fd, on success and on failure, never when
# RADON_DEPLOY_STAGE=1. `pull <sha>` prunes before it fetches: from deploy.sh
# that prune skips (the deploy lock is held). The CI prepull job runs `pull`
# without the lock, so its prune runs and protects the target SHA plus green
# line 1, not line 2. Every prune skips while another process holds the pull
# lock (see acquire_pull_lock).
cmd_prune() {
  mkdir -p -m 0755 "${PULL_LOCK_FILE%/*}" 2>/dev/null || true
  prune_stale_app_images "" 0
}

# `prune-locked [<sha>]` runs INSIDE a deploy-lock holder: deploy.sh's
# supervisor before the inner deploy pulls or admits anything, and root's
# weekly disk_cleanup. The caller hands over its own held deploy-lock
# descriptor as stdin; a second open of the lock would conflict with that hold
# and skip every time (#1012). It keeps the target, green lines 1 and 2 (or
# both journal SHAs) and any image a running container uses.
cmd_prune_locked() {
  local target="${1:-}"
  if [[ -n "$target" && ! "$target" =~ ^[0-9a-f]{40}$ ]]; then
    echo "radon-app-runtime: prune-locked takes a 40-hex release SHA" >&2
    exit 64
  fi
  mkdir -p -m 0755 "${PULL_LOCK_FILE%/*}" 2>/dev/null || true
  # The pruner's own script arrives on stdin; the lock descriptor moves to 7.
  exec 7<&0
  prune_stale_app_images "$target" 1
}

# $2 is 1 when fd 7 is the caller's held deploy-lock descriptor.
prune_stale_app_images() {
  "$PYTHON" -I - "$DOCKER" "$1" "$DEPLOY_LOCK_FILE" "$GREEN_MARKER_FILE" "$TRANSITION_JOURNAL_FILE" "$PULL_LOCK_FILE" "$2" "$LEGACY_DEPLOY_LOCK_FILE" <<'PYCODE'
import fcntl, json, os, re, stat, subprocess, sys
from pathlib import Path

docker, target, lock_path, green_path, journal_path, pull_lock_path, held_deploy, legacy_lock_path = sys.argv[1:]
def skip(reason):
    print(f"radon-app-runtime: image prune skipped: {reason}", file=sys.stderr)
    raise SystemExit(0)
def command(*args):
    return subprocess.run([docker, *args], capture_output=True, text=True,
                          check=True, timeout=20).stdout.splitlines()
def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)

# Exclusive and non-blocking: another pull's shared hold means a tag may be
# mid-pull or unverified. It never waits, so it cannot deadlock.
try:
    pull_fd = os.open(pull_lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    if not stat.S_ISREG(os.fstat(pull_fd).st_mode):
        skip("pull lock is not a regular file")
    fcntl.flock(pull_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    skip("app image pull in progress")
except OSError as exc:
    skip(f"pull lock unavailable ({type(exc).__name__})")

try:
    if held_deploy == "1":
        # fd 7 is the caller's own open of the deploy lock. Same inode, and our
        # flock on that shared descriptor succeeds only if the caller holds it
        # (or nobody does); a lock held through any other open refuses.
        try:
            held, named = os.fstat(7), os.lstat(lock_path)
        except OSError:
            skip("stdin is not the held deploy lock")
        if not stat.S_ISREG(named.st_mode) or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
            skip("stdin is not the held deploy lock")
        try:
            fcntl.flock(7, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            skip("deploy lock is held by another process")
    else:
        # Do not create a root-owned lock that would prevent radon from deploying.
        lock = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            skip("deploy lock is not a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if legacy_lock_path and legacy_lock_path != lock_path:
            legacy_fd = os.open(legacy_lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            held, named = os.fstat(legacy_fd), os.lstat(legacy_lock_path)
            if not stat.S_ISREG(named.st_mode) or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino):
                skip("compatibility deploy lock changed")
            fcntl.flock(legacy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    protected = {target} if target else set()
    journal = Path(journal_path)
    if journal.exists() or journal.is_symlink():
        state = json.loads(journal.read_text())
        if not isinstance(state, dict) or state.get("version") != 1 or not all(
            sha(state.get(key)) for key in ("requested_sha", "previous_sha")
        ):
            skip("invalid transition journal")
        protected.update((state["requested_sha"], state["previous_sha"]))
    else:
        green = Path(green_path).read_text().splitlines()
        if not green or not sha(green[0]):
            skip("invalid green marker")
        protected.add(green[0])
        if not target:
            # Line 1 is the running release; line 2 is the rollback pair.
            if len(green) < 2 or not sha(green[1]):
                skip("green marker records no previous release")
            protected.add(green[1])
        elif held_deploy == "1" and len(green) > 1 and sha(green[1]):
            # A deploy keeps its rollback pair while it pulls the target.
            protected.add(green[1])
    repos = ("ghcr.io/joemccann/radon-node", "ghcr.io/joemccann/radon-python", "ghcr.io/joemccann/radon-model-gateway")
    in_use = {image for image in command("ps", "--format", "{{.Image}}", "--filter", "name=radon-")
              if any(image.startswith(repo + ":") or image.startswith(repo + "@") for repo in repos)}
    if not in_use:
        skip("no running app containers")
    stale = []
    for repo in repos:
        for image in command("images", "--format", "{{.Repository}}:{{.Tag}}", repo):
            prefix, separator, tag = image.rpartition(":")
            if prefix == repo and sha(tag) and tag not in protected and image not in in_use:
                stale.append(image)
    # One rmi per tag: an image carrying several stale tags is deleted when
    # its last tag goes. A refused rmi (image still referenced) keeps the
    # rest of the sweep going.
    for image in stale:
        try:
            command("rmi", image)
        except subprocess.SubprocessError as exc:
            print(f"radon-app-runtime: rmi {image} failed ({type(exc).__name__})", file=sys.stderr)
except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
    skip(f"deploy lock or rollback state unavailable ({type(exc).__name__})")
PYCODE
}

# `rm -f` by --name, on the active engine and, under podman, on docker too so
# a docker-era container never shares data/ with its podman successor.
reap_container() {
  local unit="$1" engine
  for engine in "$DOCKER" ${LEGACY_DOCKER:+"$LEGACY_DOCKER"}; do
    if ! "$engine" rm -f "$unit" >/dev/null 2>&1; then
      if "$engine" inspect "$unit" >/dev/null 2>&1; then
        echo "radon-app-runtime: ${engine##*/} rm -f ${unit} failed and the orphan container is still present; refusing to clear its staged credential" >&2
        exit 75
      fi
    fi
  done
}

cmd_stop() {
  local unit="${1:-}"
  [[ -n "$unit" ]] || usage
  refuse_host_plane "$unit"
  is_app_unit "$unit" || exit 64
  # Idempotent: ExecStopPost runs on every stop, including ones where the
  # container already exited on its own. R-232.
  # R-628: `rm -f` failing is exactly the orphan case documented above, and
  # the cleanup below then deletes the staged key out from under a container
  # that is still running and still serving — its CREDENTIALS_DIRECTORY is a
  # read-only bind of that directory, so the next in-container secret_store
  # open fails on a missing key instead of a clear name conflict. A non-zero
  # rm for a container that does NOT exist is the ordinary first-start case
  # and stays benign; only a SURVIVING container aborts.
  reap_container "$unit"
  cleanup_runtime_credential "$unit"
}

# ExecStop. systemd's own stop signals only the foreground `podman run`
# client, which proxies SIGTERM into the container. On 2026-09-23..25 that
# proxy intermittently never delivered it: the app logged no SIGTERM, kept
# working, and ran until the 90s SIGKILL, past the deploy helper's 60s
# inactive wait, so six deploys rolled back. Ask the engine to stop the
# container by name instead: SIGTERM to its init, SIGKILL after <grace>.
# systemd also runs ExecStop after the main process exited on its own, so a
# missing container is success. Only a container that survives is an error.
cmd_halt() {
  local unit="${1:-}" grace="${2:-}"
  [[ -n "$unit" && "$grace" =~ ^[0-9]+$ ]] || usage
  refuse_host_plane "$unit"
  is_app_unit "$unit" || exit 64
  if [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" != 1 ]]; then
    local ids cache
    ids="$("$ID_BIN" -u radon):$("$ID_BIN" -g radon)"
    cache="$(prepare_research_dir "$ids" model-gateway)" || return $?
    prepare_private_dir "$ids" "$cache/pacing-imports" "model pacing imports"
    case " $APP_UNITS " in
      *" $unit "*)
        # Recheck account clocks immediately before stop removes the last
        # chance to read an ephemeral source. The global capture already
        # prepared the private import directory and captured host state.
        "$PYTHON" -I /usr/local/lib/radon/model_pacing.py "$DOCKER" "$unit"
        if [[ -n "$LEGACY_DOCKER" && "$LEGACY_DOCKER" != "$DOCKER" ]]; then
          "$PYTHON" -I /usr/local/lib/radon/model_pacing.py "$LEGACY_DOCKER" "$unit"
        fi
        ;;
    esac
  fi
  "$DOCKER" container inspect "$unit" >/dev/null 2>&1 || return 0
  "$DOCKER" stop --time "$grace" "$unit" >/dev/null && return 0
  if "$DOCKER" container inspect "$unit" >/dev/null 2>&1; then
    echo "radon-app-runtime: ${DOCKER##*/} stop ${unit} failed and the container is still present" >&2
    exit 75
  fi
}


# systemd accepts sd_notify datagrams only from PIDs inside the unit's cgroup.
# The container's PIDs live in system.slice/docker-<id>.scope (see the
# --cgroup-parent note in cmd_run), so a READY=1 sent from inside the
# container straight to $NOTIFY_SOCKET is silently dropped even with
# NotifyAccess=all: under Type=notify the relay hit "start operation timed
# out" twice on 2026-08-29 and the drop-in was hot-patched to Type=simple,
# which then failed every deploy's control-plane preflight. This forwarder
# is a child of the ExecStart process, so it IS in the cgroup. It owns the
# socket the container sees and relays permitted notices to systemd. R-429.
cmd_notify_proxy() {
  local listen="${1:-}" upstream="${2:-}"
  [[ -n "$listen" && -n "$upstream" ]] || usage
  exec "$PYTHON" -I - "$listen" "$upstream" <<'PY'
import os, socket, sys
listen, upstream = sys.argv[1], sys.argv[2]
try:
    os.unlink(listen)
except FileNotFoundError:
    pass
inbound = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
# Bind under a restrictive umask. chmod after bind leaves a window where the
# socket exists as 0755 and the mode assertion can observe it.
previous_umask = os.umask(0o177)
try:
    inbound.bind(listen)
finally:
    os.umask(previous_umask)
# NotifyAccess=all makes systemd trust whatever this proxy relays, so the
# socket is owner-only; start_notify_proxy chowns it to the container uid.
os.chmod(listen, 0o600)
outbound = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
# Exit with the ExecStart process (the docker client, which exec'd over the
# bash parent), so a stopped unit never leaves a forwarder holding fds.
parent = os.getppid()
inbound.settimeout(0.25)
while True:
    try:
        data = inbound.recv(65536)
    except TimeoutError:
        if os.getppid() != parent:
            raise SystemExit(0)
        continue
    # REL-158 / R-439: the proxy PID is trusted by systemd, the container
    # is not. Never forward MAINPID, lifecycle or timeout-control fields.
    data = b"\n".join(line for line in data.splitlines()
                      if line in (b"READY=1", b"WATCHDOG=1")
                      or line.startswith(b"STATUS="))
    if not data:
        continue
    try:
        outbound.sendto(data, upstream)
    except OSError as exc:
        print(f"notify proxy: forward failed: {exc}", file=sys.stderr)
PY
}

# Starts the forwarder for one unit and sets NOTIFY_PROXY_SOCKET to the path
# the container must use as NOTIFY_SOCKET. Refuses to launch the container if
# the socket never appears: without it a Type=notify unit can only time out.
# Must run in THIS shell, never in a $(...) substitution: the forwarder exits
# when its parent changes, and a subshell parent is gone the moment it
# returns, which left a dead socket behind on the first live probe.
start_notify_proxy() {
  local unit="$1" upstream="$2" ids="$3" listen attempt
  listen="${NOTIFY_PROXY_DIR}/${unit}.sock"
  mkdir -p -m 0755 "$NOTIFY_PROXY_DIR" || {
    echo "radon-app-runtime: notify proxy dir is unavailable: ${NOTIFY_PROXY_DIR}" >&2
    return 71
  }
  rm -f "$listen"
  "$0" notify-proxy "$listen" "$upstream" &
  for attempt in $(seq 1 50); do
    if [[ -S "$listen" ]]; then
      # The proxy binds the socket 0600 as root; hand it to the container
      # uid so only that uid can write READY/WATCHDOG datagrams.
      "$CHOWN" -h "$ids" "$listen" || return 71
      NOTIFY_PROXY_SOCKET="$listen"
      return 0
    fi
    sleep 0.1
  done
  echo "radon-app-runtime: notify proxy for ${unit} did not bind ${listen}" >&2
  return 71
}

# docker --env-file takes each line VERBATIM: there is no shell quoting, so
# a quoted provider key reached the container WITH its quotes and xAI answered
# 400 "Incorrect API key" (2026-08-30; six secrets in /etc/radon/env are
# single-quoted because `set -a; . file` consumers need $-bearing values
# quoted, see CLAUDE.md). Strip one matching pair of surrounding quotes per
# line into a root-only copy under the runtime dir and hand docker that; the
# host file stays the secret of record and is never rewritten.
render_env_file() {
  local unit="$1" out="${NOTIFY_PROXY_DIR}/${1}.env" temporary status
  # REL-158 / R-438: substitutions do not inherit errexit. Check each
  # renderer explicitly, and publish only a complete root-only copy.
  mkdir -p "$NOTIFY_PROXY_DIR" || return 71
  temporary="$(mktemp "${out}.XXXXXX")" || return 71
  (
    umask 077
    sed -E \
      -e "s/^([A-Za-z_][A-Za-z0-9_]*=)'(.*)'[[:space:]]*\$/\1\2/" \
      -e 's/^([A-Za-z_][A-Za-z0-9_]*=)"(.*)"[[:space:]]*$/\1\2/' \
      -e '/^(ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_API_KEY|XAI_API_KEY|GROK_API_KEY|OPENAI_API_KEY|RADON_LADDER_ALLOW_PREPAID)=/d' \
      "$ENV_FILE" > "$temporary" || exit 71
    # ^ Subscriptions only (operator policy 2026-10-26): prepaid Anthropic,
    # Claude, xAI and OpenAI keys left in the host file never reach any
    # container, where an agent CLI would otherwise bill them. Scrub list
    # mirrors scripts/clients/prepaid_denylist.json.
    # The newsfeed's Chromium renders third-party web content (and
    # may fall back to --no-sandbox); hand that unit only the keys its own code reads,
    # never the full production secret set.
    if [[ "$unit" == "radon-newsfeed.service" ]]; then
      grep -E '^(#|$|(NODE_ENV|CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CONFIG_DIR|CODEX_HOME|GROK_AUTH_FILE|ANTIGRAVITY_CLI|ANTIGRAVITY_MODEL|RADON_LADDER_[A-Z0-9_]+|NVIDIA_API_KEY|NVIDIA_TEXT_MODEL|NVIDIA_VISION_MODEL|NVIDIA_MODEL|CEREBRAS_API_KEY|RADON_PYTHON_BIN|TURSO_DB_URL|TURSO_AUTH_TOKEN|PLAYWRIGHT_CHROMIUM_SANDBOX|RADON_DB_NO_REPLICA|RADON_DB_USE_REPLICA|RADON_MEDIA_LOCAL|RADON_MEDIA_REMOTE|RADON_NEWSFEED_[A-Z0-9_]+|THEMARKETEAR_EMAIL|THEMARKETEAR_PASSWORD|MARKETDESK_FIREBASE_REFRESH_TOKEN)=)' \
        "$temporary" > "${temporary}.filtered"
      status=$?
      # grep's 1 means a valid empty allowlist; 2 means a failed filter.
      [[ "$status" -le 1 ]] || exit 71
      mv "${temporary}.filtered" "$temporary" || exit 71
    fi
    if [[ "$unit" == "radon-uw-stream.service" || "$unit" == "radon-uw-research.service" ]]; then
      grep -E '^(#|$|(UW_TOKEN|RADON_UW_[A-Z0-9_]+|TURSO_DB_URL|TURSO_AUTH_TOKEN|RADON_DB_NO_REPLICA)=)' \
        "$temporary" > "${temporary}.filtered"
      status=$?
      [[ "$status" -le 1 ]] || exit 71
      mv "${temporary}.filtered" "$temporary" || exit 71
    fi
    # The marketdesk.ai ingest fetches third-party content over plain HTTP.
    # Turso, the model ladder, the marketdesk session seed and its fallback
    # login, and the WRITE-scoped Dropbox app only: never the read-only
    # research reader (DROPBOX_APP_KEY...), B2, IB or Clerk secrets.
    if [[ "$unit" == "radon-marketdesk.service" ]]; then
      grep -E '^(#|$|(CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CODE_OAUTH_TOKEN_FILE|CLAUDE_CONFIG_DIR|CODEX_HOME|GROK_AUTH_FILE|GROK_MODEL|XAI_MODEL|ANTIGRAVITY_CLI|ANTIGRAVITY_MODEL|RADON_LADDER_[A-Z0-9_]+|RADON_NVIDIA_LADDER_MAX_WAIT_S|RADON_NVIDIA_RPM|RADON_NVIDIA_TRIP_AFTER_429S|RADON_NVIDIA_TRIP_SECS|RADON_NVIDIA_AUTH_BLOCK_SECS|RADON_RESEARCH_MODEL|RADON_RESEARCH_PARSE_[A-Z0-9_]+|CLAIRIC_[A-Z0-9_]+|NVIDIA_API_KEY|NVIDIA_TEXT_MODEL|NVIDIA_VISION_MODEL|NVIDIA_MODEL|CEREBRAS_API_KEY|TURSO_DB_URL|TURSO_AUTH_TOKEN|RADON_DB_NO_REPLICA|RADON_DB_USE_REPLICA|THEMARKETEAR_EMAIL|THEMARKETEAR_PASSWORD|MARKETDESK_[A-Z0-9_]+|DROPBOX_WRITE_[A-Z0-9_]+)=)' \
        "$temporary" > "${temporary}.filtered"
      status=$?
      [[ "$status" -le 1 ]] || exit 71
      mv "${temporary}.filtered" "$temporary" || exit 71
    fi
    # The independent PDF archive needs only Turso and its provider login.
    # No model grants, Dropbox credentials, broker or secret-store master key.
    if [[ "$unit" == "radon-marketdesk-pdf.service" ]]; then
      grep -E '^(#|$|(TURSO_DB_URL|TURSO_AUTH_TOKEN|RADON_DB_NO_REPLICA|RADON_DB_USE_REPLICA|THEMARKETEAR_EMAIL|THEMARKETEAR_PASSWORD|MARKETDESK_FIREBASE_REFRESH_TOKEN|MARKETDESK_PDF_MIN_INTERVAL_SECONDS)=)' \
        "$temporary" > "${temporary}.filtered"
      status=$?
      [[ "$status" -le 1 ]] || exit 71
      mv "${temporary}.filtered" "$temporary" || exit 71
    fi
    if is_model_gateway "$unit"; then
      # The gateway never receives operator DB, broker, Clerk or source logins.
      grep -E '^(#|$|(CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CODE_OAUTH_TOKEN_FILE|CLAUDE_CONFIG_DIR|CODEX_HOME|GROK_AUTH_FILE|GROK_MODEL|XAI_MODEL|ANTIGRAVITY_CLI|ANTIGRAVITY_MODEL|RADON_LADDER_[A-Z0-9_]+|RADON_NVIDIA_LADDER_MAX_WAIT_S|RADON_NVIDIA_RPM|RADON_NVIDIA_TRIP_AFTER_429S|RADON_NVIDIA_TRIP_SECS|RADON_NVIDIA_AUTH_BLOCK_SECS|RADON_RESEARCH_MODEL|RADON_RESEARCH_PARSE_[A-Z0-9_]+|NVIDIA_API_KEY|NVIDIA_TEXT_MODEL|NVIDIA_VISION_MODEL|NVIDIA_MODEL|CEREBRAS_API_KEY|CLOUDFLARE_ACCOUNT_ID|CLOUDFLARE_API_TOKEN|CLAIRIC_[A-Z0-9_]+|TURSO_DB_URL|TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN|PUSHOVER_USER|PUSHOVER_TOKEN|RADON_MODEL_GATEWAY_[A-Z0-9_]+|RADON_MODEL_TUNNEL_UID|RADON_MODEL_TUNNEL_GID|RADON_MODEL_CLIENT_UIDS|RADON_SLM_TAGGER_[A-Z0-9_]+)=)' \
        "$temporary" > "${temporary}.filtered"
      status=$?
      [[ "$status" -le 1 ]] || exit 71
      sed 's/^TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN=/TURSO_AUTH_TOKEN=/' "${temporary}.filtered" > "$temporary" || exit 71
      rm -f "${temporary}.filtered"
    else
      # Provider credentials and direct-provider configuration terminate here.
      # Application/source/broker credentials keep their established scope.
      sed -E '/^(ANTHROPIC_[A-Z0-9_]+|CLAUDE_[A-Z0-9_]+|CODEX_[A-Z0-9_]+|GROK_[A-Z0-9_]+|XAI_[A-Z0-9_]+|OPENAI_[A-Z0-9_]+|ANTIGRAVITY_[A-Z0-9_]+|GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_API_KEY|GEMINI_API_KEY|NVIDIA_[A-Z0-9_]+|CEREBRAS_[A-Z0-9_]+|CURSOR_API_KEY|CLOUDFLARE_ACCOUNT_ID|CLOUDFLARE_API_TOKEN|TURSO_SUBSCRIPTION_KEEPER_AUTH_TOKEN)=/d' \
        "$temporary" > "${temporary}.filtered" || exit 71
      mv "${temporary}.filtered" "$temporary" || exit 71
    fi
  ) || { rm -f "$temporary" "${temporary}.filtered"; return 71; }
  mv "$temporary" "$out" || { rm -f "$temporary"; return 71; }
  printf '%s\n' "$out"
}

# Keep the root-owned mount anchor outside radon's writable state directory.
# Once its root-owned ancestor chain is verified, radon cannot exchange the
# child for a symlink between provisioning and the Docker bind operation.
prepare_research_dir() {
  local ids="$1" name="${2:-research}" anchor=/var/lib/radon-private
  [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" == "1" ]] && anchor="${STATE_DIR}/private"
  "$PYTHON" -I - "$anchor" "$ids" "${RADON_APP_RUNTIME_TEST_MODE:-0}" "$name" <<'PY_RESEARCH'
import os, stat, sys
from pathlib import Path
anchor = Path(sys.argv[1])
uid, gid = map(int, sys.argv[2].split(':'))
test = sys.argv[3] == '1'
owner = os.getuid() if test else 0
if test:
    uid, gid = os.getuid(), os.getgid()

def trusted(path, expected_owner, exact_mode=None):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_owner
            or info.st_mode & 0o022
            or (exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode)):
        raise ValueError('Untrusted private research directory')

try:
    # Test roots are isolated fixtures. Production checks every ancestor.
    parents = [anchor.parent] if test else list(reversed(anchor.parents))
    for parent in parents:
        trusted(parent, owner)
    try:
        anchor.mkdir(mode=0o700)
    except FileExistsError:
        pass
    trusted(anchor, owner, 0o700)
    name = sys.argv[4]
    if name not in {'research', 'model-gateway'}:
        raise ValueError('Invalid private directory')
    child = anchor / name
    try:
        child.mkdir(mode=0o700)
        os.chown(child, uid, gid, follow_symlinks=False)
    except FileExistsError:
        pass
    trusted(child, uid, 0o700)
except (OSError, ValueError):
    print('Private research directory ownership or permissions invalid', file=sys.stderr)
    raise SystemExit(78) from None
print(child)
PY_RESEARCH
}

# These directories live under radon-writable parents, so root must never
# follow a link while creating or owning them: mkdir refuses a symlink final
# component and the fd-based fchown/fchmod cannot be retargeted between check
# and use. Shared chokepoint for the secret store and the 2FA lease dir.
prepare_private_dir() {
  local ids="$1" dir="$2" label="$3" mode="${4:-700}"
  "$PYTHON" -I - "$dir" "$ids" "${RADON_APP_RUNTIME_TEST_MODE:-0}" "$label" "$mode" <<'PY_PRIVATE_DIR' || exit 78
import os, sys
from pathlib import Path
path, ids, test, label = sys.argv[1], sys.argv[2], sys.argv[3] == '1', sys.argv[4]
uid, gid = (os.getuid(), os.getgid()) if test else tuple(int(part) for part in ids.split(':'))
parent_fd = None
try:
    # Pin each ancestor; a host rename/link swap cannot redirect root's writes.
    parts = Path(path).parts
    if not parts or parts[0] != '/' or '..' in parts:
        raise OSError('directory must be absolute without traversal')
    flags = os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY
    parent_fd = os.open('/', flags)
    for part in parts[1:-1]:
        child_fd = os.open(part, flags, dir_fd=parent_fd)
        os.close(parent_fd)
        parent_fd = child_fd
    try:
        os.mkdir(parts[-1], 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(parts[-1], flags, dir_fd=parent_fd)
    try:
        os.fchown(fd, uid, gid)
        mode = int(sys.argv[5], 8)
        if mode not in {0o700, 0o710}:
            raise OSError('invalid private directory mode')
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
except OSError:
    print(f'radon-app-runtime: {label} directory is a symlink or unusable; refusing', file=sys.stderr)
    raise SystemExit(78)
finally:
    if parent_fd is not None:
        os.close(parent_fd)
PY_PRIVATE_DIR
}

# Container bind sources under radon-owned parents. The engine follows links
# when it binds, so every component must be a real directory (walked with
# O_NOFOLLOW from /) and the last must be radon-owned: a planted link would
# otherwise hand a root-only tree to the container. Prints the verified
# directory's device, inode, and inode generation. ext4 reissues a just-freed
# inode to the next mkdir of the same name, so device and inode alone treat
# that replacement as the directory that was checked.
resolve_bind_source() {
  local ids="$1" dir="$2"
  "$PYTHON" -I - "$dir" "$ids" "${RADON_APP_RUNTIME_TEST_MODE:-0}" <<'PY_BIND_SOURCE' || exit 78
import array, errno, fcntl, os, sys
from pathlib import Path
# _IOR('v', 1, long). The kernel stores i_generation as a 32-bit int.
FS_IOC_GETVERSION = 0x80087601
path, ids, test = sys.argv[1], sys.argv[2], sys.argv[3] == '1'
uid = os.getuid() if test else int(ids.split(':')[0])
fd = None
try:
    parts = Path(path).parts
    if not parts or parts[0] != '/' or '..' in parts:
        raise OSError('bind source must be absolute without traversal')
    flags = os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY
    fd = os.open('/', flags)
    for part in parts[1:]:
        child = os.open(part, flags, dir_fd=fd)
        os.close(fd)
        fd = child
    info = os.fstat(fd)
    if info.st_uid != uid:
        raise OSError('bind source has an unexpected owner')
    generation = array.array('I', [0])
    try:
        fcntl.ioctl(fd, FS_IOC_GETVERSION, generation, True)
    except OSError as exc:
        if exc.errno not in (errno.ENOTTY, errno.EOPNOTSUPP, errno.ENOSYS):
            print(f'radon-app-runtime: bind source generation is unreadable; refusing: {path}', file=sys.stderr)
            raise SystemExit(78) from exc
        generation = None
except OSError:
    print(f'radon-app-runtime: bind source is a symlink, under a symlink, or not radon-owned; refusing: {path}', file=sys.stderr)
    raise SystemExit(78)
finally:
    if fd is not None:
        os.close(fd)
if generation is None:
    print(f'{info.st_dev}:{info.st_ino}')
else:
    print(f'{info.st_dev}:{info.st_ino}:{generation[0]}')
PY_BIND_SOURCE
}

# Every bind source validated so far, as "<path>|<device>:<inode>[:<generation>]".
BIND_SOURCES=()

# Validating a name does not pin it. All of these leaves sit in a radon-owned
# parent, so radon may rename one after the check, and the engine resolves the
# source by NAME when it mounts -- the playbook's standing rule that root never
# acts by name inside a radon-owned directory. Record what was verified here
# and re-resolve it in recheck_bind_sources as the last act before the engine
# runs, so the long tail of the start path (credential staging, env rendering,
# the orphan reap, argument assembly) stops being usable swap window.
require_bind_source() {
  local ids="$1" dir="$2" identity
  identity="$(resolve_bind_source "$ids" "$dir")" || exit 78
  BIND_SOURCES+=("${dir}|${identity}")
}

# Re-resolve every validated bind source and require the SAME directory.
# Generation is part of that identity where the filesystem provides it, so a
# recreate that reuses the inode still mismatches. This narrows the
# check-then-use window to the engine's own mount, it does not close it: an
# inode-pinned staging mount under a root-owned anchor is a host topology
# change and an operator action.
recheck_bind_sources() {
  local ids="$1" entry path want got
  for entry in ${BIND_SOURCES[@]+"${BIND_SOURCES[@]}"}; do
    path="${entry%|*}"
    want="${entry##*|}"
    got="$(resolve_bind_source "$ids" "$path")" || exit 78
    if [[ "$got" != "$want" ]]; then
      echo "radon-app-runtime: bind source was replaced after validation; refusing: ${path}" >&2
      exit 78
    fi
  done
}

# DS-2026-09-24-01 residual: the engine loads this filter as root, and
# /etc/radon is radon-writable (root:radon 1770), so the profile's own
# directory is the untrusted component. A leaf -f/-L test follows a planted
# directory link, which is how radon could hand Chromium a filter it wrote
# itself. Check the last two components without following either, and require
# both to be owned by this user and writable by nobody else.
require_trusted_seccomp_profile() {
  # -I: the interpreter reads the program from stdin, so without isolation
  # sys.path[0] is the working directory and a planted module would load.
  "$PYTHON" -I - "$1" "${RADON_APP_RUNTIME_TEST_MODE:-0}" <<'PY_SECCOMP_PROFILE'
import os, stat, sys
from pathlib import Path

path, test = Path(sys.argv[1]), sys.argv[2] == '1'
expected_uid = os.getuid() if test else 0


def refuse(reason):
    print(f'radon-app-runtime: chromium seccomp profile {reason}: {path}', file=sys.stderr)
    raise SystemExit(78)


if not path.is_absolute() or '..' in path.parts:
    refuse('is not an absolute path without traversal')
grandparent_fd = dir_fd = None
try:
    try:
        grandparent_fd = os.open(str(path.parent.parent), os.O_DIRECTORY | os.O_RDONLY)
        dir_fd = os.open(
            path.parent.name,
            os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY,
            dir_fd=grandparent_fd,
        )
    except OSError:
        refuse('directory is missing or a symlink')
    dir_st = os.fstat(dir_fd)
    if dir_st.st_uid != expected_uid or dir_st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        refuse('directory is not owned by this user or is writable by others')
    try:
        fd = os.open(path.name, os.O_NOFOLLOW | os.O_RDONLY, dir_fd=dir_fd)
    except OSError:
        refuse('is missing or a symlink')
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISREG(st.st_mode):
        refuse('is not a regular file')
    if st.st_uid != expected_uid or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        refuse('is not owned by this user or is writable by others')
finally:
    for open_fd in (dir_fd, grandparent_fd):
        if open_fd is not None:
            os.close(open_fd)
PY_SECCOMP_PROFILE
}

migrate_model_cache() {
  local ids="$1" image="$2" cache="$3" model home="${RADON_SUBSCRIPTION_HOME:-/home/radon}"
  # Only model-specific subtrees are mounted. HF_HOME may contain access tokens.
  local chronos="${home}/.cache/huggingface/hub/models--amazon--chronos-2"
  local embedding="${home}/.cache/fastembed/models--qdrant--bge-small-en-v1.5-onnx-q"
  set -- "$DOCKER" run --rm --pull=never --network none --user "$ids" \
    --cap-drop ALL --security-opt no-new-privileges --entrypoint python \
    -v "${cache}:/cache:rw"
  local found=0
  for model in "$chronos" "$embedding"; do
    if [[ -e "$model" || -L "$model" ]]; then
      require_bind_source "$ids" "$model"
      if [[ "$model" == "$chronos" ]]; then
        set -- "$@" -v "${model}:/source/chronos:ro"
      else
        set -- "$@" -v "${model}:/source/embedding:ro"
      fi
      found=1
    fi
  done
  if [[ -d "$cache/pacing-imports" ]]; then
    require_bind_source "$ids" "$cache/pacing-imports"
    set -- "$@" -v "$cache/pacing-imports:/source/nvidia-pacing:ro"
    found=1
  fi
  (( found == 1 )) || return 0
  # All copying, digest checks and promotion run as the application UID in
  # the exact CI image. Host root neither imports nor executes app modules.
  # Preparatory Podman runs must not replace systemd's MAINPID with their
  # short-lived conmon. Keep the foreground service's notify proxy intact.
  /usr/bin/env -u NOTIFY_SOCKET "$@" "$image" -I /home/radon/radon/scripts/model_gateway/cache_migrate.py
}

cmd_run() {
  local unit="${1:-}"
  local ids image workdir rendered_env gateway_release=""
  [[ -n "$unit" ]] || usage
  refuse_host_plane "$unit"
  is_app_unit "$unit" || {
    echo "radon-app-runtime: unit is not an app-plane service: ${unit}" >&2
    exit 64
  }

  ids="$("$ID_BIN" -u radon):$("$ID_BIN" -g radon)"
  workdir=/home/radon/radon
  case "$unit" in
    radon-model-gateway.service)
      image="$(model_gateway_image)" || return $?
      [[ "$image" =~ ^ghcr\.io/joemccann/radon-model-gateway:([0-9a-f]{40})$ ]] || {
        echo "radon-app-runtime: gateway image must carry an exact release SHA" >&2
        return 69
      }
      gateway_release="${BASH_REMATCH[1]}"
      ;;
    radon-uw-stream.service|radon-uw-research.service|radon-api.service|radon-monitor.service|radon-research.service|radon-research-archive.service|radon-research-asset-archive.service|radon-marketdesk.service|radon-marketdesk-pdf.service) image="$(python_image)" ;;
    radon-subscription-vault.service|radon-ai-cycle.service|radon-ai-cycle-backfill.service|radon-aa-frontier-refresh.service)
      image="$(python_image)"
      ;;
    radon-nextjs.service)
      image="$(node_image)"
      workdir=/home/radon/radon/web
      ;;
    radon-relay.service|radon-newsfeed.service) image="$(node_image)" ;;
    *) exit 64 ;;
  esac

  # Fail closed: a Chromium unit never starts under the engine default
  # filter, which would kill Chromium's namespace sandbox at launch.
  if is_chromium_unit "$unit"; then
    require_trusted_seccomp_profile "$CHROMIUM_SECCOMP_PROFILE" || exit 78
  fi

  if ! is_research_unit "$unit" && ! is_store_job "$unit" && ! is_model_gateway "$unit"; then
    require_bind_source "$ids" "$DATA_DIR"
    require_bind_source "$ids" "$MEDIA_DIR"
  fi

  local credential_gid=""
  if uses_secret_store "$unit"; then
    validate_api_startup_inputs
    assert_radon_cannot_open_credential_group || exit $?
    credential_gid="$(credential_group_gid)" || exit $?
  fi

  # R-232, and its limit: the container's processes land in
  # system.slice/docker-<id>.scope, NOT the unit's cgroup, so systemd's
  # KillMode=control-group sweep and the post-TimeoutStopSec SIGKILL reach only
  # the `docker run` client and the container survives as an orphan holding
  # --name and both state bind mounts. Pointing --cgroup-parent at the unit is
  # NOT the fix: Docker's systemd cgroup driver accepts a slice, not a unit
  # path (asserted in cloud/tests/test_app_runtime.py). The reachable half is
  # reaping the orphan — here on the way in, and via ExecStopPost= in each
  # runtime-container.conf example on the way out. Without this the restart
  # fails on `Conflict. The container name "/<unit>" is already in use`, and
  # Restart=always + RestartSec=5 + StartLimitBurst=5 parks the unit
  # start-limit-hit inside 25s while the orphan keeps writing to data/.
  # R-628: `rm -f` failing is exactly the orphan case documented above, and
  # the cleanup below then deletes the staged key out from under a container
  # that is still running and still serving — its CREDENTIALS_DIRECTORY is a
  # read-only bind of that directory, so the next in-container secret_store
  # open fails on a missing key instead of a clear name conflict. A non-zero
  # rm for a container that does NOT exist is the ordinary first-start case
  # and stays benign; only a SURVIVING container aborts.
  reap_container "$unit"
  cleanup_runtime_credential "$unit"

  if uses_secret_store "$unit"; then
    prepare_private_dir "$ids" "${DATA_DIR}/secret_store" "secret store"
    stage_secret_store_credential "$unit" "$credential_gid"
  fi
  case "$unit" in
    radon-ai-cycle.service|radon-ai-cycle-backfill.service|radon-aa-frontier-refresh.service)
      prepare_private_dir "$ids" "$AI_CYCLE_DIR" "ai-cycle state"
      ;;
    radon-subscription-vault.service)
      prepare_private_dir "$ids" "$SUBSCRIPTION_VAULT_DIR" "subscription vault socket"
      ;;
  esac

  # The container gets NARROW binds, never $STATE_DIR itself. /var/lib/radon
  # holds control-plane-ready, the manifest digest and the root deploy
  # transaction journal; the container runs as uid radon and write permission on
  # the PARENT is all unlink/rename needs, so the whole-directory bind handed the
  # newsfeed's headless Chromium the ability to delete the readiness gate. The
  # one thing an app genuinely writes outside media/ is the shared 2FA lease,
  # which now has its own subdirectory. Create it here: the container can no
  # longer mkdir it, because the parent is not mounted. R-381.
  if ! is_research_unit "$unit" && ! is_store_job "$unit" && ! is_model_gateway "$unit" && [[ "$unit" != "radon-uw-stream.service" ]]; then
    prepare_private_dir "$ids" "$LEASE_DIR" "2FA lease"
  fi

  # Newsfeed renders third-party content in Chromium (sandbox fallback possible): it
  # gets an isolated bridge network (egress only), never the host stack.
  local container_network=host
  is_chromium_unit "$unit" && container_network=bridge
  [[ "$unit" == "radon-marketdesk-pdf.service" ]] && container_network=bridge

  rendered_env="$(render_env_file "$unit")" || exit 71
  set -- \
    run \
    --network "$container_network" \
    --user "$ids" \
    --rm \
    --name "$unit" \
    --init \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --cgroupns host \
    --env-file "$rendered_env" \
    --env RADON_DB_NO_REPLICA=1 \
    --env PYTHONPATH=/home/radon/radon/scripts \
    -w "$workdir"

  if is_model_gateway "$unit"; then
    # Bind status/activation identity to the image already selected above.
    # Caller environment, env-file values and a second HEAD lookup cannot
    # describe a different release from the bytes this process will execute.
    set -- "$@" --env "RADON_DEPLOY_SHA=${gateway_release}"
  fi

  if [[ "$ENGINE" == podman ]]; then
    # REL-087: conmon and the container join the unit's own cgroup (the unit
    # delegates it), so KillMode reaches them. Podman must not consume
    # NOTIFY_SOCKET itself: the notify proxy below stays the one path.
    set -- "$@" --cgroups=split --sdnotify=ignore
  else
    # Docker fallback: its systemd driver accepts a slice, not a unit path,
    # so the container is outside the unit and only reaping stops it.
    set -- "$@" --cgroup-parent=system.slice
  fi

  if is_model_gateway "$unit"; then
    local model_cache
    model_cache="$(prepare_research_dir "$ids" model-gateway)" || exit $?
    require_bind_source "$ids" "$model_cache"
    if [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" != 1 ]]; then
      migrate_model_cache "$ids" "$image" "$model_cache" || exit $?
    fi
    set -- "$@" -v "${model_cache}:/var/lib/radon/model-cache:rw" \
      --env HF_HOME=/var/lib/radon/model-cache/huggingface \
      --env FASTEMBED_CACHE_PATH=/var/lib/radon/model-cache/fastembed \
      --env RADON_NVIDIA_RATE_STATE=/var/lib/radon/model-cache/nvidia-rate.json \
      --env OMP_NUM_THREADS=2 --env TOKENIZERS_PARALLELISM=false
    prepare_private_dir "$ids" "$SUBSCRIPTION_VAULT_DIR" "subscription vault socket"
    prepare_private_dir "$ids" "$SUBSCRIPTION_STATE_DIR" "subscription refresh state"
    set -- "$@" \
      -v "${SUBSCRIPTION_VAULT_DIR}:${SUBSCRIPTION_VAULT_DIR_IN_CONTAINER}:rw" \
      -v "${SUBSCRIPTION_STATE_DIR}:/var/lib/radon/subscription-tokens:rw" \
      --env "RADON_SUBSCRIPTION_VAULT_SOCKET=${SUBSCRIPTION_VAULT_DIR_IN_CONTAINER}/vault.sock"
  elif is_store_job "$unit"; then
    # DS-2026-10-05-05: the store and nothing else of data/.
    set -- "$@" -v "${DATA_DIR}/secret_store:/home/radon/radon/data/secret_store"
    case "$unit" in
      radon-subscription-vault.service)
        set -- "$@" \
          -v "${SUBSCRIPTION_VAULT_DIR}:${SUBSCRIPTION_VAULT_DIR_IN_CONTAINER}" \
          --env "RADON_SUBSCRIPTION_VAULT_SOCKET=${SUBSCRIPTION_VAULT_DIR_IN_CONTAINER}/vault.sock"
        ;;
      *)
        set -- "$@" -v "${AI_CYCLE_DIR}:${AI_CYCLE_DIR_IN_CONTAINER}"
        ;;
    esac
  elif [[ "$unit" == "radon-uw-stream.service" ]]; then
    # Fully trusted app-plane process, like API. Shared data is required for
    # the SAME governor SQLite WAL/SHM and legacy atomic budget used by every
    # REST caller; never put reconciliation on a private ephemeral quota.
    # No media, broker control/lease or subscription grant mounts.
    prepare_private_dir "$ids" "${DATA_DIR}/uw_stream" "UW stream state"
    set -- "$@" -v "${DATA_DIR}:/home/radon/radon/data"
  elif ! is_research_unit "$unit"; then
    # DS-2026-10-17-20: media.radon.run serves this directory through Caddy
    # file_server, which follows symlinks, so write here lets a container have
    # the host Caddy process read a file the container cannot -- including
    # Caddy's own key material. The newsfeed downloads the images and is the
    # only writer; every other unit reads. The newsfeed's own write path needs
    # a publication-topology change and is an operator action.
    local media_mode=ro
    [[ "$unit" == radon-newsfeed.service ]] && media_mode=rw
    set -- "$@" \
      -v "${DATA_DIR}:/home/radon/radon/data" \
      -v "${MEDIA_DIR}:${MEDIA_DIR_IN_CONTAINER}:${media_mode}" \
      -v "${LEASE_DIR}:/var/lib/radon/ib-lease"
  fi

  # Only the gateway owns provider grants and executes image-pinned CLIs.
  # Consumers receive a read-only socket-directory bind, including bridge
  # isolated workers; inference never needs a public/host-network listener.
  if is_model_gateway "$unit" || is_model_consumer "$unit"; then
    # Only this narrow group can traverse inference for the model-only SSH UID.
    # Never grant it the vault or management directories.
    local tunnel_gid tunnel_uid client_uids inference_ids="$ids" inference_mode=700
    tunnel_gid="$(awk -F= '/^RADON_MODEL_TUNNEL_GID=/{v=$2} END{print v}' "$ENV_FILE")"
    tunnel_uid="$(awk -F= '/^RADON_MODEL_TUNNEL_UID=/{v=$2} END{print v}' "$ENV_FILE")"
    client_uids="$(awk -F= '/^RADON_MODEL_CLIENT_UIDS=/{v=$2} END{print v}' "$ENV_FILE")"
    if [[ -n "$tunnel_gid" || -n "$tunnel_uid" || -n "$client_uids" ]]; then
      [[ "$tunnel_gid" =~ ^[1-9][0-9]*$ && "$tunnel_gid" != "${ids##*:}" ]] || exit 78
      if [[ -n "$tunnel_uid" ]]; then
        [[ "$tunnel_uid" =~ ^[1-9][0-9]*$ && "$tunnel_uid" != "${ids%%:*}" ]] || exit 78
      fi
      if [[ -n "$client_uids" ]]; then
        [[ "$client_uids" =~ ^[1-9][0-9]*(,[1-9][0-9]*){0,15}$ ]] || exit 78
      fi
      inference_ids="${ids%%:*}:${tunnel_gid}"
      inference_mode=710
      is_model_gateway "$unit" && set -- "$@" --group-add "$tunnel_gid"
    fi
    prepare_private_dir "$inference_ids" "$MODEL_GATEWAY_DIR" "model gateway socket" "$inference_mode"
    if is_model_gateway "$unit" && [[ -n "$tunnel_uid" ]]; then
      "$PYTHON" -I "$MODEL_TRANSPORT_HELPER" bind-inference || exit 78
    fi
    local gateway_mode=ro
    is_model_gateway "$unit" && gateway_mode=rw
    set -- "$@" \
      -v "${MODEL_GATEWAY_DIR}:${MODEL_GATEWAY_DIR_IN_CONTAINER}:${gateway_mode}" \
      --env "RADON_MODEL_GATEWAY_SOCKET=${MODEL_GATEWAY_DIR_IN_CONTAINER}/gateway.sock"
    is_model_gateway "$unit" || set -- "$@" --env RADON_MODEL_GATEWAY_REQUIRED=1
  fi
  if is_model_gateway "$unit" || [[ "$unit" == radon-api.service ]]; then
    prepare_private_dir "$ids" "$MODEL_CONTROL_DIR" "model management socket"
    local management_mode=ro
    is_model_gateway "$unit" && management_mode=rw
    set -- "$@" -v "${MODEL_CONTROL_DIR}:${MODEL_CONTROL_DIR_IN_CONTAINER}:${management_mode}" \
      --env "RADON_MODEL_CONTROL_SOCKET=${MODEL_CONTROL_DIR_IN_CONTAINER}/control.sock"
  fi
  if is_model_gateway "$unit"; then
    local subscription_home="${RADON_SUBSCRIPTION_HOME:-/home/radon}" cred_dir
    for cred_dir in .grok .codex .claude .gemini/antigravity-cli .gemini/config; do
      if [[ -L "${subscription_home}/${cred_dir}" || -d "${subscription_home}/${cred_dir}" ]]; then
        require_bind_source "$ids" "${subscription_home}/${cred_dir}"
        set -- "$@" -v "${subscription_home}/${cred_dir}:/home/radon/${cred_dir}:rw"
      fi
    done
    # Provider CLIs are immutable image payloads, never host ~/.local/bin.
  fi
  set -- "$@" --env HOME=/home/radon
  if is_research_unit "$unit" || [[ "$unit" == "radon-api.service" ]]; then
    local research_dir research_mode=ro
    research_dir="$(prepare_research_dir "$ids")" || exit $?
    is_research_unit "$unit" && research_mode=rw
    set -- "$@" --env RADON_RESEARCH_DIR=/var/lib/radon/research \
      -v "${research_dir}:/var/lib/radon/research:${research_mode}"
  fi

  # App-role Gateway control: mTLS client pair lives on the host at
  # /etc/radon/ib-remote. Mount that directory only, never /etc/radon
  # (TWS secrets, Turso tokens). Read-only. Missing dir is combined/broker.
  if [[ "$unit" == "radon-api.service" ]]; then
    local ib_remote_certs="${RADON_IB_REMOTE_CERT_DIR:-/etc/radon/ib-remote}"
    if [[ -L "$ib_remote_certs" || -d "$ib_remote_certs" ]]; then
      require_bind_source "$ids" "$ib_remote_certs"
      set -- "$@" -v "${ib_remote_certs}:${ib_remote_certs}:ro"
    fi
    # Robinhood read-only MCP token store. Its own dir, never /etc/radon, and
    # read-write: refresh rotates the token by atomic replace plus a .lock
    # sidecar, which a single-file bind cannot do. Without it every Robinhood
    # rung inside the API fell through to UW (2026-09-19).
    local rh_token_dir="${RADON_RH_TOKEN_DIR:-/var/lib/radon/rh-mcp}"
    if [[ -L "$rh_token_dir" || -d "$rh_token_dir" ]]; then
      require_bind_source "$ids" "$rh_token_dir"
      set -- "$@" -v "${rh_token_dir}:${rh_token_dir}" \
        --env "ROBINHOOD_MCP_TOKEN_FILE=${rh_token_dir}/rh-mcp.json"
    fi
    # Host control socket: the API's only path to unit control (it has no
    # systemctl or sudo). Bind the DIRECTORY, not the socket, so a restart of
    # radon-control re-creating control.sock stays visible. Root pre-creates
    # it radon-owned 0700 (symlink-safe) so start order does not matter; the
    # unit keeps it with RuntimeDirectoryPreserve=yes. A path socket, never an
    # abstract one: the other host-network containers share the netns.
    prepare_private_dir "$ids" "$CONTROL_DIR" "host control socket"
    set -- "$@" -v "${CONTROL_DIR}:${CONTROL_DIR_IN_CONTAINER}" \
      --env "RADON_CONTROL_SOCKET=${CONTROL_DIR_IN_CONTAINER}/control.sock"
  fi
  if uses_secret_store "$unit"; then
    local credential_host_dir="${SECRET_STORE_CREDENTIAL_STAGE_ROOT}/${unit}"
    local credential_container_dir="/run/credentials/${unit}"
    set -- "$@" \
      --group-add "$credential_gid" \
      --env "CREDENTIALS_DIRECTORY=${credential_container_dir}" \
      --env "RADON_SECRET_STORE_PATH=${SECRET_STORE_DB_CONTAINER_PATH}" \
      --mount "type=bind,src=${credential_host_dir},dst=${credential_container_dir},readonly"
  fi

  if [[ -n "${NOTIFY_SOCKET:-}" && "${NOTIFY_SOCKET}" == /* ]]; then
    start_notify_proxy "$unit" "$NOTIFY_SOCKET" "$ids" || exit $?
    set -- "$@" --env "NOTIFY_SOCKET=${NOTIFY_PROXY_SOCKET}" --env WATCHDOG_USEC \
      --mount "type=bind,src=${NOTIFY_PROXY_SOCKET},dst=${NOTIFY_PROXY_SOCKET}"
  fi
  if [[ "$unit" == "radon-newsfeed.service" ]]; then
    # Page 3e952746: the image ENV is PLAYWRIGHT_BROWSERS_PATH=/ms-playwright,
    # but `bun x playwright install` during the image build did not leave
    # chromium_headless_shell-1217 there. Host deploy already caches that
    # revision at radon's ms-playwright dir. Bind it onto /ms-playwright so
    # this unit can launch without waiting for a new GHCR tag (R-234).
    # Read-only: host radon processes launch the same cache, so the
    # third-party-content container must not rewrite those browsers.
    # Overlay scripts/newsfeed from the live checkout so browser.js launch
    # changes apply before the next image build.
    local newsfeed_browsers newsfeed_scripts
    if [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" == "1" ]]; then
      newsfeed_browsers="${RADON_NEWSFEED_BROWSERS_PATH:-${STATE_DIR}/ms-playwright}"
      newsfeed_scripts="${RADON_NEWSFEED_SCRIPTS_PATH:-${DATA_DIR}/newsfeed-scripts}"
    else
      newsfeed_browsers="${RADON_NEWSFEED_BROWSERS_PATH:-/home/radon/.cache/ms-playwright}"
      newsfeed_scripts="${RADON_NEWSFEED_SCRIPTS_PATH:-/home/radon/radon/scripts/newsfeed}"
    fi
    require_bind_source "$ids" "$newsfeed_browsers"
    require_bind_source "$ids" "$newsfeed_scripts"
    # The scraper's default media dir is <repo>/web/public/media, which lives
    # in the image layer and is discarded on every restart, while Caddy serves
    # the bind mount above. Point the downloader straight at the mount and
    # override the HOST-shaped RADON_MEDIA_REMOTE that --env-file carries in
    # (/home/radon/radon-cloud/media/ does not exist in the container), so the
    # rsync hop collapses to a no-op instead of failing on an image with no
    # rsync. Without this every scraped image 404s on media.radon.run.
    #
    # DS-2026-09-20-04: Chromium renders hostile pages, so it keeps its own
    # sandbox. The seccomp profile is the engine default plus the clone /
    # unshare / setns / chroot its namespace sandbox needs without
    # CAP_SYS_ADMIN (config/seccomp/chromium.json). No host IPC namespace; the
    # private /dev/shm is sized because the engine's 64 MiB crashes renderers.
    set -- "$@" \
      --security-opt "seccomp=${CHROMIUM_SECCOMP_PROFILE}" \
      --shm-size 512m \
      --env PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
      -v "${newsfeed_browsers}:/ms-playwright:ro" \
      -v "${newsfeed_scripts}:/home/radon/radon/scripts/newsfeed:ro" \
      --env "RADON_NEWSFEED_MEDIA_DIR=${MEDIA_DIR_IN_CONTAINER}" \
      --env "RADON_MEDIA_REMOTE=${MEDIA_DIR_IN_CONTAINER}/"
  fi
  if [[ "$unit" == "radon-marketdesk.service" ]]; then
    # Same Chromium isolation as the newsfeed (DS-2026-09-20-04). The Python
    # image carries its own pinned Playwright headless shell under
    # PLAYWRIGHT_BROWSERS_PATH, so no host browser cache is bound.
    set -- "$@" \
      --security-opt "seccomp=${CHROMIUM_SECCOMP_PROFILE}" \
      --shm-size 512m
  fi

  set -- "$@" "$image"
  case "$unit" in
    radon-model-gateway.service)
      set -- "$@" python -m scripts.model_gateway.server
      ;;
    radon-api.service)
      set -- "$@" sh -c 'python scripts/db/migrate.py --boot && python scripts/secret_store.py && exec uvicorn scripts.api.server:app --host 0.0.0.0 --port 8321 --proxy-headers --forwarded-allow-ips 127.0.0.1 --timeout-keep-alive 75'
      ;;
    radon-research.service)
      set -- "$@" python -m research.worker --daemon
      ;;
    # Same script as the base unit's host ExecStart; this bind is the only
    # place the private root is readable.
    radon-research-archive.service)
      set -- "$@" python scripts/research/archive.py
      ;;
    radon-research-asset-archive.service)
      set -- "$@" python scripts/research/asset_archive.py
      ;;
    # Same script as the base unit's host ExecStart.
    radon-marketdesk.service)
      set -- "$@" python scripts/research/marketdesk_job.py
      ;;
    radon-marketdesk-pdf.service)
      set -- "$@" python scripts/research/marketdesk_pdf_job.py
      ;;
    # DS-2026-10-05-05: the same module and arguments as each base unit's
    # host ExecStart (pinned by cloud/tests/test_ds_secret_store_key_units.py).
    radon-subscription-vault.service)
      set -- "$@" sh -c 'python scripts/secret_store.py && exec python -m scripts.subscription_vault --serve'
      ;;
    radon-ai-cycle.service)
      set -- "$@" sh -c 'python scripts/secret_store.py && exec python -m scripts.ai_cycle.collect --record'
      ;;
    radon-ai-cycle-backfill.service)
      set -- "$@" sh -c 'python scripts/secret_store.py && exec python -m scripts.ai_cycle.collect --record --backfill --start 2006-12-31 --sources vercel,gpu-rental,sec,eia,noaa,openrouter --checkpoint /home/radon/.radon/ai-cycle/backfill-checkpoint.json --max-requests 400'
      ;;
    radon-aa-frontier-refresh.service)
      set -- "$@" sh -c 'python scripts/secret_store.py && exec python -m scripts.aa_frontier_refresh'
      ;;
    radon-uw-stream.service)
      set -- "$@" sh -c 'python scripts/secret_store.py && exec python scripts/uw_stream_collect.py'
      ;;
    radon-uw-research.service)
      set -- "$@" sh -c 'python scripts/secret_store.py && exec python scripts/uw_research.py --scheduled --json'
      ;;
    radon-monitor.service)
      set -- "$@" python -m scripts.monitor_daemon.run --daemon
      ;;
    radon-nextjs.service)
      set -- "$@" /usr/local/bin/next-clerk-guard
      ;;
    radon-relay.service)
      set -- "$@" node scripts/ib_realtime_server.js
      ;;
    radon-newsfeed.service)
      set -- "$@" node scripts/newsfeed/index.js
      ;;
  esac

  recheck_bind_sources "$ids"
  exec "$DOCKER" "$@"
}

[[ $# -ge 1 ]] || usage
case "$1" in
  check-model-space)
    [[ $# -eq 1 ]] || usage
    cmd_check_model_space
    ;;
  capture-model-pacing)
    [[ $# -eq 1 ]] || usage
    cmd_capture_model_pacing
    ;;
  install-model-clients)
    [[ $# -eq 1 ]] || usage
    cmd_install_model_clients
    ;;
  recover-model-clients)
    [[ $# -eq 1 ]] || usage
    cmd_recover_model_clients
    ;;
  commit-model-clients)
    [[ $# -eq 1 ]] || usage
    cmd_commit_model_clients
    ;;
  stop)
    [[ $# -eq 2 ]] || usage
    cmd_stop "$2"
    ;;
  halt)
    [[ $# -eq 3 ]] || usage
    cmd_halt "$2" "$3"
    ;;
  pull)
    [[ $# -eq 1 || $# -eq 2 ]] || usage
    cmd_pull "${2:-}"
    ;;
  prune)
    [[ $# -eq 1 ]] || usage
    cmd_prune
    ;;
  prune-locked)
    [[ $# -eq 1 || $# -eq 2 ]] || usage
    cmd_prune_locked "${2:-}"
    ;;
  run)
    [[ $# -eq 2 ]] || usage
    cmd_run "$2"
    ;;
  notify-proxy)
    [[ $# -eq 3 ]] || usage
    cmd_notify_proxy "$2" "$3"
    ;;
  *)
    usage
    ;;
esac
