#!/bin/bash
#
# Hourly RTH refresh for scanner, flow_analysis, and discover.
#
# Fired by radon-flow-refresh.timer. POSTs through local FastAPI so the
# Turso mirror and service_health row share the SCAN path. Every POST carries
# force=true: the cooldown equals the timer period, so a jittered early fire
# would otherwise be cache-served and skip the UW spend this job exists for.
# Direct fallback only on connection-refused (curl exit 7).
#
# Discover scoring: --min-alerts 3 --dp-pages 2. evaluate.py still walks
# the full darkpool tape.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                        python interpreter
#   RADON_FLOW_REFRESH_FASTAPI_HOST         FastAPI host (default 127.0.0.1)
#   RADON_FLOW_REFRESH_FASTAPI_PORT         FastAPI port (default 8321)
#   RADON_FLOW_REFRESH_RETRIES              502/503 retries (default 2)
#   RADON_FLOW_REFRESH_RETRY_DELAY_SECS     delay between retries (default 8)
#

set -u
cd "$(dirname "$0")/.."

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
    for candidate in "${RADON_PYTHON_BIN:-}" .venv/bin/python python3.13 python3; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null 2>&1 || continue
        echo "$candidate"
        return 0
    done
    return 1
}

PYTHON_BIN=$(resolve_python)
if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): No Python interpreter available for flow refresh" >&2
    exit 1
fi

IS_TRADING=$("$PYTHON_BIN" - <<'PY' 2>/dev/null || echo "no"
import sys
try:
    sys.path.insert(0, 'scripts')
    from utils.market_calendar import market_state
    print('yes' if market_state().get('is_open') else 'no')
except Exception:
    print('no')
PY
)

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market closed - skipping flow refresh"
    exit 0
fi

FASTAPI_HOST="${RADON_FLOW_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_FLOW_REFRESH_FASTAPI_PORT:-8321}"
# Per-scan wall budget. Attempts and retry sleeps are charged against it so
# three scans fit inside TimeoutStartSec=600 by construction.
SCAN_TIMEOUT="${RADON_FLOW_REFRESH_SCAN_TIMEOUT:-180}"
# Retry transient FastAPI shedding a bounded number of times:
#   502/503 — API up but subprocess slot-cap full (2026-08-24 19:00Z:
#             all three flow POSTs 502 while /health 200; capacity exhausted)
# Connection refused (curl 7) still falls through to direct invocation.
# A timeout or other HTTP response still refuses the direct fallback so an
# accepted in-flight scan is never duplicated (BUG-013).
RETRY_LIMIT="${RADON_FLOW_REFRESH_RETRIES:-2}"
RETRY_DELAY="${RADON_FLOW_REFRESH_RETRY_DELAY_SECS:-8}"
# Distinct return code for "the general subprocess lane was full" (R-170), so
# the caller can tell a capacity shed from a real scan failure.
SHED_EXIT=75

mkdir -p logs

_retryable_flow_shed() {
    # $1 = attempt (0-based), $2 = curl exit, $3 = http code
    [ "$1" -lt "$RETRY_LIMIT" ] || return 1
    [ "$2" -eq 0 ] || return 1
    case "$3" in
        502|503) return 0 ;;
    esac
    return 1
}

refresh_scan() {
    local label="$1" endpoint="$2"
    shift 2
    local fallback_args=("$@")
    local url="http://${FASTAPI_HOST}:${FASTAPI_PORT}${endpoint}"
    local attempt=0
    local HTTP_CODE=000 CURL_EXIT=28
    local SCAN_DEADLINE=$((SECONDS + SCAN_TIMEOUT))
    local remaining delay

    echo "$(date): POST ${url}"
    while :; do
        remaining=$((SCAN_DEADLINE - SECONDS))
        if [ "$remaining" -le 0 ]; then
            echo "$(date): ${label} scan deadline (${SCAN_TIMEOUT}s) reached" >&2
            break
        fi
        HTTP_CODE=$(curl -sS -X POST -m "$remaining" -o /dev/null -w "%{http_code}" "$url")
        CURL_EXIT=$?
        if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
            echo "$(date): ${label} refresh via FastAPI complete (OK)"
            return 0
        fi
        if ! _retryable_flow_shed "$attempt" "$CURL_EXIT" "$HTTP_CODE"; then
            break
        fi
        attempt=$((attempt + 1))
        delay=$((SCAN_DEADLINE - SECONDS))
        [ "$delay" -gt "$RETRY_DELAY" ] && delay="$RETRY_DELAY"
        if [ "$delay" -le 0 ]; then
            echo "$(date): ${label} scan deadline reached before retry ${attempt}" >&2
            break
        fi
        echo "$(date): ${label} FastAPI transient (curl=${CURL_EXIT} http=${HTTP_CODE}) - retry ${attempt}/${RETRY_LIMIT} in ${delay}s"
        sleep "$delay"
    done

    # R-170: a 502/503 that survived the retries is the general subprocess
    # lane being FULL, not a fault. Capacity is
    # MAX_CONCURRENT_SUBPROCESSES(4) - RESERVED_ORDER_SLOTS(1) = 3, and the
    # top-of-hour breadth / portfolio / peer scans hold the slots, so the
    # retry budget cannot clear a multi-minute hold. This wrapper runs hourly:
    # the next slot picks the scan up.
    if [ "$CURL_EXIT" -eq 0 ] && { [ "$HTTP_CODE" = "502" ] || [ "$HTTP_CODE" = "503" ]; }; then
        echo "$(date): ${label} shed for subprocess capacity (http=${HTTP_CODE}); the next slot retries" >&2
        return "$SHED_EXIT"
    fi

    if [ "$CURL_EXIT" -ne 7 ]; then
        echo "$(date): ${label} FastAPI outcome indeterminate (curl=${CURL_EXIT}, http=${HTTP_CODE}); not launching duplicate" >&2
        return 1
    fi

    echo "$(date): ${label}: FastAPI unavailable, fallback to ${fallback_args[*]}"
    if "$PYTHON_BIN" "${fallback_args[@]}" \
        >/dev/null 2>>"logs/flow_refresh.err.log"; then
        echo "$(date): ${label} fallback refresh complete (OK)"
        return 0
    fi

    echo "$(date): ${label} refresh FAILED" >&2
    return 1
}

FAILURES=0
SHED=0

run_one() {
    refresh_scan "$@"
    case $? in
        0) ;;
        "$SHED_EXIT") SHED=$((SHED + 1)) ;;
        *) FAILURES=$((FAILURES + 1)) ;;
    esac
}

run_one "scanner" "/scan?force=true" scripts/scanner.py --top 25
run_one "flow-analysis" "/flow-analysis?force=true" scripts/flow_analysis.py
run_one "discover" "/discover?force=true" scripts/discover.py --min-alerts 3 --dp-pages 2

if [ "$FAILURES" -gt 0 ]; then
    echo "$(date): Flow refresh finished with ${FAILURES} failed scan(s)" >&2
    exit 1
fi

if [ "$SHED" -gt 0 ]; then
    echo "$(date): Flow refresh shed ${SHED} scan(s) for subprocess capacity; the next slot retries" >&2
    exit 0
fi

echo "$(date): Flow refresh complete (OK)"
exit 0
