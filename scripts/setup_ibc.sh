#!/bin/bash
#
# IBC Gateway Service Manager
#
# Automates IB Gateway startup via IBC (vendored at vendor/ibc/).
# Installs a launchd service definition for lock-aware starts, login handling,
# and dialog suppression. No launchd or IBC schedule may start it directly.
#
# Usage:
#   ./scripts/setup_ibc.sh install   - Install and start service
#   ./scripts/setup_ibc.sh uninstall - Stop and remove service
#   ./scripts/setup_ibc.sh status    - Check service status
#   ./scripts/setup_ibc.sh logs      - Tail service logs
#   ./scripts/setup_ibc.sh start     - Manually start Gateway now
#   ./scripts/setup_ibc.sh stop      - Stop Gateway via IBC command server

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IBC_VENDOR="$PROJECT_DIR/vendor/ibc"
PLIST_NAME="com.radon.ibc-gateway.plist"
PLIST_SRC="$PROJECT_DIR/config/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
IBC_CONFIG="$HOME/ibc/config.ini"
IBC_LOG_DIR="$HOME/ibc/logs"
LABEL="com.radon.ibc-gateway"
INSTALL_LOCK_HOLDER="scripts.setup_ibc.install"
MANUAL_LOCK_HOLDER="scripts.setup_ibc.start"

# --- Helpers ---

detect_gateway_version() {
    local gw_dir
    gw_dir=$(ls -d "$HOME/Applications/IB Gateway"* 2>/dev/null | head -1)
    if [[ -z "$gw_dir" ]]; then
        echo ""
        return
    fi
    # Extract version from folder name: "IB Gateway 10.44" -> "10.44"
    basename "$gw_dir" | sed 's/IB Gateway //'
}

patch_config_setting() {
    local key="$1"
    local value="$2"
    local file="$IBC_CONFIG"

    if grep -q "^${key}=" "$file" 2>/dev/null; then
        # Setting exists — update in-place
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$file"
    else
        # Setting missing — append
        echo "${key}=${value}" >> "$file"
    fi
}

acquire_push_lease() {
    local holder="$1"
    if ! python3.13 "$PROJECT_DIR/scripts/utils/ib_2fa_lock.py" acquire "$holder"; then
        echo "ERROR: another IB Gateway cycle already has a 2FA push in flight"
        exit 1
    fi
}

generate_plist() {
    local version="$1"
    # Call displaybannerandlaunch.sh directly — gatewaystartmacos.sh clobbers env vars
    local launcher="$IBC_VENDOR/scripts/displaybannerandlaunch.sh"

    cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${launcher}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>APP</key>
        <string>GATEWAY</string>
        <key>TWS_MAJOR_VRSN</key>
        <string>${version}</string>
        <key>IBC_PATH</key>
        <string>${IBC_VENDOR}</string>
        <key>IBC_INI</key>
        <string>${IBC_CONFIG}</string>
        <key>LOG_PATH</key>
        <string>${IBC_LOG_DIR}</string>
        <key>TWS_PATH</key>
        <string>${HOME}/Applications</string>
        <key>TWOFA_TIMEOUT_ACTION</key>
        <string>exit</string>
        <key>RELOGIN_AFTER_TWOFA_TIMEOUT</key>
        <string>no</string>
    </dict>

    <!-- Loaded as a definition only. FastAPI/watchdog acquire the shared
         2FA lease before invoking launchctl start. -->
    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>

    <key>StandardOutPath</key>
    <string>${IBC_LOG_DIR}/ibc-gateway.log</string>

    <key>StandardErrorPath</key>
    <string>${IBC_LOG_DIR}/ibc-gateway.log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST
}

# --- Commands ---

install() {
    echo "Installing IBC Gateway service..."
    echo ""

    # 1. Verify vendor IBC exists
    if [[ ! -f "$IBC_VENDOR/IBC.jar" ]]; then
        echo "ERROR: IBC not found at $IBC_VENDOR/IBC.jar"
        echo "Download IBC and extract to vendor/ibc/"
        exit 1
    fi

    # 2. Detect Gateway version
    local version
    version=$(detect_gateway_version)
    if [[ -z "$version" ]]; then
        echo "ERROR: IB Gateway not found in ~/Applications/"
        echo "Install IB Gateway first."
        exit 1
    fi
    echo "  Gateway version: $version"

    # 3. Ensure IBC scripts are executable
    chmod +x "$IBC_VENDOR/gatewaystartmacos.sh"
    chmod +x "$IBC_VENDOR/scripts/"*.sh 2>/dev/null || true

    # 4. Create log directory
    mkdir -p "$IBC_LOG_DIR"
    echo "  Log directory: $IBC_LOG_DIR"

    # 5. Verify config.ini exists
    if [[ ! -f "$IBC_CONFIG" ]]; then
        echo "ERROR: IBC config not found at $IBC_CONFIG"
        echo "Copy vendor/ibc/config.ini to ~/ibc/config.ini and set credentials."
        exit 1
    fi

    # 6. Patch config.ini (preserves credentials, updates operational settings)
    echo "  Patching config.ini..."
    patch_config_setting "ExistingSessionDetectedAction" "primary"
    patch_config_setting "AcceptIncomingConnectionAction" "accept"
    patch_config_setting "AcceptNonBrokerageAccountWarning" "yes"
    patch_config_setting "AutoRestartTime" ""
    patch_config_setting "ColdRestartTime" ""
    patch_config_setting "CommandServerPort" "7462"
    patch_config_setting "ControlFrom" ""
    patch_config_setting "ReloginAfterSecondFactorAuthenticationTimeout" "no"
    echo "    ExistingSessionDetectedAction=primary"
    echo "    AcceptNonBrokerageAccountWarning=yes"
    echo "    AutoRestartTime=disabled"
    echo "    ColdRestartTime=disabled"
    echo "    CommandServerPort=7462"

    # 7. Generate plist
    echo "  Generating plist..."
    generate_plist "$version"

    # 8. Replacing a running launch agent is a Gateway cycle and must acquire
    # the same cross-process lease as every other start path.
    acquire_push_lease "$INSTALL_LOCK_HOLDER"
    launchctl unload "$PLIST_DST" 2>/dev/null || true

    # 9. Install and load
    cp "$PLIST_SRC" "$PLIST_DST"
    launchctl load "$PLIST_DST"
    launchctl start "$LABEL"

    echo ""
    echo "Service installed and loaded."
    echo ""
    status
}

