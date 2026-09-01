#!/bin/bash
#
# Autonomous LEAP IV-mispricing refresh wrapper.
#
# Runs once per trading day via radon-leap.timer on Hetzner. POSTs through
# the local FastAPI /leap/scan endpoint so the dual-write +
# service_health[leap-scan] row update happens on the same code path as a
# dashboard "Run latest →" click.
#
# Falls back to invoking leap_scanner_uw.py directly when FastAPI is
# unreachable so the file cache at least stays warm (the systemd watchdog
# will surface radon-api.service health separately).
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                python interpreter
#   RADON_LEAP_REFRESH_PRESET       UW preset (default: largecaps)
#   RADON_LEAP_REFRESH_MIN_GAP      HV-IV gap threshold (default: 10)
#   RADON_LEAP_REFRESH_FASTAPI_PORT FastAPI port (default 8321)
#   RADON_LEAP_REFRESH_FASTAPI_HOST FastAPI host (default 127.0.0.1)
#

set -u
cd "$(dirname "$0")/.."

# Load env vars from both .env files. Neither systemd nor launchd
# inherits shell env to children, so we re-source here. Parses each
# line literally rather than via `set -a; . "$tmp"; set +a` to avoid
# shell-expanding `$VAR` substrings inside values (see
# feedback_env_file_shell_expansion.md).
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
    for candidate in "${RADON_PYTHON_BIN:-}" python3.13 python3.9 /usr/bin/python3 python3; do
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
    echo "$(date): No Python interpreter available for LEAP refresh" >&2
    exit 1
fi

# Trading-day gate — LEAP IV signals are only actionable on trading days.
IS_TRADING=$("$PYTHON_BIN" - <<'PY' 2>/dev/null || echo "yes"
import sys
try:
    sys.path.insert(0, 'scripts')
    from utils.market_calendar import _is_trading_day
    from datetime import datetime
    print('yes' if _is_trading_day(datetime.now()) else 'no')
except Exception:
    print('yes')
PY
)

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday or weekend — skipping LEAP refresh"
    exit 0
fi

PRESET="${RADON_LEAP_REFRESH_PRESET:-largecaps}"
MIN_GAP="${RADON_LEAP_REFRESH_MIN_GAP:-10}"
FASTAPI_HOST="${RADON_LEAP_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_LEAP_REFRESH_FASTAPI_PORT:-8321}"
FASTAPI_TIMEOUT_SECS="${RADON_SCAN_FASTAPI_TIMEOUT_SECS:-3610}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/leap/scan?preset=${PRESET}&min_gap=${MIN_GAP}"
# Daily oneshot at 10:00 ET shares the FastAPI lane with the top-of-hour
# pile-up. 2026-08-27 14:00:20Z: instant 502 capacity shed, GARCH cleared
# the same lane at 14:02. Wait up to SHED_WAIT for a slot; keep the 3610s
# scan budget intact (TimeoutStartSec=3900 → 240s headroom).
SHED_WAIT_SECS="${RADON_LEAP_SHED_WAIT_SECS:-240}"
RETRY_DELAY="${RADON_LEAP_REFRESH_RETRY_DELAY_SECS:-15}"
# Injection point for the ladder's wait (T-283). Production leaves this as
# `sleep`; the tests substitute a recorder so the retry ladder is driven by
# the accounted budget instead of the wall clock.
SLEEP_CMD="${RADON_LEAP_SLEEP_CMD:-sleep}"
# Matches scripts/api/server.py _CAPACITY_SHED_MARKER. Status alone cannot
# tell a shed from a script-failed 502 (R-221).
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

# Try FastAPI first — preserves the dual-write + service_health path the
# dashboard "Run latest →" button uses. 3610s matches the FastAPI timeout
# for the leap preset subprocess (3600s) with 10s of slack.
echo "$(date): POST ${FASTAPI_URL}"
# R-144: only curl exit 7 (connection refused) proves the request was never
# accepted. A 502 "Subprocess capacity exhausted" means the box is ALREADY
# running too many scans, and a -m timeout means FastAPI's child may still be
# mid-flight — falling back on either launches a duplicate outside
# MAX_CONCURRENT_SUBPROCESSES and outside the cooldown. Same guard as
# run_signals_refresh.sh. Capacity shed is retried inside SHED_WAIT_SECS;
# other non-7 outcomes still refuse the direct fallback.
# T-283: budget the shed wait against the seconds this loop actually WAITS,
# not against `SECONDS` since the script started. A `SECONDS`-based deadline
# charged process startup and the first curl round trip to the wait budget, so
# on a loaded box the whole budget could be gone before the first retry
# decision and the wrapper gave up with zero retries — the exact silent
# no-retry the 2026-08-27 page was about. A shed 502 returns instantly, so in
# production the two accountings differ by milliseconds.
attempt=0
shed_waited=0
HTTP_CODE=000
CURL_EXIT=28
RESPONSE_BODY=""
while :; do
    BODY_FILE="$(mktemp)"
    HTTP_CODE=$(curl -sS -X POST -m "$FASTAPI_TIMEOUT_SECS" -o "$BODY_FILE" \
        -w "%{http_code}" "${FASTAPI_URL}" 2>/tmp/leap-refresh.curl.err)
    CURL_EXIT=$?
    RESPONSE_BODY="$(cat "$BODY_FILE" 2>/dev/null || true)"
    rm -f "$BODY_FILE"
    if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
        echo "$(date): LEAP refresh via FastAPI complete (OK)"
        exit 0
    fi
    if ! _is_capacity_shed "$CURL_EXIT" "$HTTP_CODE" "$RESPONSE_BODY"; then
        break
    fi
    remaining=$((SHED_WAIT_SECS - shed_waited))
    if [ "$remaining" -le 0 ]; then
        echo "$(date): LEAP shed for subprocess capacity (http=${HTTP_CODE}) after ${attempt} retries; not launching duplicate" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    delay="$RETRY_DELAY"
    [ "$delay" -gt "$remaining" ] && delay="$remaining"
    echo "$(date): LEAP FastAPI capacity shed (curl=${CURL_EXIT} http=${HTTP_CODE}) - retry ${attempt} in ${delay}s"
    "$SLEEP_CMD" "$delay"
    shed_waited=$((shed_waited + delay))
done

if [ "$CURL_EXIT" -ne 7 ]; then
    echo "$(date): LEAP FastAPI outcome indeterminate (curl=${CURL_EXIT}, http=${HTTP_CODE}); not launching duplicate" >&2
    exit 1
fi

# Connection refused: nothing was accepted, so a direct run is safe and
# keeps the file cache warm. service_health and Turso won't update on this
# path; the systemd watchdog surfaces radon-api.service health separately.
echo "$(date): FastAPI unavailable — fallback to direct leap_scanner_uw.py invocation"
if "$PYTHON_BIN" scripts/leap_scanner_uw.py --preset "$PRESET" --min-gap "$MIN_GAP" --json 2>>/tmp/leap-scan.err; then
    echo "$(date): LEAP fallback refresh complete (OK)"
    exit 0
else
    EXIT_CODE=$?
fi

echo "$(date): LEAP fallback refresh FAILED (exit ${EXIT_CODE})" >&2
exit "${EXIT_CODE}"
