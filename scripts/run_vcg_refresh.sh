#!/bin/bash
#
# Autonomous VCG refresh wrapper.
#
# Runs every 5 minutes during ET trading hours (09:30-16:00) via
# radon-vcg-refresh.timer on Hetzner and com.radon.vcg-refresh on the
# laptop. Gives vcg-scan the same "real-time during market hours"
# cadence the user expects without depending on a browser tab being
# open.
#
# Behaviour:
#
#   1. Skip on weekends / market holidays (matches run_data_refresh.sh).
#   2. POST through the local FastAPI /vcg/scan endpoint when the
#      server is reachable — keeps the cache write, Turso dual-write,
#      and service_health row update on the same code path the
#      browser-driven trigger uses.
#   3. Fall back to invoking vcg_scan.py directly when FastAPI is
#      unreachable, so a one-off cron / manual run still updates the
#      cache file.
#
# Configuration via environment:
#
#   RADON_PYTHON_BIN              python interpreter (matches other wrappers)
#   RADON_VCG_REFRESH_FASTAPI_PORT FastAPI port (default 8321)
#   RADON_VCG_REFRESH_FASTAPI_HOST FastAPI host (default 127.0.0.1)
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
    echo "$(date): No Python interpreter available for vcg refresh" >&2
    exit 1
fi

# Exchange-session gate. It is holiday and early-close aware, so broad timer
# windows cannot launch paid/broker work outside the current session.
IS_TRADING=$("$PYTHON_BIN" - <<'PY' 2>/dev/null || echo "yes"
import sys
try:
    sys.path.insert(0, 'scripts')
    from utils.market_calendar import market_state
    print('yes' if market_state().get('is_open') else 'no')
except Exception:
    # Fail-open: if the calendar import fails for any reason
    # (e.g. missing dependency, syntax error in scripts/utils),
    # default to running the scan rather than silently skipping.
    print('yes')
PY
)

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market closed — skipping VCG refresh"
    exit 0
fi

FASTAPI_HOST="${RADON_VCG_REFRESH_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${RADON_VCG_REFRESH_FASTAPI_PORT:-8321}"
FASTAPI_URL="http://${FASTAPI_HOST}:${FASTAPI_PORT}/vcg/scan"

# Try FastAPI first — preserves the dual-write + service_health path
# the browser-driven trigger uses.
echo "$(date): POST ${FASTAPI_URL}"
if curl -fsS -X POST -m 130 -o /dev/null -w "%{http_code}" "${FASTAPI_URL}" 2>/tmp/vcg-refresh.curl.err | grep -q '^2'; then
    echo "$(date): VCG refresh via FastAPI complete (OK)"
    exit 0
fi

# FastAPI unreachable or non-2xx — fall through to direct invocation
# so the file cache at least stays warm. service_health and Turso
# won't update on this path; that's acceptable for the rare "FastAPI
# is down" failure mode (the systemd watchdog will surface that
# separately via radon-api.service health).
echo "$(date): FastAPI unreachable — fallback to direct vcg_scan.py invocation"
mkdir -p data
TMP_PATH="data/vcg.json.tmp"
if "$PYTHON_BIN" scripts/vcg_scan.py --json > "$TMP_PATH" 2>>/tmp/vcg-scan.err; then
    mv "$TMP_PATH" data/vcg.json
    echo "$(date): VCG fallback refresh complete (OK)"
    exit 0
else
    EXIT_CODE=$?
fi

rm -f "$TMP_PATH"
echo "$(date): VCG fallback refresh FAILED (exit ${EXIT_CODE})" >&2
exit "${EXIT_CODE}"
