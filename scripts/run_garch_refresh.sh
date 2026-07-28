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
#   RADON_GARCH_REFRESH_PRESET       Scanner preset (default: mega-tech)
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

. scripts/lib/python_bin.sh

PYTHON_BIN=$(radon_resolve_python numpy dotenv)
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

PRESET="${RADON_GARCH_REFRESH_PRESET:-mega-tech}"
FASTAPI_HOST="${RADON_GARCH_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_GARCH_REFRESH_FASTAPI_PORT:-8321}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/garch-convergence/scan?preset=${PRESET}"

# Try FastAPI first. 190s matches the FastAPI subprocess timeout (180s) +
# 10s slack.
echo "$(date): POST ${FASTAPI_URL}"
if curl -fsS -X POST -m 190 -o /dev/null -w "%{http_code}" "${FASTAPI_URL}" 2>/tmp/garch-refresh.curl.err | grep -q '^2'; then
    echo "$(date): GARCH refresh via FastAPI complete (OK)"
    exit 0
fi

# FastAPI unreachable or non-2xx — fall through to direct invocation so
# the file cache at least stays warm. service_health + Turso won't update
# on this path; radon-ib-watchdog and the systemd journal surface
# radon-api.service health separately.
echo "$(date): FastAPI unreachable — fallback to direct garch_convergence.py invocation"
if "$PYTHON_BIN" scripts/garch_convergence.py --preset "$PRESET" --json --no-open >/dev/null 2>>/tmp/garch-scan.err; then
    echo "$(date): GARCH fallback refresh complete (OK)"
    exit 0
fi

EXIT_CODE=$?
echo "$(date): GARCH fallback refresh FAILED (exit ${EXIT_CODE})" >&2
exit "${EXIT_CODE}"
