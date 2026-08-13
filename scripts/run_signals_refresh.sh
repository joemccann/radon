#!/bin/bash
#
# Autonomous refresh for the dashboard's "Top candidates" scans.
#
# Runs every 15 minutes during ET trading hours via
# radon-signals-refresh.timer on Hetzner. Both scans behind the panel
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
# FastAPI caps the theta child at 420s and the strength child at 480s; a full
# NDX100 pass measures ~8s. 200s covers a badly degraded UW without holding
# the unit past its next slot.
SCAN_TIMEOUT=200

mkdir -p logs

refresh_scan() {
    local label="$1" endpoint="$2" script="$3"
    local url="http://${FASTAPI_HOST}:${FASTAPI_PORT}${endpoint}?preset=${PRESET}"

    echo "$(date): POST ${url}"
    HTTP_CODE=$(curl -sS -X POST -m "$SCAN_TIMEOUT" -o /dev/null -w "%{http_code}" "$url")
    CURL_EXIT=$?
    if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
        echo "$(date): ${label} refresh via FastAPI complete (OK)"
        return 0
    fi

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
