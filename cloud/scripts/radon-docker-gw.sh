#!/usr/bin/env bash
set -euo pipefail

# Root-owned, argument-validating Gateway docker operator.
#
# Group `docker` is root-equivalent: a member can mount the host filesystem
# into a container and walk out as root. radon was in it (setup-vps.sh) purely
# so ib-gateway-control.sh and jvm_forensics.py could drive ONE container. This
# shim is what replaces that membership -- a fixed verb set against a pinned
# container, with no caller-supplied paths, images, mounts or flags.
#
# Every input is a constant here. The compose body is read from
# /etc/radon/ib-gateway-compose.yml, installed root-owned by the control plane
# from the git blob at the deployed commit, NOT from /home/radon/radon/cloud
# which the radon account can write -- root acting on a compose file its caller
# can rewrite is the same escalation with extra steps.

readonly CONTAINER=ib-gateway
readonly PROJECT=cloud
readonly JVM_PGREP_PATTERN=ibcalpha.ibc.IbcGateway

if [[ "${RADON_DOCKER_GW_TEST_MODE:-0}" == "1" ]]; then
  DOCKER="${RADON_TEST_DOCKER:?test docker is required}"
  COMPOSE_FILE="${RADON_TEST_COMPOSE_FILE:?test compose file is required}"
  COMPOSE_ENV_FILE="${RADON_TEST_COMPOSE_ENV_FILE:?test compose env file is required}"
else
  if (( EUID != 0 )); then
    echo "radon-docker-gw must run as root" >&2
    exit 77
  fi
  DOCKER=/usr/bin/docker
  COMPOSE_FILE=/etc/radon/ib-gateway-compose.yml
  COMPOSE_ENV_FILE=/etc/radon/env
fi

usage() {
  echo "usage: radon-docker-gw {compose-up|compose-down|inspect-running|pgrep-jvm|pgrep-java|thread-dump <pid>|logs|stats|ps}" >&2
  exit 64
}

# The compose body must be a root-owned regular file. A symlink or a
# radon-owned file at that path is the escalation this shim exists to prevent,
# so it is refused rather than followed.
require_trusted_compose_file() {
  local owner
  if [[ -L "$COMPOSE_FILE" || ! -f "$COMPOSE_FILE" ]]; then
    echo "radon-docker-gw: ${COMPOSE_FILE} must be a regular, non-symlink file" >&2
    exit 78
  fi
  if [[ "${RADON_DOCKER_GW_TEST_MODE:-0}" != "1" ]]; then
    owner="$(stat -c '%u' "$COMPOSE_FILE")"
    if [[ "$owner" != "0" ]]; then
      echo "radon-docker-gw: ${COMPOSE_FILE} must be owned by root" >&2
      exit 78
    fi
  fi
}

# --project-name is pinned so the named volume stays cloud_ib-config no matter
# where the file lives; moving it off the checkout must not orphan the
# Gateway's Jts settings and 2FA state.
compose() {
  require_trusted_compose_file
  RADON_COMPOSE_ENV_FILE="$COMPOSE_ENV_FILE" \
    "$DOCKER" compose \
    --env-file "$COMPOSE_ENV_FILE" \
    --project-name "$PROJECT" \
    -f "$COMPOSE_FILE" \
    "$@"
}

main() {
  local verb="${1:-}"
  [[ -n "$verb" ]] || usage
  shift || true

  case "$verb" in
    compose-up)
      (( $# == 0 )) || usage
      compose up -d
      ;;
    compose-down)
      (( $# == 0 )) || usage
      compose down
      ;;
    # stdout, stderr and the exit code pass through untouched: gateway_state()
    # reads the exit code and matches "No such object" on stderr to tell
    # `missing` from `unknown`, and a swallowed stderr wedges the watchdog's
    # restart ladder at unknown forever.
    inspect-running)
      (( $# == 0 )) || usage
      exec "$DOCKER" inspect --format '{{.State.Running}}' "$CONTAINER"
      ;;
    pgrep-jvm)
      (( $# == 0 )) || usage
      exec "$DOCKER" exec "$CONTAINER" pgrep -f "$JVM_PGREP_PATTERN"
      ;;
    pgrep-java)
      (( $# == 0 )) || usage
      exec "$DOCKER" exec "$CONTAINER" pgrep java
      ;;
    thread-dump)
      (( $# == 1 )) || usage
      [[ "$1" =~ ^[1-9][0-9]{0,6}$ ]] || {
        echo "radon-docker-gw: thread-dump takes a numeric pid" >&2
        exit 64
      }
      # bash -c so the shell builtin kill works even if /bin/kill is absent.
      exec "$DOCKER" exec "$CONTAINER" bash -c "kill -3 $1"
      ;;
    logs)
      (( $# == 0 )) || usage
      exec "$DOCKER" logs --since 5m "$CONTAINER"
      ;;
    stats)
      (( $# == 0 )) || usage
      exec "$DOCKER" stats --no-stream "$CONTAINER"
      ;;
    ps)
      (( $# == 0 )) || usage
      exec "$DOCKER" exec "$CONTAINER" ps aux
      ;;
    *)
      usage
      ;;
  esac
}

main "$@"
