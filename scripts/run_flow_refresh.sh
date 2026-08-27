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

# "no" is a VERDICT, not a fallback. This used to swallow twice - a bare
# `except Exception: print('no')` inside and `|| echo "no"` outside - so a
# broken venv, a renamed utils.market_calendar or a corrupt holiday calendar
# all printed "Market closed" and exited 0, on every hourly fire, with the
# three flow tabs frozen and nothing but successful oneshots to show for it.
# R-223.
IS_TRADING=$("$PYTHON_BIN" - <<'PY' 2>>"logs/flow_refresh.err.log"
import sys
sys.path.insert(0, 'scripts')
from utils.market_calendar import market_state
print('yes' if market_state().get('is_open') else 'no')
PY
) || IS_TRADING="unknown"

case "$IS_TRADING" in
    yes) ;;
    no)
        echo "$(date): Market closed - skipping flow refresh"
        exit 0
        ;;
    *)
        echo "$(date): Could not determine market state (probe failed); not skipping blind" >&2
        _flow_health "error" "market-state probe failed; flow refresh did not run"
        exit 1
        ;;
esac

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

# The API raises HTTPException(502, detail=result.error) for EVERY failure -
# capacity exhaustion, a nonzero script exit, an asyncio timeout at the script
# deadline, invalid JSON, any other exception. Status alone therefore cannot
# tell a shed from a fault, and the body is the only signal there is; the
# marker matches scripts/api/server.py's _CAPACITY_SHED_MARKER. R-221.
CAPACITY_SHED_MARKER="subprocess capacity exhausted"

_is_capacity_shed() {
    # $1 = curl exit, $2 = http code, $3 = response body
    [ "$1" -eq 0 ] || return 1
    case "$2" in
        502|503) ;;
        *) return 1 ;;
    esac
    printf '%s' "$3" | tr '[:upper:]' '[:lower:]' | grep -qF "$CAPACITY_SHED_MARKER"
}

_retryable_flow_shed() {
    # $1 = attempt (0-based), $2 = curl exit, $3 = http code, $4 = body
    [ "$1" -lt "$RETRY_LIMIT" ] || return 1
    _is_capacity_shed "$2" "$3" "$4"
}

# Best-effort durable signal. Nothing on the shed or skip paths wrote a
# service_health row, so the watchdog had no error row either. R-221 / R-265.
_flow_health() {
    RADON_FLOW_HEALTH_STATE="$1" RADON_FLOW_HEALTH_MESSAGE="$2" \
        "$PYTHON_BIN" - >>"logs/flow_refresh.err.log" 2>&1 <<'HEALTHPY' || true
import os
import sys
sys.path.insert(0, 'scripts')
try:
    from db.hrana_http import write_service_health_http
    state = os.environ["RADON_FLOW_HEALTH_STATE"]
    message = os.environ.get("RADON_FLOW_HEALTH_MESSAGE") or ""
    write_service_health_http(
        "flow-refresh",
        state,
        error=None if state == "ok" else {"message": message, "class": "flow-refresh"},
    )
except Exception as exc:
    print(f"flow-refresh health write skipped: {exc}", file=sys.stderr)
HEALTHPY
}

refresh_scan() {
    local label="$1" endpoint="$2"
    shift 2
    local fallback_args=("$@")
    local url="http://${FASTAPI_HOST}:${FASTAPI_PORT}${endpoint}"
    local attempt=0
    local HTTP_CODE=000 CURL_EXIT=28 RESPONSE_BODY="" BODY_FILE=""
    local SCAN_DEADLINE=$((SECONDS + SCAN_TIMEOUT))
    local remaining delay

    echo "$(date): POST ${url}"
    while :; do
        remaining=$((SCAN_DEADLINE - SECONDS))
        if [ "$remaining" -le 0 ]; then
            echo "$(date): ${label} scan deadline (${SCAN_TIMEOUT}s) reached" >&2
            break
        fi
        BODY_FILE="$(mktemp)"
        HTTP_CODE=$(curl -sS -X POST -m "$remaining" -o "$BODY_FILE" -w "%{http_code}" "$url")
        CURL_EXIT=$?
        RESPONSE_BODY="$(cat "$BODY_FILE" 2>/dev/null)"
        rm -f "$BODY_FILE"
        if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
            echo "$(date): ${label} refresh via FastAPI complete (OK)"
            return 0
        fi
        if ! _retryable_flow_shed "$attempt" "$CURL_EXIT" "$HTTP_CODE" "$RESPONSE_BODY"; then
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
    if _is_capacity_shed "$CURL_EXIT" "$HTTP_CODE" "$RESPONSE_BODY"; then
        echo "$(date): ${label} shed for subprocess capacity (http=${HTTP_CODE}); the next slot retries" >&2
        return "$SHED_EXIT"
    fi

    if [ "$CURL_EXIT" -ne 7 ]; then
        echo "$(date): ${label} FastAPI outcome indeterminate (curl=${CURL_EXIT}, http=${HTTP_CODE}); not launching duplicate" >&2
        return 1
    fi

    # Bounded by what is LEFT of this scan's budget. The fallback used to run
    # outside SCAN_DEADLINE entirely - connection-refused is instant, so with
    # FastAPI down all three scans reached an unbounded python run and systemd
    # SIGTERMed the cgroup mid-write. R-222.
    remaining=$((SCAN_DEADLINE - SECONDS))
    [ "$remaining" -gt 0 ] || remaining=1
    echo "$(date): ${label}: FastAPI unavailable, fallback to ${fallback_args[*]} (${remaining}s budget)"
    if timeout "${remaining}s" "$PYTHON_BIN" "${fallback_args[@]}" \
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
    # Exit 0 still, so a transient shed does not page - but the row records
    # that no scan ran, which is what makes a PERMANENT shed visible. R-265.
    _flow_health "warn" "shed ${SHED} scan(s) for subprocess capacity"
    exit 0
fi

echo "$(date): Flow refresh complete (OK)"
_flow_health "ok" ""
exit 0
