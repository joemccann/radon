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

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.convex-scavenger.monitor-daemon.plist"
PLIST_SRC="$PROJECT_DIR/config/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOG_DIR="$PROJECT_DIR/logs"
LOG_FILE="$LOG_DIR/monitor-daemon.log"

# Also manage old exit order service
OLD_PLIST_NAME="com.convex-scavenger.exit-order-service.plist"
OLD_PLIST_DST="$HOME/Library/LaunchAgents/$OLD_PLIST_NAME"

install() {
    echo "Installing Monitor Daemon service..."
    
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
    
    echo "✓ Service installed and started"
    echo ""
    status
}

uninstall() {
    echo "Uninstalling Monitor Daemon service..."
    
    if [ -f "$PLIST_DST" ]; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "✓ Service removed"
    else
        echo "Service not installed"
    fi
}

status() {
    echo "Monitor Daemon Status"
    echo "====================="
    
    # Check if plist exists
    if [ ! -f "$PLIST_DST" ]; then
        echo "Service: NOT INSTALLED"
        return 1
    fi
    
    # Check launchctl
    if launchctl list | grep -q "com.convex-scavenger.monitor-daemon"; then
        echo "Service: RUNNING"
        
        # Show last run from state file
        STATE_FILE="$PROJECT_DIR/data/daemon_state.json"
        if [ -f "$STATE_FILE" ]; then
            echo ""
            echo "Last state:"
            python3 -c "
import json
from datetime import datetime
state = json.load(open('$STATE_FILE'))
print(f'  Saved: {state.get(\"saved_at\", \"N/A\")}')
for name, h in state.get('handlers', {}).items():
    last_run = h.get('last_run', 'Never')
    print(f'  {name}: {last_run}')
" 2>/dev/null || echo "  (unable to parse state)"
        fi
        
        echo ""
        echo "Recent log:"
        tail -5 "$LOG_FILE" 2>/dev/null || echo "  (no logs yet)"
    else
        echo "Service: STOPPED"
    fi
}

logs() {
    echo "Tailing monitor daemon logs..."
    echo "(Ctrl+C to stop)"
    echo ""
    tail -f "$LOG_FILE" "$LOG_DIR/monitor-daemon.out.log" "$LOG_DIR/monitor-daemon.err.log" 2>/dev/null
}

test_run() {
    echo "Running monitor daemon once..."
    echo ""
    cd "$PROJECT_DIR/scripts"
    python3 -m monitor_daemon.run --once --verbose
}

handlers() {
    python3 -m monitor_daemon.run --list-handlers
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
    *)
        echo "Usage: $0 {install|uninstall|status|logs|test|handlers}"
        exit 1
        ;;
esac
