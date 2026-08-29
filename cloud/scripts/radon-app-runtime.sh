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
  PYTHON="${RADON_TEST_PYTHON:-$(command -v python3)}"
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
  PYTHON=/usr/bin/python3
  NOTIFY_PROXY_DIR=/run/radon-app-runtime
fi

usage() {
  echo "usage: radon-app-runtime {pull|run <unit>|stop <unit>|notify-proxy <listen> <upstream>}" >&2
  exit 64
}

# Tag used when the pinned SHA was never pushed. app-images.yml sets
# cancel-in-progress, so a second push to main inside the 60-minute build
# budget cancels the first build and SHA1's `Push SHA tags to GHCR` step never
# runs — while ci.yml's deploy deliberately does not wait on app-images, so
# SHA1 still deploys. Without a fallback all five app units docker-run a
# `manifest unknown` at once. R-234.
RADON_APP_IMAGE_FALLBACK_TAG="${RADON_APP_IMAGE_FALLBACK_TAG:-latest}"

image_tag() {
  if [[ -n "${RADON_APP_IMAGE_TAG:-}" ]]; then
    printf '%s\n' "$RADON_APP_IMAGE_TAG"
    return 0
  fi
  if [[ -d /home/radon/radon/.git ]]; then
    /usr/bin/git -C /home/radon/radon rev-parse HEAD
    return 0
  fi
  printf 'latest\n'
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

# Resolve a repo to a tag that actually exists, preferring the pinned SHA.
resolve_image() {
  local repo="$1"
  local pinned="${repo}:$(image_tag)"
  if image_available "$pinned"; then
    printf '%s\n' "$pinned"
    return 0
  fi
  local fallback="${repo}:${RADON_APP_IMAGE_FALLBACK_TAG}"
  if image_available "$fallback"; then
    printf 'image %s unavailable; falling back to %s\n' "$pinned" "$fallback" >&2
    printf '%s\n' "$fallback"
    return 0
  fi
  printf 'neither %s nor %s is available\n' "$pinned" "$fallback" >&2
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

cmd_pull() {
  "$DOCKER" pull "$(python_image)"
  "$DOCKER" pull "$(node_image)"
}

cmd_stop() {
  local unit="${1:-}"
  [[ -n "$unit" ]] || usage
  refuse_host_plane "$unit"
  is_app_unit "$unit" || exit 64
  # Idempotent: ExecStopPost runs on every stop, including ones where the
  # container already exited on its own. R-232.
  "$DOCKER" rm -f "$unit" >/dev/null 2>&1 || true
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
    --env-file "$ENV_FILE" \
    --env RADON_DB_NO_REPLICA=1 \
    --env PYTHONPATH=/home/radon/radon/scripts \
    -w "$workdir" \
    -v "${DATA_DIR}:/home/radon/radon/data" \
    -v "${MEDIA_DIR}:${MEDIA_DIR_IN_CONTAINER}" \
    -v "${LEASE_DIR}:/var/lib/radon/ib-lease"

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
      set -- "$@" sh -c 'python scripts/db/migrate.py && exec uvicorn scripts.api.server:app --host 0.0.0.0 --port 8321 --proxy-headers --forwarded-allow-ips 127.0.0.1'
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
    [[ $# -eq 1 ]] || usage
    cmd_pull
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
