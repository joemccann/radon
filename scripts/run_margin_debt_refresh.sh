#!/bin/bash
#
# Margin Debt Acceleration Indicator — daily refresh wrapper for launchd /
# systemd (fetch_margin_debt.py).
#
# The FINRA workbook updates once a month (~3rd week); the fetcher's
# If-Modified-Since fast path makes the unchanged days cost one 304 plus a
# snapshot heartbeat. No trading-day gate: this is a calendar-monthly macro
# source, not a market-hours writer, and the fetcher heartbeats every run.
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
    echo "$(date): No Python interpreter available for margin debt refresh"
    exit 1
fi

mkdir -p logs

echo "$(date): Refreshing margin debt series..."
"$PYTHON_BIN" scripts/fetch_margin_debt.py >/dev/null 2>>"logs/margin_debt.err.log"
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "$(date): Margin debt refresh complete (OK)"
else
    echo "$(date): Margin debt refresh failed (exit $EXIT_CODE) — keeping existing cache"
fi
exit $EXIT_CODE
