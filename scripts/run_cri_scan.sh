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
echo "$(date): Running CRI scan..."
python3 scripts/cri_scan.py --json > "data/cri_scheduled/cri-${TIMESTAMP}.json" 2>>"logs/cri-scan.err.log"
EXIT_CODE=$?
echo "$(date): CRI scan complete (exit $EXIT_CODE)"
exit $EXIT_CODE
