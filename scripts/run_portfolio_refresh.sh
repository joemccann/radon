#!/bin/bash
#
# Autonomous portfolio refresh wrapper.
#
# Runs every 60 seconds during ET trading hours via
# radon-portfolio-sync.timer on Hetzner. Replaces the dashboard-poll-
# only freshness model where the portfolio data was only refetched from
# IB when a browser tab was actively polling /api/portfolio.
#
# Behaviour:
#
#   1. Skip on weekends / market holidays (same gate as vcg/data refresh).
#   2. POST through FastAPI /portfolio/sync — same code path the
#      dashboard's useSyncHook uses, so cache file + Turso dual-write +
#      service_health row update all stay on the canonical write path.
#   3. No fallback to direct invocation: portfolio sync requires the
#      IB pool (FastAPI-owned). If FastAPI is down, the operator gets
#      a separate radon-api.service alert and trying again here would
#      just add noise.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                   python interpreter (matches other wrappers)
#   RADON_PORTFOLIO_REFRESH_FASTAPI_HOST  FastAPI host (default 127.0.0.1)
#   RADON_PORTFOLIO_REFRESH_FASTAPI_PORT  FastAPI port (default 8321)
#

set -u
cd "$(dirname "$0")/.."

# Load env vars from both .env files. Neither systemd nor launchd
# inherits shell env to children.
#
# Parses each line literally rather than via `set -a; . "$tmp"; set +a`
# because the latter shell-expands `$VARNAME` substrings inside values —
# silently aborting under `set -u` when a secret contains `$` followed
# by [a-zA-Z_]. See feedback_env_file_shell_expansion.md.
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
    echo "$(date): No Python interpreter available for portfolio refresh" >&2
    exit 1
fi

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
    echo "$(date): Market holiday or weekend — skipping portfolio refresh"
    exit 0
fi

FASTAPI_HOST="${RADON_PORTFOLIO_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_PORTFOLIO_REFRESH_FASTAPI_PORT:-8321}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/portfolio/sync"

echo "$(date): POST ${FASTAPI_URL}"
HTTP_CODE=$(curl -fsS -X POST -m 35 -o /dev/null -w "%{http_code}" \
    "${FASTAPI_URL}" 2>/tmp/portfolio-refresh.curl.err)
CURL_EXIT=$?
if [ "$CURL_EXIT" -eq 0 ] && [[ "$HTTP_CODE" == 2* ]]; then
    echo "$(date): Portfolio refresh via FastAPI complete (OK)"
    exit 0
fi

EXIT_CODE="$CURL_EXIT"
[ "$EXIT_CODE" -ne 0 ] || EXIT_CODE=22
echo "$(date): Portfolio refresh FAILED (exit ${EXIT_CODE})" >&2
cat /tmp/portfolio-refresh.curl.err 2>/dev/null >&2 || true
exit "${EXIT_CODE}"
