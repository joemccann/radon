#!/bin/bash
set -euo pipefail

# Root-owned app-plane image runner. systemd ExecStart calls `run <unit>`.
# Does not take the deploy lifecycle lock. radon is not in group docker.
# Never Gateway, Caddy, health, or the engine socket.

readonly APP_UNITS="radon-api.service radon-nextjs.service radon-relay.service radon-monitor.service radon-newsfeed.service"

# Where the media volume lands INSIDE the container. Fixed regardless of the
# host path, because Caddy's root and the newsfeed's download dir must agree.
readonly MEDIA_DIR_IN_CONTAINER=/var/lib/radon/media

if [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" == "1" ]]; then
  DOCKER="${RADON_TEST_DOCKER:?test docker is required}"
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
else
  if (( EUID != 0 )); then
    echo "radon-app-runtime must run as root" >&2
    exit 77
  fi
  DOCKER=/usr/bin/docker
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
  echo "usage: radon-app-runtime {pull [<sha>]|run <unit>|stop <unit>|notify-proxy <listen> <upstream>}" >&2
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
# fallback tag, nor in use by a running container (the previous release stays
# until the deploy after next, so a rollback never pulls). Every deploy since
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
  # pair is already local, deploy performs only these two fast inspections.
  # On a miss, pull both independent images concurrently and wait for both.
  if [[ -n "$target" ]] \
    && image_in_local_store "$python_ref" \
    && image_in_local_store "$node_ref"; then
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
  local target="$1" in_use image tag repo
  in_use="$("$DOCKER" ps --format '{{.Image}}' 2>/dev/null || true)"
  for repo in ghcr.io/joemccann/radon-node ghcr.io/joemccann/radon-python; do
    while IFS= read -r image; do
      [[ -n "$image" ]] || continue
      tag="${image##*:}"
      [[ "$tag" =~ ^[0-9a-f]{40}$ ]] || continue
      [[ "$tag" == "$target" ]] && continue
      grep -qxF -- "$image" <<< "$in_use" && continue
      "$DOCKER" rmi "$image" >/dev/null 2>&1 || true
    done < <("$DOCKER" images --format '{{.Repository}}:{{.Tag}}' "$repo" 2>/dev/null || true)
  done
}

