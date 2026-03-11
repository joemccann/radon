#!/bin/bash
#
# Holiday-aware data refresh wrapper for launchd
#
# Checks if today is a trading day (weekday + not holiday) before running
# scanner.py, flow_analysis.py, and discover.py. Saves output to data/.
#

cd "$(dirname "$0")/.."

# Load env vars from both .env files — launchd doesn't inherit shell env
# Avoid process substitution <(...) which is unreliable under launchd's bash 3.2
_load_env() {
    local f="$1"
    [ -f "$f" ] || return
    local tmp
    tmp=$(mktemp)
    grep -v '^#' "$f" | grep -v '^\s*$' | sed 's/^export //' > "$tmp"
    set -a
    # shellcheck disable=SC1090
    . "$tmp"
    set +a
    rm -f "$tmp"
}
_load_env "web/.env"
_load_env ".env"

# Check if today is a trading day (reuses market_holidays.json)
IS_TRADING=$(python3 -c "
import sys; sys.path.insert(0, 'scripts')
from utils.market_calendar import _is_trading_day
from datetime import datetime
print('yes' if _is_trading_day(datetime.now()) else 'no')
" 2>/dev/null || echo "yes")

if [ "$IS_TRADING" = "no" ]; then
    echo "$(date): Market holiday — skipping data refresh"
    exit 0
fi

mkdir -p data

SCANNER_STATUS="FAIL"
FLOW_STATUS="FAIL"
DISCOVER_STATUS="FAIL"

# --- scanner.py ---
echo "$(date): Running scanner.py --top 25..."
python3 scripts/scanner.py --top 25 > data/scanner.json.tmp 2>/tmp/scanner.err
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv data/scanner.json.tmp data/scanner.json
    SCANNER_STATUS="OK"
    echo "$(date): scanner.py complete (OK)"
else
    rm -f data/scanner.json.tmp
    echo "$(date): scanner.py failed (exit $EXIT_CODE) — keeping existing data/scanner.json"
fi

# --- flow_analysis.py ---
echo "$(date): Running flow_analysis.py..."
python3 scripts/flow_analysis.py > data/flow_analysis.json.tmp 2>/tmp/flow_analysis.err
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv data/flow_analysis.json.tmp data/flow_analysis.json
    FLOW_STATUS="OK"
    echo "$(date): flow_analysis.py complete (OK)"
else
    rm -f data/flow_analysis.json.tmp
    echo "$(date): flow_analysis.py failed (exit $EXIT_CODE) — keeping existing data/flow_analysis.json"
fi

# --- discover.py ---
echo "$(date): Running discover.py --min-alerts 1..."
python3 scripts/discover.py --min-alerts 1 > data/discover.json.tmp 2>/tmp/discover.err
EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
    mv data/discover.json.tmp data/discover.json
    DISCOVER_STATUS="OK"
    echo "$(date): discover.py complete (OK)"
else
    rm -f data/discover.json.tmp
    echo "$(date): discover.py failed (exit $EXIT_CODE) — keeping existing data/discover.json"
fi

echo "$(date): Data refresh complete (scanner: $SCANNER_STATUS, flow: $FLOW_STATUS, discover: $DISCOVER_STATUS)"

cri_cache_has_complete_rvol() {
    python3 - "$1" "$2" <<'PY'
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

    cache_status=$(cri_cache_has_complete_rvol "data/cri.json" "$today_et")
    if [ "$cache_status" = "yes" ]; then
        echo "$(date): CRI cache already contains 20 RVOL history points for $today_et — skipping"
        return
    fi

    mkdir -p data/cri_scheduled logs
    timestamp=$(TZ=America/New_York date +"%Y-%m-%dT%H-%M")
    tmp_cache="data/cri.json.tmp"
    scheduled_path="data/cri_scheduled/cri-${timestamp}.json"

    echo "$(date): Refreshing CRI cache with 20-session RVOL history..."
    if python3 scripts/cri_scan.py --json > "$tmp_cache" 2>>"logs/cri-scan.err.log"; then
        mv "$tmp_cache" data/cri.json
        cp data/cri.json "$scheduled_path"
        echo "$(date): CRI cache refresh complete (OK) → data/cri.json, $scheduled_path"
    else
        local exit_code=$?
        rm -f "$tmp_cache"
        echo "$(date): CRI cache refresh failed (exit $exit_code)"
    fi
}

# --- fetch_menthorq_cta.py (once per day, post-close only) ---
# Only run after 16:00 ET and only if today's cache doesn't already exist.
# This avoids running the expensive Playwright automation multiple times per day.
CURRENT_HOUR_ET=$(TZ=America/New_York date +%H)
TODAY_ET=$(TZ=America/New_York date +%Y-%m-%d)
CTA_CACHE="data/menthorq_cache/cta_${TODAY_ET}.json"

if [ "$CURRENT_HOUR_ET" -ge 16 ]; then
    refresh_cri_cache_post_close "$TODAY_ET"

    if [ ! -f "$CTA_CACHE" ]; then
        echo "$(date): Running fetch_menthorq_cta.py (post-close, cache missing)..."
        mkdir -p data/menthorq_cache
        python3 scripts/fetch_menthorq_cta.py 2>/tmp/menthorq_cta.err
        EXIT_CODE=$?
        if [ "$EXIT_CODE" -eq 0 ]; then
            echo "$(date): fetch_menthorq_cta.py complete (OK) → $CTA_CACHE"
        else
            echo "$(date): fetch_menthorq_cta.py failed (exit $EXIT_CODE)"
        fi
    else
        echo "$(date): MenthorQ CTA cache already exists for $TODAY_ET — skipping"
    fi
else
    echo "$(date): Post-close CRI/CTA refresh skipped (market not yet closed, hour=$CURRENT_HOUR_ET ET)"
fi
