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
# Canonical not-held prefix written by scripts/utils/ib_operator_hold.py; both
# readers apply the same rule so they can never disagree.
readonly HOLD_NOT_HELD_PREFIX='{"held": false'
readonly HOLD_RC=73

if [[ "${RADON_DOCKER_GW_TEST_MODE:-0}" == "1" ]]; then
  DOCKER="${RADON_TEST_DOCKER:?test docker is required}"
  COMPOSE_FILE="${RADON_TEST_COMPOSE_FILE:?test compose file is required}"
  COMPOSE_ENV_FILE="${RADON_TEST_COMPOSE_ENV_FILE:?test compose env file is required}"
  HOLD_FILE="${RADON_TEST_OPERATOR_HOLD_PATH:?test operator hold path is required}"
else
  if (( EUID != 0 )); then
    echo "radon-docker-gw must run as root" >&2
    exit 77
  fi
  DOCKER=/usr/bin/docker
  COMPOSE_FILE=/etc/radon/ib-gateway-compose.yml
  COMPOSE_ENV_FILE=/etc/radon/env
  HOLD_FILE=/var/lib/radon/ib-operator-hold.json
fi

usage() {
  echo "usage: radon-docker-gw {compose-up|compose-down|kill|config-check|inspect-running|pgrep-jvm|pgrep-java|thread-dump <pid>|logs|stats|ps}" >&2
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

# The env file feeds credentials and bind addresses into the body root runs.
# A symlink, a non-root owner, or a group/other-writable file at that path is
# the same radon->root escalation as a writable compose body. REL-234.
require_trusted_env_file() {
  local perms
  if [[ -L "$COMPOSE_ENV_FILE" || ! -f "$COMPOSE_ENV_FILE" ]]; then
    echo "radon-docker-gw: ${COMPOSE_ENV_FILE} env file must be a regular, non-symlink file" >&2
    exit 78
  fi
  if ! perms="$(stat -c '%a' "$COMPOSE_ENV_FILE" 2>/dev/null)"; then
    perms="$(stat -f '%Lp' "$COMPOSE_ENV_FILE")"
  fi
  if (( ( $((8#$perms)) & 18 ) != 0 )); then
    echo "radon-docker-gw: ${COMPOSE_ENV_FILE} env file must not be group/other-writable" >&2
    exit 78
  fi
  if [[ "${RADON_DOCKER_GW_TEST_MODE:-0}" != "1" ]]; then
    if [[ "$(stat -c '%u' "$COMPOSE_ENV_FILE")" != "0" ]]; then
      echo "radon-docker-gw: ${COMPOSE_ENV_FILE} env file must be owned by root" >&2
      exit 78
    fi
  fi
}

# `radon ib release` holds the Gateway logged out so the operator can use the
# shared IBKR username (2026-09-25: IBC reclaimed it three times). Every start
# funnels through compose-up here, so this is the backstop for any caller.
# Fail-safe: anything but an absent flag or a root-owned regular file that
# begins with the canonical not-held prefix is a hold.
operator_hold_active() {
  local head
  [[ -e "$HOLD_FILE" || -L "$HOLD_FILE" ]] || return 1
  [[ -L "$HOLD_FILE" || ! -f "$HOLD_FILE" ]] && return 0
  if [[ "${RADON_DOCKER_GW_TEST_MODE:-0}" != "1" \
    && "$(stat -c '%u' "$HOLD_FILE" 2>/dev/null)" != "0" ]]; then
    return 0
  fi
  head="$(head -c "${#HOLD_NOT_HELD_PREFIX}" "$HOLD_FILE" 2>/dev/null)" || return 0
  [[ "$head" != "$HOLD_NOT_HELD_PREFIX" ]]
}

refuse_while_held() {
  if operator_hold_active; then
    echo "radon-docker-gw: REFUSING compose-up: IBKR operator hold active (radon ib resume clears it)" >&2
    exit "$HOLD_RC"
  fi
}

# --project-name is pinned so the named volume stays cloud_ib-config no matter
# where the file lives; moving it off the checkout must not orphan the
# Gateway's Jts settings and 2FA state.
compose() {
  require_trusted_compose_file
  require_trusted_env_file
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
      refuse_while_held
      compose up -d
      ;;
    compose-down)
      (( $# == 0 )) || usage
      compose down
      ;;
    # Last-resort release step for `radon ib release` when a graceful down is
    # blocked or hangs. Root only: deliberately absent from radon-ops sudoers.
    kill)
      (( $# == 0 )) || usage
      exec "$DOCKER" kill "$CONTAINER"
      ;;
    # deploy.sh's preflight render check. Takes no env-file argument: a
    # caller-supplied path is the one thing this shim exists to refuse, and in
    # production the deploy's own ENV_FILE_DEFAULT is this same /etc/radon/env.
    # Output is suppressed by the caller so a failure never prints expanded
    # secret fragments.
    config-check)
      (( $# == 0 )) || usage
      compose config --quiet
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