cmd_stop() {
  local unit="${1:-}"
  [[ -n "$unit" ]] || usage
  refuse_host_plane "$unit"
  is_app_unit "$unit" || exit 64
  # Idempotent: ExecStopPost runs on every stop, including ones where the
  # container already exited on its own. R-232.
  "$DOCKER" rm -f "$unit" >/dev/null 2>&1 || true
  cleanup_runtime_credential "$unit"
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
os.chmod(listen, 0o666)
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
  local unit="$1" upstream="$2" listen attempt
  listen="${NOTIFY_PROXY_DIR}/${unit}.sock"
  mkdir -p -m 0755 "$NOTIFY_PROXY_DIR" || {
    echo "radon-app-runtime: notify proxy dir is unavailable: ${NOTIFY_PROXY_DIR}" >&2
    return 71
  }
  rm -f "$listen"
  "$0" notify-proxy "$listen" "$upstream" &
  for attempt in $(seq 1 50); do
    [[ -S "$listen" ]] && { NOTIFY_PROXY_SOCKET="$listen"; return 0; }
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
  )
  printf '%s\n' "$out"
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
    radon-api.service|radon-monitor.service) image="$(python_image)" ;;
    radon-nextjs.service)
      image="$(node_image)"
      workdir=/home/radon/radon/web
      ;;
    radon-relay.service|radon-newsfeed.service) image="$(node_image)" ;;
    *) exit 64 ;;
  esac

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
  "$DOCKER" rm -f "$unit" >/dev/null 2>&1 || true
  cleanup_runtime_credential "$unit"

  if [[ "$unit" == "radon-api.service" ]]; then
    local secret_store_dir="${DATA_DIR}/secret_store"
    install -d -m 0700 "$secret_store_dir"
    "$CHOWN" "$ids" "$secret_store_dir"
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
  mkdir -p "$LEASE_DIR"
  chown "$ids" "$LEASE_DIR" 2>/dev/null || true

  set -- \
    run \
    --network host \
    --user "$ids" \
    --rm \
    --name "$unit" \
    --init \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --cgroupns host \
    --cgroup-parent=system.slice \
    --env-file "$(render_env_file "$unit")" \
    --env RADON_DB_NO_REPLICA=1 \
    --env PYTHONPATH=/home/radon/radon/scripts \
    -w "$workdir" \
    -v "${DATA_DIR}:/home/radon/radon/data" \
    -v "${MEDIA_DIR}:${MEDIA_DIR_IN_CONTAINER}" \
    -v "${LEASE_DIR}:/var/lib/radon/ib-lease"

  # App-role Gateway control: mTLS client pair lives on the host at
  # /etc/radon/ib-remote. Mount that directory only, never /etc/radon
  # (TWS secrets, Turso tokens). Read-only. Missing dir is combined/broker.
  if [[ "$unit" == "radon-api.service" ]]; then
    local ib_remote_certs="${RADON_IB_REMOTE_CERT_DIR:-/etc/radon/ib-remote}"
    if [[ -d "$ib_remote_certs" ]]; then
      set -- "$@" -v "${ib_remote_certs}:${ib_remote_certs}:ro"
    fi
    local credential_host_dir="${SECRET_STORE_CREDENTIAL_STAGE_ROOT}/${unit}"
    set -- "$@" \
      --group-add "$credential_gid" \
      --env "CREDENTIALS_DIRECTORY=${SECRET_STORE_CREDENTIAL_CONTAINER_DIR}" \
      --env "RADON_SECRET_STORE_PATH=${SECRET_STORE_DB_CONTAINER_PATH}" \
      --mount "type=bind,src=${credential_host_dir},dst=${SECRET_STORE_CREDENTIAL_CONTAINER_DIR},readonly"
  fi

  if [[ -n "${NOTIFY_SOCKET:-}" && "${NOTIFY_SOCKET}" == /* ]]; then
    start_notify_proxy "$unit" "$NOTIFY_SOCKET" || exit $?
    set -- "$@" --env "NOTIFY_SOCKET=${NOTIFY_PROXY_SOCKET}" --env WATCHDOG_USEC \
      --mount "type=bind,src=${NOTIFY_PROXY_SOCKET},dst=${NOTIFY_PROXY_SOCKET}"
  fi
  if [[ "$unit" == "radon-newsfeed.service" ]]; then
    # Page 3e952746: the image ENV is PLAYWRIGHT_BROWSERS_PATH=/ms-playwright,
    # but `bun x playwright install` during the image build did not leave
    # chromium_headless_shell-1217 there. Host deploy already caches that
    # revision at radon's ms-playwright dir. Bind it onto /ms-playwright so
    # this unit can launch without waiting for a new GHCR tag (R-234).
    # Overlay scripts/newsfeed from the live checkout so --no-sandbox in
    # browser.js applies before the next image build.
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
    set -- "$@" --ipc host \
      --env PLAYWRIGHT_CHROMIUM_SANDBOX=0 \
      --env PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
      -v "${newsfeed_browsers}:/ms-playwright" \
      -v "${newsfeed_scripts}:/home/radon/radon/scripts/newsfeed" \
      --env "RADON_NEWSFEED_MEDIA_DIR=${MEDIA_DIR_IN_CONTAINER}" \
      --env "RADON_MEDIA_REMOTE=${MEDIA_DIR_IN_CONTAINER}/"
  fi

  set -- "$@" "$image"
  case "$unit" in
    radon-api.service)
      set -- "$@" sh -c 'python scripts/db/migrate.py && python scripts/secret_store.py && exec uvicorn scripts.api.server:app --host 0.0.0.0 --port 8321 --proxy-headers --forwarded-allow-ips 127.0.0.1'
      ;;
    radon-monitor.service)
      set -- "$@" python -m scripts.monitor_daemon.run --daemon
      ;;
    radon-nextjs.service)
      set -- "$@" bun run start
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
