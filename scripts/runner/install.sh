#!/bin/bash
# Installs the nightly runner for the named loops on the always-on Mac.
#
#   sudo scripts/runner/install.sh documentation [more loops...]
#   scripts/runner/install.sh --print-plist documentation     (no root; for review/tests)
#   scripts/runner/install.sh --print-guard security          (no root; the gh shim a GH_GUARD=1 loop gets)
#   scripts/runner/install.sh --print-gitconfig               (no root; the git config every runner git reads)
#   scripts/runner/install.sh --print-publisher-plist         (no root; the off-by-default publisher job)
#   scripts/runner/install.sh --publisher-plan                (no root; prints enabled or disabled, creates nothing)
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
# Off until a root-owned flag exists. install never creates the flag.
LOGD_USER=_radonlog
LOGD_GROUP=_radonlog_ingest
LOGD_FLAG="${RADON_RUNNER_LOGD_FLAG:-$PREFIX/enable-runner-logd}"
LOGD_ROOT="${RADON_RUNNER_LOGD_ROOT:-/Library/Radon/runner-logs}"
LOGD_PLIST=/Library/LaunchDaemons/com.radon.runner-logd.plist
# pwd -P, matching check_source: with a logical path the ancestor walk
# validates one chain and the copies later read another.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REAL_GH="${RADON_RUNNER_GH:-/opt/homebrew/bin/gh}"
GUARD_PYTHON="${RADON_RUNNER_PYTHON:-/opt/homebrew/bin/python3.13}"
# Copied from scripts/ into $PREFIX/lib, root-owned: the hooks, the resolver
# and the gh guard run these, never the agent-writable clone's copies.
LIB_FILES="nightly_pr_guard.py nightly_publish.py nightly_issue_prune.py nightly_green_base.py
  nightly_audit_context.py nightly_phase_status.py nightly_deliver.py credential_redaction.py security_claude_ladder.py claude_cli_env_drift.py
  claude_cli_env_reviewed.txt model_gateway/__init__.py model_gateway/client.py model_gateway/cli.py model_gateway/cli_bridge.py ci/check_pr_body.py nightly_toolchain_update.py utils/atomic_io.py"
# The Mac-local model gateway: copied from scripts/ into $PREFIX/gateway/src,
# paths kept. Runner sessions reach this gateway, signed with the Mac's own
# subscription logins, instead of a tunnel to the VPS.
GATEWAY_FILES="model_gateway/__init__.py model_gateway/client.py model_gateway/contracts.py model_gateway/credentials.py
  model_gateway/engine.py model_gateway/latest.py model_gateway/lifecycle.py model_gateway/native.py model_gateway/chat.py model_gateway/provider_gate.py
  model_gateway/server.py model_gateway/specialized.py model_gateway/_forecast_backend.py model_gateway/_catalog_backend.py clients/__init__.py clients/prepaid_denylist.py clients/prepaid_denylist.json
  clients/provider_cooldown.py subscription_tokens.py subscription_vault.py secret_store.py utils/__init__.py utils/atomic_io.py utils/ipv4_first.py"
GW_USER=_radonmodelgw
GW_GROUP=_radonmodel
GW_ROOT=/Library/Radon/model-gateway-local
GW_HOME="$GW_ROOT/home"
# macOS agy keeps its grant in a keychain under $HOME; this home holds only that
# keychain (empty password, no timeout), so agy never sees the other grants.
GW_AGY_HOME="$GW_ROOT/agy-home"
GW_AGY_KEYCHAIN="$GW_AGY_HOME/Library/Keychains/radon-agy.keychain-db"
GW_SOCKET="$GW_ROOT/run/gateway.sock"
GW_CONTROL="$GW_ROOT/control/control.sock"
# Concurrent calls per provider. The two security loops share one Claude login.
# They run on different weekdays and take subscription-claude.lock so they
# are not in that login at the same time. Nested calls inside one loop still
# use this cap.
GW_CONCURRENCY=16
# Off until /usr/local/radon-runner/enable-publisher exists. The flag and its
# parent must be root-owned and not group/world-writable, so _radonbot cannot
# replace the flag. install_publisher copies no credential.
PUB_USER="${RADON_PUBLISHER_USER:-_radonpub}"
PUB_CONNECT_GROUP="${RADON_PUBLISHER_CONNECT_GROUP:-_radonpub_connect}"
PUB_ROOT="${RADON_PUBLISHER_ROOT:-/Library/Radon/publisher}"

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
  paths+=("$tree/scripts/runner/gateway_launch.py" "$tree/scripts/runner/gateway-requirements.txt"
    "$tree/scripts/runner/install_antigravity.py" "$tree/scripts/runner/antigravity-darwin.json")
  for file in $LIB_FILES $GATEWAY_FILES; do
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

