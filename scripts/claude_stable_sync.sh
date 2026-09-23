#!/usr/bin/env bash
# Keep one fixed-path copy of the installed Claude Code binary for the nightly
# loops, so macOS privacy grants (Full Disk Access, Documents, Desktop, ...)
# survive CLI updates.
#
# Why: the native installer puts every release at
# ~/.local/share/claude/versions/<version> and repoints ~/.local/bin/claude.
# The binary is a bare Mach-O, not an .app bundle, so TCC records the grant
# against its file PATH (client_type=1 in TCC.db). Every update is a new path,
# so System Settings grew one entry per version (2.1.246, 2.1.248, ...) and the
# loops prompted again after each release.
#
# TCC stores the path plus the binary's designated requirement, which for
# Claude Code is identifier + Anthropic team, not a hash. A newer
# Anthropic-signed build copied to the same path still satisfies the stored
# grant, so the operator grants this one path once.
#
# The loop plists put $DEST_DIR ahead of ~/.local/bin, so `command -v claude`
# in every wrapper resolves here. When the copy is missing, PATH falls through
# to ~/.local/bin/claude exactly as before.
#
# Nothing is copied unless the source passes a strict signature check against
# Anthropic's Developer ID. That way an agent cannot plant a binary at the
# path the operator granted.
set -euo pipefail

SRC_LINK="${RADON_CLAUDE_SOURCE:-$HOME/.local/bin/claude}"
DEST_DIR="${RADON_CLAUDE_STABLE_DIR:-$HOME/.local/share/radon/claude-stable}"
DEST="$DEST_DIR/claude"
CODESIGN=/usr/bin/codesign
REQUIREMENT='identifier "com.anthropic.claude-code" and anchor apple generic and certificate leaf[subject.OU] = "Q6L2SF6YDW"'

log() { echo "[claude-stable] $(date -u +%FT%TZ) $*"; }

verify() {
  "$CODESIGN" --verify --strict "$1" 2>/dev/null &&
    "$CODESIGN" --verify --strict -R="$REQUIREMENT" "$1" 2>/dev/null
}

[[ -e "$SRC_LINK" ]] || { log "SKIP: $SRC_LINK does not exist"; exit 0; }
# Resolve the symlink chain to the versioned file; `cd -P` + `pwd -P` works on
# the stock macOS bash 3.2 and BSD userland (no `readlink -f` before 12.3).
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
  log "REFUSED: $src is not an Anthropic-signed Claude Code binary; stable copy left unchanged"
  exit 1
fi
if [[ -f "$DEST" ]] && cmp -s "$src" "$DEST"; then
  exit 0
fi

mkdir -p "$DEST_DIR"
chmod 0755 "$DEST_DIR"
tmp="$(mktemp "$DEST_DIR/.claude.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
# -c: APFS clone, so this takes no extra disk space. A plain copy is the
# fallback on other filesystems. It must not be a hard link:
# proc_pidpath() can report either name of a hard-linked file, so TCC could
# key on the versioned path again.
cp -c "$src" "$tmp" 2>/dev/null || cp "$src" "$tmp"
chmod 0755 "$tmp"
verify "$tmp" || { log "REFUSED: copy of $src failed verification"; exit 1; }
# rename(2) is atomic. A loop that is already running keeps the old inode.
mv -f "$tmp" "$DEST"
trap - EXIT
log "UPDATED: $DEST <- $src ($("$DEST" --version 2>/dev/null | head -1 || echo unknown))"
