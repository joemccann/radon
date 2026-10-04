#!/usr/bin/env bash
# install-grok-upgrade-controller.sh <scripts-dir> [target]
#
# Installs the Grok CLI upgrader's code root-owned (DS-2026-09-29-07). The
# upgrade unit promotes the binary the secret-bearing subscription-tokens
# unit executes, so it must not run code from the responder clone, which
# grok rewrites over untrusted page text. <scripts-dir> must be the repo
# `scripts/` tree from setup-grok-page-responder.sh's root-owned stage.
# The upgrader's import closure is stdlib-only and runs on the system
# interpreter (`python3.13 -E -S`), so no venv is copied.
set -euo pipefail

SRC="${1:?usage: install-grok-upgrade-controller.sh <scripts-dir> [target]}"
TARGET="${2:-/usr/local/lib/radon/grok-upgrade}"
PARENT="$(dirname -- "$TARGET")"

if [[ -L "$SRC" || ! -d "$SRC" || ! -f "$SRC/grok_upgrade.py" ]]; then
  echo "refusing: $SRC is not a scripts tree with grok_upgrade.py" >&2
  exit 1
fi
if [[ ! -e "$PARENT" ]]; then
  install -d -m 0755 "$PARENT"
fi
if [[ -L "$PARENT" || ! -d "$PARENT" || -L "$TARGET" ]]; then
  echo "refusing: $TARGET or its parent is a link or not a directory" >&2
  exit 1
fi
if [[ -e "$TARGET" && ! -d "$TARGET" ]]; then
  echo "refusing: $TARGET is not a directory" >&2
  exit 1
fi

new="$(mktemp -d "$PARENT/.grok-upgrade.new.XXXXXX")"
old=""
cleanup() {
  local status=$?
  # R-724 / REL-305: preserve the trusted tree if the second rename fails.
  # A failed rollback retains its backup for explicit operator recovery.
  if [[ -n "$old" && -d "$old/tree" && ! -e "$TARGET" ]]; then
    if ! mv -- "$old/tree" "$TARGET"; then
      echo "controller rollback failed; previous tree retained at $old/tree" >&2
      rm -rf -- "$new"
      return "$status"
    fi
  fi
  rm -rf -- "$new" ${old:+"$old"}
  return "$status"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP
cp -R -- "$SRC" "$new/scripts"
find "$new" -name __pycache__ -prune -exec rm -rf -- {} +
if [[ "${RADON_HELPER_SKIP_CHOWN:-0}" != "1" ]]; then
  chown -R root:root "$new"
fi
chmod -R u+rwX,go+rX,go-w "$new"
chmod 0755 "$new"

# Two renames: the old tree moves aside, then the new one takes its name.
# A fire inside that window fails to start and retries on the next timer.
if [[ -e "$TARGET" ]]; then
  old="$(mktemp -d "$PARENT/.grok-upgrade.old.XXXXXX")"
  mv -- "$TARGET" "$old/tree"
fi
mv -- "$new" "$TARGET"
echo "grok upgrade controller installed at $TARGET"