# One dict fires every day. SCHEDULE_WEEKDAYS (0-6, Sunday is 0) is an array
# of dicts, one weekday each. launchd also treats 7 as Sunday, so 7 is refused
# here to keep a Sunday fire from being listed twice.
calendar_interval() {
  local hour="$1" minute="$2" days="$3" day seen=" "
  if [[ -z "$days" ]]; then
    cat <<EOF
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$hour</integer>
        <key>Minute</key>
        <integer>$minute</integer>
    </dict>
EOF
    return 0
  fi
  printf '    <key>StartCalendarInterval</key>\n    <array>\n'
  local IFS=,
  for day in $days; do
    [[ "$day" =~ ^[0-6]$ ]] || { echo "SCHEDULE_WEEKDAYS must be 0-6 comma-separated, got '$day'" >&2; return 64; }
    [[ "$seen" != *" $day "* ]] || { echo "SCHEDULE_WEEKDAYS repeats $day" >&2; return 64; }
    seen="$seen$day "
    cat <<EOF
        <dict>
            <key>Weekday</key>
            <integer>$day</integer>
            <key>Hour</key>
            <integer>$hour</integer>
            <key>Minute</key>
            <integer>$minute</integer>
        </dict>
EOF
  done
  printf '    </array>\n'
}

print_plist() {
  local loop="$1" hour minute days interval
  hour="$(loop_setting "$loop" SCHEDULE_HOUR)"
  minute="$(loop_setting "$loop" SCHEDULE_MINUTE)"
  days="$(loop_setting "$loop" SCHEDULE_WEEKDAYS)"
  interval="$(calendar_interval "$hour" "$minute" "$days")"
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
$interval
    <key>StandardOutPath</key>
    <string>$BOT_HOME/radon-runner/launchd-$loop.log</string>
    <key>StandardErrorPath</key>
    <string>$BOT_HOME/radon-runner/launchd-$loop.log</string>
</dict>
</plist>
EOF
}

# Nested native agent calls use the same private model bridge and root-owned
# immutable clients. They inherit local tool policies and Git guards.
print_model_shim() {
  local kind="$1"
  cat <<EOF
#!/bin/bash
set -euo pipefail
exec $GUARD_PYTHON -I /Library/Radon/model-clients/current/lib/model_gateway/cli.py launch $kind -- /Library/Radon/model-clients/current/bin/$kind "\$@"
EOF
}

# Ids baked into the gateway plists. As root they come from the directory
# service; the env overrides exist only for the no-root print mode tests use.
gateway_ids() {
  if [[ $EUID -eq 0 ]]; then
    GW_CLIENT_UID="$(id -u "$BOT")"
    GW_GID="$(dscl . -read "/Groups/$GW_GROUP" PrimaryGroupID | awk '{print $2}')"
  else
    GW_CLIENT_UID="${RADON_GATEWAY_CLIENT_UID:?}"
    GW_GID="${RADON_GATEWAY_GID:?}"
  fi
  [[ "$GW_CLIENT_UID" =~ ^[1-9][0-9]*$ && "$GW_GID" =~ ^[1-9][0-9]*$ ]] \
    || { echo "model gateway identities are invalid" >&2; return 77; }
}

