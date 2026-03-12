#!/bin/bash
#
# Holiday-aware CRI scan wrapper for launchd
#
# Checks if today is a trading day (weekday + not holiday) before running
# cri_scan.py --json. Saves timestamped output to data/cri_scheduled/.
#

cd "$(dirname "$0")/.."

resolve_python() {
    local candidate
    for candidate in "${RADON_PYTHON_BIN:-}" python3.9 /usr/bin/python3 python3; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null 2>&1 || continue
        "$candidate" - <<'PY' >/dev/null 2>&1
import importlib.util
required = ("ib_insync",)
raise SystemExit(0 if all(importlib.util.find_spec(name) for name in required) else 1)
PY
        if [ $? -eq 0 ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN=$(resolve_python)
if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): No Python interpreter with ib_insync available for CRI scan"
    exit 1
fi

# Check if today is a trading day (reuses market_holidays.json)
IS_TRADING=$("$PYTHON_BIN" -c "
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
"$PYTHON_BIN" scripts/cri_scan.py --json > "$TMP_PATH" 2>>"logs/cri-scan.err.log"
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
