#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAILSCALE_DEFAULT_APP="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
TAILSCALE_ALT_APP="$HOME/Applications/Tailscale.app/Contents/MacOS/Tailscale"
IBC_BIN_DIR="${IBC_BIN_DIR:-$HOME/ibc/bin}"
START_CMD="$IBC_BIN_DIR/start-secure-ibc-service.sh"
STOP_CMD="$IBC_BIN_DIR/stop-secure-ibc-service.sh"
RESTART_CMD="$IBC_BIN_DIR/restart-secure-ibc-service.sh"
STATUS_CMD="$IBC_BIN_DIR/status-secure-ibc-service.sh"

print_usage() {
  cat <<'EOF'
IBC remote control helper

Usage:
  ./scripts/ibc_remote_control.sh check
  ./scripts/ibc_remote_control.sh tailscale-status
  ./scripts/ibc_remote_control.sh tailscale-login [--open]
  ./scripts/ibc_remote_control.sh ibc-status
  ./scripts/ibc_remote_control.sh ibc-start
  ./scripts/ibc_remote_control.sh ibc-stop
  ./scripts/ibc_remote_control.sh ibc-restart
  ./scripts/ibc_remote_control.sh remote-help

Notes:
  - This helper uses the installed Tailscale.app bundle directly.
  - On this Mac variant, remote access is standard macOS SSH over Tailscale.
  - Tailscale SSH server mode is not assumed here.
  - The canonical IBC service commands remain the secure machine-local wrappers in ~/ibc/bin/.
EOF
}

find_tailscale_bin() {
  if [[ -n "${TAILSCALE_BIN:-}" && -x "${TAILSCALE_BIN}" ]]; then
    printf '%s\n' "${TAILSCALE_BIN}"
    return 0
  fi

  if [[ -x "$TAILSCALE_DEFAULT_APP" ]]; then
    printf '%s\n' "$TAILSCALE_DEFAULT_APP"
    return 0
  fi

  if [[ -x "$TAILSCALE_ALT_APP" ]]; then
    printf '%s\n' "$TAILSCALE_ALT_APP"
    return 0
  fi

  return 1
}

tailscale_status_raw() {
  local tailscale_bin="$1"
  "$tailscale_bin" status 2>&1 || true
}

tailscale_info_plist() {
  local tailscale_bin="$1"
  printf '%s\n' "${tailscale_bin%/MacOS/Tailscale}/Info.plist"
}

tailscale_version() {
  local tailscale_bin="$1"
  local version info_plist
  version="$("$tailscale_bin" version 2>/dev/null | head -n 1 || true)"
  if [[ -n "$version" ]]; then
    printf '%s\n' "$version"
    return 0
  fi

  info_plist="$(tailscale_info_plist "$tailscale_bin")"
  if [[ -f "$info_plist" ]]; then
    /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$info_plist" 2>/dev/null || true
  fi
}

tailscale_app_job() {
  launchctl print "gui/$(id -u)" 2>/dev/null \
    | rg -o 'application\.io\.tailscale\.ipn\.macos[^[:space:]]*' \
    | head -n 1 \
    || true
}

tailscale_gui_state() {
  local job state
  job="$(tailscale_app_job)"
  if [[ -z "$job" ]]; then
    return 0
  fi

  state="$(
    launchctl print "gui/$(id -u)/$job" 2>/dev/null \
      | awk -F' = ' '/state = / { print $2; exit }' \
      || true
  )"
  printf '%s\n' "$state"
}

tailscale_has_cached_profile() {
  defaults read io.tailscale.ipn.macos com.tailscale.cached.currentProfile >/dev/null 2>&1
}

