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
#   RADON_LEAP_REFRESH_PRESET       UW preset (default: mag7)
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

. scripts/lib/python_bin.sh

PYTHON_BIN=$(radon_resolve_python dotenv)
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

PRESET="${RADON_LEAP_REFRESH_PRESET:-mag7}"
MIN_GAP="${RADON_LEAP_REFRESH_MIN_GAP:-10}"
FASTAPI_HOST="${RADON_LEAP_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_LEAP_REFRESH_FASTAPI_PORT:-8321}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/leap/scan?preset=${PRESET}&min_gap=${MIN_GAP}"

# Try FastAPI first — preserves the dual-write + service_health path the
# dashboard "Run latest →" button uses. 310s matches the FastAPI timeout
# for the leap subprocess (300s) with 10s of slack.
echo "$(date): POST ${FASTAPI_URL}"
if curl -fsS -X POST -m 310 -o /dev/null -w "%{http_code}" "${FASTAPI_URL}" 2>/tmp/leap-refresh.curl.err | grep -q '^2'; then
    echo "$(date): LEAP refresh via FastAPI complete (OK)"
    exit 0
fi

# FastAPI unreachable or non-2xx — fall through to direct invocation
# so the file cache at least stays warm. service_health and Turso
# won't update on this path; the systemd watchdog will surface
# radon-api.service health separately.
echo "$(date): FastAPI unreachable — fallback to direct leap_scanner_uw.py invocation"
if "$PYTHON_BIN" scripts/leap_scanner_uw.py --preset "$PRESET" --min-gap "$MIN_GAP" --json 2>>/tmp/leap-scan.err; then
    echo "$(date): LEAP fallback refresh complete (OK)"
    exit 0
fi

EXIT_CODE=$?
echo "$(date): LEAP fallback refresh FAILED (exit ${EXIT_CODE})" >&2
exit "${EXIT_CODE}"
