#!/bin/bash
#
# Autonomous market-wide OI-changes refresh wrapper.
#
# Runs 3x per US trading day via radon-oi-changes.timer on Hetzner
# (14:00 / 17:00 / 20:00 UTC Mon-Fri — open / mid-day / near-close).
# Invokes scripts/fetch_oi_changes.py --market, which dual-writes the
# Turso oi_changes snapshot + service_health[oi-changes] row via
# mirror_scan_snapshot (market-wide only; per-ticker eval lookups do
# not heartbeat so they cannot mask a dead scheduled scan).
#
# No FastAPI path: oi-changes has no scan route; the producer is the
# CLI. UW-only data flow — no IB dependency.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                   python interpreter
#   RADON_OI_CHANGES_MIN_PREMIUM       min premium filter (default 0)
#   RADON_OI_CHANGES_MIN_OI_CHANGE     min OI delta filter (default 0)
#   RADON_OI_CHANGES_LIMIT             max results (default 50)
#

set -u
cd "$(dirname "$0")/.."

# Load env vars from both .env files. Literal parser (not `set -a`) so
# values containing `$` are not shell-expanded. See
# feedback_env_file_shell_expansion.md.
_load_env() {
    local f="$1"
    [ -f "$f" ] || return
    local line key value first last
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [ -n "$line" ] || continue
        case "$line" in
            \#*) continue ;;
            export\ *) line="${line#export }" ;;
        esac
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        key="${key%"${key##*[![:space:]]}"}"
        value="${value#"${value%%[![:space:]]*}"}"
        value="${value%"${value##*[![:space:]]}"}"
        [ -n "$key" ] || continue
        if [ "${#value}" -ge 2 ]; then
            first="${value:0:1}"
            last="${value: -1}"
            if { [ "$first" = "'" ] && [ "$last" = "'" ]; } || { [ "$first" = '"' ] && [ "$last" = '"' ]; }; then
                value="${value:1:${#value}-2}"
            fi
        fi
        export "$key=$value"
    done < "$f"
}
_load_env "web/.env"
_load_env ".env"

resolve_python() {
    local candidate
    for candidate in "${RADON_PYTHON_BIN:-}" python3.13 python3.11 python3.9 /usr/bin/python3 python3; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null 2>&1 || continue
        "$candidate" -c "import sys" >/dev/null 2>&1 || continue
        echo "$candidate"
        return 0
    done
    return 1
}

PYTHON_BIN=$(resolve_python)
if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): No Python interpreter available for OI-changes refresh" >&2
    exit 1
fi

mkdir -p logs

# Trading-day gate — OI settles on session days; holidays/weekends skip
# with an ok heartbeat so the row does not age through a long weekend.
IS_TRADING=$("$PYTHON_BIN" - <<'PY' 2>/dev/null || echo "yes"
import sys
try:
    sys.path.insert(0, 'scripts')
    from utils.market_calendar import _is_trading_day
    from datetime import datetime
    print('yes' if _is_trading_day(datetime.now()) else 'no')
except Exception:
    # Fail-open: prefer running over silently skipping if calendar import fails.
    print('yes')
PY
)

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday or weekend — skipping OI-changes refresh"
    "$PYTHON_BIN" -c "
import sys; sys.path.insert(0, 'scripts')
from datetime import datetime, timezone
from db import writer
writer.record_service_health('oi-changes', 'ok', finished_at=datetime.now(timezone.utc).isoformat())
" 2>>"logs/oi_changes.err.log" || echo "$(date): Holiday heartbeat write failed (non-fatal)"
    exit 0
fi

MIN_PREMIUM="${RADON_OI_CHANGES_MIN_PREMIUM:-0}"
MIN_OI_CHANGE="${RADON_OI_CHANGES_MIN_OI_CHANGE:-0}"
LIMIT="${RADON_OI_CHANGES_LIMIT:-50}"

echo "$(date): Fetching market-wide OI changes (min_premium=${MIN_PREMIUM}, min_oi_change=${MIN_OI_CHANGE}, limit=${LIMIT})"
if "$PYTHON_BIN" scripts/fetch_oi_changes.py --market \
    --min-premium "$MIN_PREMIUM" \
    --min-oi-change "$MIN_OI_CHANGE" \
    --limit "$LIMIT" \
    --json >/dev/null 2>>"logs/oi_changes.err.log"; then
    echo "$(date): OI-changes refresh complete (OK)"
    exit 0
else
    EXIT_CODE=$?
fi

echo "$(date): OI-changes refresh FAILED (exit ${EXIT_CODE})" >&2
exit "${EXIT_CODE}"