tailscale_state() {
  local raw="$1"
  local gui_state="$2"
  if printf '%s\n' "$raw" | grep -q '^Logged out\.$'; then
    printf 'logged_out\n'
    return 0
  fi

  if printf '%s\n' "$raw" | grep -q 'Tailscale is stopped'; then
    printf 'stopped\n'
    return 0
  fi

  if [[ -n "$raw" ]]; then
    printf 'running\n'
    return 0
  fi

  if [[ "$gui_state" == "running" ]]; then
    if tailscale_has_cached_profile; then
      printf 'running_gui_app_with_cached_profile\n'
    else
      printf 'running_gui_app\n'
    fi
    return 0
  fi

  printf 'unknown\n'
}

sshd_state() {
  if launchctl print-disabled system 2>/dev/null | grep -q '"com.openssh.sshd" => enabled'; then
    printf 'enabled\n'
  else
    printf 'disabled\n'
  fi
}

ssh_auth_mode() {
  if [[ -s "$HOME/.ssh/authorized_keys" ]]; then
    printf 'authorized_keys_present\n'
  else
    printf 'password_or_manual_key_setup\n'
  fi
}

ibc_service_state() {
  local state
  state="$(launchctl print "gui/$(id -u)/local.ibc-gateway" 2>/dev/null | awk '/state = / { print $3; exit }' || true)"
  if [[ -n "$state" ]]; then
    printf '%s\n' "$state"
  else
    printf 'unknown\n'
  fi
}

check_ibc_wrappers() {
  local missing=0
  for file in "$START_CMD" "$STOP_CMD" "$RESTART_CMD" "$STATUS_CMD"; do
    if [[ -x "$file" ]]; then
      printf '  [ok] %s\n' "$file"
    else
      printf '  [missing] %s\n' "$file"
      missing=1
    fi
  done
  return "$missing"
}

show_check() {
  local tailscale_bin tailscale_raw tailscale_mode tailscale_gui_mode ssh_mode ssh_auth ibc_mode tailscale_ver

  echo "IBC remote access readiness"
  echo "Project: $PROJECT_DIR"
  echo

  if tailscale_bin="$(find_tailscale_bin)"; then
    tailscale_raw="$(tailscale_status_raw "$tailscale_bin")"
    tailscale_gui_mode="$(tailscale_gui_state)"
    tailscale_mode="$(tailscale_state "$tailscale_raw" "$tailscale_gui_mode")"
    tailscale_ver="$(tailscale_version "$tailscale_bin")"
    printf 'Tailscale binary: %s\n' "$tailscale_bin"
    printf 'Tailscale version: %s\n' "${tailscale_ver:-unknown}"
    printf 'Tailscale state: %s\n' "$tailscale_mode"
    echo "Tailscale status:"
    if [[ -n "$tailscale_raw" ]]; then
      printf '%s\n' "$tailscale_raw" | sed 's/^/  /'
    else
      echo "  (CLI status unavailable from this shell)"
    fi
    if [[ -n "$tailscale_gui_mode" ]]; then
      printf '  launchd app state: %s\n' "$tailscale_gui_mode"
    fi
    if tailscale_has_cached_profile; then
      echo "  cached profile: present"
    fi
  else
    echo "Tailscale binary: not found"
  fi

  echo
  ssh_mode="$(sshd_state)"
  ssh_auth="$(ssh_auth_mode)"
  ibc_mode="$(ibc_service_state)"
  printf 'macOS SSH (Remote Login): %s\n' "$ssh_mode"
  printf 'SSH auth readiness: %s\n' "$ssh_auth"
  printf 'IBC launchd service: %s\n' "$ibc_mode"
  echo "IBC wrapper commands:"
  check_ibc_wrappers || true

  echo
  echo "Next actions:"
  case "${tailscale_mode:-missing}" in
    running|running_gui_app|running_gui_app_with_cached_profile)
      echo "  1. Tailscale appears available."
      ;;
    logged_out|stopped)
      echo "  1. Run ./scripts/ibc_remote_control.sh tailscale-login --open and complete Tailscale auth."
      ;;
    *)
      echo "  1. Confirm the Tailscale app is connected; CLI status was unavailable from this shell."
      ;;
  esac

  if [[ "$ssh_mode" != "enabled" ]]; then
    echo "  2. Enable macOS Remote Login manually in System Settings > General > Sharing."
  else
    echo "  2. macOS Remote Login is enabled."
  fi

  if [[ "$ssh_auth" == "password_or_manual_key_setup" ]]; then
    echo "  3. iPhone SSH will be password-based unless you install a client key and add its public key to ~/.ssh/authorized_keys."
  else
    echo "  3. Server-side authorized_keys is present."
  fi

  echo "  4. From iPhone, SSH to this Mac over its Tailscale hostname/IP and run:"
  echo "     cd $PROJECT_DIR && ./scripts/ibc_remote_control.sh ibc-status"
}

