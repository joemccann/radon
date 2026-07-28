#!/bin/bash
#
# Holiday-aware data refresh wrapper for launchd
#
# Checks if today is a trading day (weekday + not holiday) before running
# scanner.py, flow_analysis.py, and discover.py. Saves output to data/.
#

cd "$(dirname "$0")/.."

# Load env vars from both .env files. Neither systemd nor launchd
# inherits shell env to children.
#
# Parses each line literally rather than via `set -a; . "$tmp"; set +a`
# because the latter shell-expands `$VARNAME` substrings inside values -
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

RADON_PYTHON_MIN_VERSION="3.13"
PYTHON_BIN=$(radon_resolve_python ib_insync libsql_experimental)
if [ -z "$PYTHON_BIN" ]; then
    echo "$(date): No Python 3.13 interpreter with ib_insync and libsql_experimental available for data refresh"
    exit 1
fi

# Check if today is a trading day (reuses market_holidays.json)
IS_TRADING=$("$PYTHON_BIN" -c "
import sys; sys.path.insert(0, 'scripts')
from utils.market_calendar import _is_trading_day
from datetime import datetime
print('yes' if _is_trading_day(datetime.now()) else 'no')
" 2>/dev/null || echo "yes")

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday - skipping data refresh"
    exit 0
fi

mkdir -p data

SCANNER_STATUS="FAIL"
FLOW_STATUS="FAIL"
DISCOVER_STATUS="FAIL"

# --- scanner.py ---
echo "$(date): Running scanner.py --top 25..."
"$PYTHON_BIN" scripts/scanner.py --top 25 > data/scanner.json.tmp 2>/tmp/scanner.err
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv data/scanner.json.tmp data/scanner.json
    SCANNER_STATUS="OK"
    echo "$(date): scanner.py complete (OK)"
else
    rm -f data/scanner.json.tmp
    echo "$(date): scanner.py failed (exit $EXIT_CODE) - keeping existing data/scanner.json"
fi

# --- flow_analysis.py ---
echo "$(date): Running flow_analysis.py..."
"$PYTHON_BIN" scripts/flow_analysis.py > data/flow_analysis.json.tmp 2>/tmp/flow_analysis.err
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv data/flow_analysis.json.tmp data/flow_analysis.json
    FLOW_STATUS="OK"
    echo "$(date): flow_analysis.py complete (OK)"
else
    rm -f data/flow_analysis.json.tmp
    echo "$(date): flow_analysis.py failed (exit $EXIT_CODE) - keeping existing data/flow_analysis.json"
fi

# --- discover.py ---
echo "$(date): Running discover.py --min-alerts 1..."
"$PYTHON_BIN" scripts/discover.py --min-alerts 1 > data/discover.json.tmp 2>/tmp/discover.err
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv data/discover.json.tmp data/discover.json
    DISCOVER_STATUS="OK"
    echo "$(date): discover.py complete (OK)"
else
    rm -f data/discover.json.tmp
    echo "$(date): discover.py failed (exit $EXIT_CODE) - keeping existing data/discover.json"
fi

cri_cache_has_complete_rvol() {
    "$PYTHON_BIN" - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

cache_path = Path(sys.argv[1])
today_et = sys.argv[2]

if not cache_path.exists():
    print("no")
    raise SystemExit(0)

try:
    data = json.loads(cache_path.read_text())
except Exception:
    print("no")
    raise SystemExit(0)

history = data.get("history") or []
complete = (
    data.get("date") == today_et
    and len(history) >= 20
    and all(entry.get("realized_vol") is not None for entry in history[-20:])
)
print("yes" if complete else "no")
PY
}

refresh_cri_cache_post_close() {
    local today_et="$1"
    local cache_status
    local timestamp
    local tmp_cache
    local scheduled_path
    local scan_complete

    cache_status=$(cri_cache_has_complete_rvol "data/cri.json" "$today_et")
    if [ "$cache_status" = "yes" ]; then
        echo "$(date): CRI cache already contains 20 RVOL history points for $today_et - skipping"
        return
    fi

    mkdir -p data/cri_scheduled logs
    timestamp=$(TZ=America/New_York date +"%Y-%m-%dT%H-%M")
    tmp_cache="data/cri.json.tmp"
    scheduled_path="data/cri_scheduled/cri-${timestamp}.json"

    echo "$(date): Refreshing CRI cache with 20-session RVOL history..."
    if "$PYTHON_BIN" scripts/cri_scan.py --json > "$tmp_cache" 2>>"logs/cri-scan.err.log"; then
        mv "$tmp_cache" data/cri.json
        cp data/cri.json "$scheduled_path"
        scan_complete=$(cri_cache_has_complete_rvol "data/cri.json" "$today_et")
        if [ "$scan_complete" = "yes" ]; then
            echo "$(date): CRI cache refresh complete (OK): data/cri.json, $scheduled_path"
            return
        fi
        echo "$(date): CRI scan output is still missing complete RVOL history - attempting repair fallback"
    else
        local exit_code=$?
        rm -f "$tmp_cache"
        echo "$(date): CRI cache refresh failed (exit $exit_code) - attempting repair fallback"
    fi

    if "$PYTHON_BIN" scripts/repair_cri_rvol_cache.py --write --target-date "$today_et" 2>>"logs/cri-scan.err.log"; then
        echo "$(date): CRI cache repair complete (OK)"
        return 0
    else
        local repair_exit_code=$?
        echo "$(date): CRI cache repair failed (exit $repair_exit_code)"
        return 1
    fi
}

# --- post-close CRI repair ---
# CTA sync now runs via the dedicated com.radon.cta-sync launch agent so
# this wrapper only owns the CRI post-close repair path.
CURRENT_HOUR_ET=$(TZ=America/New_York date +%H)
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)
CRI_STATUS="SKIPPED"

if [ "$CURRENT_HOUR_ET" -ge 16 ]; then
    if refresh_cri_cache_post_close "$TODAY_ET"; then
        CRI_STATUS="OK"
    else
        CRI_STATUS="FAIL"
    fi
else
    echo "$(date): Post-close CRI refresh skipped (market not yet closed, hour=$CURRENT_HOUR_ET ET)"
fi

echo "$(date): Data refresh complete (scanner: $SCANNER_STATUS, flow: $FLOW_STATUS, discover: $DISCOVER_STATUS, cri: $CRI_STATUS)"

if [ "$SCANNER_STATUS" != "OK" ] || [ "$FLOW_STATUS" != "OK" ] || [ "$DISCOVER_STATUS" != "OK" ] || [ "$CRI_STATUS" = "FAIL" ]; then
    exit 1
fi

exit 0