print_gateway_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.radon.model-gateway-local</string>
<key>UserName</key><string>$GW_USER</string>
<key>GroupName</key><string>$GW_GROUP</string>
<key>ProgramArguments</key><array><string>$PREFIX/gateway/venv/bin/python</string><string>-I</string><string>$PREFIX/gateway/gateway_launch.py</string><string>serve</string><string>--socket</string><string>$GW_SOCKET</string><string>--control-socket</string><string>$GW_CONTROL</string></array>
<key>EnvironmentVariables</key><dict>
<key>HOME</key><string>$GW_HOME</string>
<key>PATH</key><string>/Library/Radon/model-clients/current/bin:/usr/bin:/bin</string>
<key>CLAUDE_CODE_OAUTH_TOKEN_FILE</key><string>$GW_HOME/claude-oauth-token</string>
<key>CODEX_HOME</key><string>$GW_HOME/.codex</string>
<key>ANTIGRAVITY_CLI</key><string>$PREFIX/gateway/bin/agy</string>
<key>RADON_ANTIGRAVITY_KEYCHAIN_HOME</key><string>$GW_AGY_HOME</string>
<key>RADON_SUBSCRIPTION_STATE_DIR</key><string>$GW_HOME/state</string>
<key>RADON_MODEL_CLIENT_UIDS</key><string>$GW_CLIENT_UID</string>
<key>RADON_MODEL_TUNNEL_GID</key><string>$GW_GID</string>
<key>RADON_MODEL_PROVIDER_CONCURRENCY</key><string>$GW_CONCURRENCY</string>
</dict>
<key>KeepAlive</key><true/>
<key>RunAtLoad</key><true/>
<key>SessionCreate</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>$GW_ROOT/log/gateway.log</string>
<key>StandardErrorPath</key><string>$GW_ROOT/log/gateway.log</string>
</dict></plist>
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

# A fixed disabled-login account holds the Mac's model grants. The bot never
# can read them: it reaches the gateway only through the group socket.
ensure_gateway_user() {
  local gid next_uid membership
  if ! dscl . -read "/Groups/$GW_GROUP" >/dev/null 2>&1; then
    dseditgroup -o create "$GW_GROUP"
  fi
  gid="$(dscl . -read "/Groups/$GW_GROUP" PrimaryGroupID | awk '{print $2}')"
  [[ "$gid" =~ ^[0-9]+$ && "$gid" -gt 0 ]] || { echo "model gateway group is invalid" >&2; return 77; }
  if ! id "$GW_USER" >/dev/null 2>&1; then
    next_uid=400
    while dscl . -search /Users UniqueID "$next_uid" | grep -q .; do
      next_uid=$((next_uid + 1))
      [[ "$next_uid" -lt 500 ]] || { echo "no reserved gateway uid available" >&2; return 77; }
    done
    dscl . -create "/Users/$GW_USER"
    dscl . -create "/Users/$GW_USER" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Users/$GW_USER" UniqueID "$next_uid"
    dscl . -create "/Users/$GW_USER" PrimaryGroupID "$gid"
    dscl . -create "/Users/$GW_USER" NFSHomeDirectory "$GW_HOME"
    dscl . -create "/Users/$GW_USER" UserShell /usr/bin/false
    dscl . -create "/Users/$GW_USER" Password '*'
    dscl . -create "/Users/$GW_USER" IsHidden 1
  fi
  [[ "$(id -g "$GW_USER")" == "$gid" ]] || { echo "model gateway account must use $GW_GROUP" >&2; return 77; }
  [[ "$(dscl . -read "/Users/$GW_USER" UserShell)" == 'UserShell: /usr/bin/false' ]] \
    || { echo "model gateway account must have a disabled login shell" >&2; return 77; }
  membership="$(dsmemberutil checkmembership -U "$GW_USER" -G admin)" \
    || { echo "model gateway group membership is unverifiable" >&2; return 77; }
  [[ "$membership" == *'is not a member'* ]] || { echo "model gateway account must not be an administrator" >&2; return 77; }
  dseditgroup -o edit -a "$GW_USER" -t user "$GW_GROUP"
  dseditgroup -o edit -a "$BOT" -t user "$GW_GROUP"
}

