#!/bin/bash
# Installs the nightly runner for the named loops on the always-on Mac.
#
#   sudo scripts/runner/install.sh documentation [more loops...]
#   scripts/runner/install.sh --print-plist documentation     (no root; for review/tests)
#   scripts/runner/install.sh --print-guard security          (no root; the gh shim a GH_GUARD=1 loop gets)
#   scripts/runner/install.sh --print-gitconfig               (no root; the git config every runner git reads)
#   scripts/runner/install.sh --write-dotenv-path             (no root; writes $PREFIX/dotenv-path from RADON_RUNNER_DOTENV)
#
# Idempotent. Creates the unprivileged runner user on first use, installs the
# runner root-owned (the agent cannot edit what launches it), and writes one
# LaunchDaemon per loop that runs as that user. CLI logins and the GitHub token
# are one-time manual steps: docs/runner.md.
set -euo pipefail

BOT="${RADON_RUNNER_USER:-_radonbot}"
BOT_HOME="/Users/$BOT"
PREFIX="${RADON_RUNNER_PREFIX:-/usr/local/radon-runner}"
# pwd -P, matching check_source: with a logical path the ancestor walk
# validates one chain and the copies later read another.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REAL_GH="${RADON_RUNNER_GH:-/opt/homebrew/bin/gh}"
GUARD_PYTHON="${RADON_RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
# Copied from scripts/ into $PREFIX/lib, root-owned: the hooks, the resolver
# and the gh guard run these, never the agent-writable clone's copies.
LIB_FILES="nightly_pr_guard.py nightly_publish.py nightly_issue_prune.py nightly_green_base.py
  nightly_audit_context.py nightly_phase_status.py nightly_deliver.py credential_redaction.py security_claude_ladder.py claude_cli_env_drift.py
  claude_cli_env_reviewed.txt nvidia_rate_limit.py ci/check_pr_body.py nightly_toolchain_update.py utils/atomic_io.py"

