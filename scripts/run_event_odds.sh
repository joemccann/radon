#!/bin/bash
#
# FU4 — Polymarket event-odds overlay wrapper for launchd (F5).
#
# fetch_event_odds.py is per-ticker and currently maps macro / index markets.
# This refreshes the representative index set a few times a day so the divergence
# vs options-skew is fresh through the session. Holiday-aware.
#
# Scope: the macro/index tickers F5 maps today. Extend RADON_EVENT_ODDS_TICKERS
# (space-separated) to add symbols without touching the plist cadence.
#

cd "$(dirname "$0")/.."

EVENT_ODDS_TICKERS="${RADON_EVENT_ODDS_TICKERS:-SPX NDX RUT VIX}"

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
    echo "$(date): No Python interpreter available for event-odds fetch"
    exit 1
fi

IS_TRADING=$("$PYTHON_BIN" -c "
import sys; sys.path.insert(0, 'scripts')
from utils.market_calendar import _is_trading_day
from datetime import datetime
print('yes' if _is_trading_day(datetime.now()) else 'no')
" 2>/dev/null || echo "yes")

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday — skipping event-odds fetch"
    exit 0
fi

mkdir -p logs
OVERALL=0
for TICKER in $EVENT_ODDS_TICKERS; do
    echo "$(date): Fetching event odds for $TICKER..."
    "$PYTHON_BIN" scripts/fetch_event_odds.py "$TICKER" >/dev/null 2>>"logs/event-odds.err.log"
    CODE=$?
    if [ "$CODE" -ne 0 ]; then
        echo "$(date): Event-odds fetch failed for $TICKER (exit $CODE)"
        OVERALL=$CODE
    fi
done
if [ "$OVERALL" -eq 0 ]; then
    echo "$(date): Event-odds fetch complete (OK)"
fi
exit $OVERALL
