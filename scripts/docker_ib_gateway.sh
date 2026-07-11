#!/usr/bin/env bash
# Convenience wrapper for Docker-managed IB Gateway.
# Usage: scripts/docker_ib_gateway.sh {start|stop|restart|status|logs}

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_COMPOSE_DIR="$(cd "$SCRIPT_DIR/../docker/ib-gateway" && pwd)"
COMPOSE_DIR="${IB_GATEWAY_COMPOSE_DIR:-$DEFAULT_COMPOSE_DIR}"
COMPOSE_OVERRIDE=""
if [[ -f "$COMPOSE_DIR/docker-compose.override.yml" ]]; then
    COMPOSE_OVERRIDE="-f $COMPOSE_DIR/docker-compose.override.yml"
fi
COMPOSE_CMD="docker compose -f $COMPOSE_DIR/docker-compose.yml $COMPOSE_OVERRIDE --env-file $COMPOSE_DIR/.env"
LOCK_PYTHON="${IB_2FA_LOCK_PYTHON:-python3.13}"
LOCK_CLI="$SCRIPT_DIR/utils/ib_2fa_lock.py"
LOCK_HOLDER="scripts.docker_ib_gateway"

acquire_push_lease() {
    if ! "$LOCK_PYTHON" "$LOCK_CLI" acquire "$LOCK_HOLDER"; then
        echo "ERROR: refusing IB Gateway cycle because the shared 2FA push lease is unavailable or already held." >&2
        exit 1
    fi
}

ensure_docker_running() {
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker daemon is not running. Start Docker Desktop first." >&2
        exit 1
    fi
}

check_launchd_not_running() {
    if launchctl print "gui/$(id -u)/local.ibc-gateway" >/dev/null 2>&1; then
        echo "ERROR: launchd IBC service (local.ibc-gateway) is still running." >&2
        echo "Stop it first:  ~/ibc/bin/stop-secure-ibc-service.sh" >&2
        echo "Then disable:   launchctl bootout gui/\$(id -u)/local.ibc-gateway" >&2
        exit 1
    fi
}

validate_secrets() {
    local secret_file="$COMPOSE_DIR/secrets/ib_password.txt"
    if [[ ! -f "$secret_file" ]]; then
        echo "ERROR: Password file not found at $secret_file" >&2
        echo "Create it:  echo 'YOUR_IB_PASSWORD' > $secret_file && chmod 600 $secret_file" >&2
        exit 1
    fi
    local perms
    perms=$(stat -f "%Lp" "$secret_file" 2>/dev/null || stat -c "%a" "$secret_file" 2>/dev/null)
    if [[ "$perms" != "600" ]]; then
        echo "WARNING: Fixing permissions on $secret_file (was $perms, setting 600)" >&2
        chmod 600 "$secret_file"
    fi
}

validate_env() {
    local env_file="$COMPOSE_DIR/.env"
    if [[ ! -f "$env_file" ]]; then
        echo "ERROR: .env file not found at $env_file" >&2
        echo "Create it:  cp $COMPOSE_DIR/.env.example $env_file" >&2
        exit 1
    fi
}

container_is_running() {
    $COMPOSE_CMD ps --status running --services 2>/dev/null | grep -qx "ib-gateway"
}

cmd_start() {
    ensure_docker_running
    check_launchd_not_running
    validate_env
    validate_secrets
    if container_is_running; then
        echo "IB Gateway Docker container is already running."
        return
    fi
    acquire_push_lease
    echo "Starting IB Gateway Docker container..."
    $COMPOSE_CMD up -d
    echo "Container started. Check status: $0 status"
    echo "Healthcheck will pass after ~120s startup period."
}

cmd_stop() {
    ensure_docker_running
    echo "Stopping IB Gateway Docker container..."
    $COMPOSE_CMD down
    echo "Container stopped."
}

cmd_restart() {
    ensure_docker_running
    validate_env
    validate_secrets
    acquire_push_lease
    echo "Restarting IB Gateway Docker container..."
    $COMPOSE_CMD restart ib-gateway
    echo "Container restarted."
}

cmd_status() {
    ensure_docker_running
    $COMPOSE_CMD ps
}

cmd_logs() {
    ensure_docker_running
    $COMPOSE_CMD logs -f --tail 100 ib-gateway
}

case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    logs)    cmd_logs ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "Commands:"
        echo "  start    Start IB Gateway (checks launchd not running, validates secrets)"
        echo "  stop     Stop and remove IB Gateway container"
        echo "  restart  Restart IB Gateway container"
        echo "  status   Show container status and healthcheck"
        echo "  logs     Tail container logs"
        exit 1
        ;;
esac
