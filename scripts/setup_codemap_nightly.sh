#!/usr/bin/env bash
# Install the 02:00 codemap refresh on the always-on runner (Mac mini):
#   bash scripts/setup_codemap_nightly.sh
set -euo pipefail

ROOT="${RADON_WEEKEND_ROOT:-$HOME/radon-weekend}"
CLONE="$ROOT/radon-codemap"
SRC_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIGIN_URL="$(git -C "$SRC_REPO" config --get remote.origin.url)"
PLIST="$HOME/Library/LaunchAgents/com.radon.codemap-nightly.plist"

command -v python3.13 >/dev/null || { echo "MISSING python3.13" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "MISSING gh auth" >&2; exit 1; }

mkdir -p "$ROOT/logs" "$(dirname "$PLIST")"
[[ -d "$CLONE/.git" ]] || git clone "$ORIGIN_URL" "$CLONE"
git -C "$CLONE" fetch -q origin
git -C "$CLONE" checkout -f -q main
git -C "$CLONE" reset --hard -q origin/main

sed -e "s|__CODEMAP_REPO__|$CLONE|g" -e "s|__HOME__|$HOME|g" \
  "$CLONE/config/com.radon.codemap-nightly.plist" > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "loaded $PLIST (daily 02:00). Smoke test: bash $CLONE/scripts/codemap_nightly.sh"
