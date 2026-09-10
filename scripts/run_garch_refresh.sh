#!/bin/bash
#
# Autonomous GARCH convergence refresh wrapper.
#
# Runs 3x per trading day via radon-garch.timer on Hetzner (14:00, 17:00,
# 20:00 UTC Mon-Fri — early session, mid-day, near-close). POSTs through
# the local FastAPI /garch-convergence/scan endpoint so the dual-write +
# service_health[garch-scan] row update happens on the same code path as
# a dashboard "Run latest →" click.
#
# Falls back to invoking garch_convergence.py directly when FastAPI is
# unreachable so the file cache at least stays warm.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                 python interpreter
#   RADON_GARCH_REFRESH_PRESET       Scanner preset (default: largecaps)
#   RADON_GARCH_REFRESH_FASTAPI_PORT FastAPI port (default 8321)
#   RADON_GARCH_REFRESH_FASTAPI_HOST FastAPI host (default 127.0.0.1)
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
    echo "$(date): No Python interpreter available for GARCH refresh" >&2
    exit 1
fi

# Trading-day gate. IV repricing signals only matter on trading days.
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
    echo "$(date): Market holiday or weekend — skipping GARCH refresh"
    exit 0
fi

PRESET="${RADON_GARCH_REFRESH_PRESET:-largecaps}"
FASTAPI_HOST="${RADON_GARCH_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_GARCH_REFRESH_FASTAPI_PORT:-8321}"
FASTAPI_TIMEOUT_SECS="${RADON_SCAN_FASTAPI_TIMEOUT_SECS:-3610}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/garch-convergence/scan?preset=${PRESET}"
# 14:00 UTC oneshot shares the FastAPI lane with leap and the top-of-hour
# pile-up. 2026-09-01 14:00:12Z: instant 502 capacity shed, LEAP cleared
# the same lane at 14:02. Wait up to SHED_WAIT for a slot; keep the 3610s
# scan budget intact (TimeoutStartSec=3900 → 240s headroom).
SHED_WAIT_SECS="${RADON_GARCH_SHED_WAIT_SECS:-240}"
RETRY_DELAY="${RADON_GARCH_REFRESH_RETRY_DELAY_SECS:-15}"
# Injection point for the ladder's wait (T-283). Production leaves this as
# `sleep`; the tests substitute a recorder so the retry ladder is driven by
# the accounted budget instead of the wall clock.
SLEEP_CMD="${RADON_GARCH_SLEEP_CMD:-sleep}"
# Injection point for the ladder's clock. Production leaves this empty
# and reads `date +%s`; the tests substitute a scripted epoch so the
# attempt-elapsed accounting below is exact instead of sampled.
NOW_CMD="${RADON_GARCH_NOW_CMD:-}"

_now() {
    if [ -n "$NOW_CMD" ]; then "$NOW_CMD"; else date +%s; fi
}
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

# Try FastAPI first. 3610s matches the FastAPI preset subprocess timeout
# (3600s) + 10s slack.
echo "$(date): POST ${FASTAPI_URL}"
# R-144: only curl exit 7 (connection refused) proves the request was never
# accepted. A 502 "Subprocess capacity exhausted" means the box is ALREADY
# running too many scans, and a -m timeout means FastAPI's child may still be
# mid-flight — falling back on either launches a duplicate outside
# MAX_CONCURRENT_SUBPROCESSES and outside the cooldown. Same guard as
# run_signals_refresh.sh. Capacity shed is retried inside SHED_WAIT_SECS;
# other non-7 outcomes still refuse the direct fallback.
# T-283: budget the shed wait against the seconds this loop actually WAITS,
# not against `SECONDS` since the script started. A shed 502 returns
# instantly, so in production the two accountings differ by milliseconds.
attempt=0
shed_waited=0
HTTP_CODE=000
CURL_EXIT=28
RESPONSE_BODY=""
while :; do
    # REL-208 (R-573): budget the ATTEMPT wall time too — a slow marker-bodied
    # 502 is uncounted by sleep-only accounting and can run the unit past
    # TimeoutStartSec into Result=timeout instead of the clean exit 1.
    attempt_started=$(_now)
    BODY_FILE="$(mktemp)"
    HTTP_CODE=$(curl -sS -X POST -m "$FASTAPI_TIMEOUT_SECS" -o "$BODY_FILE" \
        -w "%{http_code}" "${FASTAPI_URL}" 2>/tmp/garch-refresh.curl.err)
    CURL_EXIT=$?
    RESPONSE_BODY="$(cat "$BODY_FILE" 2>/dev/null || true)"
    rm -f "$BODY_FILE"
    if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
        echo "$(date): GARCH refresh via FastAPI complete (OK)"
        exit 0
    fi
    if ! _is_capacity_shed "$CURL_EXIT" "$HTTP_CODE" "$RESPONSE_BODY"; then
        break
    fi
    # `date +%s` is whole-second, so an instant 502 reads 0 or 1 purely on
    # where the boundary fell. Billing that phantom second shortens the
    # ladder under load. Drop one second of granularity: an attempt only
    # bills the time it demonstrably spent, and a genuinely slow attempt
    # (REL-208 / R-573) is still budgeted to within 1s.
    attempt_elapsed=$(( $(_now) - attempt_started - 1 ))
    [ "$attempt_elapsed" -gt 0 ] && shed_waited=$((shed_waited + attempt_elapsed))
    remaining=$((SHED_WAIT_SECS - shed_waited))
    if [ "$remaining" -le 0 ]; then
        echo "$(date): GARCH shed for subprocess capacity (http=${HTTP_CODE}) after ${attempt} retries; not launching duplicate" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    delay="$RETRY_DELAY"
    [ "$delay" -gt "$remaining" ] && delay="$remaining"
    echo "$(date): GARCH FastAPI capacity shed (curl=${CURL_EXIT} http=${HTTP_CODE}) - retry ${attempt} in ${delay}s"
    "$SLEEP_CMD" "$delay"
    shed_waited=$((shed_waited + delay))
done

if [ "$CURL_EXIT" -ne 7 ]; then
    echo "$(date): GARCH FastAPI outcome indeterminate (curl=${CURL_EXIT}, http=${HTTP_CODE}); not launching duplicate" >&2
    exit 1
fi

# Connection refused: nothing was accepted, so a direct run is safe and
# keeps the file cache warm. service_health + Turso won't update on this
# path; radon-ib-watchdog and the systemd journal surface radon-api.service
# health separately.
echo "$(date): FastAPI unavailable — fallback to direct garch_convergence.py invocation"
if "$PYTHON_BIN" scripts/garch_convergence.py --preset "$PRESET" --json --no-open >/dev/null 2>>/tmp/garch-scan.err; then
    echo "$(date): GARCH fallback refresh complete (OK)"
    exit 0
else
    EXIT_CODE=$?
fi

echo "$(date): GARCH fallback refresh FAILED (exit ${EXIT_CODE})" >&2
exit "${EXIT_CODE}"
