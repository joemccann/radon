#!/bin/bash
#
# Monitor Daemon Service Manager
#
# Usage:
#   ./scripts/setup_monitor_daemon.sh install   - Install and start service
#   ./scripts/setup_monitor_daemon.sh uninstall - Stop and remove service
#   ./scripts/setup_monitor_daemon.sh status    - Check service status
#   ./scripts/setup_monitor_daemon.sh logs      - Tail service logs
#   ./scripts/setup_monitor_daemon.sh test      - Run daemon once manually
#   ./scripts/setup_monitor_daemon.sh preflight - Verify launchd Python runtime

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.radon.monitor-daemon.plist"
PLIST_SRC="$PROJECT_DIR/config/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LABEL="com.radon.monitor-daemon"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/monitor-daemon.log"
LAUNCHD_PATH="${RADON_LAUNCHD_PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin}"
STATE_STALE_SECONDS="${MONITOR_STATE_STALE_SECONDS:-300}"

# Also manage old exit order service
OLD_PLIST_NAME="com.radon.exit-order-service.plist"
OLD_PLIST_DST="$HOME/Library/LaunchAgents/$OLD_PLIST_NAME"

resolve_service_python() {
    local python_bin
    python_bin=$(PATH="$LAUNCHD_PATH" command -v python3.13 2>/dev/null) || {
        echo "ERROR: Python 3.13 was not found in launchd PATH: $LAUNCHD_PATH"
        return 1
    }
    echo "$python_bin"
}

preflight_python() {
    local python_bin
    python_bin=$(resolve_service_python) || return 1

    if ! "$python_bin" - <<'PY'
import importlib.util
import sys

required = ("ib_insync", "libsql_experimental", "playwright", "pytz")
missing = [name for name in required if importlib.util.find_spec(name) is None]
if sys.version_info < (3, 13) or missing:
    raise SystemExit(1)
PY
    then
        echo "ERROR: Python 3.13 dependency preflight failed: $python_bin"
        return 1
    fi

    echo "Python preflight: OK ($python_bin)"
}

state_file_mtime() {
    local state_file="$1"
    if [ "$(uname -s)" = "Darwin" ]; then
        stat -f "%m" "$state_file" 2>/dev/null
    else
        stat -c "%Y" "$state_file" 2>/dev/null
    fi
}

install() {
    echo "Installing Monitor Daemon service..."

    # Validate the exact interpreter launchd will resolve before changing jobs.
    preflight_python

    # Create logs directory
    mkdir -p "$LOG_DIR"
    
    # Unload old service if exists
    if [ -f "$OLD_PLIST_DST" ]; then
        echo "Removing old exit-order-service..."
        launchctl unload "$OLD_PLIST_DST" 2>/dev/null || true
        rm -f "$OLD_PLIST_DST"
    fi
    
    # Unload if already loaded
    launchctl unload "$PLIST_DST" 2>/dev/null || true
    
    # Copy plist
    cp "$PLIST_SRC" "$PLIST_DST"
    
    # Load service
    launchctl load "$PLIST_DST"
    
    echo "Service installed and loaded."
    echo ""
    status
}

uninstall() {
    echo "Uninstalling Monitor Daemon service..."
    
    if [ -f "$PLIST_DST" ]; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Service removed."
    else
        echo "Service not installed"
    fi
}

status() {
    local job_output job_state last_exit runs
    local state_file state_mtime now age

    echo "Monitor Daemon Status"
    echo "====================="
    
    # Check if plist exists
    if [ ! -f "$PLIST_DST" ]; then
        echo "Service: NOT INSTALLED"
        return 1
    fi
    
    if ! job_output=$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null); then
        echo "Service: STOPPED"
        return 1
    fi

    job_state=$(printf '%s\n' "$job_output" | awk -F ' = ' '/^[[:space:]]*state = / { print $2; exit }')
    last_exit=$(printf '%s\n' "$job_output" | awk -F ' = ' '/^[[:space:]]*last exit code = / { print $2; exit }')
    runs=$(printf '%s\n' "$job_output" | awk -F ' = ' '/^[[:space:]]*runs = / { print $2; exit }')

    if [[ "$last_exit" =~ ^-?[0-9]+$ ]] && [ "$last_exit" -ne 0 ]; then
        echo "Service: FAILED (last exit code: $last_exit)"
        echo "Launchd state: ${job_state:-unknown}"
        echo "Runs: ${runs:-unknown}"
        return 1
    fi

    if [ "$job_state" = "xpcproxy" ] && [ -z "$last_exit" ]; then
        echo "Service: ACTIVE (first scheduled run is starting)"
        echo "Launchd state: $job_state"
        echo "Last exit code: not recorded"
        echo "Runs: ${runs:-unknown}"
        echo "State heartbeat: PENDING (first scheduled run has not exited)"
        return 0
    elif [ "$job_state" = "running" ]; then
        echo "Service: ACTIVE (scheduled run in progress)"
    else
        echo "Service: LOADED (scheduled, idle)"
    fi
    echo "Launchd state: ${job_state:-unknown}"
    echo "Last exit code: ${last_exit:-not recorded}"
    echo "Runs: ${runs:-unknown}"

    state_file="$PROJECT_DIR/data/daemon_state.json"
    if [ ! -f "$state_file" ]; then
        if [ "$job_state" = "running" ]; then
            echo "State heartbeat: PENDING (first scheduled run is active)"
            return 0
        fi
        echo "State heartbeat: STALE (state file is missing)"
        return 1
    fi

    state_mtime=$(state_file_mtime "$state_file") || {
        echo "State heartbeat: STALE (unable to read state timestamp)"
        return 1
    }
    now=$(date +%s)
    age=$((now - state_mtime))
    if [ "$age" -lt 0 ]; then
        age=0
    fi
    if [ "$age" -gt "$STATE_STALE_SECONDS" ]; then
        echo "State heartbeat: STALE (${age}s old, limit ${STATE_STALE_SECONDS}s)"
        return 1
    fi
    echo "State heartbeat: FRESH (${age}s old)"

    echo ""
    echo "Recent log:"
    tail -5 "$LOG_FILE" 2>/dev/null || echo "  (no logs yet)"
}

logs() {
    echo "Tailing monitor daemon logs..."
    echo "(Ctrl+C to stop)"
    echo ""
    tail -f "$LOG_FILE" "$LOG_DIR/monitor-daemon.out.log" "$LOG_DIR/monitor-daemon.err.log" 2>/dev/null
}

test_run() {
    local python_bin
    preflight_python
    python_bin=$(resolve_service_python)
    echo "Running monitor daemon once..."
    echo ""
    cd "$PROJECT_DIR/scripts"
    "$python_bin" -m monitor_daemon.run --once --verbose
}

handlers() {
    local python_bin
    preflight_python
    python_bin=$(resolve_service_python)
    "$python_bin" -m monitor_daemon.run --list-handlers
}

# Main
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
    test)
        test_run
        ;;
    handlers)
        handlers
        ;;
    preflight)
        preflight_python
        ;;
    *)
        echo "Usage: $0 {install|uninstall|status|logs|test|handlers|preflight}"
        exit 1
        ;;
esac
