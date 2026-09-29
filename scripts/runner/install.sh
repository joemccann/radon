#!/bin/bash
# Installs the nightly runner for the named loops on the always-on Mac.
#
#   sudo scripts/runner/install.sh documentation [more loops...]
#   scripts/runner/install.sh --print-plist documentation     (no root; for review/tests)
#   scripts/runner/install.sh --print-guard security          (no root; the gh shim a GH_GUARD=1 loop gets)
#   scripts/runner/install.sh --print-gitconfig               (no root; the git config every runner git reads)
#
# Idempotent. Creates the unprivileged runner user on first use, installs the
# runner root-owned (the agent cannot edit what launches it), and writes one
# LaunchDaemon per loop that runs as that user. CLI logins and the GitHub token
# are one-time manual steps: docs/runner.md.
set -euo pipefail

BOT="${RADON_RUNNER_USER:-_radonbot}"
BOT_HOME="/Users/$BOT"
PREFIX="${RADON_RUNNER_PREFIX:-/usr/local/radon-runner}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAL_GH="${RADON_RUNNER_GH:-/opt/homebrew/bin/gh}"
GUARD_PYTHON="${RADON_RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
# Copied from scripts/ into $PREFIX/lib, root-owned: the hooks, the resolver
# and the gh guard run these, never the agent-writable clone's copies.
LIB_FILES="nightly_pr_guard.py nightly_publish.py nightly_issue_prune.py nightly_green_base.py
  nightly_audit_context.py nightly_deliver.py security_claude_ladder.py claude_cli_env_drift.py
  claude_cli_env_reviewed.txt"

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
        <string>/opt/homebrew/bin:/usr/bin:/bin</string>
        <key>DISABLE_AUTOUPDATER</key>
        <string>1</string>
    </dict>
    <key>ExitTimeOut</key>
    <integer>60</integer>
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

# The gh a GH_GUARD=1 loop's agent finds first on PATH. The loop and the real
# gh are baked in, so the agent cannot point the guard anywhere else.
print_guard() {
  local loop="$1"
  cat <<EOF
#!/bin/bash
# Installed by install.sh for loop $loop. Routes every pr/api/issue/alias call
# through nightly_pr_guard.py (no merges, no aliases, no unguarded PR creation,
# no issue writes for the security loops).
set -euo pipefail
export RADON_NIGHTLY_REAL_GH=$REAL_GH
export RADON_NIGHTLY_LOOP=$loop
for arg in "\$@"; do
  case "\$arg" in
    pr|api|issue|alias) exec $GUARD_PYTHON -I $PREFIX/lib/nightly_pr_guard.py "\$@" ;;
  esac
done
exec "\$RADON_NIGHTLY_REAL_GH" "\$@"
EOF
}

ensure_user() {
  id "$BOT" >/dev/null 2>&1 && return 0
  sysadminctl -addUser "$BOT" -fullName "Radon runner" -home "$BOT_HOME" -shell /bin/zsh -password -
  dscl . create "/Users/$BOT" IsHidden 1
  createhomedir -c -u "$BOT" >/dev/null
}

# Everything under the bot's home is created by the bot itself: root never
# writes, chmods or chowns a path the bot could have swapped for a symlink.
as_bot() { sudo -u "$BOT" "$@"; }

configure_user() {
  local dir
  for dir in "$BOT_HOME/radon-runner" "$BOT_HOME/radon-runner/state"; do
    as_bot /bin/mkdir -p -m 700 "$dir"
    as_bot /bin/chmod 700 "$dir"
  done
  as_bot /bin/sh -c 'umask 077; f="$1"; [ -e "$f" ] || [ -L "$f" ] || printf "%s\n" "# Runner secrets. Never a production credential." "GH_TOKEN=" "PUSHOVER_USER=" "PUSHOVER_TOKEN=" > "$f"' \
    sh "$BOT_HOME/.radon-runner.env"
}

# The runner exports GIT_CONFIG_GLOBAL at this root-owned file, so no git it
# runs reads the bot-writable ~/.gitconfig.
print_gitconfig() {
  cat <<EOF
[credential "https://github.com"]
	helper = !$REAL_GH auth git-credential
[user]
	name = radon-runner
	email = radon-runner@users.noreply.github.com
EOF
}

install_runner() {
  local file
  install -d -o root -g wheel -m 755 "$PREFIX" "$PREFIX/loops" "$PREFIX/hooks" "$PREFIX/lib" "$PREFIX/guard"
  install -o root -g wheel -m 755 "$SRC/run_loop.sh" "$PREFIX/run_loop.sh"
  print_gitconfig > "$PREFIX/gitconfig"
  chown root:wheel "$PREFIX/gitconfig"
  chmod 644 "$PREFIX/gitconfig"
  for file in "$SRC/hooks/"*; do
    [[ -f "$file" ]] && install -o root -g wheel -m 755 "$file" "$PREFIX/hooks/$(basename "$file")"
  done
  for file in $LIB_FILES; do
    install -o root -g wheel -m 755 "$SRC/../$file" "$PREFIX/lib/$file"
  done
}

install_loop() {
  local loop="$1" label="com.radon.runner.$1"
  local plist="/Library/LaunchDaemons/$label.plist"
  install -o root -g wheel -m 644 "$SRC/loops/$loop.env" "$PREFIX/loops/$loop.env"
  if [[ "$(loop_setting "$loop" GH_GUARD)" == 1 ]]; then
    install -d -o root -g wheel -m 755 "$PREFIX/guard/$loop"
    print_guard "$loop" > "$PREFIX/guard/$loop/gh"
    chown root:wheel "$PREFIX/guard/$loop/gh"
    chmod 755 "$PREFIX/guard/$loop/gh"
  fi
  print_plist "$loop" > "$plist"
  chown root:wheel "$plist"
  chmod 644 "$plist"
  plutil -lint "$plist" >/dev/null
  launchctl bootout "system/$label" 2>/dev/null || true
  launchctl bootstrap system "$plist"
  echo "installed $label"
}

main() {
  [[ $# -gt 0 ]] || { echo "usage: $0 <loop>... | --print-plist <loop> | --print-guard <loop>" >&2; exit 64; }
  if [[ "$1" == "--print-plist" ]]; then
    print_plist "${2:?loop}"
    return 0
  fi
  if [[ "$1" == "--print-guard" ]]; then
    print_guard "${2:?loop}"
    return 0
  fi
  if [[ "$1" == "--print-gitconfig" ]]; then
    print_gitconfig
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
