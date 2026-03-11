#!/bin/bash
#
# Holiday-aware CRI scan wrapper for launchd
#
# Checks if today is a trading day (weekday + not holiday) before running
# cri_scan.py --json. Saves timestamped output to data/cri_scheduled/.
#

cd "$(dirname "$0")/.."

# Check if today is a trading day (reuses market_holidays.json)
IS_TRADING=$(python3 -c "
import sys; sys.path.insert(0, 'scripts')
from utils.market_calendar import _is_trading_day
from datetime import datetime
print('yes' if _is_trading_day(datetime.now()) else 'no')
" 2>/dev/null || echo "yes")

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday — skipping CRI scan"
    exit 0
fi

mkdir -p data/cri_scheduled logs
TIMESTAMP=$(TZ=America/New_York date +"%Y-%m-%dT%H-%M")
OUT_PATH="data/cri_scheduled/cri-${TIMESTAMP}.json"
TMP_PATH=$(mktemp "data/cri_scheduled/.cri-${TIMESTAMP}.XXXXXX.tmp")
echo "$(date): Running CRI scan..."
python3 scripts/cri_scan.py --json > "$TMP_PATH" 2>>"logs/cri-scan.err.log"
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv "$TMP_PATH" "$OUT_PATH"
    cp "$OUT_PATH" data/cri.json
    echo "$(date): CRI scan complete (OK) → $OUT_PATH"
else
    rm -f "$TMP_PATH"
    echo "$(date): CRI scan failed (exit $EXIT_CODE) — keeping existing CRI caches"
fi
exit $EXIT_CODE
