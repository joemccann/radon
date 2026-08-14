#!/usr/bin/env bash
# Install the laptop launchd job that turns iPhone P1 pages into a
# headless Grok diagnose-and-fix run.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/config/com.radon.grok-page-responder.plist"
DST="$HOME/Library/LaunchAgents/com.radon.grok-page-responder.plist"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$SRC" "$DST"
plutil -lint "$DST" >/dev/null
launchctl bootout "gui/$(id -u)/com.radon.grok-page-responder" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DST"
launchctl enable "gui/$(id -u)/com.radon.grok-page-responder"
launchctl kickstart -k "gui/$(id -u)/com.radon.grok-page-responder"

echo "loaded $DST"
echo "kill switch: GROK_PAGE_RESPONDER=0"
echo "logs: /tmp/radon-grok-page-responder.log"
