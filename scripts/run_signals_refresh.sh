#!/bin/bash
#
# Autonomous refresh for the dashboard's "Top candidates" scans.
#
# Fired by radon-signals-refresh.timer on Hetzner during ET trading hours;
# the timer's OnCalendar is the cadence SoT (hourly as of 2026-08-16,
# matching the 3600s FastAPI scan cooldown). Both scans behind the panel
# (theta harvester, 7-step strength confirmation) shipped with a FastAPI
# endpoint, a Turso mirror and a service_health row but no scheduler, so the
# panel served whatever snapshot a human had last triggered by hand.
#
# POSTs through the local FastAPI scan endpoints so the cache +
# service_health row + Turso mirror all happen on the same code path a
# manual "SCAN NDX" click uses. Falls back to invoking the scanner directly
# when FastAPI is unreachable so the file cache at least stays warm.
#
# One scan failing never skips the other — the panel's two tabs are
# independent — but any failure still exits non-zero so the unit watchdog
# sees it.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                     python interpreter
#   RADON_SIGNALS_REFRESH_PRESET         UW preset (default: ndx100)
#   RADON_SIGNALS_REFRESH_FASTAPI_HOST   FastAPI host (default 127.0.0.1)
#   RADON_SIGNALS_REFRESH_FASTAPI_PORT   FastAPI port (default 8321)
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

# The repo venv comes first: it is the only interpreter with the UW client's
# dependencies installed, so a bare `python3` fallback would fail on import
# (see feedback_scan_wrapper_fallback_picks_system_python.md).
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
    echo "$(date): No Python interpreter available for signals refresh" >&2
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
    echo "$(date): Market closed — skipping signals refresh"
    exit 0
fi

PRESET="${RADON_SIGNALS_REFRESH_PRESET:-ndx100}"
FASTAPI_HOST="${RADON_SIGNALS_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_SIGNALS_REFRESH_FASTAPI_PORT:-8321}"
# FastAPI caps the theta child at 420s and the strength child at 480s.
# Curl -m must outlive both: a shorter abort disconnects, Starlette
# cancels the request, run_script kills the scanner, and the oneshot
# pages P1 (Result=exit-code, NRestarts=0) on every hourly fire.
# Default 200 stays under TimeoutStartSec=450 on hosts that have not
# yet installed the 1050s unit (which exports RADON_SIGNALS_SCAN_TIMEOUT=490).
SCAN_TIMEOUT="${RADON_SIGNALS_SCAN_TIMEOUT:-200}"
# Retry transient FastAPI shedding a bounded number of times:
#   502/503 — API up but subprocess slot-cap full (2026-08-21 14:00Z:
#             both signals POSTs 502 while /health 200; capacity exhausted)
# Connection refused (curl 7) still falls through to direct invocation.
# A timeout or other HTTP response still refuses the direct fallback so an
# accepted in-flight scan is never duplicated (BUG-013).
RETRY_LIMIT="${RADON_SIGNALS_REFRESH_RETRIES:-2}"
RETRY_DELAY="${RADON_SIGNALS_REFRESH_RETRY_DELAY_SECS:-8}"
# R-093: the retry ladder used to give every attempt a FRESH -m and charged
# the sleeps nowhere, so one scan's worst case was 3x490+2x8 = 1486s and two
# scans 2972s against TimeoutStartSec=1050. systemd then SIGTERMed the group
# mid-write: Result=signal, partial snapshot, P1 page outside a deploy window.
# SCAN_TIMEOUT is now the whole scan's wall budget — attempts and retry
# sleeps are charged against it — so two scans plus the calendar probe fit
# inside the unit cap by construction.
PROBE_BUDGET_SECS="${RADON_SIGNALS_PROBE_BUDGET_SECS:-60}"

mkdir -p logs

_retryable_signals_shed() {
    # $1 = attempt (0-based), $2 = curl exit, $3 = http code
    [ "$1" -lt "$RETRY_LIMIT" ] || return 1
    [ "$2" -eq 0 ] || return 1
    case "$3" in
        502|503) return 0 ;;
    esac
    return 1
}

refresh_scan() {
    local label="$1" endpoint="$2" script="$3"
    local url="http://${FASTAPI_HOST}:${FASTAPI_PORT}${endpoint}?preset=${PRESET}"
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
        if ! _retryable_signals_shed "$attempt" "$CURL_EXIT" "$HTTP_CODE"; then
            break
        fi
        attempt=$((attempt + 1))
        # The retry sleep is charged against the same deadline.
        delay=$((SCAN_DEADLINE - SECONDS))
        [ "$delay" -gt "$RETRY_DELAY" ] && delay="$RETRY_DELAY"
        if [ "$delay" -le 0 ]; then
            echo "$(date): ${label} scan deadline reached before retry ${attempt}" >&2
            break
        fi
        echo "$(date): ${label} FastAPI transient (curl=${CURL_EXIT} http=${HTTP_CODE}) — retry ${attempt}/${RETRY_LIMIT} in ${delay}s"
        sleep "$delay"
    done

    # A timeout or HTTP response means FastAPI may have accepted the scan.
    # Starting a direct fallback then can duplicate the provider/IB job.
    if [ "$CURL_EXIT" -ne 7 ]; then
        echo "$(date): ${label} FastAPI outcome indeterminate (curl=${CURL_EXIT}, http=${HTTP_CODE}); not launching duplicate" >&2
        return 1
    fi

    # Connection refused: the request was not accepted, so direct fallback is safe.
    echo "$(date): ${label} — FastAPI unavailable, fallback to direct ${script}"
    if "$PYTHON_BIN" "scripts/${script}" --preset "$PRESET" --json \
        >/dev/null 2>>"logs/signals_refresh.err.log"; then
        echo "$(date): ${label} fallback refresh complete (OK)"
        return 0
    fi

    echo "$(date): ${label} refresh FAILED" >&2
    return 1
}

FAILURES=0
refresh_scan "Theta harvester" "/theta-harvester/scan" "theta_harvester_scanner.py" \
    || FAILURES=$((FAILURES + 1))
refresh_scan "Strength confirmation" "/strength-confirmation/scan" "strength_confirmation_scanner.py" \
    || FAILURES=$((FAILURES + 1))

if [ "$FAILURES" -gt 0 ]; then
    echo "$(date): Signals refresh finished with ${FAILURES} failed scan(s)" >&2
    exit 1
fi

echo "$(date): Signals refresh complete (OK)"
exit 0
