#!/usr/bin/env bash
# Explicit operator preparation from a trusted root-owned stage. Ordinary
# source deploys do not install or enable the optional responder domain.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ACCOUNT=radon-responder
ACCOUNT_HOME=/var/lib/radon-responder
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
STAGE="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
[[ "$(id -u)" == 0 ]] || { echo 'run as root' >&2; exit 77; }
is_root_owned_tree() {
  local directory="$1" mode uid gid
  while :; do
    [[ ! -L "$directory" ]] || return 1
    read -r mode uid gid < <(stat -c '%a %u %g' -- "$directory")
    [[ "$uid" == 0 ]] || return 1
    (( (8#$mode & 8#002) == 0 )) || return 1
    (( (8#$mode & 8#020) == 0 || gid == 0 )) || return 1
    [[ "$directory" == / ]] && return 0
    directory="$(dirname -- "$directory")"
  done
}
[[ -f "$SCRIPT_DIR/grok_responder_account.py" && ! -L "$SCRIPT_DIR/grok_responder_account.py" ]] && \
is_root_owned_tree "$SCRIPT_DIR/grok_responder_account.py" && is_root_owned_tree "$STAGE/scripts" || {
  echo 'refusing: setup requires a root-owned tree' >&2
  exit 77
}
python3.13 -I "$SCRIPT_DIR/grok_responder_account.py" prepare "$STAGE"
echo "Native login: sudo -u $ACCOUNT -H $ACCOUNT_HOME/.local/bin/grok login --device-auth"
echo 'Finalize: /usr/bin/python3.13 -I /usr/local/lib/radon/grok_responder_account.py finalize --pickup-configured'
echo 'Rollback: /usr/bin/python3.13 -I /usr/local/lib/radon/grok_responder_account.py rollback'
