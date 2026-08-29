#!/bin/bash
set -euo pipefail

# Root-owned app-plane image runner. systemd ExecStart calls `run <unit>`.
# Does not take the deploy lifecycle lock. radon is not in group docker.
# Never Gateway, Caddy, health, or the engine socket.

readonly APP_UNITS="radon-api.service radon-nextjs.service radon-relay.service radon-monitor.service radon-newsfeed.service"

if [[ "${RADON_APP_RUNTIME_TEST_MODE:-0}" == "1" ]]; then
  DOCKER="${RADON_TEST_DOCKER:?test docker is required}"
  ID_BIN="${RADON_TEST_ID:?test id is required}"
  ENV_FILE="${RADON_TEST_ENV_FILE:?test env file is required}"
  DATA_DIR="${RADON_TEST_DATA_DIR:?test data dir is required}"
  MEDIA_DIR="${RADON_TEST_MEDIA_DIR:?test media dir is required}"
  STATE_DIR="${RADON_TEST_STATE_DIR:?test state dir is required}"
  LEASE_DIR="${STATE_DIR}/ib-lease"
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
fi

usage() {
  echo "usage: radon-app-runtime {pull|run <unit>|stop <unit>}" >&2
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
    -v "${MEDIA_DIR}:/var/lib/radon/media" \
    -v "${LEASE_DIR}:/var/lib/radon/ib-lease"

  if [[ -n "${NOTIFY_SOCKET:-}" && "${NOTIFY_SOCKET}" == /* ]]; then
    set -- "$@" --env NOTIFY_SOCKET --env WATCHDOG_USEC \
      --mount "type=bind,src=${NOTIFY_SOCKET},dst=${NOTIFY_SOCKET}"
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
    set -- "$@" --ipc host \
      --env PLAYWRIGHT_CHROMIUM_SANDBOX=0 \
      --env PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
      -v "${newsfeed_browsers}:/ms-playwright" \
      -v "${newsfeed_scripts}:/home/radon/radon/scripts/newsfeed"
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
  *)
    usage
    ;;
esac
