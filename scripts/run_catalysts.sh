#!/bin/bash
#
# FU4 — Scheduled catalyst feed wrapper (F3 fetch_catalysts.py).
#
# Trading-day refresh of the earnings / FDA / economic catalyst stream.
# Holiday-aware: skips on non-trading days. Writes data/catalysts.json (the
# fetcher itself owns the write); this wrapper just gates + logs.
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
    echo "$(date): No Python interpreter available for catalyst fetch"
    exit 1
fi

mkdir -p logs

IS_TRADING=$("$PYTHON_BIN" -c "
import sys; sys.path.insert(0, 'scripts')
from utils.market_calendar import _is_trading_day
from datetime import datetime
print('yes' if _is_trading_day(datetime.now()) else 'no')
" 2>/dev/null || echo "yes")

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday — skipping catalyst fetch"
    # Heartbeat the skip: the writer ran and correctly decided not to fetch.
    # Without this the row ages through the holiday and flags stale
    # (see feedback_service_health_heartbeat — ok every cycle, or rows latch).
    "$PYTHON_BIN" -c "
import sys; sys.path.insert(0, 'scripts')
from datetime import datetime, timezone
from db import writer
writer.record_service_health('catalysts', 'ok', finished_at=datetime.now(timezone.utc).isoformat())
" 2>>"logs/catalysts.err.log" || echo "$(date): Holiday heartbeat write failed (non-fatal)"
    exit 0
fi

echo "$(date): Fetching catalysts..."
"$PYTHON_BIN" scripts/fetch_catalysts.py >/dev/null 2>>"logs/catalysts.err.log"
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "$(date): Catalyst fetch complete (OK)"
else
    echo "$(date): Catalyst fetch failed (exit $EXIT_CODE) — keeping existing cache"
fi
exit $EXIT_CODE
