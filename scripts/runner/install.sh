#!/bin/bash
# Installs the nightly runner for the named loops on the always-on Mac.
#
#   sudo scripts/runner/install.sh documentation [more loops...]
#   scripts/runner/install.sh --print-plist documentation     (no root; for review/tests)
#
# Idempotent. Creates the unprivileged runner user on first use, installs the
# runner root-owned (the agent cannot edit what launches it), and writes one
# LaunchDaemon per loop that runs as that user. CLI logins and the GitHub token
# are one-time manual steps: docs/runner.md.
set -euo pipefail

BOT="${RADON_RUNNER_USER:-_radonbot}"
BOT_HOME="/Users/$BOT"
PREFIX=/usr/local/radon-runner
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

loop_setting() {
  sed -n "s/^$2=//p" "$SRC/loops/$1.env" | tail -n 1 | tr -d "\"'"
}

print_plist() {
  local loop="$1"
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.radon.runner.$loop</string>
    <key>UserName</key>
    <string>$BOT</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PREFIX/run_loop.sh</string>
        <string>$loop</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>$BOT_HOME</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$BOT_HOME/.local/bin:$BOT_HOME/.bun/bin</string>
    </dict>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$(loop_setting "$loop" SCHEDULE_HOUR)</integer>
        <key>Minute</key>
        <integer>$(loop_setting "$loop" SCHEDULE_MINUTE)</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$BOT_HOME/radon-runner/launchd-$loop.log</string>
    <key>StandardErrorPath</key>
    <string>$BOT_HOME/radon-runner/launchd-$loop.log</string>
</dict>
</plist>
EOF
}

ensure_user() {
  id "$BOT" >/dev/null 2>&1 && return 0
  sysadminctl -addUser "$BOT" -fullName "Radon runner" -home "$BOT_HOME" -shell /bin/zsh -password -
  dscl . create "/Users/$BOT" IsHidden 1
  createhomedir -c -u "$BOT" >/dev/null
}

configure_user() {
  local file="$BOT_HOME/.radon-runner.env"
  install -d -o "$BOT" -m 700 "$BOT_HOME/radon-runner"
  if [[ ! -f "$file" ]]; then
    printf '%s\n' '# Runner secrets. Never a production credential.' 'GH_TOKEN=' 'PUSHOVER_USER=' 'PUSHOVER_TOKEN=' > "$file"
    chown "$BOT" "$file"
    chmod 600 "$file"
  fi
  sudo -u "$BOT" -H git config --global credential.https://github.com.helper '!gh auth git-credential'
  sudo -u "$BOT" -H git config --global user.name "radon-runner"
  sudo -u "$BOT" -H git config --global user.email "radon-runner@users.noreply.github.com"
}

install_runner() {
  install -d -o root -g wheel -m 755 "$PREFIX" "$PREFIX/loops"
  install -o root -g wheel -m 755 "$SRC/run_loop.sh" "$PREFIX/run_loop.sh"
}

install_loop() {
  local loop="$1" label="com.radon.runner.$1"
  local plist="/Library/LaunchDaemons/$label.plist"
  install -o root -g wheel -m 644 "$SRC/loops/$loop.env" "$PREFIX/loops/$loop.env"
  print_plist "$loop" > "$plist"
  chown root:wheel "$plist"
  chmod 644 "$plist"
  plutil -lint "$plist" >/dev/null
  launchctl bootout "system/$label" 2>/dev/null || true
  launchctl bootstrap system "$plist"
  echo "installed $label"
}

main() {
  [[ $# -gt 0 ]] || { echo "usage: $0 <loop>... | --print-plist <loop>" >&2; exit 64; }
  if [[ "$1" == "--print-plist" ]]; then
    print_plist "${2:?loop}"
    return 0
  fi
  [[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
  local loop
  for loop in "$@"; do
    [[ -f "$SRC/loops/$loop.env" ]] || { echo "no scripts/runner/loops/$loop.env" >&2; exit 64; }
  done
  ensure_user
  configure_user
  install_runner
  for loop in "$@"; do install_loop "$loop"; done
}

main "$@"