# Root installs from $SRC, so every path it reads must be one only root or the
# operator can change: each directory from / down to the clone owned by one of
# them and not group/world-writable (a sticky shared parent like /tmp is fine),
# and the clone, scripts/ and everything copied held to the same bar with no
# sticky exemption and no symlinks. RADON_RUNNER_INSTALL_TRUSTED_UID narrows
# the trusted uid for tests and is ignored as root.
check_source() {
  local uid bad dir tree
  refuse() {
    echo "refusing to install: untrusted source path $1 (owner must be root or uid $uid, not group/world-writable)" >&2
    exit 77
  }
  if [[ $EUID -eq 0 ]]; then
    uid="${SUDO_UID:-0}"
  else
    uid="${RADON_RUNNER_INSTALL_TRUSTED_UID:-$EUID}"
  fi
  tree="$(cd "$SRC/../.." && pwd -P)"
  dir="$(dirname "$tree")"
  while :; do
    bad="$(find "$dir" -maxdepth 0 \( \( ! -user 0 ! -user "$uid" \) -o \( \( -perm -0020 -o -perm -0002 \) ! -perm -1000 \) \) -print)"
    [[ -z "$bad" ]] || refuse "$bad"
    [[ "$dir" == / ]] && break
    dir="$(dirname "$dir")"
  done
  local paths=("$tree/scripts/runner") file
  # Every directory a LIB_FILES entry lives in, derived rather than listed: a
  # leaf-only check accepted a group-writable scripts/utils, and that is where
  # utils/atomic_io.py the root updater imports comes from.
  local dirs=("$tree" "$tree/scripts") seen=" scripts "
  for file in $LIB_FILES; do
    paths+=("$tree/scripts/$file")
    [[ "$file" == */* ]] || continue
    local sub="${file%/*}"
    [[ "$seen" == *" $sub "* ]] && continue
    seen+="$sub "
    dirs+=("$tree/scripts/$sub")
  done
  bad="$({ find "${dirs[@]}" -maxdepth 0 \( \( ! -user 0 ! -user "$uid" \) -o -perm -0020 -o -perm -0002 \) -print
    find "${paths[@]}" \( \( ! -user 0 ! -user "$uid" \) -o -perm -0020 -o -perm -0002 -o -type l \) -print; } 2>&1 | sed -n '1p')"
  [[ -z "$bad" ]] || refuse "$bad"
}

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
        <string>$GUARD_PYTHON</string>
        <string>-I</string>
        <string>$PREFIX/lifecycle_gate.py</string>
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
        <key>TMPDIR</key>
        <string>$BOT_HOME/radon-runner/tmp/</string>
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

# A fixed disabled-login account owns only its private probe inputs and the
# snapshot handoff. No sudoers entry or root command is exposed to the bot.
ensure_perf_probe_user() {
  local helper=_radonprobe helper_uid next_uid membership read_group=_radonprobe_read read_gid members member helper_uuid bot_uuid
  if ! dscl . -read "/Groups/$read_group" >/dev/null 2>&1; then
    read_gid=400
    while dscl . -search /Groups PrimaryGroupID "$read_gid" | grep -q .; do
      read_gid=$((read_gid + 1))
      [[ "$read_gid" -lt 500 ]] || { echo "no reserved snapshot group available" >&2; return 77; }
    done
    dscl . -create "/Groups/$read_group"
    dscl . -create "/Groups/$read_group" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Groups/$read_group" PrimaryGroupID "$read_gid"
    dscl . -create "/Groups/$read_group" Password '*'
  fi
  read_gid="$(dscl . -read "/Groups/$read_group" PrimaryGroupID | awk '{print $2}')"
  [[ "$read_gid" =~ ^[0-9]+$ && "$read_gid" -gt 0 ]] \
    || { echo "snapshot read group is invalid" >&2; return 77; }
  if ! id "$helper" >/dev/null 2>&1; then
    next_uid=400
    while dscl . -search /Users UniqueID "$next_uid" | grep -q .; do
      next_uid=$((next_uid + 1))
      [[ "$next_uid" -lt 500 ]] || { echo "no reserved helper uid available" >&2; return 77; }
    done
    dscl . -create "/Users/$helper"
    dscl . -create "/Users/$helper" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Users/$helper" UniqueID "$next_uid"
    dscl . -create "/Users/$helper" PrimaryGroupID "$read_gid"
    dscl . -create "/Users/$helper" NFSHomeDirectory /var/empty
    dscl . -create "/Users/$helper" UserShell /usr/bin/false
    dscl . -create "/Users/$helper" Password '*'
    dscl . -create "/Users/$helper" IsHidden 1
  fi
  [[ "$(id -g "$helper")" == "$read_gid" ]] \
    || { echo "snapshot helper must use its dedicated read group" >&2; return 77; }
  # Existing dedicated groups must not silently export data to other users.
  members="$(dscl . -read "/Groups/$read_group" GroupMembership 2>/dev/null || true)"
  for member in ${members#GroupMembership:}; do
    [[ "$member" == "$helper" || "$member" == "$BOT" ]] \
      || { echo "snapshot read group contains an unexpected member" >&2; return 77; }
  done
  helper_uuid="$(dscl . -read "/Users/$helper" GeneratedUID | awk '{print $2}')"
  bot_uuid="$(dscl . -read "/Users/$BOT" GeneratedUID | awk '{print $2}')"
  [[ -n "$helper_uuid" && -n "$bot_uuid" ]] || { echo "snapshot member identity is unverifiable" >&2; return 77; }
  members="$(dscl . -read "/Groups/$read_group" GroupMembers 2>/dev/null || true)"
  for member in ${members#GroupMembers:}; do
    [[ "$member" == "$helper_uuid" || "$member" == "$bot_uuid" ]] \
      || { echo "snapshot read group contains an unexpected identity" >&2; return 77; }
  done
  dseditgroup -o edit -a "$helper" -t user "$read_group"
  dseditgroup -o edit -a "$BOT" -t user "$read_group"
  helper_uid="$(id -u "$helper")"
  [[ "$helper_uid" -gt 0 && "$helper_uid" != "$(id -u "$BOT")" ]] \
    || { echo "snapshot helper uid must be distinct and unprivileged" >&2; return 77; }
  [[ "$(dscl . -read "/Users/$helper" UserShell)" == 'UserShell: /usr/bin/false' ]] \
    || { echo "snapshot helper must have a disabled login shell" >&2; return 77; }
  membership="$(dsmemberutil checkmembership -U "$helper" -G admin)" \
    || { echo "snapshot helper group membership is unverifiable" >&2; return 77; }
  [[ "$membership" == *'is not a member'* ]] \
    || { echo "snapshot helper must not be an administrator" >&2; return 77; }
  [[ "$(dscl . -read "/Users/$helper" NFSHomeDirectory)" == 'NFSHomeDirectory: /var/empty' ]] \
    || { echo "snapshot helper must use its disabled-login home" >&2; return 77; }
}

print_perf_probe_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.radon.runner-perf-probe</string>
<key>UserName</key><string>_radonprobe</string>
<key>ProgramArguments</key><array><string>$GUARD_PYTHON</string><string>-I</string><string>$PREFIX/perf_probe_helper.py</string></array>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>55</integer></dict>
<key>ProcessType</key><string>Background</string>
<key>ExitTimeOut</key><integer>10</integer>
<key>StandardOutPath</key><string>/var/log/radon-perf-probe.log</string>
<key>StandardErrorPath</key><string>/var/log/radon-perf-probe.log</string>
</dict></plist>
EOF
}

install_perf_probe_service() {
  local label=com.radon.runner-perf-probe plist=/Library/LaunchDaemons/com.radon.runner-perf-probe.plist
  # Fixed source/config only. Provisioning never reads or copies credentials.
  "$GUARD_PYTHON" -I "$PREFIX/perf_probe_helper.py" provision
  atomic_text "$plist" 644 print_perf_probe_plist
  plutil -lint "$plist" >/dev/null
  launchctl bootout "system/$label" 2>/dev/null || true
  launchctl bootstrap system "$plist"
}

# Everything under the bot's home is created by the bot itself: root never
# writes, chmods or chowns a path the bot could have swapped for a symlink.
as_bot() { sudo -u "$BOT" "$@"; }

configure_user() {
  local dir
  for dir in "$BOT_HOME/radon-runner" "$BOT_HOME/radon-runner/state" "$BOT_HOME/radon-runner/tmp"; do
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

# Path only, never a secret. Written only when RADON_RUNNER_DOTENV is set so a
# refresh cannot drop it. Never chmod, chown or copy the target .env.
write_dotenv_path() {
  local dest="$PREFIX/dotenv-path" path="${RADON_RUNNER_DOTENV:-}"
  if [[ -z "$path" ]]; then
    return 0
  fi
  case "$path" in
    /*) ;;
    *)
      echo "RADON_RUNNER_DOTENV must be an absolute path" >&2
      return 64
      ;;
  esac
  mkdir -p "$PREFIX"
  printf '%s\n' "$path" > "$dest"
  if [[ $EUID -eq 0 ]]; then
    chown root:wheel "$dest"
    chmod 644 "$dest"
    if ! sudo -u "$BOT" test -r "$path"; then
      echo "WARNING: $BOT cannot read $path" >&2
    fi
  else
    chmod 644 "$dest" 2>/dev/null || true
  fi
}

# Preserve complete installed programs and plists across kills or power loss.
# All targets are under trusted root-owned directories; never truncate the
# installed updater that must repair an interrupted generation on its next fire.
atomic_replace() {
  "$GUARD_PYTHON" -I - "$1" "$2" <<'PY_REPLACE'
import os, sys
source, target = sys.argv[1:]
fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
try:
    os.fsync(fd)
finally:
    os.close(fd)
os.replace(source, target)
fd = os.open(os.path.dirname(target), os.O_RDONLY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY_REPLACE
}

atomic_install() {
  local args=("$@") target temporary
  target="${args[${#args[@]}-1]}"
  temporary="$(mktemp "$(dirname "$target")/.radon-copy.XXXXXX")"
  args[${#args[@]}-1]="$temporary"
  if ! install "${args[@]}"; then rm -f -- "$temporary"; return 1; fi
  atomic_replace "$temporary" "$target"
}

atomic_text() {
  local target="$1" mode="$2" temporary
  shift 2
  temporary="$(mktemp "$(dirname "$target")/.radon-text.XXXXXX")"
  if ! "$@" > "$temporary"; then rm -f -- "$temporary"; return 1; fi
  chown root:wheel "$temporary"
  chmod "$mode" "$temporary"
  atomic_replace "$temporary" "$target"
}

install_runner() {
  local file
  rm -f -- "$PREFIX/maintenance-ready.json"
  install -d -o root -g wheel -m 755 "$PREFIX" "$PREFIX/loops" "$PREFIX/hooks" "$PREFIX/lib" "$PREFIX/guard"
  atomic_install -o root -g wheel -m 755 "$SRC/run_loop.sh" "$PREFIX/run_loop.sh"
  atomic_install -o root -g wheel -m 644 "$SRC/phase_status.sh" "$PREFIX/phase_status.sh"
  atomic_install -o root -g wheel -m 755 "$SRC/lifecycle_gate.py" "$PREFIX/lifecycle_gate.py"
  atomic_install -o root -g wheel -m 755 "$SRC/update_installed.py" "$PREFIX/update_installed.py"
  atomic_install -o root -g wheel -m 755 "$SRC/perf_probe_helper.py" "$PREFIX/perf_probe_helper.py"
  [[ -e "$PREFIX/maintenance.lock" ]] || atomic_install -o root -g wheel -m 644 /dev/null "$PREFIX/maintenance.lock"
  atomic_install -o root -g wheel -m 644 "$SRC/toolchain-policy.json" "$PREFIX/toolchain-policy.json"
  install -d -o root -g wheel -m 755 "$PREFIX/toolchain"
  atomic_install -o root -g wheel -m 644 "$SRC/toolchain/package.json" "$PREFIX/toolchain/package.json"
  atomic_install -o root -g wheel -m 644 "$SRC/toolchain/package-lock.json" "$PREFIX/toolchain/package-lock.json"
  atomic_text "$PREFIX/gitconfig" 644 print_gitconfig
  for file in "$SRC/hooks/"*; do
    [[ -f "$file" ]] && atomic_install -o root -g wheel -m 755 "$file" "$PREFIX/hooks/$(basename "$file")"
  done
  # Every loop env, not only the named loops: run_loop.sh reads the installed
  # env, so a single-loop install must not leave a sibling's config behind it.
  for file in "$SRC/loops/"*.env; do
    atomic_install -o root -g wheel -m 644 "$file" "$PREFIX/loops/$(basename "$file")"
  done
  for file in $LIB_FILES; do
    if [[ "$file" == utils/* ]]; then
      install -d -o root -g wheel -m 755 "$PREFIX/lib/utils"
      atomic_install -o root -g wheel -m 644 "$SRC/../$file" "$PREFIX/lib/$file"
    else
      atomic_install -o root -g wheel -m 755 "$SRC/../$file" "$PREFIX/lib/${file##*/}"
    fi
  done
  write_dotenv_path
}

install_loop() {
  local loop="$1" label="com.radon.runner.$1"
  local plist="/Library/LaunchDaemons/$label.plist"
  if [[ "$(loop_setting "$loop" GH_GUARD)" == 1 ]]; then
    install -d -o root -g wheel -m 755 "$PREFIX/guard/$loop"
    atomic_text "$PREFIX/guard/$loop/gh" 755 print_guard "$loop"
  fi
  atomic_text "$plist" 644 print_plist "$loop"
  plutil -lint "$plist" >/dev/null
  launchctl bootout "system/$label" 2>/dev/null || true
  launchctl bootstrap system "$plist"
  echo "installed $label"
}

refuse_active_install() {
  local loop output
  for loop in documentation ci-performance performance testing reliability security security-deepsec; do
    if output="$(launchctl print "system/com.radon.runner.$loop" 2>/dev/null)"; then
      if ! grep -qE '^[[:space:]]*state = not running[[:space:]]*$' <<< "$output"; then
        echo "maintenance deferred: $loop is active or unverifiable" >&2
        return 75
      fi
    fi
  done
  if output="$(launchctl print system/com.radon.runner-perf-probe 2>/dev/null)"; then
    if ! grep -qE '^[[:space:]]*state = not running[[:space:]]*$' <<< "$output"; then
      echo "maintenance deferred: snapshot helper is active or unverifiable" >&2
      return 75
    fi
  fi
  if output="$(launchctl print system/com.radon.runner-native-update 2>/dev/null)"; then
    if ! grep -qE '^[[:space:]]*state = not running[[:space:]]*$' <<< "$output"; then
      echo "maintenance deferred: native CLI update is active or unverifiable" >&2
      return 75
    fi
  fi
}

print_native_update_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.radon.runner-native-update</string>
<key>UserName</key><string>$BOT</string>
<key>EnvironmentVariables</key><dict><key>HOME</key><string>$BOT_HOME</string><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
<key>ProgramArguments</key><array><string>$GUARD_PYTHON</string><string>-I</string><string>$PREFIX/lib/nightly_toolchain_update.py</string><string>native</string><string>--policy</string><string>$PREFIX/toolchain-policy.json</string><string>--package-dir</string><string>$PREFIX/toolchain</string><string>--release-root</string><string>$BOT_HOME/radon-runner/toolchain</string></array>
<key>StartInterval</key><integer>3600</integer>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>$BOT_HOME/radon-runner/native-update.log</string>
<key>StandardErrorPath</key><string>$BOT_HOME/radon-runner/native-update.log</string>
</dict></plist>
EOF
}

