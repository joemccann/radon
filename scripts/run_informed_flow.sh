#!/bin/bash
#
# FU4 — Informed-flow watchlist sweep wrapper for launchd (F4).
#
# fetch_informed_flow.py is per-ticker; this drives the sweep entrypoint
# (sweep_informed_flow.py) which iterates the Turso watchlist. Holiday-aware:
# congress / insider / institutional data only changes on filing (business)
# days, so a non-trading-day run is wasted UW budget.
#

cd "$(dirname "$0")/.."

resolve_python() {
    local candidate
    for candidate in "${RADON_PYTHON_BIN:-}" python3.13 python3.11 python3.9 /usr/bin/python3 python3; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null 2>&1 || continue
        echo "$candidate"
        return 0
    done
    return 1
}

PYTHON_BIN=$(resolve_python)
if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): No Python interpreter available for informed-flow sweep"
    exit 1
fi

IS_TRADING=$("$PYTHON_BIN" -c "
import sys; sys.path.insert(0, 'scripts')
from utils.market_calendar import _is_trading_day
from datetime import datetime
print('yes' if _is_trading_day(datetime.now()) else 'no')
" 2>/dev/null || echo "yes")

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday — skipping informed-flow sweep"
    exit 0
fi

mkdir -p logs
echo "$(date): Sweeping watchlist for informed flow..."
"$PYTHON_BIN" scripts/sweep_informed_flow.py >/dev/null 2>>"logs/informed-flow.err.log"
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "$(date): Informed-flow sweep complete (OK)"
else
    echo "$(date): Informed-flow sweep failed (exit $EXIT_CODE) — keeping existing caches"
fi
exit $EXIT_CODE