show_tailscale_status() {
  local tailscale_bin
  tailscale_bin="$(find_tailscale_bin)"
  tailscale_status_raw "$tailscale_bin"
}

tailscale_login() {
  local tailscale_bin raw auth_url open_flag="${1:-}"
  tailscale_bin="$(find_tailscale_bin)"
  raw="$("$tailscale_bin" up --timeout 10s --json 2>&1 || true)"
  printf '%s\n' "$raw"

  auth_url="$(
    printf '%s\n' "$raw" \
      | grep -Eo '"AuthURL"[[:space:]]*:[[:space:]]*"[^"]+"' \
      | head -n 1 \
      | sed -E 's/.*"AuthURL"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
  )"

  if [[ -n "$auth_url" ]]; then
    echo
    echo "Auth URL: $auth_url"
    if [[ "$open_flag" == "--open" ]]; then
      open "$auth_url"
    fi
  fi
}

run_wrapper() {
  local cmd="$1"
  if [[ ! -x "$cmd" ]]; then
    echo "Missing executable: $cmd" >&2
    exit 1
  fi
  exec "$cmd"
}

show_remote_help() {
  cat <<EOF
Remote command examples

Once Tailscale is connected and macOS Remote Login is enabled:

  ssh ${USER}@<tailscale-host-or-ip> '~/ibc/bin/status-secure-ibc-service.sh'
  ssh ${USER}@<tailscale-host-or-ip> '~/ibc/bin/start-secure-ibc-service.sh'
  ssh ${USER}@<tailscale-host-or-ip> '~/ibc/bin/stop-secure-ibc-service.sh'
  ssh ${USER}@<tailscale-host-or-ip> '~/ibc/bin/restart-secure-ibc-service.sh'

Repo convenience wrapper:

  ssh ${USER}@<tailscale-host-or-ip> 'cd $PROJECT_DIR && ./scripts/ibc_remote_control.sh ibc-status'
  ssh ${USER}@<tailscale-host-or-ip> 'cd $PROJECT_DIR && ./scripts/ibc_remote_control.sh ibc-start'
  ssh ${USER}@<tailscale-host-or-ip> 'cd $PROJECT_DIR && ./scripts/ibc_remote_control.sh ibc-stop'
  ssh ${USER}@<tailscale-host-or-ip> 'cd $PROJECT_DIR && ./scripts/ibc_remote_control.sh ibc-restart'
EOF
}

main() {
  local command="${1:-help}"
  shift || true

  case "$command" in
    check)
      show_check
      ;;
    tailscale-status)
      show_tailscale_status
      ;;
    tailscale-login)
      tailscale_login "${1:-}"
      ;;
    ibc-status)
      run_wrapper "$STATUS_CMD"
      ;;
    ibc-start)
      run_wrapper "$START_CMD"
      ;;
    ibc-stop)
      run_wrapper "$STOP_CMD"
      ;;
    ibc-restart)
      run_wrapper "$RESTART_CMD"
      ;;
    remote-help)
      show_remote_help
      ;;
    help|-h|--help)
      print_usage
      ;;
    *)
      echo "Unknown command: $command" >&2
      echo >&2
      print_usage >&2
      exit 1
      ;;
  esac
}

main "$@"