uninstall() {
    echo "Uninstalling IBC Gateway service..."

    if [[ -f "$PLIST_DST" ]]; then
        launchctl unload "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "Service removed."
    else
        echo "Service not installed."
    fi
}

status() {
    echo "IBC Gateway Status"
    echo "==================="

    # Check plist installed
    if [[ ! -f "$PLIST_DST" ]]; then
        echo "Service: NOT INSTALLED"
        echo ""
        echo "Run: ./scripts/setup_ibc.sh install"
        return 1
    fi

    # Check launchctl
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
        echo "Service: LOADED"
    else
        echo "Service: NOT LOADED (plist exists but not loaded)"
    fi

    # Gateway version
    local version
    version=$(detect_gateway_version)
    if [[ -n "$version" ]]; then
        echo "Gateway: $version"
    else
        echo "Gateway: NOT FOUND"
    fi

    # Config
    if [[ -f "$IBC_CONFIG" ]]; then
        echo "Config:  $IBC_CONFIG"
        local restart_time
        restart_time=$(grep "^AutoRestartTime=" "$IBC_CONFIG" 2>/dev/null | cut -d= -f2)
        echo "  AutoRestartTime=$restart_time"
        local cold_restart
        cold_restart=$(grep "^ColdRestartTime=" "$IBC_CONFIG" 2>/dev/null | cut -d= -f2)
        echo "  ColdRestartTime=$cold_restart"
        local session_action
        session_action=$(grep "^ExistingSessionDetectedAction=" "$IBC_CONFIG" 2>/dev/null | cut -d= -f2)
        echo "  ExistingSessionDetectedAction=$session_action"
    fi

    # Gateway process
    local gw_pid
    gw_pid=$(pgrep -f "ibgateway" 2>/dev/null || true)
    if [[ -n "$gw_pid" ]]; then
        echo "Process: RUNNING (PID $gw_pid)"
    else
        # Also check for java process with IBC
        gw_pid=$(pgrep -f "java.*IBC" 2>/dev/null || true)
        if [[ -n "$gw_pid" ]]; then
            echo "Process: RUNNING (PID $gw_pid)"
        else
            echo "Process: NOT RUNNING"
        fi
    fi

    # Recent logs
    if [[ -f "$IBC_LOG_DIR/ibc-gateway.log" ]]; then
        echo ""
        echo "Recent log:"
        tail -5 "$IBC_LOG_DIR/ibc-gateway.log" 2>/dev/null || echo "  (empty)"
    fi
}

logs() {
    echo "Tailing IBC Gateway logs..."
    echo "(Ctrl+C to stop)"
    echo ""
    tail -f "$IBC_LOG_DIR"/ibc-gateway.log "$IBC_LOG_DIR"/ibc-*.log 2>/dev/null
}

start_gateway() {
    echo "Starting IB Gateway manually..."

    local version
    version=$(detect_gateway_version)
    if [[ -z "$version" ]]; then
        echo "ERROR: IB Gateway not found"
        exit 1
    fi

    acquire_push_lease "$MANUAL_LOCK_HOLDER"

    export APP=GATEWAY
    export TWS_MAJOR_VRSN="$version"
    export IBC_PATH="$IBC_VENDOR"
    export IBC_INI="$IBC_CONFIG"
    export LOG_PATH="$IBC_LOG_DIR"
    export TWS_PATH="$HOME/Applications"
    export TWOFA_TIMEOUT_ACTION=exit
    export RELOGIN_AFTER_TWOFA_TIMEOUT=no

    exec "$IBC_VENDOR/scripts/displaybannerandlaunch.sh"
}

stop_gateway() {
    echo "Sending STOP to IBC command server (port 7462)..."
    echo "STOP" | nc -w 5 localhost 7462 2>/dev/null && echo "Stop command sent." || echo "Could not connect to IBC command server. Gateway may not be running."
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
        start_gateway
        ;;
    stop)
        stop_gateway
        ;;
    *)
        echo "Usage: $0 {install|uninstall|status|logs|start|stop}"
        exit 1
        ;;
esac