install_native_update_service() {
  local label=com.radon.runner-native-update plist=/Library/LaunchDaemons/com.radon.runner-native-update.plist
  atomic_text "$plist" 644 print_native_update_plist
  plutil -lint "$plist" >/dev/null
  if ! launchctl print "system/$label" >/dev/null 2>&1; then
    launchctl bootstrap system "$plist"
  fi
}

print_update_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.radon.runner-update</string>
<key>UserName</key><string>root</string>
<key>ProgramArguments</key><array><string>$GUARD_PYTHON</string><string>-I</string><string>$PREFIX/update_installed.py</string></array>
<key>StartInterval</key><integer>21600</integer>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>/var/log/radon-runner-update.log</string>
<key>StandardErrorPath</key><string>/var/log/radon-runner-update.log</string>
</dict></plist>
EOF
}

install_update_service() {
  local label=com.radon.runner-update plist=/Library/LaunchDaemons/com.radon.runner-update.plist
  atomic_text "$plist" 644 print_update_plist
  plutil -lint "$plist" >/dev/null
  # Refresh must not boot out its own live launchd job before writing receipt.
  if ! launchctl print "system/$label" >/dev/null 2>&1; then
    launchctl bootstrap system "$plist"
  fi
}

write_maintenance_ready() {
  "$GUARD_PYTHON" -I - "$PREFIX" <<'PY_READY'
import json, os, pathlib, plistlib, tempfile, subprocess
root = pathlib.Path(__import__('sys').argv[1])
loops = ['documentation', 'ci-performance', 'performance', 'testing', 'reliability', 'security', 'security-deepsec']
ready = root / 'maintenance-ready.json'
for loop in loops:
    path = pathlib.Path('/Library/LaunchDaemons') / f'com.radon.runner.{loop}.plist'
    if not path.is_file() or path.is_symlink() or path.stat().st_uid != 0:
        ready.unlink(missing_ok=True)
        raise SystemExit(0)
    args = plistlib.loads(path.read_bytes()).get('ProgramArguments', [])
    loaded = subprocess.run(['/bin/launchctl', 'print', f'system/com.radon.runner.{loop}'], capture_output=True, text=True)
    if loaded.returncode or str(root / 'lifecycle_gate.py') not in loaded.stdout or str(root / 'lifecycle_gate.py') not in args:
        ready.unlink(missing_ok=True)
        raise SystemExit(0)
fd, temporary = tempfile.mkstemp(dir=root, prefix='.maintenance-ready-')
with os.fdopen(fd, 'w') as stream:
    json.dump({'version': 1, 'loops': loops}, stream)
    stream.flush()
    os.fsync(stream.fileno())
os.chmod(temporary, 0o644)
os.replace(temporary, ready)
PY_READY
}

main() {
  [[ $# -gt 0 ]] || { echo "usage: $0 <loop>... | --print-plist <loop> | --print-guard <loop> | --print-gitconfig | --write-dotenv-path" >&2; exit 64; }
  if [[ "$1" == "--print-perf-probe-plist" ]]; then
    print_perf_probe_plist
    return 0
  fi
  if [[ "$1" == "--print-native-update-plist" ]]; then
    print_native_update_plist
    return 0
  fi
  if [[ "$1" == "--print-update-plist" ]]; then
    print_update_plist
    return 0
  fi
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
  if [[ "$1" == "--write-dotenv-path" ]]; then
    write_dotenv_path
    return
  fi
  check_source
  [[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
  local loop
  for loop in "$@"; do
    [[ -f "$SRC/loops/$loop.env" ]] || { echo "no scripts/runner/loops/$loop.env" >&2; exit 64; }
  done
  refuse_active_install
  ensure_user
  ensure_perf_probe_user
  configure_user
  install_runner
  for loop in "$@"; do install_loop "$loop"; done
  if [[ -f "$PREFIX/loops/performance.env" ]]; then install_perf_probe_service; fi
  write_maintenance_ready
  install_update_service
  install_native_update_service
}

main "$@"
