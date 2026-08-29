#!/bin/bash
set -euo pipefail

# Runs as radon from the immutable deploy runner, before deploy.sh takes the
# deploy lock. Asks root to reconcile the installed control-plane bundle with
# the GitHub main tip (`radon-deploy-root sync-control-plane`), so a release
# that edits the helper, sudoers, polkit, a control-plane unit or a drop-in
# passes deploy.sh's manifest preflight without a human bootstrap over SSH.
# Always exits 0 on a sync failure: deploy.sh's preflight is the authority
# and prints the exact incompatibility. Exit 75 (bootstrap saw the deploy
# lock held or a pending transition) is retried; everything else is logged.

readonly HELPER="${RADON_DEPLOY_ROOT_HELPER:-/usr/local/sbin/radon-deploy-root}"
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
      return 0
    else
      rc=$?
    fi
    if (( rc != 75 )); then
      echo "[control-plane] sync-control-plane failed (exit ${rc}); deploy.sh preflight decides" >&2
      return 0
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
