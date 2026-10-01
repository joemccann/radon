#!/usr/bin/env bash
set -euo pipefail

# radon ib {release|resume|status} -- installed root-owned as
# /usr/local/sbin/radon-ib-hold on the broker.
#
# The Gateway and the operator share one IBKR username, and IBKR allows one
# session per username. On 2026-09-25 IBC reclaimed the session three times
# and kicked the operator off interactivebrokers.com. `release` sets the
# durable operator hold (scripts/utils/ib_operator_hold.py) that every Gateway
# start path refuses under, then takes the Gateway down without waiting on the
# deploy lock or the lifecycle mutex. `resume` clears it and logs in once.
# Design: docs/ibkr-session-release.md.

readonly HOLD_PYTHON="${RADON_IB_HOLD_PYTHON:-/usr/bin/python3.13}"
readonly HOLD_CLI="${RADON_APP_DIR:-/home/radon/radon}/scripts/utils/ib_operator_hold.py"
readonly GATEWAY_CONTROL="${RADON_IB_GATEWAY_CONTROL:-/usr/local/bin/radon-ib-gateway-control}"
readonly DOCKER_GW="${RADON_DOCKER_GW:-/usr/local/sbin/radon-docker-gw}"
readonly SYSTEMCTL="${RADON_SYSTEMCTL:-systemctl}"
readonly WATCHDOG_TIMER=radon-ib-watchdog.timer
readonly STEP_TIMEOUT_SECS=30
readonly START_TIMEOUT_SECS=120
readonly RELEASE_POLLS=15
readonly RELEASE_POLL_SECS="${RADON_IB_RELEASE_POLL_SECS:-2}"
readonly LEASE_HELD_RC=75

usage() {
  echo "usage: radon ib {release [--reason TEXT]|resume|status}" >&2
  exit 64
}

require_root() {
  if [[ "${RADON_IB_HOLD_TEST_MODE:-0}" != "1" && $EUID -ne 0 ]]; then
    echo "radon ib: run as root on the broker (ssh root@radon-broker radon ib ...)" >&2
    exit 77
  fi
}

actor() {
  local who="${SUDO_USER:-${USER:-root}}"
  local from="${SSH_CLIENT%% *}"
  printf 'ssh:%s@%s\n' "$who" "${from:-local}"
}

audit_line() {
  logger -t radon-ib-hold -- "$*" 2>/dev/null || true
}

hold_cli() {
  "$HOLD_PYTHON" "$HOLD_CLI" "$@"
}

gateway_running() {
  [[ "$(timeout "$STEP_TIMEOUT_SECS" "$DOCKER_GW" inspect-running 2>/dev/null)" == "true" ]]
}

wait_until_stopped() {
  local poll
  for (( poll = 0; poll < RELEASE_POLLS; poll++ )); do
    gateway_running || return 0
    sleep "$RELEASE_POLL_SECS"
  done
  ! gateway_running
}

# Graceful first (releases the 2FA lease), then a root compose-down that
# ignores the deploy lock and mutex, then a kill. The hold is already set, so
# nothing can start the container back up between steps.
stop_gateway() {
  timeout "$STEP_TIMEOUT_SECS" "$GATEWAY_CONTROL" stop >/dev/null 2>&1 || true
  wait_until_stopped && return 0
  echo "radon ib: graceful stop did not converge; forcing compose-down" >&2
  timeout "$STEP_TIMEOUT_SECS" "$DOCKER_GW" compose-down >/dev/null 2>&1 || true
  wait_until_stopped && return 0
  echo "radon ib: compose-down did not converge; killing the container" >&2
  timeout "$STEP_TIMEOUT_SECS" "$DOCKER_GW" kill >/dev/null 2>&1 || true
  wait_until_stopped
}

release() {
  local reason="operator release"
  while (( $# )); do
    case "$1" in
      --reason) (( $# >= 2 )) || usage; reason="$2"; shift 2 ;;
      *) usage ;;
    esac
  done
  require_root

  local hold_written=1
  if ! hold_cli hold --reason "$reason" --actor "$(actor)" >/dev/null; then
    hold_written=0
    echo "radon ib: HOLD NOT WRITTEN; stopping the Gateway anyway" >&2
  fi
  "$SYSTEMCTL" stop "$WATCHDOG_TIMER" || true

  if ! stop_gateway; then
    echo "radon ib: FAILED - Gateway container STILL RUNNING; it may still hold the IBKR session" >&2
    audit_line "release failed: container still running"
    exit 1
  fi
  if (( hold_written == 0 )); then
    echo "radon ib: Gateway stopped but the hold was not written; a restart could log back in" >&2
    exit 1
  fi
  audit_line "released by $(actor): ${reason}"
  echo "RELEASED gateway=stopped watchdog=paused. Wait ~30s, then log in to IBKR."
  echo "When done, log out of IBKR and run: radon ib resume"
}

resume() {
  (( $# == 0 )) || usage
  require_root
  hold_cli clear --actor "$(actor)" >/dev/null
  "$SYSTEMCTL" start "$WATCHDOG_TIMER" || true
  local rc=0
  timeout "$START_TIMEOUT_SECS" "$GATEWAY_CONTROL" start || rc=$?
  if (( rc == LEASE_HELD_RC )); then
    echo "radon ib: hold cleared, but a 2FA login is already in flight; the watchdog will finish it" >&2
    exit "$rc"
  fi
  if (( rc != 0 )); then
    echo "radon ib: hold cleared, but the Gateway start failed (rc=${rc})" >&2
    exit "$rc"
  fi
  audit_line "resumed by $(actor)"
  echo "RESUMED: Approve the IBKR Mobile 2FA push now."
}

status() {
  (( $# == 0 )) || usage
  hold_cli status || true
  printf 'gateway: %s\n' "$(timeout "$STEP_TIMEOUT_SECS" "$GATEWAY_CONTROL" status 2>/dev/null || true)"
  printf 'watchdog timer: %s\n' "$("$SYSTEMCTL" is-active "$WATCHDOG_TIMER" 2>/dev/null || true)"
}

main() {
  local verb="${1:-}"
  (( $# )) && shift
  case "$verb" in
    release) release "$@" ;;
    resume) resume "$@" ;;
    status) status "$@" ;;
    *) usage ;;
  esac
}

main "$@"
