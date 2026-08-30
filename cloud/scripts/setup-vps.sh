#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# setup-vps.sh -- One-time VPS bootstrap for Radon Cloud
# Clones repos, installs deps, configures Caddy, enables systemd services.
# Idempotent: safe to re-run (skips steps already completed).
# ---------------------------------------------------------------------------

if [[ $EUID -ne 0 && "${RADON_SETUP_SOURCE_ONLY:-0}" != "1" ]]; then
  echo "Error: setup-vps.sh must be run as root"
  echo "Usage: ssh root@<VPS_IP> 'bash -s' < scripts/setup-vps.sh"
  exit 1
fi

# Monorepo: app + production infra share one checkout under RADON_DIR.
# CLOUD_DIR defaults to $RADON_DIR/cloud. Legacy dual-checkout path remains
# overridable via RADON_CLOUD_DIR=/home/radon/radon-cloud during migration.
readonly RADON_DIR="${RADON_APP_DIR:-/home/radon/radon}"
readonly CLOUD_DIR="${RADON_CLOUD_DIR:-${RADON_DIR}/cloud}"
readonly ENV_FILE="${RADON_DEPLOY_ENV_FILE:-/etc/radon/env}"
readonly RADON_REPO="git@github.com:joemccann/radon.git"
readonly CLOUD_REPO="git@github.com:joemccann/radon-cloud.git"  # legacy only
readonly PYTHON_BIN="python3.13"
readonly BUN_VERSION="1.3.14"
readonly VENV_DIR="${RADON_DIR}/.venv"
readonly CADDY_CONFIG_PATH="${RADON_CADDY_CONFIG_PATH:-/etc/caddy/Caddyfile}"
readonly CADDY_LOG_DIR="${RADON_CADDY_LOG_DIR:-/var/log/caddy}"
readonly CADDY_BIN="${RADON_CADDY_BIN:-/usr/bin/caddy}"
readonly CADDY_SYSTEMCTL="${RADON_CADDY_SYSTEMCTL:-/usr/bin/systemctl}"
readonly CADDY_TIMEOUT="${RADON_CADDY_TIMEOUT:-/usr/bin/timeout}"
readonly CADDY_SYNC="${RADON_CADDY_SYNC:-/usr/bin/sync}"

readonly SERVICE_FILES=(
  radon-ib-gateway.service
  radon-ib-gateway-preheld-restart.service
  radon-nextjs.service
  radon-api.service
  radon-relay.service
  radon-monitor.service
  radon-health.service
  radon-newsfeed.service
  radon-refresh.service
  radon-refresh.timer
  radon-vcg-refresh.service
  radon-vcg-refresh.timer
  radon-cta-sync.service
  radon-cta-sync.timer
  radon-portfolio-sync.service
  radon-portfolio-sync.timer
  radon-watchdog-intraday.service
  radon-watchdog-intraday.timer
  radon-watchdog-continuous.service
  radon-watchdog-continuous.timer
  radon-watchdog-daily.service
  radon-watchdog-daily.timer
  radon-watchdog-error.service
  radon-watchdog-error.timer
  radon-ib-watchdog.service
  radon-ib-watchdog.timer
  radon-incident-watchdog.service
  radon-incident-watchdog.timer
  radon-grok-page-responder.service
  radon-grok-page-responder.timer
  radon-flex-pull.service
  radon-flex-pull.timer
  radon-llm-index.service
  radon-llm-index.timer
  radon-leap.service
  radon-leap.timer
  radon-garch.service
  radon-garch.timer
  radon-drift-audit.service
  radon-drift-audit.timer
  radon-db-backup.service
  radon-db-backup.timer
  radon-disk-cleanup.service
  radon-disk-cleanup.timer
  radon-portfolio-archive.service
  radon-portfolio-archive.timer
  radon-media-backup.service
  radon-media-backup.timer
  radon-db-retention.service
  radon-db-retention.timer
  radon-host-metrics.service
  radon-host-metrics.timer
  radon-breadth.service
  radon-breadth.timer
  radon-catalysts.service
  radon-catalysts.timer
  radon-forecast-nightly.service
  radon-forecast-nightly.timer
  radon-nextjs-db-watchdog.service
  radon-nextjs-db-watchdog.timer
  radon-demo-mirror.service
  radon-demo-mirror.timer
  radon-margin-debt.service
  radon-margin-debt.timer
  radon-mktnews.service
  radon-model-catalog.service
  radon-model-catalog.timer
  radon-oi-changes.service
  radon-oi-changes.timer
  radon-knowledge.service
  radon-knowledge.timer
  radon-bpi.service
  radon-bpi.timer
  radon-yield-curve.service
  radon-yield-curve.timer
  radon-straddle.service
  radon-straddle.timer
  radon-cor.service
  radon-cor.timer
  radon-vixcor.service
  radon-vixcor.timer
  radon-skew.service
  radon-skew.timer
  radon-skew2d.service
  radon-skew2d.timer
  radon-signals-refresh.service
  radon-signals-refresh.timer
  radon-flow-refresh.service
  radon-flow-refresh.timer
  radon-vol-cone.service
  radon-vol-cone.timer
  radon-vol-cone-intraday.service
  radon-vol-cone-intraday.timer
  radon-perf-twr.service
  radon-perf-twr.timer
  radon-equibles-ats.service
  radon-equibles-ats.timer
  radon-equibles-short-crowding.service
  radon-equibles-short-crowding.timer
  radon-equibles-13f.service
  radon-equibles-13f.timer
  radon-equibles-cot.service
  radon-equibles-cot.timer
  radon-equibles-filings.service
  radon-equibles-filings.timer
  radon-credit-spread.service
  radon-credit-spread.timer
  radon-ivrank.service
  radon-ivrank.timer
  radon-iei-hyg.service
  radon-iei-hyg.timer
  radon-trin.service
  radon-trin.timer
  radon-divyield.service
  radon-divyield.timer
  radon-hyad.service
  radon-hyad.timer
  radon-hhlev.service
  radon-hhlev.timer
  radon-vixts.service
  radon-vixts.timer
  radon-dispersion.service
  radon-dispersion.timer
)


