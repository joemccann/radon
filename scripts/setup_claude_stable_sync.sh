#!/usr/bin/env bash
# Install the fixed-path Claude Code copy the nightly loops run, so macOS
# privacy grants survive CLI updates (see scripts/claude_stable_sync.sh).
# Run it on the always-on runner (Mac mini):
#   bash scripts/setup_claude_stable_sync.sh
# Then grant Full Disk Access ONCE to ~/.local/share/radon/claude-stable/claude.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STABLE_DIR="$HOME/.local/share/radon/claude-stable"
PLIST="$HOME/Library/LaunchAgents/com.radon.claude-stable-sync.plist"

mkdir -p "$STABLE_DIR" "$HOME/radon-weekend/logs" "$(dirname "$PLIST")"
chmod 0755 "$STABLE_DIR"
install -m 0755 "$SRC_DIR/scripts/claude_stable_sync.sh" "$STABLE_DIR/sync.sh"
sed -e "s|__HOME__|$HOME|g" "$SRC_DIR/config/com.radon.claude-stable-sync.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl unload "$PLIST" 2>/dev/null || true
/bin/bash "$STABLE_DIR/sync.sh"
launchctl load "$PLIST"

cat <<EOF
loaded $PLIST (re-syncs on every Claude Code update, plus daily at 23:45).

One-time step: grant Full Disk Access to
  $STABLE_DIR/claude
System Settings > Privacy & Security > Full Disk Access > "+", press Cmd+Shift+G,
paste the path above. Updates keep the grant, because the path stays the same
and each new build is still Anthropic-signed.
The old per-version rows (2.1.246, 2.1.248, ...) can be removed with "-".

Re-render the loop plists so they pick up the new PATH entry:
  bash scripts/setup_security_nightly.sh   # and the other setup_* loop scripts
EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
