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
SCAN_TIMEOUT=180

mkdir -p logs

refresh_scan() {
    local label="$1" endpoint="$2"
    shift 2
    local fallback_args=("$@")
    local url="http://${FASTAPI_HOST}:${FASTAPI_PORT}${endpoint}"

    echo "$(date): POST ${url}"
    HTTP_CODE=$(curl -sS -X POST -m "$SCAN_TIMEOUT" -o /dev/null -w "%{http_code}" "$url")
    CURL_EXIT=$?
    if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
        echo "$(date): ${label} refresh via FastAPI complete (OK)"
        return 0
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
refresh_scan "scanner" "/scan?force=true" scripts/scanner.py --top 25 \
    || FAILURES=$((FAILURES + 1))
refresh_scan "flow-analysis" "/flow-analysis?force=true" scripts/flow_analysis.py \
    || FAILURES=$((FAILURES + 1))
refresh_scan "discover" "/discover?force=true" scripts/discover.py --min-alerts 3 --dp-pages 2 \
    || FAILURES=$((FAILURES + 1))

if [ "$FAILURES" -gt 0 ]; then
    echo "$(date): Flow refresh finished with ${FAILURES} failed scan(s)" >&2
    exit 1
fi

echo "$(date): Flow refresh complete (OK)"
exit 0