run_as_radon() {
  sudo -u radon bash -c "$*"
}

# -- Colors ----------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# -- Base packages ----------------------------------------------------------

install_base_packages() {
  local packages=(git curl ca-certificates gnupg software-properties-common ufw)
  local missing=()

  for pkg in "${packages[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
      missing+=("$pkg")
    fi
  done

  if [[ ${#missing[@]} -eq 0 ]]; then
    log_warn "Base packages already installed -- skipping"
    return
  fi

  log_info "Installing base packages: ${missing[*]}..."
  apt update && apt install -y "${missing[@]}"
  log_success "Base packages installed"
}

# -- Prerequisites ----------------------------------------------------------

install_docker() {
  if docker compose version &>/dev/null; then
    log_warn "Docker with compose already installed -- skipping"
    return
  fi

  log_info "Installing Docker from official repo..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null

  apt update
  apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  if id radon &>/dev/null; then
    usermod -aG docker radon
  fi

  log_success "Docker installed"
}

install_python313() {
  if python3.13 --version &>/dev/null; then
    log_warn "Python 3.13 already installed -- skipping"
    return
  fi

  log_info "Installing Python 3.13 from deadsnakes PPA..."
  add-apt-repository -y ppa:deadsnakes/ppa
  apt update
  apt install -y python3.13 python3.13-venv python3.13-dev
  log_success "Python 3.13 installed"
}

install_node22() {
  local need_install=true

  if command -v node &>/dev/null; then
    local node_major
    node_major=$(node --version | sed 's/v//' | cut -d. -f1)
    if [[ "$node_major" -ge 20 ]]; then
      need_install=false
      log_warn "Node.js $(node --version) already installed -- skipping"
    fi
  fi

  if $need_install; then
    log_info "Installing Node.js 22 from nodesource..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt install -y nodejs
    log_success "Node.js 22 installed"
  fi
}

install_bun() {
  if command -v bun &>/dev/null && [[ "$(bun --version)" == "$BUN_VERSION" ]]; then
    log_warn "Bun ${BUN_VERSION} already installed -- skipping"
    return
  fi

  log_info "Installing Bun ${BUN_VERSION}..."
  npm install --global "bun@${BUN_VERSION}"
  log_success "Bun ${BUN_VERSION} installed"
}

install_prerequisites() {
  install_docker
  install_python313
  install_node22
  install_bun
}

# -- Version validation -----------------------------------------------------

validate_versions() {
  local failed=false

  if ! command -v docker &>/dev/null; then
    log_error "docker not found"
    failed=true
  fi

  if ! docker compose version &>/dev/null; then
    log_error "docker compose not available"
    failed=true
  fi

  if ! python3.13 --version &>/dev/null; then
    log_error "python3.13 not found"
    failed=true
  fi

  if ! command -v node &>/dev/null; then
    log_error "node not found"
    failed=true
  else
    local node_major
    node_major=$(node --version | sed 's/v//' | cut -d. -f1)
    if [[ "$node_major" -lt 20 ]]; then
      log_error "Node.js version must be >= 20 (found $(node --version))"
      failed=true
    fi
  fi

  if ! command -v bun &>/dev/null; then
    log_error "bun not found"
    failed=true
  elif [[ "$(bun --version)" != "$BUN_VERSION" ]]; then
    log_error "Bun version must be ${BUN_VERSION} (found $(bun --version))"
    failed=true
  fi

  if $failed; then
    log_error "Version validation failed -- aborting"
    exit 1
  fi

  log_success "Version validation passed (docker, docker compose, python3.13, node $(node --version), bun $(bun --version))"
}

# -- Preflight checks -------------------------------------------------------

preflight_checks() {
  if ! id radon &>/dev/null; then
    log_info "Creating radon user..."
    useradd -m -s /bin/bash radon
    log_success "radon user created"
  else
    log_warn "radon user already exists -- skipping"
  fi

  # Ensure radon is in the docker group (needed even if Docker was installed before user)
  if command -v docker &>/dev/null && ! id -nG radon 2>/dev/null | grep -qw docker; then
    usermod -aG docker radon
  fi

  # Copy root's authorized_keys so radon user is accessible via SSH
  if [[ -f /root/.ssh/authorized_keys ]] && [[ ! -f /home/radon/.ssh/authorized_keys ]]; then
    log_info "Copying SSH authorized_keys to radon user..."
    mkdir -p /home/radon/.ssh
    cp /root/.ssh/authorized_keys /home/radon/.ssh/
    chown -R radon:radon /home/radon/.ssh
    chmod 700 /home/radon/.ssh
    chmod 600 /home/radon/.ssh/authorized_keys
  fi

  # Ensure radon has an SSH key for GitHub access
  if [[ ! -f /home/radon/.ssh/id_ed25519 ]]; then
    log_info "Generating SSH deploy key for radon user..."
    mkdir -p /home/radon/.ssh
    chmod 700 /home/radon/.ssh
    ssh-keygen -t ed25519 -C "radon@ib-gateway" -f /home/radon/.ssh/id_ed25519 -N "" -q
    chown -R radon:radon /home/radon/.ssh
    log_success "SSH key generated"
    echo ""
    echo -e "  ${YELLOW}ACTION REQUIRED:${NC} Add this deploy key to GitHub before continuing:"
    echo ""
    cat /home/radon/.ssh/id_ed25519.pub
    echo ""
    echo "  Go to: https://github.com/settings/keys → New SSH key"
    echo ""
    echo -e "  ${RED}Then re-run this script to continue.${NC}"
    exit 0
  fi

  # Accept GitHub host key
  if ! sudo -u radon ssh-keygen -F github.com &>/dev/null; then
    ssh-keyscan -t ed25519 github.com >> /home/radon/.ssh/known_hosts 2>/dev/null
    chown radon:radon /home/radon/.ssh/known_hosts
  fi

  if ! command -v "$PYTHON_BIN" &>/dev/null; then
    log_error "${PYTHON_BIN} not found after install_prerequisites."
    exit 1
  fi

  if ! command -v git &>/dev/null; then
    log_error "git not found after install_base_packages."
    exit 1
  fi

  if ! command -v node &>/dev/null; then
    log_error "node not found after install_prerequisites."
    exit 1
  fi

  log_success "Preflight checks passed"
}

create_etc_radon_dir() {
  # Canonical secrets and media dirs. Live units load /etc/radon/env.
  # Compatibility: ~/radon-cloud/.env and ~/radon-cloud/media are host
  # symlinks after P2, not a checkout.
  local dir="/etc/radon"
  local media="/var/lib/radon/media"
  if [[ "${RADON_HELPER_SKIP_CHOWN:-0}" == "1" ]]; then
    install -d -m 0750 "$dir"
    install -d -m 0750 "$media"
  else
    install -d -m 0750 -o radon -g radon "$dir"
    install -d -m 0750 -o radon -g radon "$media"
  fi
}

# -- Repo cloning -----------------------------------------------------------

clone_repo() {
  local target_dir="$1"
  local repo_url="$2"
  local name
  name="$(basename "$target_dir")"

  if [[ -d "$target_dir/.git" ]]; then
    log_warn "${name} already cloned at ${target_dir} -- skipping"
    return
  fi

  log_info "Cloning ${name}..."
  run_as_radon git clone "$repo_url" "$target_dir"
  log_success "${name} cloned"
}

clone_repos() {
  clone_repo "$RADON_DIR" "$RADON_REPO"
  # Monorepo: production infra lives at $RADON_DIR/cloud after the app clone.
  if [[ "$CLOUD_DIR" == "$RADON_DIR/cloud" ]]; then
    if [[ ! -d "$CLOUD_DIR/scripts" || ! -d "$CLOUD_DIR/services" ]]; then
      log_error "Monorepo checkout missing cloud/ at ${CLOUD_DIR}"
      return 1
    fi
    log_success "Using monorepo cloud/ at ${CLOUD_DIR}"
    return 0
  fi
  # Legacy dual-checkout only when CLOUD_DIR is forced outside the app tree.
  clone_repo "$CLOUD_DIR" "$CLOUD_REPO"
}

# -- Python setup ------------------------------------------------------------

refuse_active_runtime_mutation() {
  local unit
  for unit in "${SERVICE_FILES[@]}"; do
    if systemctl is-active --quiet "$unit"; then
      log_error "Refusing in-place dependency mutation while ${unit} is active"
      log_error "Use scripts/deploy.sh with the exact tested commit SHA"
      return 1
    fi
  done
}

setup_python() {
  refuse_active_runtime_mutation || return 1
  if [[ -d "$VENV_DIR" ]]; then
    log_warn "Python venv already exists at ${VENV_DIR} -- skipping creation"
  else
    log_info "Creating Python ${PYTHON_BIN} venv..."
    run_as_radon "$PYTHON_BIN" -m venv "$VENV_DIR"
    log_success "Venv created"
  fi

  log_info "Installing Python dependencies..."
  run_as_radon "${VENV_DIR}/bin/pip" install --upgrade pip --quiet
  run_as_radon "${VENV_DIR}/bin/pip" install \
    -r "${RADON_DIR}/requirements.txt" \
    -r "${RADON_DIR}/scripts/requirements-api.txt" --quiet
  log_success "Python dependencies installed"
}

# -- Node setup --------------------------------------------------------------

setup_node() {
  refuse_active_runtime_mutation || return 1
  log_info "Installing Node dependencies and building Next.js..."

  # Persist only browser-safe build variables. Server-side values are injected
  # into the build process by run_with_env.py and never copied into web/.env.
  if [[ -f "$ENV_FILE" ]]; then
    chmod 0600 "$ENV_FILE"
    chown radon:radon "$ENV_FILE"
    local public_env_tmp
    public_env_tmp="$(mktemp)"
    grep -E '^NEXT_PUBLIC_[A-Z0-9_]+=' "$ENV_FILE" > "$public_env_tmp" || true
    install -m 0600 -o radon -g radon "$public_env_tmp" "${RADON_DIR}/web/.env"
    rm -f "$public_env_tmp"
  fi

  if [[ ! -f "${RADON_DIR}/package.json" || ! -f "${RADON_DIR}/bun.lock" ]]; then
    log_error "Root package.json/bun.lock artifact contract is incomplete"
    return 1
  fi
  if [[ ! -f "${RADON_DIR}/web/package.json" || ! -f "${RADON_DIR}/web/bun.lock" ]]; then
    log_error "Web package.json/bun.lock artifact contract is incomplete"
    return 1
  fi

  run_as_radon "cd ${RADON_DIR} && bun install --frozen-lockfile"
  run_as_radon "cd ${RADON_DIR} && ./node_modules/.bin/playwright install chromium chromium-headless-shell >/dev/null"
  run_as_radon "cd ${RADON_DIR}/web && bun install --frozen-lockfile"
  run_as_radon "cd ${RADON_DIR}/web && ./node_modules/.bin/playwright install chromium chromium-headless-shell >/dev/null"
  run_as_radon "cd ${RADON_DIR}/web && ${VENV_DIR}/bin/python ${CLOUD_DIR}/scripts/run_with_env.py ${ENV_FILE} -- bun run build"
  log_success "Next.js built"
}

# -- Caddy -------------------------------------------------------------------

install_caddy() {
  if command -v caddy &>/dev/null; then
    log_warn "Caddy already installed -- skipping installation"
  else
    log_info "Installing Caddy from official repos..."
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update
    apt-get install -y caddy
    log_success "Caddy installed"
  fi

  # /var/lib/radon stays 0750 radon:radon (2FA leases live beside media/), so
  # the caddy user needs radon group membership to traverse into media/ —
  # without it every media.radon.run request 403s (2026-08-23 regression).
  # Supplementary groups apply at process start: a fresh grant needs a
  # restart, not a reload. Runs here (setup-only) because deploys reuse
  # configure_caddy, which must never restart the proxy.
  if [[ "${RADON_HELPER_SKIP_CHOWN:-0}" != "1" ]] \
    && ! id -nG caddy 2>/dev/null | grep -qw radon; then
    usermod -aG radon caddy
    if ! "$CADDY_TIMEOUT" --signal=TERM --kill-after=2s 15s \
      "$CADDY_SYSTEMCTL" restart caddy; then
      log_error "Caddy restart after radon group grant failed"
      return 1
    fi
  fi
}

configure_caddy() {
  local config_dir candidate rollback=""
  config_dir="$(dirname "$CADDY_CONFIG_PATH")"
  mkdir -p "$CADDY_LOG_DIR" "$config_dir"
  chown caddy:caddy "$CADDY_LOG_DIR"

  candidate="$(mktemp "${CADDY_CONFIG_PATH}.candidate.XXXXXX")"
  install -m 0644 "${CLOUD_DIR}/caddy/Caddyfile" "$candidate"
  if ! "$CADDY_BIN" validate --config "$candidate" --adapter caddyfile >/dev/null; then
    rm -f "$candidate"
    log_error "Caddy candidate validation failed; live configuration is unchanged"
    return 1
  fi

  if [[ -f "$CADDY_CONFIG_PATH" ]]; then
    rollback="$(mktemp "${CADDY_CONFIG_PATH}.rollback.XXXXXX")"
    cp -a "$CADDY_CONFIG_PATH" "$rollback"
    "$CADDY_SYNC" -f "$rollback"
  fi
  mv -f "$candidate" "$CADDY_CONFIG_PATH"
  "$CADDY_SYNC" -f "$CADDY_CONFIG_PATH"
  "$CADDY_SYNC" -f "$config_dir"

  if ! "$CADDY_TIMEOUT" --signal=TERM --kill-after=2s 15s \
    "$CADDY_SYSTEMCTL" reload caddy; then
    if [[ -n "$rollback" ]]; then
      mv -f "$rollback" "$CADDY_CONFIG_PATH"
      "$CADDY_SYNC" -f "$CADDY_CONFIG_PATH"
    else
      rm -f "$CADDY_CONFIG_PATH"
    fi
    "$CADDY_SYNC" -f "$config_dir"
    if [[ -n "$rollback" ]] && ! "$CADDY_TIMEOUT" --signal=TERM --kill-after=2s 15s \
      "$CADDY_SYSTEMCTL" reload caddy; then
      log_error "Caddy candidate reload failed and known-good reload reconciliation also failed"
      return 1
    fi
    log_error "Caddy candidate reload failed; restored and reloaded the known-good configuration"
    return 1
  fi
  [[ -z "$rollback" ]] || rm -f "$rollback"
  log_success "Caddy configured"
}

# -- Systemd -----------------------------------------------------------------

copy_systemd_services() {
  log_info "Copying systemd service files..."
  for svc in "${SERVICE_FILES[@]}"; do
    # Never follow a legacy /home/radon/radon-cloud symlink and mutate the
    # retired source tree. Every managed unit is a regular canonical artifact.
    rm -f "/etc/systemd/system/${svc}"
    install -m 0644 -o root -g root \
      "${CLOUD_DIR}/services/${svc}" "/etc/systemd/system/${svc}"
  done
  systemctl daemon-reload
  log_success "Systemd units copied and daemon reloaded"
}

# DUR-02 (2026-06-12): cap journald disk usage. See services/journald-radon.conf
# for rationale (beta crash loop grew the journal to 3.9G).
install_journald_limits() {
  log_info "Installing journald disk cap (SystemMaxUse=1G)..."
  mkdir -p /etc/systemd/journald.conf.d
  cp "${CLOUD_DIR}/services/journald-radon.conf" /etc/systemd/journald.conf.d/radon.conf
  # journald.conf(5): changes apply on a restart of systemd-journald. Not a
  # radon unit; restart keeps the sockets up and does not drop log streams.
  systemctl restart systemd-journald
  log_success "journald capped at SystemMaxUse=1G"
}

# DUR-07 (2026-06-12): fleet-wide env invariants via a systemd prefix drop-in.
# "radon-.service.d" matches every unit named radon-*.service (systemd v250+).
# Carries RADON_DB_NO_REPLICA=1 as belt-and-suspenders for the retired libsql
# embedded replica (code default is direct-to-cloud since DUR-07; opt-in only
# via RADON_DB_USE_REPLICA=1). Applies at daemon-reload; no unit restart.
install_fleet_dropin() {
  log_info "Installing radon-.service.d fleet drop-in (RADON_DB_NO_REPLICA=1)..."
  mkdir -p /etc/systemd/system/radon-.service.d
  cp "${CLOUD_DIR}/services/radon-.service.d/common.conf" /etc/systemd/system/radon-.service.d/common.conf
  systemctl daemon-reload
  log_success "Fleet drop-in installed (verify: systemctl show radon-api.service -p Environment)"
}
enable_services() {
  log_info "Enabling services..."
  local persistent_units=()
  local timer_units=()
  local timer_owned_services=()
  local base svc
  for svc in "${SERVICE_FILES[@]}"; do
    [[ "$svc" == "radon-ib-gateway-preheld-restart.service" ]] && continue
    if [[ "$svc" == *.timer ]]; then
      timer_units+=("$svc")
    elif [[ -f "${CLOUD_DIR}/services/${svc%.service}.timer" ]]; then
      timer_owned_services+=("$svc")
    else
      persistent_units+=("$svc")
    fi
  done
  # Upgrade repair: oneshots are timer-owned and must never be enabled as boot
  # services. Enabling both caused every scheduled job to run at once.
  systemctl disable "${timer_owned_services[@]}"
  systemctl enable "${persistent_units[@]}" "${timer_units[@]}"
  log_success "Persistent daemons and timers enabled; timer-owned oneshots disabled"
}

start_services() {
  log_info "Starting the full stack through the locked operator..."
  /usr/local/bin/radon start
  log_success "All services started"
}

# -- Firewall ----------------------------------------------------------------

open_firewall() {
  if ufw status | grep -q "80/tcp.*ALLOW"; then
    log_warn "Port 80 already open -- skipping"
  else
    ufw allow 80/tcp
    log_success "Port 80 opened"
  fi

  if ufw status | grep -q "443/tcp.*ALLOW"; then
    log_warn "Port 443 already open -- skipping"
  else
    ufw allow 443/tcp
    log_success "Port 443 opened"
  fi
}

# -- Fixed deploy privilege boundary -----------------------------------------

install_deploy_root_helper() {
  local source="${CLOUD_DIR}/scripts/deploy-root-helper.sh"
  local target="/usr/local/sbin/radon-deploy-root"
  local staged

  if [[ ! -f "$source" ]]; then
    log_error "deploy-root-helper.sh missing from ${CLOUD_DIR}/scripts"
    return 1
  fi
  staged="$(mktemp "${target}.tmp.XXXXXX")"
  install -m 0755 -o root -g root "$source" "$staged"
  if ! bash -n "$staged"; then
    rm -f "$staged"
    log_error "Deploy root helper failed syntax validation"
    return 1
  fi
  mv -f "$staged" "$target"
  log_success "Deploy root helper installed"
}

# -- Sudoers for fixed helper invocations ------------------------------------

configure_sudoers() {
  local sudoers_dir="${RADON_SUDOERS_DIR:-/etc/sudoers.d}"
  local owner="${RADON_POLICY_OWNER:-root}"
  local group="${RADON_POLICY_GROUP:-root}"
  local visudo_bin="${RADON_VISUDO_BIN:-visudo}"
  local -a owner_args=(-o "$owner" -g "$group")
  [[ "${RADON_POLICY_SKIP_CHOWN:-0}" == "1" ]] && owner_args=()
  local sources=(
    "${CLOUD_DIR}/config/sudoers.d/radon-deploy"
    "${CLOUD_DIR}/config/sudoers.d/radon-monitor"
    "${CLOUD_DIR}/config/sudoers.d/radon-ops"
  )
  local targets=(
    "${sudoers_dir}/radon-deploy"
    "${sudoers_dir}/radon-monitor"
    "${sudoers_dir}/radon-ops"
  )
  local index source target candidate staged

  log_info "Replacing deploy, watchdog, and operator sudoers policies..."
  for index in "${!sources[@]}"; do
    source="${sources[$index]}"
    target="${targets[$index]}"
    candidate="$(mktemp)"
    staged="${target}.tmp.$$"
    install -m 0440 "${owner_args[@]}" "$source" "$candidate"
    if ! "$visudo_bin" -cf "$candidate" >/dev/null; then
      rm -f "$candidate"
      log_error "Malformed sudoers candidate for $target"
      return 1
    fi
    install -m 0440 "${owner_args[@]}" "$candidate" "$staged"
    mv -f "$staged" "$target"
    rm -f "$candidate"
  done

  log_success "Sudoers configured (deploy + watchdog + sanitized ops)"
}

# -- Operator CLI -----------------------------------------------------------
#
# Installs /usr/local/bin/radon — the stop/start/restart/status helper that
# auto-enumerates every loaded radon-* unit on each run. Previously installed
# by hand on 2026-05-04; checking the canonical copy into setup-vps.sh so a
# wipe-vps.sh rebuild restores it automatically.

install_gateway_control() {
  local target="${RADON_GATEWAY_CONTROL_TARGET:-/usr/local/bin/radon-ib-gateway-control}"
  local source="${CLOUD_DIR}/scripts/ib-gateway-control.sh"
  local state_dir="${RADON_STATE_DIR:-/var/lib/radon}"
  local system_python="${RADON_SYSTEM_PYTHON:-/usr/bin/python3.13}"
  local -a owner_args=(-o root -g root)
  [[ "${RADON_HELPER_SKIP_CHOWN:-0}" == "1" ]] && owner_args=()
  local staged

  if [[ ! -f "$source" ]]; then
    log_error "ib-gateway-control.sh missing from ${CLOUD_DIR}/scripts/"
    return 1
  fi
  if [[ ! -x "$system_python" ]]; then
    log_error "System Python 3.13 is required by the Gateway control plane"
    return 1
  fi

  log_info "Installing authoritative IB Gateway control helper..."
  staged="$(mktemp "${target}.tmp.XXXXXX")"
  install -m 0755 "${owner_args[@]}" "$source" "$staged"
  if ! bash -n "$staged" || [[ ! -x "$staged" ]]; then
    rm -f "$staged"
    log_error "Gateway control candidate failed syntax/permission validation"
    return 1
  fi
  # Both the shared 2FA lease and consumed-lease marker live here. Repair the
  # ownership on upgrades as well as creating it on a clean host.
  if [[ "${RADON_HELPER_SKIP_CHOWN:-0}" == "1" ]]; then
    install -d -m 0750 "$state_dir"
  else
    install -d -m 0750 -o radon -g radon "$state_dir"
    if [[ -e /home/radon/.radon-deploy.lock ]]; then
      if [[ "$(stat -c '%U:%G:%a' /home/radon/.radon-deploy.lock)" != "radon:radon:600" ]]; then
        log_error "Deploy lock must already be radon:radon mode 0600; refusing unsafe replacement"
        return 1
      fi
    else
      install -m 0600 -o radon -g radon /dev/null /home/radon/.radon-deploy.lock
    fi
  fi
  mv -f "$staged" "$target"
  log_success "IB Gateway control helper installed"
}

install_operator_cli() {
  local target="${RADON_OPERATOR_CLI_TARGET:-/usr/local/bin/radon}"
  local source="${CLOUD_DIR}/scripts/operator-radon.sh"
  local -a owner_args=(-o root -g root)
  [[ "${RADON_HELPER_SKIP_CHOWN:-0}" == "1" ]] && owner_args=()
  local staged

  if [[ ! -f "$source" ]]; then
    log_error "operator-radon.sh missing from ${CLOUD_DIR}/scripts/"
    return 1
  fi

  log_info "Installing /usr/local/bin/radon operator CLI..."
  staged="$(mktemp "${target}.tmp.XXXXXX")"
  install -m 0755 "${owner_args[@]}" "$source" "$staged"
  if ! bash -n "$staged" || [[ ! -x "$staged" ]]; then
    rm -f "$staged"
    log_error "Operator CLI candidate failed syntax/permission validation"
    return 1
  fi
  mv -f "$staged" "$target"

  log_success "Operator CLI installed (radon {stop|start|restart|status})"
}

install_app_runtime() {
  local source="${CLOUD_DIR}/scripts/radon-app-runtime.sh"
  local target="/usr/local/sbin/radon-app-runtime"
  local -a owner_args=(-o root -g root)
  [[ "${RADON_HELPER_SKIP_CHOWN:-0}" == "1" ]] && owner_args=()
  local staged

  if [[ ! -f "$source" ]]; then
    log_error "radon-app-runtime.sh missing from ${CLOUD_DIR}/scripts/"
    return 1
  fi

  log_info "Installing /usr/local/sbin/radon-app-runtime..."
  staged="$(mktemp "${target}.tmp.XXXXXX")"
  install -m 0755 "${owner_args[@]}" "$source" "$staged"
  if ! bash -n "$staged" || [[ ! -x "$staged" ]]; then
    rm -f "$staged"
    log_error "App runtime candidate failed syntax/permission validation"
    return 1
  fi
  mv -f "$staged" "$target"

  log_success "App runtime wrapper installed"
}

# The only direct systemd privilege left to the radon user is the watchdog's
# fixed preheld adapter. All other mutations route through /usr/local/bin/radon.
install_admin_polkit_rule() {
  local rules_dir="${RADON_POLKIT_RULES_DIR:-/etc/polkit-1/rules.d}"
  local source="${CLOUD_DIR}/config/polkit/50-radon-services.rules"
  local target="${rules_dir}/50-radon-services.rules"
  local owner="${RADON_POLICY_OWNER:-root}"
  local group="${RADON_POLICY_GROUP:-root}"
  local -a owner_args=(-o "$owner" -g "$group")
  [[ "${RADON_POLICY_SKIP_CHOWN:-0}" == "1" ]] && owner_args=()
  local staged="${target}.tmp.$$"

  log_info "Installing exact watchdog preheld-unit polkit rule..."
  install -m 0644 "${owner_args[@]}" "$source" "$staged"
  mv -f "$staged" "$target"

  if [[ "${RADON_SKIP_POLKIT_RELOAD:-0}" != "1" ]] && systemctl is-active --quiet polkit; then
    systemctl restart polkit || log_warn "Failed to restart polkit; rule may not be active until reboot"
  fi

  log_success "Polkit rule installed (Gateway excluded except fixed preheld unit)"
}

# -- Environment validation -------------------------------------------------

validate_env() {
  local env_file="$ENV_FILE"

  if [[ ! -f "$env_file" ]]; then
    log_error ".env file not found at ${env_file}"
    echo "  Copy the example and fill in your values:"
    echo "    install -m 0600 -o radon -g radon ${CLOUD_DIR}/.env.example ${env_file}"
    return 1
  fi

  chmod 0600 "$env_file"
  chown radon:radon "$env_file"

  if ! run_as_radon "$PYTHON_BIN" "${CLOUD_DIR}/scripts/check-env.py" \
    "$env_file" "${CLOUD_DIR}/config/required-env.txt"; then
    log_error "Environment does not satisfy config/required-env.txt"
    return 1
  fi

  log_success "Environment validated"
}

# -- Main --------------------------------------------------------------------

main() {
  log_info "Starting Radon VPS bootstrap..."
  echo ""

  install_base_packages
  install_prerequisites
  validate_versions
  preflight_checks
  create_etc_radon_dir
  clone_repos
  validate_env
  setup_python
  setup_node
  install_caddy
  configure_caddy
  copy_systemd_services
  install_gateway_control
  install_journald_limits
  install_fleet_dropin
  enable_services
  open_firewall
  install_deploy_root_helper
  install_operator_cli
  install_app_runtime
  configure_sudoers
  install_admin_polkit_rule
  start_services

  echo ""
  log_success "Bootstrap complete."
  echo -e "  Verify with: ${GREEN}curl https://app.radon.run/health${NC}"
}

if [[ "${RADON_SETUP_SOURCE_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