# Copies the gateway source and launcher into $1; the rest is install's
# ownership flags (root:wheel on the Mac, none for --stage-gateway).
stage_gateway() {
  local dest="$1" file
  shift
  install -d "$@" -m 755 "$dest" "$dest/src"
  for file in $GATEWAY_FILES; do
    [[ "$file" != */* ]] || install -d "$@" -m 755 "$dest/src/${file%/*}"
    atomic_install "$@" -m 644 "$SRC/../$file" "$dest/src/$file"
  done
  atomic_install "$@" -m 644 "$SRC/gateway_launch.py" "$dest/gateway_launch.py"
}

# As the gateway account with agy's home; from / because root's cwd is the
# operator's, which that account cannot read.
as_agy_home() { (cd / && sudo -u "$GW_USER" env HOME="$GW_AGY_HOME" PATH=/usr/bin:/bin "$@"); }

ensure_agy_keychain() {
  install -d -o "$GW_USER" -g "$GW_GROUP" -m 700 "$GW_AGY_HOME"
  as_agy_home /bin/mkdir -p -m 700 "$GW_AGY_HOME/Library/Keychains" "$GW_AGY_HOME/Library/Preferences"
  as_agy_home /bin/test -f "$GW_AGY_KEYCHAIN" || as_agy_home /usr/bin/security create-keychain -p '' "$GW_AGY_KEYCHAIN"
  as_agy_home /usr/bin/security set-keychain-settings "$GW_AGY_KEYCHAIN"
  as_agy_home /usr/bin/security default-keychain -d user -s "$GW_AGY_KEYCHAIN"
  as_agy_home /usr/bin/security list-keychains -d user -s "$GW_AGY_KEYCHAIN"
}

# Root owns the tree and its root (group traversal only), so the gateway
# account cannot swap a directory root later writes or chowns.
install_gateway() {
  local file stamp want
  install -d -o root -g "$GW_GROUP" -m 710 "$GW_ROOT"
  install -d -o "$GW_USER" -g "$GW_GROUP" -m 700 "$GW_HOME" "$GW_ROOT/run" "$GW_ROOT/control"
  install -d -o "$GW_USER" -g "$GW_GROUP" -m 750 "$GW_ROOT/log"
  stage_gateway "$PREFIX/gateway" -o root -g wheel
  want="$(shasum -a 256 "$SRC/gateway-requirements.txt" | awk '{print $1}') $("$GUARD_PYTHON" -I -c 'import sys; print(sys.version.split()[0])')"
  stamp="$PREFIX/gateway/venv/.radon-requirements"
  if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$want" ]]; then
    "$GUARD_PYTHON" -I -m venv --clear "$PREFIX/gateway/venv"
    "$PREFIX/gateway/venv/bin/python" -I -m pip install --quiet --disable-pip-version-check --no-input \
      --require-hashes --no-deps --only-binary :all: -r "$SRC/gateway-requirements.txt"
    printf '%s\n' "$want" > "$stamp"
  fi
  chown -R root:wheel "$PREFIX/gateway/venv"
  install -d -o root -g wheel -m 755 "$PREFIX/gateway/bin"
  "$GUARD_PYTHON" -I "$SRC/install_antigravity.py" "$SRC/antigravity-darwin.json" "$PREFIX/gateway/bin/agy"
  ensure_agy_keychain
  chmod -R go-w "$PREFIX/gateway/venv"
  gateway_ids
  atomic_text /Library/LaunchDaemons/com.radon.model-gateway-local.plist 644 print_gateway_plist
  plutil -lint /Library/LaunchDaemons/com.radon.model-gateway-local.plist >/dev/null
  launchctl bootout system/com.radon.model-gateway-local 2>/dev/null || true
  launchctl bootstrap system /Library/LaunchDaemons/com.radon.model-gateway-local.plist
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
  atomic_install -o root -g wheel -m 755 "$SRC/runner_logd.py" "$PREFIX/runner_logd.py"
  atomic_install -o root -g wheel -m 755 "$SRC/egress_proxy.py" "$PREFIX/egress_proxy.py"
  atomic_install -o root -g wheel -m 755 "$SRC/egress_report.py" "$PREFIX/egress_report.py"
  atomic_install -o root -g wheel -m 644 "$SRC/egress-allowlist.example" "$PREFIX/egress-allowlist.example"
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
    if [[ "$file" == utils/* || "$file" == model_gateway/* ]]; then
      install -d -o root -g wheel -m 755 "$PREFIX/lib/${file%/*}"
      atomic_install -o root -g wheel -m 644 "$SRC/../$file" "$PREFIX/lib/$file"
    else
      atomic_install -o root -g wheel -m 755 "$SRC/../$file" "$PREFIX/lib/${file##*/}"
    fi
  done
  install -d -o root -g wheel -m 755 "$PREFIX/model-bin"
  for file in claude codex grok; do
    atomic_text "$PREFIX/model-bin/$file" 755 print_model_shim "$file"
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

# Creates the egress account, daemon, and packet filter only when
# /usr/local/radon-runner/enable-egress is a root-owned regular file the
# runner account cannot write. Otherwise this is a no-op.
install_egress() {
  RADON_EGRESS_PYTHON="$GUARD_PYTHON" "$GUARD_PYTHON" -I "$SRC/egress_proxy.py" install
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

# Hidden disabled-login account. The bot may write the ingest socket and
# cannot read the store or the deploy key.
ensure_logd_user() {
  local gid next_uid membership
  id "$BOT" >/dev/null || { echo "runner account $BOT is missing" >&2; return 1; }
  if ! dscl . -read "/Groups/$LOGD_GROUP" >/dev/null 2>&1; then
    gid=400
    while dscl . -search /Groups PrimaryGroupID "$gid" | grep -q .; do
      gid=$((gid + 1))
      [[ "$gid" -lt 500 ]] || { echo "no reserved log group available" >&2; return 77; }
    done
    dscl . -create "/Groups/$LOGD_GROUP"
    dscl . -create "/Groups/$LOGD_GROUP" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Groups/$LOGD_GROUP" PrimaryGroupID "$gid"
    dscl . -create "/Groups/$LOGD_GROUP" Password '*'
  fi
  gid="$(dscl . -read "/Groups/$LOGD_GROUP" PrimaryGroupID | awk '{print $2}')"
  [[ "$gid" =~ ^[0-9]+$ && "$gid" -gt 0 ]] || { echo "log group is invalid" >&2; return 77; }
  if ! id "$LOGD_USER" >/dev/null 2>&1; then
    next_uid=400
    while dscl . -search /Users UniqueID "$next_uid" | grep -q .; do
      next_uid=$((next_uid + 1))
      [[ "$next_uid" -lt 500 ]] || { echo "no reserved log uid available" >&2; return 77; }
    done
    dscl . -create "/Users/$LOGD_USER"
    dscl . -create "/Users/$LOGD_USER" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Users/$LOGD_USER" UniqueID "$next_uid"
    dscl . -create "/Users/$LOGD_USER" PrimaryGroupID "$gid"
    dscl . -create "/Users/$LOGD_USER" NFSHomeDirectory /var/empty
    dscl . -create "/Users/$LOGD_USER" UserShell /usr/bin/false
    dscl . -create "/Users/$LOGD_USER" Password '*'
    dscl . -create "/Users/$LOGD_USER" IsHidden 1
  fi
  [[ "$(id -g "$LOGD_USER")" == "$gid" ]] || { echo "log account must use $LOGD_GROUP" >&2; return 77; }
  [[ "$(dscl . -read "/Users/$LOGD_USER" UserShell)" == 'UserShell: /usr/bin/false' ]] \
    || { echo "log account must have a disabled login shell" >&2; return 77; }
  membership="$(dsmemberutil checkmembership -U "$LOGD_USER" -G admin)" \
    || { echo "log account group membership is unverifiable" >&2; return 77; }
  [[ "$membership" == *'is not a member'* ]] || { echo "log account must not be an administrator" >&2; return 77; }
  dseditgroup -o edit -a "$LOGD_USER" -t user "$LOGD_GROUP"
  dseditgroup -o edit -a "$BOT" -t user "$LOGD_GROUP"
  [[ "$(id -u "$LOGD_USER")" != "$(id -u "$BOT")" ]] || { echo "log account must not be the bot" >&2; return 77; }
}

print_logd_plist() {
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.radon.runner-logd</string>
<key>UserName</key><string>$LOGD_USER</string>
<key>ProgramArguments</key><array>
<string>$GUARD_PYTHON</string><string>-I</string>
<string>$PREFIX/runner_logd.py</string><string>serve</string>
<string>--root</string><string>$LOGD_ROOT</string>
<string>--config</string><string>$PREFIX/runner-logd.conf</string>
</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>$LOGD_ROOT/daemon.log</string>
<key>StandardErrorPath</key><string>$LOGD_ROOT/daemon.log</string>
</dict></plist>
EOF
}

# A hidden, disabled-login account. It is the only reader of the publisher
# secrets directory. _radonbot is in the connect group so it can use the
# socket, and that directory is mode 700 so the group cannot read secrets.
ensure_publisher_user() {
  local gid next_uid membership
  if ! dscl . -read "/Groups/$PUB_CONNECT_GROUP" >/dev/null 2>&1; then
    gid=400
    while dscl . -search /Groups PrimaryGroupID "$gid" | grep -q .; do
      gid=$((gid + 1))
      [[ "$gid" -lt 500 ]] || { echo "no reserved publisher group available" >&2; return 77; }
    done
    dscl . -create "/Groups/$PUB_CONNECT_GROUP"
    dscl . -create "/Groups/$PUB_CONNECT_GROUP" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Groups/$PUB_CONNECT_GROUP" PrimaryGroupID "$gid"
    dscl . -create "/Groups/$PUB_CONNECT_GROUP" Password '*'
  fi
  gid="$(dscl . -read "/Groups/$PUB_CONNECT_GROUP" PrimaryGroupID | awk '{print $2}')"
  [[ "$gid" =~ ^[0-9]+$ && "$gid" -gt 0 ]] || { echo "publisher group is invalid" >&2; return 77; }
  if ! id "$PUB_USER" >/dev/null 2>&1; then
    next_uid=400
    while dscl . -search /Users UniqueID "$next_uid" | grep -q .; do
      next_uid=$((next_uid + 1))
      [[ "$next_uid" -lt 500 ]] || { echo "no reserved publisher uid available" >&2; return 77; }
    done
    dscl . -create "/Users/$PUB_USER"
    dscl . -create "/Users/$PUB_USER" GeneratedUID "$(/usr/bin/uuidgen)"
    dscl . -create "/Users/$PUB_USER" UniqueID "$next_uid"
    dscl . -create "/Users/$PUB_USER" PrimaryGroupID "$gid"
    dscl . -create "/Users/$PUB_USER" NFSHomeDirectory /var/empty
    dscl . -create "/Users/$PUB_USER" UserShell /usr/bin/false
    dscl . -create "/Users/$PUB_USER" Password '*'
    dscl . -create "/Users/$PUB_USER" IsHidden 1
  fi
  [[ "$(id -g "$PUB_USER")" == "$gid" ]] || { echo "publisher account must use $PUB_CONNECT_GROUP" >&2; return 77; }
  [[ "$(dscl . -read "/Users/$PUB_USER" UserShell)" == 'UserShell: /usr/bin/false' ]] \
    || { echo "publisher account must have a disabled login shell" >&2; return 77; }
  membership="$(dsmemberutil checkmembership -U "$PUB_USER" -G admin)" \
    || { echo "publisher group membership is unverifiable" >&2; return 77; }
  [[ "$membership" == *'is not a member'* ]] || { echo "publisher account must not be an administrator" >&2; return 77; }
  [[ "$(id -u "$PUB_USER")" != "$(id -u "$BOT")" ]] || { echo "publisher uid must differ from $BOT" >&2; return 77; }
  dseditgroup -o edit -a "$PUB_USER" -t user "$PUB_CONNECT_GROUP"
  dseditgroup -o edit -a "$BOT" -t user "$PUB_CONNECT_GROUP"
}

print_publisher_gitconfig() {
  cat <<EOF
[credential "https://github.com"]
	helper = !$GUARD_PYTHON -I $PREFIX/publisher.py credential
	useHttpPath = false
[user]
	name = radon-publisher
	email = radon-publisher@users.noreply.github.com
EOF
}

print_publisher_plist() {
  local uid
  if [[ $EUID -eq 0 ]]; then
    uid="$(id -u "$BOT")"
  else
    uid="${RADON_PUBLISHER_CLIENT_UID:?}"
  fi
  [[ "$uid" =~ ^[0-9]+$ ]] || { echo "publisher client uid is invalid" >&2; return 64; }
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.radon.publisher</string>
<key>UserName</key><string>$PUB_USER</string>
<key>GroupName</key><string>$PUB_CONNECT_GROUP</string>
<key>ProgramArguments</key><array>
<string>$GUARD_PYTHON</string><string>-I</string><string>$PREFIX/publisher.py</string><string>serve</string>
<string>--socket</string><string>$PUB_ROOT/run/publisher.sock</string>
<string>--secrets</string><string>$PUB_ROOT/secrets</string>
<string>--log</string><string>$PUB_ROOT/log/requests.log</string>
<string>--tmp</string><string>$PUB_ROOT/tmp</string>
<string>--gitconfig</string><string>$PREFIX/publisher-gitconfig</string>
<string>--client-uid</string><string>$uid</string>
<string>--bot-home</string><string>$BOT_HOME</string>
</array>
<key>EnvironmentVariables</key><dict>
<key>HOME</key><string>/var/empty</string>
<key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string>
<key>TMPDIR</key><string>$PUB_ROOT/tmp/</string>
<key>RADON_PUBLISHER_SECRETS</key><string>$PUB_ROOT/secrets</string>
</dict>
<key>KeepAlive</key><true/>
<key>RunAtLoad</key><true/>
<key>ThrottleInterval</key><integer>10</integer>
<key>StandardOutPath</key><string>$PUB_ROOT/log/daemon.log</string>
<key>StandardErrorPath</key><string>$PUB_ROOT/log/daemon.log</string>
</dict></plist>
EOF
}

write_logd_config() {
  local dest="$PREFIX/runner-logd.conf"
  [[ -e "$dest" || -L "$dest" ]] && return 0
  cat > "$dest" <<'EOF'
repo=joemccann/radon-runner-logs
interval_secs=900
EOF
  chown root:wheel "$dest"
  chmod 644 "$dest"
}

# Creates the account, directory, key and daemon only when the flag is enabled.
# A missing or writable flag leaves every one of those absent and drops a
# previously installed job so the six-hour updater cannot keep it active.
install_log_daemon() {
  local status label=com.radon.runner-logd
  status="$("$GUARD_PYTHON" -I "$SRC/runner_logd.py" flag-status --flag "$LOGD_FLAG")" || status=disabled
  if [[ "$status" != enabled ]]; then
    if [[ -f "$LOGD_PLIST" && ! -L "$LOGD_PLIST" ]]; then
      launchctl bootout "system/$label" 2>/dev/null || true
      rm -f -- "$LOGD_PLIST"
    fi
    echo "runner-logd disabled"
    return 0
  fi
  [[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; return 1; }
  check_source
  ensure_logd_user
  install -d -o "$LOGD_USER" -g "$LOGD_GROUP" -m 710 "$LOGD_ROOT"
  install -d -o "$LOGD_USER" -g "$LOGD_GROUP" -m 700 "$LOGD_ROOT/keys" "$LOGD_ROOT/repo"
  [[ ! -L "$LOGD_ROOT/daemon.log" ]] || { echo "log daemon output is a symlink" >&2; return 77; }
  if [[ ! -e "$LOGD_ROOT/daemon.log" ]]; then
    sudo -u "$LOGD_USER" /usr/bin/touch "$LOGD_ROOT/daemon.log"
  fi
  chown "$LOGD_USER:$LOGD_GROUP" "$LOGD_ROOT/daemon.log"
  chmod 600 "$LOGD_ROOT/daemon.log"
  # keys/ belongs to the daemon account. Root never chowns or chmods a
  # name there it did not just create: a planted link would carry the
  # change to whatever it points at.
  [[ ! -L "$LOGD_ROOT/keys/id_ed25519" ]] || { echo "log daemon key path is a symlink" >&2; return 77; }
  [[ ! -L "$LOGD_ROOT/keys/id_ed25519.pub" ]] || { echo "log daemon key path is a symlink" >&2; return 77; }
  if [[ ! -e "$LOGD_ROOT/keys/id_ed25519" ]]; then
    sudo -u "$LOGD_USER" /usr/bin/ssh-keygen -t ed25519 -N "" -C radon-runner-logd -f "$LOGD_ROOT/keys/id_ed25519"
    chown "$LOGD_USER:$LOGD_GROUP" "$LOGD_ROOT/keys/id_ed25519" "$LOGD_ROOT/keys/id_ed25519.pub"
    chmod 600 "$LOGD_ROOT/keys/id_ed25519"
    chmod 644 "$LOGD_ROOT/keys/id_ed25519.pub"
  fi
  write_logd_config
  atomic_text "$LOGD_PLIST" 644 print_logd_plist
  plutil -lint "$LOGD_PLIST" >/dev/null
  launchctl bootout "system/$label" 2>/dev/null || true
  launchctl bootstrap system "$LOGD_PLIST"
  echo "installed $label"
  echo "runner-logd public key: $LOGD_ROOT/keys/id_ed25519.pub"
}

# Prints one line and creates nothing. Root install uses the same gate.
publisher_plan() {
  if "$GUARD_PYTHON" -I "$SRC/publisher.py" gate --flag "$PREFIX/enable-publisher"; then
    printf '%s\n' "publisher: enabled"
  else
    printf '%s\n' "publisher: disabled"
  fi
}

# No account, directory, job, or credential unless the flag gate passes.
# The secrets directory is created empty. Existing bot credentials are not read.
install_publisher() {
  local flag="$PREFIX/enable-publisher" label=com.radon.publisher
  local plist="/Library/LaunchDaemons/$label.plist"
  if ! "$GUARD_PYTHON" -I "$SRC/publisher.py" gate --flag "$flag"; then
    echo "publisher: disabled" >&2
    return 0
  fi
  if sudo -u "$BOT" test -w "$flag" || sudo -u "$BOT" test -w "$PREFIX"; then
    echo "publisher: disabled (writable by $BOT)" >&2
    return 0
  fi
  ensure_publisher_user
  if [[ ! -d /Library/Radon ]]; then
    install -d -o root -g wheel -m 755 /Library/Radon
  fi
  install -d -o "$PUB_USER" -g "$PUB_CONNECT_GROUP" -m 750 "$PUB_ROOT"
  install -d -o "$PUB_USER" -g "$PUB_CONNECT_GROUP" -m 700 "$PUB_ROOT/secrets" "$PUB_ROOT/log" "$PUB_ROOT/tmp"
  install -d -o "$PUB_USER" -g "$PUB_CONNECT_GROUP" -m 750 "$PUB_ROOT/run"
  atomic_install -o root -g wheel -m 755 "$SRC/publisher.py" "$PREFIX/publisher.py"
  atomic_text "$PREFIX/publisher-gitconfig" 644 print_publisher_gitconfig
  atomic_text "$plist" 644 print_publisher_plist
  plutil -lint "$plist" >/dev/null
  launchctl bootout "system/$label" 2>/dev/null || true
  launchctl bootstrap system "$plist"
  echo "installed $label"
}

main() {
  [[ $# -gt 0 ]] || { echo "usage: $0 <loop>... | --print-plist <loop> | --print-guard <loop> | --print-gitconfig | --print-logd-plist | --install-logd | --print-publisher-plist | --publisher-plan | --write-dotenv-path" >&2; exit 64; }
  if [[ "$1" == "--stage-gateway" ]]; then
    stage_gateway "${2:?destination}"
    return 0
  fi
  if [[ "$1" == "--print-gateway-plist" ]]; then
    gateway_ids
    print_gateway_plist
    return 0
  fi
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
  if [[ "$1" == "--print-logd-plist" ]]; then
    print_logd_plist
    return 0
  fi
  if [[ "$1" == "--install-logd" ]]; then
    install_log_daemon
    return
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
  if [[ "$1" == "--print-publisher-plist" ]]; then
    print_publisher_plist
    return
  fi
  if [[ "$1" == "--publisher-plan" ]]; then
    publisher_plan
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
  ensure_gateway_user
  configure_user
  install_runner
  install_gateway
  for loop in "$@"; do install_loop "$loop"; done
  if [[ -f "$PREFIX/loops/performance.env" ]]; then install_perf_probe_service; fi
  write_maintenance_ready
  install_update_service
  install_native_update_service
  install_log_daemon
  install_publisher
  install_egress
}

main "$@"
