#!/bin/bash
set -euo pipefail

# Runs as radon from the immutable deploy runner, before deploy.sh takes the
# deploy lock. Asks root to reconcile the installed control-plane bundle with
# the GitHub main tip (`radon-deploy-root sync-control-plane`), so a release
# that edits the helper, sudoers, polkit, a control-plane unit or a drop-in
# passes deploy.sh's manifest preflight without a human bootstrap over SSH.
# Exit 75 (bootstrap saw the deploy lock held or a pending transition) is
# retried and then handed to deploy.sh, which takes that lock itself. Every
# other failure is propagated: bootstrap REJECTED the tip bundle (bash -n,
# visudo, node --check, systemd-analyze) or the root helper TERM/KILLed it at
# its deadline (124). Exiting 0 there let deploy.sh's preflight treat
# "installed differs" as "refresh-control-plane applies it after promote" and
# reinstall the rejected bundle with the app tier stopped. The rejection is
# also recorded for deploy.sh, which refuses that arm while it exists. R-437.
#
# The tested SHA is passed through as the expected tip so the helper installs
# the commit CI proved green, not whatever GitHub main happens to be when root
# runs. The helper has always checked an expected SHA; nothing used to supply
# one. The sudoers line granting the pinned form ships in the bundle this verb
# installs, so until a host has taken that release the pinned grant is absent
# and we fall back to the unpinned call with a warning rather than fail the
# deploy. DS-2026-10-18-01.

readonly HELPER="${RADON_DEPLOY_ROOT_HELPER:-/usr/local/sbin/radon-deploy-root}"
readonly REJECTED="${RADON_CONTROL_PLANE_REJECTED:-/home/radon/.radon-control-plane-rejected}"
readonly SUDO="${RADON_SYNC_SUDO:-sudo}"
readonly SLEEP="${RADON_SYNC_SLEEP:-sleep}"
readonly RETRIES="${RADON_SYNC_RETRIES:-6}"
readonly RETRY_WAIT="${RADON_SYNC_RETRY_WAIT:-20}"

sync_is_granted() {
  "$SUDO" -n -l -- "$HELPER" sync-control-plane "$@" >/dev/null 2>&1
}

main() {
  local attempt rc expected="${1:-}"
  local -a pin=()
  if (( $# > 1 )) || [[ -n "$expected" && ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
    echo "usage: sync-control-plane.sh [<40-hex expected main SHA>]" >&2
    return 64
  fi
  if [[ -n "$expected" ]] && sync_is_granted "$expected"; then
    pin=("$expected")
  else
    if [[ -n "$expected" ]]; then
      echo "[control-plane] the SHA-pinned grant is not installed yet; falling back to the unpinned sync-control-plane for this release" >&2
    fi
    if ! sync_is_granted; then
      echo "[control-plane] sync-control-plane is not granted yet; run cloud/scripts/bootstrap-control-plane.sh once as root" >&2
      return 0
    fi
  fi
  for attempt in $(seq 1 "$RETRIES"); do
    if "$SUDO" -n "$HELPER" sync-control-plane "${pin[@]}"; then
      echo "[control-plane] installed control plane matches the GitHub main tip"
      rm -f -- "$REJECTED"
      return 0
    else
      rc=$?
    fi
    if (( rc != 75 )); then
      printf '%s\n' "$rc" > "$REJECTED"
      echo "[control-plane] sync-control-plane failed (exit ${rc}); refusing to deploy an unreconciled control plane (recorded in ${REJECTED})" >&2
      return "$rc"
    fi
    echo "[control-plane] deploy lock or transition pending; retry ${attempt}/${RETRIES} in ${RETRY_WAIT}s" >&2
    "$SLEEP" "$RETRY_WAIT"
  done
  echo "[control-plane] sync-control-plane still blocked after ${RETRIES} attempts; deploy.sh preflight decides" >&2
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
