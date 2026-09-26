#!/usr/bin/env bash
# Install the fixed-path Vercel fx copy the nightly loops run, so macOS
# privacy grants survive fx upgrades (see scripts/fx_stable_sync.sh).
# Run it on the always-on runner (Mac mini):
#   bash scripts/setup_fx_stable_sync.sh
# Then grant Full Disk Access ONCE to ~/.local/share/radon/fx-stable/fx.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STABLE_DIR="$HOME/.local/share/radon/fx-stable"
PLIST="$HOME/Library/LaunchAgents/com.radon.fx-stable-sync.plist"

mkdir -p "$STABLE_DIR" "$HOME/radon-weekend/logs" "$(dirname "$PLIST")"
chmod 0755 "$STABLE_DIR"
install -m 0755 "$SRC_DIR/scripts/fx_stable_sync.sh" "$STABLE_DIR/sync.sh"
sed -e "s|__HOME__|$HOME|g" "$SRC_DIR/config/com.radon.fx-stable-sync.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl unload "$PLIST" 2>/dev/null || true
/bin/bash "$STABLE_DIR/sync.sh"
launchctl load "$PLIST"

cat <<EOF
loaded $PLIST (re-syncs on every fx upgrade, plus daily at 23:45).

One-time step: grant Full Disk Access to
  $STABLE_DIR/fx
System Settings > Privacy & Security > Full Disk Access > "+", press Cmd+Shift+G,
paste the path above. Upgrades keep the grant, because the path stays the same
and each new build is still Vercel-signed.
An old ~/.local/bin/fx row can be removed with "-".
EOF
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles" 2>/dev/null || true
