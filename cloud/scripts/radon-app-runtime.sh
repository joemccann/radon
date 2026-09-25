#!/bin/bash
set -euo pipefail

# Root-owned app-plane image runner. systemd ExecStart calls `run <unit>`.
# Service starts do not take the deploy lifecycle lock; optional pruning does.
# radon is not in group docker.
# Never Gateway, Caddy, health, or the engine socket.

readonly APP_UNITS="radon-api.service radon-nextjs.service radon-relay.service radon-monitor.service radon-newsfeed.service radon-research.service"

# Where the media volume lands INSIDE the container. Fixed regardless of the
# host path, because Caddy's root and the newsfeed's download dir must agree.
readonly MEDIA_DIR_IN_CONTAINER=/var/lib/radon/media

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
  DEPLOY_LOCK_FILE="${RADON_TEST_DEPLOY_LOCK:-${STATE_DIR}/deploy.lock}"
  GREEN_MARKER_FILE="${RADON_TEST_GREEN_MARKER:-${STATE_DIR}/last-green}"
  TRANSITION_JOURNAL_FILE="${RADON_TEST_TRANSITION_JOURNAL:-${STATE_DIR}/transition.json}"
  CHROMIUM_SECCOMP_PROFILE="${RADON_TEST_SECCOMP_PROFILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/seccomp/chromium.json}"
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
  DEPLOY_LOCK_FILE=/home/radon/.radon-deploy.lock
  GREEN_MARKER_FILE=/home/radon/.radon-last-green-deploy
  TRANSITION_JOURNAL_FILE=/home/radon/.radon-deploy-transition.json
  # Root-owned control-plane copy of cloud/config/seccomp/chromium.json. Never
  # the checkout: radon can write that, and could widen its own filter.
  CHROMIUM_SECCOMP_PROFILE=/etc/radon/seccomp/chromium.json
fi

readonly SECRET_STORE_CREDENTIAL_NAME=radon-secret-store-key
# R-619. The container runs --user radon, so a staged key owned by uid radon
# is readable by every other thing that account can start. The plaintext key
# is therefore handed over through a group the radon account is NOT in:
# root:radon-secrets 0040, and the container gets the gid at start via
# --group-add. Only root can grant that group, and only for this container.
readonly SECRET_STORE_CREDENTIAL_GROUP=radon-secrets
readonly SECRET_STORE_CREDENTIAL_CONTAINER_DIR=/run/credentials/radon-api.service
readonly SECRET_STORE_DB_CONTAINER_PATH=/home/radon/radon/data/secret_store/secrets.db
readonly SECRET_STORE_CREDENTIAL_STAGE_ROOT="${NOTIFY_PROXY_DIR}/credentials"
STAGED_CREDENTIAL_UNIT=""

usage() {
  echo "usage: radon-app-runtime {pull [<sha>]|run <unit>|halt <unit> <grace-seconds>|stop <unit>|notify-proxy <listen> <upstream>}" >&2
  exit 64
}

cleanup_runtime_credential() {
  local unit="$1"
  local credential_dir="${SECRET_STORE_CREDENTIAL_STAGE_ROOT}/${unit}"
  local credential_file="${credential_dir}/${SECRET_STORE_CREDENTIAL_NAME}"
  [[ "$unit" == "radon-api.service" ]] || return 0
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
  groups="$("$ID_BIN" -nG radon 2>/dev/null)" || groups=""
  case " $groups " in
    *" ${SECRET_STORE_CREDENTIAL_GROUP} "*)
      echo "radon-app-runtime: radon must not be a member of ${SECRET_STORE_CREDENTIAL_GROUP}" >&2
      return 78
      ;;
  esac
  return 0
}

