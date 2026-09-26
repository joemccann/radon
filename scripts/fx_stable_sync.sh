#!/usr/bin/env bash
# Keep one fixed-path copy of the installed Vercel fx binary for the nightly
# loops, so macOS privacy grants (Full Disk Access, Documents, Desktop, ...)
# survive fx upgrades. Same shape as scripts/claude_stable_sync.sh.
#
# Why: fx is a bare Mach-O at ~/.local/bin/fx and `fx upgrade` replaces that
# file, so the operator re-added fx to Full Disk Access after upgrades. The
# loops instead run $DEST, which only this script writes.
#
# TCC stores the path plus the binary's designated requirement, which for fx
# is identifier + Vercel team, not a hash. A newer Vercel-signed build copied
# to the same path still satisfies the stored grant, so the operator grants
# this one path once.
#
# The loop wrappers' provider_bin prefers $DEST and falls through to
# ~/.local/bin/fx when the copy is missing.
#
# Nothing is copied unless the source passes a strict signature check against
# Vercel's Developer ID. That way an agent cannot plant a binary at the path
# the operator granted.
set -euo pipefail

SRC_LINK="${RADON_FX_SOURCE:-$HOME/.local/bin/fx}"
DEST_DIR="${RADON_FX_STABLE_DIR:-$HOME/.local/share/radon/fx-stable}"
DEST="$DEST_DIR/fx"
CODESIGN=/usr/bin/codesign
REQUIREMENT='identifier "com.vercel.fx" and anchor apple generic and certificate leaf[subject.OU] = "JW6Y669B67"'

log() { echo "[fx-stable] $(date -u +%FT%TZ) $*"; }

verify() {
  "$CODESIGN" --verify --strict "$1" 2>/dev/null &&
    "$CODESIGN" --verify --strict -R="$REQUIREMENT" "$1" 2>/dev/null
}

[[ -e "$SRC_LINK" ]] || { log "SKIP: $SRC_LINK does not exist"; exit 0; }
# Resolve any symlink chain; `cd -P` + `pwd -P` works on the stock macOS
# bash 3.2 and BSD userland (no `readlink -f` before 12.3).
src="$SRC_LINK"
while [[ -L "$src" ]]; do
  target="$(readlink "$src")"
  [[ "$target" == /* ]] || target="$(dirname "$src")/$target"
  src="$target"
done
src="$(cd -P "$(dirname "$src")" && pwd -P)/$(basename "$src")"

[[ -f "$src" ]] || { log "SKIP: $src is not a regular file"; exit 0; }
if [[ "$src" == "$DEST" ]]; then
  log "SKIP: $SRC_LINK already points at the stable copy"
  exit 0
fi
if ! verify "$src"; then
  log "REFUSED: $src is not a Vercel-signed fx binary; stable copy left unchanged"
  exit 1
fi
if [[ -f "$DEST" ]] && cmp -s "$src" "$DEST"; then
  exit 0
fi

mkdir -p "$DEST_DIR"
chmod 0755 "$DEST_DIR"
tmp="$(mktemp "$DEST_DIR/.fx.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
# -c: APFS clone, so this takes no extra disk space. It must not be a hard
# link: proc_pidpath() can report either name of a hard-linked file, so TCC
# could key on ~/.local/bin/fx again.
cp -c "$src" "$tmp" 2>/dev/null || cp "$src" "$tmp"
chmod 0755 "$tmp"
verify "$tmp" || { log "REFUSED: copy of $src failed verification"; exit 1; }
# rename(2) is atomic. A loop that is already running keeps the old inode.
mv -f "$tmp" "$DEST"
trap - EXIT
log "UPDATED: $DEST <- $src ($(FX_AUTO_UPGRADE=0 "$DEST" --version 2>/dev/null | head -1 || echo unknown))"
