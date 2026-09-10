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

readonly HELPER="${RADON_DEPLOY_ROOT_HELPER:-/usr/local/sbin/radon-deploy-root}"
readonly REJECTED="${RADON_CONTROL_PLANE_REJECTED:-/home/radon/.radon-control-plane-rejected}"
readonly SUDO="${RADON_SYNC_SUDO:-sudo}"
readonly SLEEP="${RADON_SYNC_SLEEP:-sleep}"
readonly RETRIES="${RADON_SYNC_RETRIES:-6}"
readonly RETRY_WAIT="${RADON_SYNC_RETRY_WAIT:-20}"

sync_is_granted() {
  "$SUDO" -n -l -- "$HELPER" sync-control-plane >/dev/null 2>&1
}

main() {
  local attempt rc
  if ! sync_is_granted; then
    echo "[control-plane] sync-control-plane is not granted yet; run cloud/scripts/bootstrap-control-plane.sh once as root" >&2
    return 0
  fi
  for attempt in $(seq 1 "$RETRIES"); do
    if "$SUDO" -n "$HELPER" sync-control-plane; then
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