stage_api_credential() {
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
  "$PYTHON" - "$DOCKER" "$1" <<'PYCODE'
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

is_app_unit() {
  local candidate="$1"
  local unit
  for unit in $APP_UNITS; do
    [[ "$candidate" == "$unit" ]] && return 0
  done
  return 1
}

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
cmd_pull() {
  local target="${1:-}" tag python_ref node_ref python_pid node_pid
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

  # The gated prepull job and the deploy both call this exact verb. When the
  # pair is local and matches registry digests, no layers need pulling.
  # Missing, changed, or unverified metadata requires a fresh parallel pull.
  if [[ -n "$target" ]] \
    && image_in_local_store "$python_ref" \
    && image_in_local_store "$node_ref" \
    && image_matches_registry "$python_ref" \
    && image_matches_registry "$node_ref"; then
    printf 'exact release image pair already local: %s\n' "$tag" >&2
  else
    "$DOCKER" pull "$python_ref" &
    python_pid=$!
    "$DOCKER" pull "$node_ref" &
    node_pid=$!
    if ! wait "$python_pid"; then
      status=1
    fi
    if ! wait "$node_pid"; then
      status=1
    fi
    if (( status != 0 )); then
      echo "radon-app-runtime: exact release image pull failed" >&2
      return 69
    fi
    if ! image_in_local_store "$python_ref" || ! image_in_local_store "$node_ref"; then
      echo "radon-app-runtime: exact release image pair is incomplete after pull" >&2
      return 69
    fi
  fi
  [[ -n "$target" ]] && prune_stale_app_images "$target"
  return 0
}

prune_stale_app_images() {
  "$PYTHON" - "$DOCKER" "$1" "$DEPLOY_LOCK_FILE" "$GREEN_MARKER_FILE" "$TRANSITION_JOURNAL_FILE" <<'PYCODE'
import fcntl, json, os, re, stat, subprocess, sys
from pathlib import Path

docker, target, lock_path, green_path, journal_path = sys.argv[1:]
def skip(reason):
    print(f"radon-app-runtime: image prune skipped: {reason}", file=sys.stderr)
    raise SystemExit(0)
def command(*args):
    return subprocess.run([docker, *args], capture_output=True, text=True,
                          check=True, timeout=20).stdout.splitlines()
def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)

try:
    # Do not create a root-owned lock that would prevent radon from deploying.
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(fd, "r+") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            skip("deploy lock is not a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protected = {target}
        journal = Path(journal_path)
        if journal.exists() or journal.is_symlink():
            state = json.loads(journal.read_text())
            if not isinstance(state, dict) or state.get("version") != 1 or not all(
                sha(state.get(key)) for key in ("requested_sha", "previous_sha")
            ):
                skip("invalid transition journal")
            protected.update((state["requested_sha"], state["previous_sha"]))
        else:
            previous = Path(green_path).read_text().splitlines()[0]
            if not sha(previous):
                skip("invalid green marker")
            protected.add(previous)
        repos = ("ghcr.io/joemccann/radon-node", "ghcr.io/joemccann/radon-python")
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
        for image in stale:
            command("rmi", image)
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
# socket the container sees and relays every datagram to systemd. R-429.
cmd_notify_proxy() {
  local listen="${1:-}" upstream="${2:-}"
  [[ -n "$listen" && -n "$upstream" ]] || usage
  exec "$PYTHON" - "$listen" "$upstream" <<'PY'
import os, socket, sys
listen, upstream = sys.argv[1], sys.argv[2]
try:
    os.unlink(listen)
except FileNotFoundError:
    pass
inbound = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
inbound.bind(listen)
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
# XAI_API_KEY='xai-…' reached the container WITH its quotes and xAI answered
# 400 "Incorrect API key" (2026-08-30; six secrets in /etc/radon/env are
# single-quoted because `set -a; . file` consumers need $-bearing values
# quoted, see CLAUDE.md). Strip one matching pair of surrounding quotes per
# line into a root-only copy under the runtime dir and hand docker that; the
# host file stays the secret of record and is never rewritten.
render_env_file() {
  local unit="$1" out="${NOTIFY_PROXY_DIR}/${unit}.env"
  mkdir -p "$NOTIFY_PROXY_DIR"
  (
    umask 077
    sed -E \
      -e "s/^([A-Za-z_][A-Za-z0-9_]*=)'(.*)'[[:space:]]*\$/\1\2/" \
      -e 's/^([A-Za-z_][A-Za-z0-9_]*=)"(.*)"[[:space:]]*$/\1\2/' \
      "$ENV_FILE" > "$out"
    # The newsfeed's Chromium renders third-party web content (and
    # may fall back to --no-sandbox); hand that unit only the keys its own code reads,
    # never the full production secret set.
    if [[ "$unit" == "radon-newsfeed.service" ]]; then
      grep -E '^(#|$|(NODE_ENV|ANTHROPIC_API_KEY|CLAUDE_CODE_API_KEY|CLAUDE_API_KEY|CLAUDE_CODE_OAUTH_TOKEN|CLAUDE_CONFIG_DIR|CODEX_HOME|GROK_AUTH_FILE|GEMINI_OAUTH_TOKEN|ANTIGRAVITY_CLI|RADON_LADDER_[A-Z0-9_]+|XAI_API_KEY|GROK_API_KEY|OPENAI_API_KEY|GEMINI_API_KEY|NVIDIA_API_KEY|CEREBRAS_API_KEY|RADON_PYTHON_BIN|TURSO_DB_URL|TURSO_AUTH_TOKEN|PLAYWRIGHT_CHROMIUM_SANDBOX|RADON_DB_NO_REPLICA|RADON_DB_USE_REPLICA|RADON_MEDIA_LOCAL|RADON_MEDIA_REMOTE|RADON_NEWSFEED_[A-Z0-9_]+|THEMARKETEAR_EMAIL|THEMARKETEAR_PASSWORD)=)' \
        "$out" > "${out}.filtered" || true
      mv "${out}.filtered" "$out"
    fi
  )
  printf '%s\n' "$out"
}

# Keep the root-owned mount anchor outside radon's writable state directory.
# Once its root-owned ancestor chain is verified, radon cannot exchange the
# child for a symlink between provisioning and the Docker bind operation.
prepare_research_dir() {
  local ids="$1" anchor=/var/lib/radon-private
  [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" == "1" ]] && anchor="${STATE_DIR}/private"
  "$PYTHON" - "$anchor" "$ids" "${RADON_APP_RUNTIME_TEST_MODE:-0}" <<'PY_RESEARCH'
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
    child = anchor / 'research'
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
  local ids="$1" dir="$2" label="$3"
  "$PYTHON" - "$dir" "$ids" "${RADON_APP_RUNTIME_TEST_MODE:-0}" "$label" <<'PY_PRIVATE_DIR' || exit 78
import os, sys
path, ids, test, label = sys.argv[1], sys.argv[2], sys.argv[3] == '1', sys.argv[4]
uid, gid = (os.getuid(), os.getgid()) if test else tuple(int(part) for part in ids.split(':'))
try:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    fd = os.open(path, os.O_NOFOLLOW | os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)
except OSError:
    print(f'radon-app-runtime: {label} directory is a symlink or unusable; refusing', file=sys.stderr)
    raise SystemExit(78)
PY_PRIVATE_DIR
}

cmd_run() {
  local unit="${1:-}"
  local ids image workdir
  [[ -n "$unit" ]] || usage
  refuse_host_plane "$unit"
  is_app_unit "$unit" || {
    echo "radon-app-runtime: unit is not an app-plane service: ${unit}" >&2
    exit 64
  }

  ids="$("$ID_BIN" -u radon):$("$ID_BIN" -g radon)"
  workdir=/home/radon/radon
  case "$unit" in
    radon-api.service|radon-monitor.service|radon-research.service) image="$(python_image)" ;;
    radon-nextjs.service)
      image="$(node_image)"
      workdir=/home/radon/radon/web
      ;;
    radon-relay.service|radon-newsfeed.service) image="$(node_image)" ;;
    *) exit 64 ;;
  esac

  # Fail closed: the newsfeed never starts under the engine default filter,
  # which would kill Chromium's namespace sandbox at launch.
  if [[ "$unit" == "radon-newsfeed.service" ]] && \
     [[ ! -f "$CHROMIUM_SECCOMP_PROFILE" || -L "$CHROMIUM_SECCOMP_PROFILE" ]]; then
    echo "radon-app-runtime: chromium seccomp profile missing: ${CHROMIUM_SECCOMP_PROFILE}" >&2
    exit 78
  fi

  local credential_gid=""
  if [[ "$unit" == "radon-api.service" ]]; then
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

  if [[ "$unit" == "radon-api.service" ]]; then
    prepare_private_dir "$ids" "${DATA_DIR}/secret_store" "secret store"
    stage_api_credential "$unit" "$credential_gid"
  fi

  # The container gets NARROW binds, never $STATE_DIR itself. /var/lib/radon
  # holds control-plane-ready, the manifest digest and the root deploy
  # transaction journal; the container runs as uid radon and write permission on
  # the PARENT is all unlink/rename needs, so the whole-directory bind handed the
  # newsfeed's headless Chromium the ability to delete the readiness gate. The
  # one thing an app genuinely writes outside media/ is the shared 2FA lease,
  # which now has its own subdirectory. Create it here: the container can no
  # longer mkdir it, because the parent is not mounted. R-381.
  if [[ "$unit" != "radon-research.service" ]]; then
    prepare_private_dir "$ids" "$LEASE_DIR" "2FA lease"
  fi

  # Newsfeed renders third-party content in Chromium (sandbox fallback possible): it
  # gets an isolated bridge network (egress only), never the host stack.
  local container_network=host
  [[ "$unit" == "radon-newsfeed.service" ]] && container_network=bridge

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
    --env-file "$(render_env_file "$unit")" \
    --env RADON_DB_NO_REPLICA=1 \
    --env PYTHONPATH=/home/radon/radon/scripts \
    -w "$workdir"

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

  if [[ "$unit" != "radon-research.service" ]]; then
    set -- "$@" \
      -v "${DATA_DIR}:/home/radon/radon/data" \
      -v "${MEDIA_DIR}:${MEDIA_DIR_IN_CONTAINER}" \
      -v "${LEASE_DIR}:/var/lib/radon/ib-lease"
  fi

  # Subscription grants (2026-09-18). The model ladders meter against the
  # operator's subscriptions, never prepaid keys, and read the CLI credential
  # files under ~radon (kept live by radon-subscription-tokens on the HOST).
  # No container could see them, so every container-side rung silently fell
  # to prepaid credits; when the xAI team ran dry the newsfeed voice rewrite
  # died. Bind each dir that exists, read-only, and pin HOME so Path.home()
  # and os.homedir() resolve to the mount. Never the whole home directory.
  # Next.js hosts /api/newsfeed/share and /api/assistant, so it is an LLM
  # consumer (2026-09-19: excluding it 502'd every share rewrite with
  # "Missing Anthropic subscription"). Relay still gets no binds.
  local subscription_home="${RADON_SUBSCRIPTION_HOME:-/home/radon}"
  local cred_dir
  case "$unit" in
    radon-api.service|radon-newsfeed.service|radon-research.service|radon-nextjs.service)
      for cred_dir in .grok .codex .claude; do
        if [[ -d "${subscription_home}/${cred_dir}" ]]; then
          set -- "$@" -v "${subscription_home}/${cred_dir}:/home/radon/${cred_dir}:ro"
        fi
      done
      ;;
  esac
  set -- "$@" --env HOME=/home/radon
  if [[ "$unit" == "radon-research.service" || "$unit" == "radon-api.service" ]]; then
    local research_dir research_mode=ro
    research_dir="$(prepare_research_dir "$ids")" || exit $?
    [[ "$unit" == "radon-research.service" ]] && research_mode=rw
    set -- "$@" --env RADON_RESEARCH_DIR=/var/lib/radon/research \
      -v "${research_dir}:/var/lib/radon/research:${research_mode}"
  fi

  # App-role Gateway control: mTLS client pair lives on the host at
  # /etc/radon/ib-remote. Mount that directory only, never /etc/radon
  # (TWS secrets, Turso tokens). Read-only. Missing dir is combined/broker.
  if [[ "$unit" == "radon-api.service" ]]; then
    local ib_remote_certs="${RADON_IB_REMOTE_CERT_DIR:-/etc/radon/ib-remote}"
    if [[ -d "$ib_remote_certs" ]]; then
      set -- "$@" -v "${ib_remote_certs}:${ib_remote_certs}:ro"
    fi
    # Robinhood read-only MCP token store. Its own dir, never /etc/radon, and
    # read-write: refresh rotates the token by atomic replace plus a .lock
    # sidecar, which a single-file bind cannot do. Without it every Robinhood
    # rung inside the API fell through to UW (2026-09-19).
    local rh_token_dir="${RADON_RH_TOKEN_DIR:-/var/lib/radon/rh-mcp}"
    if [[ -d "$rh_token_dir" ]]; then
      set -- "$@" -v "${rh_token_dir}:${rh_token_dir}" \
        --env "ROBINHOOD_MCP_TOKEN_FILE=${rh_token_dir}/rh-mcp.json"
    fi
    local credential_host_dir="${SECRET_STORE_CREDENTIAL_STAGE_ROOT}/${unit}"
    set -- "$@" \
      --group-add "$credential_gid" \
      --env "CREDENTIALS_DIRECTORY=${SECRET_STORE_CREDENTIAL_CONTAINER_DIR}" \
      --env "RADON_SECRET_STORE_PATH=${SECRET_STORE_DB_CONTAINER_PATH}" \
      --mount "type=bind,src=${credential_host_dir},dst=${SECRET_STORE_CREDENTIAL_CONTAINER_DIR},readonly"
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
      -v "${newsfeed_browsers}:/ms-playwright" \
      -v "${newsfeed_scripts}:/home/radon/radon/scripts/newsfeed:ro" \
      --env "RADON_NEWSFEED_MEDIA_DIR=${MEDIA_DIR_IN_CONTAINER}" \
      --env "RADON_MEDIA_REMOTE=${MEDIA_DIR_IN_CONTAINER}/"
  fi

  set -- "$@" "$image"
  case "$unit" in
    radon-api.service)
      set -- "$@" sh -c 'python scripts/db/migrate.py && python scripts/secret_store.py && exec uvicorn scripts.api.server:app --host 0.0.0.0 --port 8321 --proxy-headers --forwarded-allow-ips 127.0.0.1'
      ;;
    radon-research.service)
      set -- "$@" python -m research.worker --daemon
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

  exec "$DOCKER" "$@"
}

[[ $# -ge 1 ]] || usage
case "$1" in
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
