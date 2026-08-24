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
fi

usage() {
  echo "usage: radon-app-runtime {pull|run <unit>}" >&2
  exit 64
}

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

python_image() {
  printf 'ghcr.io/joemccann/radon-python:%s\n' "$(image_tag)"
}

node_image() {
  printf 'ghcr.io/joemccann/radon-node:%s\n' "$(image_tag)"
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
    --cgroup-parent="system.slice/${unit}" \
    --env-file "$ENV_FILE" \
    --env RADON_DB_NO_REPLICA=1 \
    -w "$workdir" \
    -v "${DATA_DIR}:/home/radon/radon/data" \
    -v "${MEDIA_DIR}:/var/lib/radon/media"

  if [[ -n "${NOTIFY_SOCKET:-}" && "${NOTIFY_SOCKET}" == /* ]]; then
    set -- "$@" --env NOTIFY_SOCKET --env WATCHDOG_USEC \
      --mount "type=bind,src=${NOTIFY_SOCKET},dst=${NOTIFY_SOCKET}"
  fi
  if [[ "$unit" == "radon-newsfeed.service" ]]; then
    set -- "$@" --ipc host --env PLAYWRIGHT_CHROMIUM_SANDBOX=0
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
