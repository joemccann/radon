#!/usr/bin/env bash
# Install the Mac mini launchd job that dispatches the Tier-3 external
# health probe workflow every five minutes. GitHub's own `*/5` cron had
# stretched to 2-5 hour gaps by 2026-09-26, past the two-hour dead-man
# window, so the always-on runner asks for the run instead; the cron stays
# as the fallback for when the mini is down. Run ON THE MINI from any
# checkout of the repo:
#
#   bash scripts/setup_external_probe_dispatch.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.radon.external-probe-dispatch"
SRC="$REPO/config/com.radon.external-probe-dispatch.plist"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/radon-weekend/logs"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
sed "s|__HOME__|$HOME|g" "$SRC" > "$DST"
plutil -lint "$DST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DST"
launchctl enable "gui/$(id -u)/$LABEL"

if gh auth status >/dev/null 2>&1; then
  echo "gh: authenticated"
else
  echo "WARNING: gh auth status reports a problem on this host; confirm the first fire in $LOG_DIR/external-probe-dispatch.err"
fi
echo "loaded $DST (every 300s)"
echo "logs: $LOG_DIR/external-probe-dispatch.{log,err}"
