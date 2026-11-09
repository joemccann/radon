#!/usr/bin/env bash
# Install the PR branch auto-update agent (scripts/pr_branch_autoupdate.py).
# Run it on the always-on Mac mini, signed in to gh as the operator:
#   bash scripts/setup_pr_branch_autoupdate.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="$HOME/.local/share/radon/pr-autoupdate"
PLIST="$HOME/Library/LaunchAgents/com.radon.pr-branch-autoupdate.plist"

mkdir -p "$INSTALL_DIR" "$HOME/radon-weekend/logs" "$(dirname "$PLIST")"
install -m 0644 "$SRC_DIR/scripts/pr_branch_autoupdate.py" "$INSTALL_DIR/pr_branch_autoupdate.py"
sed -e "s|__HOME__|$HOME|g" "$SRC_DIR/config/com.radon.pr-branch-autoupdate.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "loaded $PLIST (every 5 minutes; log: $HOME/radon-weekend/logs/pr-branch-autoupdate.log)"
