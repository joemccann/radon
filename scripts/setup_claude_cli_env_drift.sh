#!/usr/bin/env bash
# Install the 23:50 Claude Code env-drift check on the always-on runner (Mac mini):
#   bash scripts/setup_claude_cli_env_drift.sh
set -euo pipefail

ROOT="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
REPO="$ROOT/radon-security"
PLIST="$HOME/Library/LaunchAgents/com.radon.claude-cli-env-drift.plist"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/com.radon.claude-cli-env-drift.plist"

[[ -d "$REPO/.git" ]] || { echo "MISSING runner clone $REPO" >&2; exit 1; }
mkdir -p "$ROOT/logs" "$(dirname "$PLIST")"
sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$SRC" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "loaded $PLIST (daily 23:50). Smoke test: /usr/bin/python3 -I $REPO/scripts/claude_cli_env_drift.py"
