#!/bin/bash
#
# CRI Scan Scheduled Service Manager
#
# Runs CRI (Crash Risk Index) scan every 30 minutes from 4:05 AM to 8:00 PM ET,
# Mon-Fri on trading days only. Uses launchd StartCalendarInterval with ET slots
# converted into local machine time at install time (165 entries: 33 slots x 5 weekdays).
#
# Depends on IB Gateway (auto-starts at midnight via IBC Gateway service).
#
# Usage:
#   ./scripts/setup_cri_service.sh install   - Install and load service
#   ./scripts/setup_cri_service.sh uninstall - Stop and remove service
#   ./scripts/setup_cri_service.sh status    - Check service status
#   ./scripts/setup_cri_service.sh logs      - Tail service logs
#   ./scripts/setup_cri_service.sh start     - Run CRI scan manually now

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.radon.cri-scan.plist"
PLIST_SRC="$PROJECT_DIR/config/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LABEL="com.radon.cri-scan"
LOG_DIR="$PROJECT_DIR/logs"
WRAPPER="$PROJECT_DIR/scripts/run_cri_scan.sh"

# --- Helpers ---

generate_plist() {
    local entries
    entries=$(PROJECT_DIR_ENV="$PROJECT_DIR" python3 - <<'PY'
import os
import sys

project_dir = os.environ["PROJECT_DIR_ENV"]
sys.path.insert(0, os.path.join(project_dir, "scripts"))

from utils.launchd_calendar import build_local_calendar_entries, render_calendar_interval_xml

slots = [(4, 5)]
for hour in range(4, 21):
    for minute in (0, 30):
        if hour == 4 and minute == 0:
            continue
        if hour == 20 and minute == 30:
            continue
        slots.append((hour, minute))

entries = build_local_calendar_entries(slots, weekdays=[1, 2, 3, 4, 5])
print(render_calendar_interval_xml(entries), end="")
PY
)

    cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${WRAPPER}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>StartCalendarInterval</key>
    <array>
${entries}    </array>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/cri-scan.out.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/cri-scan.err.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST
}

# --- Commands ---

install() {
    echo "Installing CRI Scan service..."
    echo ""

    # 1. Verify wrapper script exists
    if [[ ! -f "$WRAPPER" ]]; then
        echo "ERROR: Wrapper script not found at $WRAPPER"
        exit 1
    fi
    chmod +x "$WRAPPER"

    # 2. Verify cri_scan.py exists
    if [[ ! -f "$PROJECT_DIR/scripts/cri_scan.py" ]]; then
        echo "ERROR: cri_scan.py not found"
        exit 1
    fi

    # 3. Create directories
    mkdir -p "$LOG_DIR"
    mkdir -p "$PROJECT_DIR/data/cri_scheduled"
    echo "  Log directory: $LOG_DIR"
    echo "  Data directory: data/cri_scheduled/"

    # 4. Generate plist
    echo "  Generating plist (165 schedule entries)..."
    generate_plist

    # 5. Validate plist
    if ! plutil -lint "$PLIST_SRC" > /dev/null 2>&1; then
        echo "ERROR: Generated plist is invalid"
        plutil -lint "$PLIST_SRC"
        exit 1
    fi
    echo "  Plist validated OK"

    # 6. Unload old service if present
    launchctl unload "$PLIST_DST" 2>/dev/null || true

    # 7. Install and load
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl load "$PLIST_DST"

    echo ""
    echo "Service installed and loaded."
    echo ""
    status
}

uninstall() {
    echo "Uninstalling CRI Scan service..."

    if [[ -f "$PLIST_DST" ]]; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Service removed."
    else
        echo "Service not installed."
    fi
}

status() {
    echo "CRI Scan Service Status"
    echo "======================="

    # Check plist installed
    if [[ ! -f "$PLIST_DST" ]]; then
        echo "Service: NOT INSTALLED"
        echo ""
        echo "Run: ./scripts/setup_cri_service.sh install"
        return 1
    fi

    # Check launchctl
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
        echo "Service: LOADED"
    else
        echo "Service: NOT LOADED (plist exists but not loaded)"
    fi

    # Schedule
    echo "Schedule: Every 30 min, 4:05 AM - 8:00 PM ET (converted to local launchd times at install), Mon-Fri"

    # Last CRI reading
    local latest
    latest=$(ls -t "$PROJECT_DIR/data/cri_scheduled"/cri-*.json 2>/dev/null | head -1)
    if [[ -n "$latest" ]]; then
        local filename
        filename=$(basename "$latest")
        echo "Last scan: $filename"
        # Extract CRI score if available
        local cri_score
        cri_score=$(python3 -c "
import json, sys
try:
    d = json.load(open('$latest'))
    cri = d.get('cri', {})
    score = cri.get('score') if isinstance(cri, dict) else d.get('score')
    level = cri.get('level', '') if isinstance(cri, dict) else ''
    if score is not None:
        print(f'CRI: {score}/100 [{level}]')
    else:
        print('(score field not found)')
except Exception:
    print('(could not parse)')
" 2>/dev/null)
        echo "  $cri_score"
    else
        echo "Last scan: (none yet)"
    fi

    # Recent logs
    if [[ -f "$LOG_DIR/cri-scan.out.log" ]]; then
        echo ""
        echo "Recent log:"
        tail -5 "$LOG_DIR/cri-scan.out.log" 2>/dev/null || echo "  (empty)"
    fi
}

logs() {
    echo "Tailing CRI Scan logs..."
    echo "(Ctrl+C to stop)"
    echo ""
    tail -f "$LOG_DIR"/cri-scan.out.log "$LOG_DIR"/cri-scan.err.log 2>/dev/null
}

start_now() {
    echo "Running CRI scan manually..."
    exec bash "$WRAPPER"
}

# --- Main ---

case "${1:-status}" in
    install)
        install
        ;;
    uninstall)
        uninstall
        ;;
    status)
        status
        ;;
    logs)
        logs
        ;;
    start)
        start_now
        ;;
    *)
        echo "Usage: $0 {install|uninstall|status|logs|start}"
        exit 1
        ;;
esac
