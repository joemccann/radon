#!/bin/bash
#
# On-demand / scheduled NYSE breadth refresh wrapper.
#
# Behaviour (mirrors run_vcg_refresh.sh):
#
#   1. Skip on weekends / market holidays.
#   2. POST through the local FastAPI /breadth/scan endpoint when the
#      server is reachable — keeps the cache write, Turso mirror, and
#      service_health row update on the same code path the
#      browser-driven trigger uses.
#   3. Fall back to invoking breadth_scan.py directly when FastAPI is
#      unreachable, so a one-off cron / manual run still updates the
#      cache file. The mv is guarded: an empty payload never
#      overwrites the last good cache.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN                   python interpreter (matches other wrappers)
#   RADON_BREADTH_SCAN_FASTAPI_PORT    FastAPI port (default 8321)
#   RADON_BREADTH_SCAN_FASTAPI_HOST    FastAPI host (default 127.0.0.1)
#

set -u
cd "$(dirname "$0")/.."

# Load env vars from both .env files. Neither systemd nor launchd
# inherits shell env to children, so we re-source here.
#
# Parses each line literally rather than via `set -a; . "$tmp"; set +a`
# because the latter shell-expands `$VARNAME` substrings inside values —
# which combined with `set -u` (line above) silently aborts the script
# when a secret happens to contain `$` followed by [a-zA-Z_]. See
# feedback_env_file_shell_expansion.md for the case that surfaced this.
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
    echo "$(date): No Python interpreter available for breadth scan" >&2
    exit 1
fi

# Trading-day gate — match the run_data_refresh.sh probe verbatim so
# we get the same calendar semantics for free.
IS_TRADING=$("$PYTHON_BIN" - <<'PY' 2>/dev/null || echo "yes"
import sys
try:
    sys.path.insert(0, 'scripts')
    from utils.market_calendar import _is_trading_day
    from datetime import datetime
    print('yes' if _is_trading_day(datetime.now()) else 'no')
except Exception:
    # Fail-open: if the calendar import fails for any reason
    # (e.g. missing dependency, syntax error in scripts/utils),
    # default to running the scan rather than silently skipping.
    print('yes')
PY
)

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday or weekend — skipping breadth scan"
    exit 0
fi

FASTAPI_HOST="${RADON_BREADTH_SCAN_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_BREADTH_SCAN_FASTAPI_PORT:-8321}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/breadth/scan"

# Try FastAPI first — preserves the Turso mirror + service_health path
# the browser-driven trigger uses.
echo "$(date): POST ${FASTAPI_URL}"
if curl -fsS -X POST -m 130 -o /dev/null -w "%{http_code}" "${FASTAPI_URL}" 2>/tmp/breadth-scan.curl.err | grep -q '^2'; then
    echo "$(date): Breadth scan via FastAPI complete (OK)"
    exit 0
fi

# FastAPI unreachable or non-2xx — fall through to direct invocation
# so the file cache at least stays warm. The subprocess mirrors its own
# Turso snapshot + service_health row when the payload is non-empty.
echo "$(date): FastAPI unreachable — fallback to direct breadth_scan.py invocation"
mkdir -p data
TMP_PATH="data/breadth.json.tmp"
if "$PYTHON_BIN" scripts/breadth_scan.py --json > "$TMP_PATH" 2>>/tmp/breadth-scan.err; then
    # Never let an empty payload clobber the last good cache.
    if "$PYTHON_BIN" - "$TMP_PATH" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
sys.exit(0 if (data.get("history") or data.get("intraday")) else 1)
PY
    then
        mv "$TMP_PATH" data/breadth.json
        echo "$(date): Breadth fallback refresh complete (OK)"
    else
        rm -f "$TMP_PATH"
        echo "$(date): Breadth fallback produced empty payload — cache left untouched"
    fi
    exit 0
else
    EXIT_CODE=$?
    rm -f "$TMP_PATH"
    echo "$(date): Breadth fallback refresh FAILED (exit ${EXIT_CODE})" >&2
    exit "${EXIT_CODE}"
fi
